"""Galaxy backend — HTTP client for Galaxy REST API.

This module handles all communication with a running Galaxy server instance.
Galaxy is a hard dependency: the CLI is useless without a reachable server.
"""

import base64
import hashlib
import json
import os
import stat
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests.adapters import TimeoutSauce

DEFAULT_CONFIG_DIR = Path.home() / ".galaxy-cli"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_CONNECT_TIMEOUT = 30
DEFAULT_REQUEST_TIMEOUT = 60
DEFAULT_UPLOAD_TIMEOUT = 300
DEFAULT_TIMEOUT = DEFAULT_REQUEST_TIMEOUT
RETRYABLE_GET_STATUS_CODES = {429, 502, 503, 504}
MIN_RETRY_DELAY = 0.0
MAX_RETRY_DELAY = 60.0


# Exit codes for structured error reporting
EXIT_OK = 0
EXIT_USER_ERROR = 1       # bad input, wrong parameter, missing argument
EXIT_SERVER_ERROR = 2     # Galaxy 5xx, unexpected server response
EXIT_AUTH_ERROR = 3       # 401/403, missing or invalid API key
EXIT_TIMEOUT = 4          # connection or request timeout


def get_with_deadline(client, path, params=None, deadline=None):
    """GET with an absolute monotonic deadline when the client supports it.

    Lightweight fake clients used by callers and tests can keep implementing
    only ``get(path, params=None)``; they transparently use that legacy path.
    """
    deadline_get = getattr(type(client), "get_with_deadline", None)
    if callable(deadline_get):
        return deadline_get(client, path, params=params, deadline=deadline)
    if params is None:
        return client.get(path)
    return client.get(path, params=params)


class GalaxyBackendError(Exception):
    """Raised when the Galaxy backend returns an error or is unreachable.

    Attributes:
        category: Error category string for machine-readable output.
        exit_code: Process exit code for this error type.
        suggestion: Optional suggestion for how to fix the error.
    """

    def __init__(
        self,
        message,
        category="unknown",
        exit_code=EXIT_SERVER_ERROR,
        suggestion=None,
        *,
        status_code=None,
        error_kind=None,
        submission_state=None,
        retry_safe=None,
        details=None,
    ):
        super().__init__(message)
        self.category = category
        self.exit_code = exit_code
        self.suggestion = suggestion
        self.status_code = status_code
        self.error_kind = error_kind
        self.submission_state = submission_state
        self.retry_safe = retry_safe
        self.details = dict(details or {})

    def to_dict(self):
        """Return a machine-readable error dict."""
        d = {
            "error": True,
            "category": self.category,
            "message": str(self),
        }
        if self.submission_state is not None or self.retry_safe is not None:
            d["success"] = False
        if self.error_kind:
            d["error_kind"] = self.error_kind
        if self.submission_state is not None:
            d["submission_state"] = self.submission_state
        if self.retry_safe is not None:
            d["retry_safe"] = bool(self.retry_safe)
        if self.status_code is not None:
            d["status_code"] = self.status_code
        if self.suggestion:
            d["suggestion"] = self.suggestion
        for key, value in self.details.items():
            if key not in d:
                d[key] = value
        return d


def _parse_timeout_seconds(value, label):
    """Parse a positive timeout value from CLI/env/config input."""
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise GalaxyBackendError(
            f"Invalid {label}: {value!r}. Expected a positive number of seconds.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        ) from exc
    if parsed <= 0:
        raise GalaxyBackendError(
            f"Invalid {label}: {value!r}. Expected a positive number of seconds.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        )
    return parsed


def read_api_key_file(file_path):
    """Read a Galaxy API key from a secret file without exposing its value."""
    path = Path(file_path)
    try:
        if not path.exists():
            raise GalaxyBackendError(
                "GALAXY_API_KEY_FILE does not exist.",
                category="auth",
                exit_code=EXIT_AUTH_ERROR,
            )
        if not path.is_file():
            raise GalaxyBackendError(
                "GALAXY_API_KEY_FILE is not a file.",
                category="auth",
                exit_code=EXIT_AUTH_ERROR,
            )
        if not path.stat().st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
            raise OSError("file has no read permission bits")
        api_key = path.read_text().rstrip()
    except GalaxyBackendError:
        raise
    except OSError as exc:
        raise GalaxyBackendError(
            "GALAXY_API_KEY_FILE is not readable.",
            category="auth",
            exit_code=EXIT_AUTH_ERROR,
        ) from exc
    if not api_key:
        raise GalaxyBackendError(
            "GALAXY_API_KEY_FILE is empty.",
            category="auth",
            exit_code=EXIT_AUTH_ERROR,
        )
    return api_key


class GalaxyClient:
    """HTTP client for the Galaxy REST API.

    Authentication priority (first non-empty wins, per field):
    1. Explicit url/api_key parameters
    2. Environment variables GALAXY_URL / GALAXY_API_KEY
    3. Secret file from GALAXY_API_KEY_FILE (API key only)
    4. Selected profile (--profile NAME → profiles[NAME])
    5. Active profile (config["active_profile"] → profiles[active])
    6. Legacy top-level url / api_key (older config format)
    """

    def __init__(
        self,
        url=None,
        api_key=None,
        profile=None,
        timeout=None,
        request_timeout=None,
        upload_timeout=None,
        connect_timeout=DEFAULT_CONNECT_TIMEOUT,
        max_get_retries=3,
        retry_backoff=1.0,
    ):
        self.url = (
            url
            or os.environ.get("GALAXY_URL")
            or self._load_config_value("url", profile=profile)
        )
        env_api_key = os.environ.get("GALAXY_API_KEY")
        file_api_key = None
        if not api_key and not env_api_key and os.environ.get("GALAXY_API_KEY_FILE"):
            file_api_key = read_api_key_file(os.environ["GALAXY_API_KEY_FILE"])
        self.api_key = api_key or env_api_key or file_api_key or self._load_config_value(
            "api_key", profile=profile
        )
        # `timeout` is kept as a backwards-compatible alias for request_timeout.
        raw_request_timeout = (
            request_timeout
            if request_timeout is not None
            else timeout
            if timeout is not None
            else os.environ.get("GALAXY_CLI_REQUEST_TIMEOUT")
            or DEFAULT_REQUEST_TIMEOUT
        )
        self.request_timeout = _parse_timeout_seconds(raw_request_timeout, "request timeout")
        self.timeout = self.request_timeout
        raw_upload_timeout = (
            upload_timeout
            if upload_timeout is not None
            else os.environ.get("GALAXY_CLI_UPLOAD_TIMEOUT")
        )
        self.upload_timeout = (
            _parse_timeout_seconds(raw_upload_timeout, "upload timeout")
            if raw_upload_timeout is not None
            else max(self.request_timeout, DEFAULT_UPLOAD_TIMEOUT)
        )
        self.connect_timeout = _parse_timeout_seconds(connect_timeout, "connect timeout")
        self.max_get_retries = max(0, int(max_get_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))

        if not self.url:
            raise GalaxyBackendError(
                "Galaxy server URL not configured.",
                category="auth",
                exit_code=EXIT_AUTH_ERROR,
                suggestion="export GALAXY_URL=https://usegalaxy.org  or  galaxy-cli profile add <name> --url <url> --api-key <key>",
            )
        if not self.api_key:
            raise GalaxyBackendError(
                "Galaxy API key not configured.",
                category="auth",
                exit_code=EXIT_AUTH_ERROR,
                suggestion="export GALAXY_API_KEY=<key>  or  galaxy-cli profile add <name> --url <url> --api-key <key>",
            )

        # Normalize URL
        if not self.url.endswith("/"):
            self.url += "/"

    @classmethod
    def _load_config_value(cls, key, profile=None):
        """Resolve url/api_key from config, honoring profile selection.

        Lookup order:
          - if `profile` is given → profiles[profile][key]
          - else → profiles[active_profile][key]
          - else → legacy top-level config[key]
        """
        data = cls.load_config()
        if profile:
            return data.get("profiles", {}).get(profile, {}).get(key)
        active = data.get("active_profile")
        if active:
            val = data.get("profiles", {}).get(active, {}).get(key)
            if val:
                return val
        return data.get(key)

    @staticmethod
    def _write_config(data):
        DEFAULT_CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(DEFAULT_CONFIG_DIR, 0o700)
        DEFAULT_CONFIG_FILE.write_text(json.dumps(data, indent=2))
        os.chmod(DEFAULT_CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)

    @staticmethod
    def save_config(key, value):
        """Save a top-level configuration value (legacy path)."""
        data = GalaxyClient.load_config()
        data[key] = value
        GalaxyClient._write_config(data)

    @staticmethod
    def load_config():
        """Load the full config dict."""
        if DEFAULT_CONFIG_FILE.exists():
            try:
                return json.loads(DEFAULT_CONFIG_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    @staticmethod
    def save_profile(name, url, api_key, use=False):
        """Create or update a profile; optionally mark it active."""
        data = GalaxyClient.load_config()
        profiles = data.setdefault("profiles", {})
        profiles[name] = {"url": url.rstrip("/"), "api_key": api_key}
        if use or "active_profile" not in data:
            data["active_profile"] = name
        GalaxyClient._write_config(data)
        return profiles[name]

    @staticmethod
    def use_profile(name):
        """Mark `name` as the active profile. Raises KeyError if missing."""
        data = GalaxyClient.load_config()
        if name not in data.get("profiles", {}):
            raise KeyError(name)
        data["active_profile"] = name
        GalaxyClient._write_config(data)
        return name

    @staticmethod
    def remove_profile(name):
        """Delete a profile. Raises KeyError if missing."""
        data = GalaxyClient.load_config()
        profiles = data.get("profiles", {})
        if name not in profiles:
            raise KeyError(name)
        del profiles[name]
        if data.get("active_profile") == name:
            data.pop("active_profile", None)
        GalaxyClient._write_config(data)

    def _headers(self):
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    def redact(self, value):
        """Remove configured credentials from text destined for output."""
        text = str(value)
        return text.replace(self.api_key, "[REDACTED]") if self.api_key else text

    def _upload_headers(self):
        return {"x-api-key": self.api_key}

    def _api_url(self, path):
        return urljoin(self.url, f"api/{path.lstrip('/')}")

    def _timeout_tuple(self, read_timeout=None):
        return (self.connect_timeout, read_timeout or self.request_timeout)

    def _retry_after_seconds(self, resp, attempt):
        retry_after = resp.headers.get("Retry-After") if resp.headers else None
        if retry_after:
            try:
                return min(
                    MAX_RETRY_DELAY,
                    max(MIN_RETRY_DELAY, float(retry_after)),
                )
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return min(
                        MAX_RETRY_DELAY,
                        max(MIN_RETRY_DELAY, retry_at.timestamp() - time.time()),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(
            MAX_RETRY_DELAY,
            max(MIN_RETRY_DELAY, self.retry_backoff * (2 ** attempt)),
        )

    @staticmethod
    def _deadline_error():
        return GalaxyBackendError(
            "Galaxy GET deadline expired.",
            category="timeout",
            error_kind="request_deadline",
            exit_code=EXIT_TIMEOUT,
        )

    def _deadline_remaining(self, deadline):
        if deadline is None:
            return None
        try:
            remaining = float(deadline) - time.monotonic()
        except (TypeError, ValueError):
            raise GalaxyBackendError(
                "Invalid absolute request deadline.",
                category="invalid_request",
                exit_code=EXIT_USER_ERROR,
            ) from None
        if remaining <= 0:
            raise self._deadline_error()
        return remaining

    def _request(self, method, path, deadline=None, **kwargs):
        """Issue an HTTP request and normalize transport-layer failures."""
        method = method.upper()
        attempt = 0
        while True:
            remaining = self._deadline_remaining(deadline)
            request_timeout = self._timeout_tuple()
            if remaining is not None:
                request_timeout = TimeoutSauce(
                    total=remaining,
                    connect=min(self.connect_timeout, remaining),
                    read=min(self.request_timeout, remaining),
                )
            try:
                resp = requests.request(
                    method,
                    self._api_url(path),
                    timeout=request_timeout,
                    **kwargs,
                )
            except requests.exceptions.SSLError as exc:
                raise GalaxyBackendError(
                    f"TLS/SSL handshake failed while connecting to Galaxy at {self.url}",
                    category="connection",
                    exit_code=EXIT_SERVER_ERROR,
                    suggestion="Use a modern Python build (uv-managed or Homebrew Python 3.9+).",
                ) from exc
            except requests.ConnectionError as exc:
                raise GalaxyBackendError(
                    f"Cannot connect to Galaxy at {self.url}",
                    category="connection",
                    exit_code=EXIT_SERVER_ERROR,
                    suggestion="Ensure the Galaxy server is running and reachable.",
                ) from exc
            except requests.Timeout as exc:
                if deadline is not None:
                    raise GalaxyBackendError(
                        "Galaxy GET did not complete within its polling deadline.",
                        category="timeout",
                        error_kind="request_deadline",
                        exit_code=EXIT_TIMEOUT,
                    ) from None
                raise GalaxyBackendError(
                    f"Request to Galaxy timed out after {self.request_timeout}s",
                    category="timeout",
                    exit_code=EXIT_TIMEOUT,
                    suggestion="Increase request timeout or check server load.",
                ) from exc

            self._deadline_remaining(deadline)
            if (
                method == "GET"
                and resp.status_code in RETRYABLE_GET_STATUS_CODES
                and attempt < self.max_get_retries
            ):
                delay = self._retry_after_seconds(resp, attempt)
                remaining = self._deadline_remaining(deadline)
                if remaining is not None and delay >= remaining:
                    time.sleep(remaining)
                    raise self._deadline_error()
                time.sleep(delay)
                attempt += 1
                continue
            return resp

    def _handle_response(self, resp):
        """Parse response and raise on errors."""
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            detail = None
            try:
                detail = resp.json()
                if isinstance(detail, dict):
                    msg = detail.get("err_msg") or detail.get("detail") or str(detail)
                else:
                    msg = str(detail)
            except ValueError:
                msg = resp.text[:500]

            msg = self.redact(msg)[:500]

            status = resp.status_code
            if status in (401, 403):
                raise GalaxyBackendError(
                    f"Galaxy API auth error ({status}): {msg}",
                    category="auth",
                    exit_code=EXIT_AUTH_ERROR,
                    suggestion="Check your API key with: galaxy-cli config test",
                    status_code=status,
                ) from exc
            elif status == 404:
                raise GalaxyBackendError(
                    f"Galaxy API not found ({status}): {msg}",
                    category="not_found",
                    exit_code=EXIT_USER_ERROR,
                    status_code=status,
                ) from exc
            elif status in (400, 422):
                details = None
                if status == 422 and isinstance(detail, dict):
                    errors = detail.get("detail")
                    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                        first = errors[0]
                        location = first.get("loc") or []
                        path = ".".join(str(part) for part in location if part != "body")
                        validation = {
                            "path": f"$.{path}" if path else "$",
                            "expected": self.redact(first.get("msg", "valid input")),
                        }
                        if first.get("type"):
                            validation["validation_type"] = self.redact(first["type"])
                        if "input" in first:
                            input_value = first.get("input")
                            validation["received_type"] = (
                                "null" if input_value is None
                                else "boolean" if isinstance(input_value, bool)
                                else "array" if isinstance(input_value, list)
                                else "object" if isinstance(input_value, dict)
                                else "number" if isinstance(input_value, (int, float))
                                else type(input_value).__name__
                            )
                        context = first.get("ctx")
                        if isinstance(context, dict):
                            allowed = (
                                context.get("allowed_values")
                                or context.get("permitted")
                                or context.get("expected")
                            )
                            if isinstance(allowed, (list, tuple, set)):
                                values = [self.redact(value) for value in allowed]
                                validation["allowed_values"] = values[:25]
                                if len(values) > 25:
                                    validation["allowed_values_truncated"] = True
                        details = {"validation": validation}
                        msg = f"validation failed at {validation['path']}: {validation['expected']}"
                raise GalaxyBackendError(
                    f"Galaxy API bad request ({status}): {msg}",
                    category="invalid_request",
                    exit_code=EXIT_USER_ERROR,
                    status_code=status,
                    details=details,
                ) from exc
            elif status == 405:
                raise GalaxyBackendError(
                    f"Galaxy API method not allowed ({status}): {msg}",
                    category="method_not_allowed",
                    exit_code=EXIT_USER_ERROR,
                    status_code=status,
                ) from exc
            elif status >= 500:
                raise GalaxyBackendError(
                    f"Galaxy server error ({status}): {msg}",
                    category="server_error",
                    exit_code=EXIT_SERVER_ERROR,
                    status_code=status,
                ) from exc
            else:
                raise GalaxyBackendError(
                    f"Galaxy API error ({status}): {msg}",
                    category="api_error",
                    exit_code=EXIT_SERVER_ERROR,
                    status_code=status,
                ) from exc

        if resp.status_code == 204:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def get(self, path, params=None):
        """GET request to Galaxy API."""
        resp = self._request(
            "GET",
            path,
            headers=self._headers(),
            params=params,
        )
        return self._handle_response(resp)

    def get_with_deadline(self, path, params=None, deadline=None):
        """GET bounded by an absolute ``time.monotonic()`` deadline."""
        if deadline is None:
            return self.get(path, params=params)
        resp = self._request(
            "GET",
            path,
            deadline=deadline,
            headers=self._headers(),
            params=params,
        )
        return self._handle_response(resp)

    def post(self, path, data=None, json_data=None, params=None):
        """POST request to Galaxy API."""
        resp = self._request(
            "POST",
            path,
            headers=self._headers(),
            data=data,
            json=json_data,
            params=params,
        )
        return self._handle_response(resp)

    def put(self, path, json_data=None):
        """PUT request to Galaxy API."""
        resp = self._request(
            "PUT",
            path,
            headers=self._headers(),
            json=json_data,
        )
        return self._handle_response(resp)

    def patch(self, path, json_data=None):
        """PATCH request to Galaxy API."""
        resp = self._request(
            "PATCH",
            path,
            headers=self._headers(),
            json=json_data,
        )
        return self._handle_response(resp)

    def delete(self, path, json_data=None):
        """DELETE request to Galaxy API."""
        resp = self._request(
            "DELETE",
            path,
            headers=self._headers(),
            json=json_data,
        )
        return self._handle_response(resp)

    def upload_file(self, file_path, history_id, file_type="auto", dbkey="?", upload_timeout=None):
        """Upload a file to a Galaxy history using the upload tool."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise GalaxyBackendError(
                f"File not found: {file_path}",
                category="invalid_request",
                exit_code=EXIT_USER_ERROR,
            )

        payload = {
            "tool_id": "upload1",
            "history_id": history_id,
            "inputs": json.dumps({
                "file_type": file_type,
                "dbkey": dbkey,
                "files_0|type": "upload_dataset",
                "files_0|to_posix_lines": False,
            }),
        }
        effective_upload_timeout = (
            _parse_timeout_seconds(upload_timeout, "upload timeout")
            if upload_timeout is not None
            else self.upload_timeout
        )
        with open(file_path, "rb") as handle:
            files = {"files_0|file_data": (file_path.name, handle)}
            try:
                resp = requests.post(
                    self._api_url("tools"),
                    headers=self._upload_headers(),
                    data=payload,
                    files=files,
                    timeout=self._timeout_tuple(effective_upload_timeout),
                )
            except requests.exceptions.SSLError as exc:
                raise GalaxyBackendError(
                    f"TLS/SSL handshake failed during upload to {self.url}",
                    category="connection",
                    exit_code=EXIT_SERVER_ERROR,
                    suggestion="Use a modern Python build (uv-managed or Homebrew Python 3.9+).",
                ) from exc
            except requests.ConnectionError as exc:
                raise GalaxyBackendError(
                    f"Cannot connect to Galaxy at {self.url}",
                    category="connection",
                    exit_code=EXIT_SERVER_ERROR,
                    suggestion="Ensure the Galaxy server is running and reachable.",
                ) from exc
            except requests.Timeout as exc:
                raise GalaxyBackendError(
                    f"Upload timed out after {effective_upload_timeout}s",
                    category="timeout",
                    exit_code=EXIT_TIMEOUT,
                    suggestion="Increase upload timeout or try a smaller file.",
                ) from exc
        return self._handle_response(resp)

    def tus_upload_file(
        self, file_path, history_id, file_type="auto", dbkey="?",
        upload_timeout=None, chunk_size=10**7, progress=None,
    ):
        """Upload through Galaxy's TUS service, then submit the fetch tool once."""
        file_path = Path(file_path)
        size = file_path.stat().st_size
        timeout = upload_timeout or self.upload_timeout
        metadata = base64.b64encode(file_path.name.encode("utf-8")).decode("ascii")
        headers = {
            "x-api-key": self.api_key,
            "Tus-Resumable": "1.0.0",
            "Upload-Length": str(size),
            "Upload-Metadata": f"filename {metadata}",
        }
        try:
            response = requests.post(
                self._api_url("upload/resumable_upload"), headers=headers,
                timeout=self._timeout_tuple(timeout), allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GalaxyBackendError(
                "TUS upload session creation failed with unknown submission state.",
                category="connection", error_kind="tus_submission_unknown",
                exit_code=EXIT_SERVER_ERROR, submission_state="unknown", retry_safe=False,
            ) from exc
        if response.status_code not in {200, 201}:
            return self._handle_response(response)
        location = response.headers.get("Location", "")
        if not location:
            raise GalaxyBackendError(
                "Galaxy TUS endpoint did not return an upload location.",
                category="api_error", error_kind="tus_response_invalid",
                exit_code=EXIT_SERVER_ERROR, submission_state="unknown", retry_safe=False,
            )
        upload_url = urljoin(self._api_url("upload/resumable_upload"), location)
        session_id = upload_url.rstrip("/").rsplit("/", 1)[-1]
        offset = 0
        source_digest = hashlib.sha256()
        with open(file_path, "rb") as handle:
            while offset < size:
                chunk = handle.read(chunk_size)
                if not chunk:
                    raise GalaxyBackendError(
                        "The local TUS upload source changed during upload.",
                        category="invalid_request",
                        error_kind="tus_local_file_changed",
                        exit_code=EXIT_USER_ERROR,
                        submission_state="submitted",
                        retry_safe=False,
                        details={
                            "tus_session_id": session_id,
                            "upload_offset": offset,
                            "local_file_size": size,
                            "local_file_sha256": source_digest.hexdigest(),
                        },
                    )
                source_digest.update(chunk)
                patch_headers = {
                    "x-api-key": self.api_key,
                    "Tus-Resumable": "1.0.0",
                    "Upload-Offset": str(offset),
                    "Content-Type": "application/offset+octet-stream",
                }
                try:
                    patched = requests.patch(
                        upload_url, headers=patch_headers, data=chunk,
                        timeout=self._timeout_tuple(timeout), allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    if progress is not None:
                        progress(
                            "TUS upload interrupted; computing the resumable "
                            "SHA-256 fingerprint before returning. Press Ctrl-C "
                            "to cancel (the interrupted upload will not be resumable)."
                        )
                    last_progress = time.monotonic()
                    while True:
                        remaining_chunk = handle.read(chunk_size)
                        if not remaining_chunk:
                            break
                        source_digest.update(remaining_chunk)
                        if (
                            progress is not None
                            and time.monotonic() - last_progress >= 5
                        ):
                            progress(
                                f"Fingerprinting interrupted upload: "
                                f"{handle.tell()} / {size} bytes"
                            )
                            last_progress = time.monotonic()
                    raise GalaxyBackendError(
                        "TUS upload interrupted; resume the recorded operation instead of retrying.",
                        category="connection", error_kind="tus_upload_interrupted",
                        exit_code=EXIT_SERVER_ERROR, submission_state="submitted", retry_safe=False,
                        details={
                            "tus_session_id": session_id,
                            "upload_offset": offset,
                            "local_file_size": size,
                            "local_file_sha256": source_digest.hexdigest(),
                            "fingerprint_completed_after_interruption": True,
                        },
                    ) from exc
                if patched.status_code not in {200, 204}:
                    self._handle_response(patched)
                try:
                    offset = int(patched.headers.get("Upload-Offset", offset + len(chunk)))
                except ValueError:
                    offset += len(chunk)
            while True:
                remaining_chunk = handle.read(chunk_size)
                if not remaining_chunk:
                    break
                source_digest.update(remaining_chunk)
        payload = {
            "history_id": history_id,
            "targets": [{
                "destination": {"type": "hdas"},
                "elements": [{
                    "src": "files", "ext": file_type, "dbkey": dbkey,
                    "to_posix_lines": True, "space_to_tab": False,
                    "name": file_path.name,
                }],
            }],
            "files_0|file_data": {"session_id": session_id, "name": file_path.name},
            "auto_decompress": False,
        }
        try:
            return self.post("tools/fetch", json_data=payload)
        except GalaxyBackendError as exc:
            exc.details = dict(
                exc.details or {},
                tus_session_id=session_id,
                upload_offset=offset,
                local_file_size=size,
                local_file_sha256=source_digest.hexdigest(),
            )
            exc.submission_state = "unknown"
            exc.retry_safe = False
            raise

    def resume_tus_upload_file(
        self, file_path, history_id, session_id, file_type="auto", dbkey="?",
        upload_timeout=None, chunk_size=10**7, before_fetch_submit=None,
        expected_size=None, expected_sha256=None,
    ):
        """Resume an existing TUS session, then submit fetch exactly once."""
        file_path = Path(file_path)
        upload_url = self._api_url(f"upload/resumable_upload/{session_id}")
        timeout = upload_timeout or self.upload_timeout
        headers = {"x-api-key": self.api_key, "Tus-Resumable": "1.0.0"}
        try:
            handle = open(file_path, "rb")
        except OSError as exc:
            raise GalaxyBackendError(
                "The local TUS upload source is unavailable.",
                category="invalid_request",
                error_kind="tus_local_file_unavailable",
                exit_code=EXIT_USER_ERROR,
                submission_state="submitted",
                retry_safe=False,
            ) from exc
        with handle:
            local_size = os.fstat(handle.fileno()).st_size
            if expected_sha256 is not None:
                digest = hashlib.sha256()
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                actual_sha256 = digest.hexdigest()
                if local_size != expected_size or actual_sha256 != expected_sha256:
                    raise GalaxyBackendError(
                        "The local TUS upload source changed after interruption.",
                        category="invalid_request",
                        error_kind="tus_local_file_changed",
                        exit_code=EXIT_USER_ERROR,
                        submission_state="submitted",
                        retry_safe=False,
                        details={
                            "expected_size": expected_size,
                            "current_size": local_size,
                        },
                    )
            try:
                head = requests.head(
                    upload_url, headers=headers,
                    timeout=self._timeout_tuple(timeout), allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise GalaxyBackendError(
                    "Unable to inspect the existing TUS upload session.",
                    category="connection", error_kind="tus_resume_unavailable",
                    exit_code=EXIT_SERVER_ERROR, submission_state="submitted",
                    retry_safe=False,
                ) from exc
            if head.status_code not in {200, 204}:
                self._handle_response(head)
            try:
                offset = int(head.headers.get("Upload-Offset", 0))
            except (TypeError, ValueError):
                offset = -1
            if offset < 0 or offset > local_size:
                raise GalaxyBackendError(
                    "Galaxy returned an invalid TUS upload offset.",
                    category="api_error",
                    error_kind="tus_offset_invalid",
                    exit_code=EXIT_SERVER_ERROR,
                    submission_state="submitted",
                    retry_safe=False,
                    details={"upload_offset": offset, "local_file_size": local_size},
                )
            handle.seek(offset)
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                patch_headers = dict(headers, **{
                    "Upload-Offset": str(offset),
                    "Content-Type": "application/offset+octet-stream",
                })
                try:
                    response = requests.patch(
                        upload_url, headers=patch_headers, data=chunk,
                        timeout=self._timeout_tuple(timeout), allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    raise GalaxyBackendError(
                        "TUS upload was interrupted again.", category="connection",
                        error_kind="tus_upload_interrupted", exit_code=EXIT_SERVER_ERROR,
                        submission_state="submitted", retry_safe=False,
                        details={"tus_session_id": session_id, "upload_offset": offset},
                    ) from exc
                if response.status_code not in {200, 204}:
                    self._handle_response(response)
                previous_offset = offset
                try:
                    offset = int(
                        response.headers.get(
                            "Upload-Offset", previous_offset + len(chunk)
                        )
                    )
                except (TypeError, ValueError):
                    offset = -1
                if offset <= previous_offset or offset > local_size:
                    raise GalaxyBackendError(
                        "Galaxy returned an invalid TUS upload offset.",
                        category="api_error",
                        error_kind="tus_offset_invalid",
                        exit_code=EXIT_SERVER_ERROR,
                        submission_state="submitted",
                        retry_safe=False,
                        details={
                            "upload_offset": offset,
                            "previous_offset": previous_offset,
                            "local_file_size": local_size,
                        },
                    )
        payload = {
            "history_id": history_id,
            "targets": [{"destination": {"type": "hdas"}, "elements": [{
                "src": "files", "ext": file_type, "dbkey": dbkey,
                "to_posix_lines": True, "space_to_tab": False, "name": file_path.name,
            }]}],
            "files_0|file_data": {"session_id": session_id, "name": file_path.name},
            "auto_decompress": False,
        }
        if before_fetch_submit is not None:
            before_fetch_submit()
        try:
            return self.post("tools/fetch", json_data=payload)
        except GalaxyBackendError as exc:
            exc.details = dict(
                exc.details or {}, tus_session_id=session_id, upload_offset=offset
            )
            exc.error_kind = "tus_fetch_submission_unknown"
            exc.submission_state = "unknown"
            exc.retry_safe = False
            raise

    def download_dataset(self, dataset_id, output_path, max_bytes=None):
        """Download a dataset to a local file."""
        resp = self._request(
            "GET",
            f"datasets/{dataset_id}/display",
            headers=self._headers(),
            stream=True,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            resp.close()
            raise GalaxyBackendError(
                f"Failed to download dataset {dataset_id}: {resp.status_code}",
                category="api_error",
                exit_code=EXIT_SERVER_ERROR,
            ) from exc

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if max_bytes is not None and written + len(chunk) > max_bytes:
                        raise GalaxyBackendError(
                            "Dataset exceeded the bounded preview download limit.",
                            category="invalid_request",
                            error_kind="preview_download_too_large",
                            exit_code=EXIT_USER_ERROR,
                            suggestion="Use 'galaxy-cli dataset download' for an explicit full download.",
                            details={"max_download_bytes": max_bytes},
                        )
                    f.write(chunk)
                    written += len(chunk)
        except Exception:
            try:
                output_path.unlink()
            except OSError:
                pass
            raise
        finally:
            resp.close()
        return {"output": str(output_path), "size": output_path.stat().st_size}

    def get_version(self):
        """Get Galaxy server version."""
        return self.get("version")

    def whoami(self):
        """Get current user info."""
        return self.get("users/current")
