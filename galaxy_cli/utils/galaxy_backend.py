"""Galaxy backend — HTTP client for Galaxy REST API.

This module handles all communication with a running Galaxy server instance.
Galaxy is a hard dependency: the CLI is useless without a reachable server.
"""

import json
import os
import stat
from pathlib import Path
from urllib.parse import urljoin

import requests

DEFAULT_CONFIG_DIR = Path.home() / ".galaxy-cli"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_TIMEOUT = 60


# Exit codes for structured error reporting
EXIT_OK = 0
EXIT_USER_ERROR = 1       # bad input, wrong parameter, missing argument
EXIT_SERVER_ERROR = 2     # Galaxy 5xx, unexpected server response
EXIT_AUTH_ERROR = 3       # 401/403, missing or invalid API key
EXIT_TIMEOUT = 4          # connection or request timeout


class GalaxyBackendError(Exception):
    """Raised when the Galaxy backend returns an error or is unreachable.

    Attributes:
        category: Error category string for machine-readable output.
        exit_code: Process exit code for this error type.
        suggestion: Optional suggestion for how to fix the error.
    """

    def __init__(self, message, category="unknown", exit_code=EXIT_SERVER_ERROR,
                 suggestion=None):
        super().__init__(message)
        self.category = category
        self.exit_code = exit_code
        self.suggestion = suggestion

    def to_dict(self):
        """Return a machine-readable error dict."""
        d = {
            "error": True,
            "category": self.category,
            "message": str(self),
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


class GalaxyClient:
    """HTTP client for the Galaxy REST API.

    Authentication priority (first non-empty wins, per field):
    1. Explicit url/api_key parameters
    2. Environment variables GALAXY_URL / GALAXY_API_KEY
    3. Selected profile (--profile NAME → profiles[NAME])
    4. Active profile (config["active_profile"] → profiles[active])
    5. Legacy top-level url / api_key (older config format)
    """

    def __init__(self, url=None, api_key=None, profile=None, timeout=DEFAULT_TIMEOUT):
        self.url = (
            url
            or os.environ.get("GALAXY_URL")
            or self._load_config_value("url", profile=profile)
        )
        self.api_key = (
            api_key
            or os.environ.get("GALAXY_API_KEY")
            or self._load_config_value("api_key", profile=profile)
        )
        self.timeout = timeout

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

    def _upload_headers(self):
        return {"x-api-key": self.api_key}

    def _api_url(self, path):
        return urljoin(self.url, f"api/{path.lstrip('/')}")

    def _request(self, method, path, **kwargs):
        """Issue an HTTP request and normalize transport-layer failures."""
        try:
            return requests.request(
                method,
                self._api_url(path),
                timeout=self.timeout,
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
            raise GalaxyBackendError(
                f"Request to Galaxy timed out after {self.timeout}s",
                category="timeout",
                exit_code=EXIT_TIMEOUT,
                suggestion="Increase timeout or check server load.",
            ) from exc

    def _handle_response(self, resp):
        """Parse response and raise on errors."""
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = resp.json()
                if isinstance(detail, dict):
                    msg = detail.get("err_msg") or detail.get("detail") or str(detail)
                else:
                    msg = str(detail)
            except ValueError:
                msg = resp.text[:500]

            status = resp.status_code
            if status in (401, 403):
                raise GalaxyBackendError(
                    f"Galaxy API auth error ({status}): {msg}",
                    category="auth",
                    exit_code=EXIT_AUTH_ERROR,
                    suggestion="Check your API key with: galaxy-cli config test",
                ) from exc
            elif status == 404:
                raise GalaxyBackendError(
                    f"Galaxy API not found ({status}): {msg}",
                    category="not_found",
                    exit_code=EXIT_USER_ERROR,
                ) from exc
            elif status == 400:
                raise GalaxyBackendError(
                    f"Galaxy API bad request ({status}): {msg}",
                    category="invalid_request",
                    exit_code=EXIT_USER_ERROR,
                ) from exc
            elif status >= 500:
                raise GalaxyBackendError(
                    f"Galaxy server error ({status}): {msg}",
                    category="server_error",
                    exit_code=EXIT_SERVER_ERROR,
                ) from exc
            else:
                raise GalaxyBackendError(
                    f"Galaxy API error ({status}): {msg}",
                    category="api_error",
                    exit_code=EXIT_SERVER_ERROR,
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

    def post(self, path, data=None, json_data=None):
        """POST request to Galaxy API."""
        resp = self._request(
            "POST",
            path,
            headers=self._headers(),
            data=data,
            json=json_data,
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

    def upload_file(self, file_path, history_id, file_type="auto", dbkey="?"):
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
                "files_0|space_to_tab": "No",
                "files_0|to_posix_lines": "No",
            }),
        }
        upload_timeout = max(self.timeout, 300)
        with open(file_path, "rb") as handle:
            files = {"files_0|file_data": (file_path.name, handle)}
            try:
                resp = requests.post(
                    self._api_url("tools"),
                    headers=self._upload_headers(),
                    data=payload,
                    files=files,
                    timeout=upload_timeout,
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
                    f"Upload timed out after {upload_timeout}s",
                    category="timeout",
                    exit_code=EXIT_TIMEOUT,
                    suggestion="Increase timeout or try a smaller file.",
                ) from exc
        return self._handle_response(resp)

    def download_dataset(self, dataset_id, output_path):
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
            raise GalaxyBackendError(
                f"Failed to download dataset {dataset_id}: {resp.status_code}",
                category="api_error",
                exit_code=EXIT_SERVER_ERROR,
            ) from exc

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return {"output": str(output_path), "size": output_path.stat().st_size}

    def get_version(self):
        """Get Galaxy server version."""
        return self.get("version")

    def whoami(self):
        """Get current user info."""
        return self.get("users/current")
