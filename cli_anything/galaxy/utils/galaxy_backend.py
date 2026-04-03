"""Galaxy backend — HTTP client for Galaxy REST API.

This module handles all communication with a running Galaxy server instance.
Galaxy is a hard dependency: the CLI is useless without a reachable server.
"""

import json
import os
from pathlib import Path
from urllib.parse import urljoin

import requests

DEFAULT_CONFIG_DIR = Path.home() / ".cli-galaxy"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_TIMEOUT = 60


class GalaxyBackendError(Exception):
    """Raised when the Galaxy backend returns an error or is unreachable."""


class GalaxyClient:
    """HTTP client for the Galaxy REST API.

    Authentication priority:
    1. Explicit url/api_key parameters
    2. Environment variables GALAXY_URL / GALAXY_API_KEY
    3. Config file ~/.cli-galaxy/config.json
    """

    def __init__(self, url=None, api_key=None, timeout=DEFAULT_TIMEOUT):
        self.url = url or os.environ.get("GALAXY_URL") or self._load_config_value("url")
        self.api_key = api_key or os.environ.get("GALAXY_API_KEY") or self._load_config_value("api_key")
        self.timeout = timeout

        if not self.url:
            raise GalaxyBackendError(
                "Galaxy server URL not configured. Set it with:\n"
                "  export GALAXY_URL=https://usegalaxy.org\n"
                "  # or\n"
                "  cli-galaxy config set-url https://usegalaxy.org"
            )
        if not self.api_key:
            raise GalaxyBackendError(
                "Galaxy API key not configured. Set it with:\n"
                "  export GALAXY_API_KEY=your-api-key\n"
                "  # or\n"
                "  cli-galaxy config set-key your-api-key\n"
                "\n"
                "Get your API key from: <your-galaxy-url>/user/api_key"
            )

        # Normalize URL
        if not self.url.endswith("/"):
            self.url += "/"

    def _load_config_value(self, key):
        """Load a value from the config file."""
        if DEFAULT_CONFIG_FILE.exists():
            try:
                data = json.loads(DEFAULT_CONFIG_FILE.read_text())
                return data.get(key)
            except (json.JSONDecodeError, OSError):
                pass
        return None

    @staticmethod
    def save_config(key, value):
        """Save a configuration value to the config file."""
        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {}
        if DEFAULT_CONFIG_FILE.exists():
            try:
                data = json.loads(DEFAULT_CONFIG_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        data[key] = value
        DEFAULT_CONFIG_FILE.write_text(json.dumps(data, indent=2))

    @staticmethod
    def load_config():
        """Load the full config dict."""
        if DEFAULT_CONFIG_FILE.exists():
            try:
                return json.loads(DEFAULT_CONFIG_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

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
                f"TLS/SSL handshake failed while connecting to Galaxy at {self.url}\n"
                "This usually means your Python runtime has an outdated SSL library.\n"
                "Use a modern Python build, such as uv-managed Python or Homebrew Python 3.10+."
            ) from exc
        except requests.ConnectionError as exc:
            raise GalaxyBackendError(
                f"Cannot connect to Galaxy at {self.url}\n"
                "Ensure the Galaxy server is running and reachable."
            ) from exc
        except requests.Timeout as exc:
            raise GalaxyBackendError(
                f"Request to Galaxy timed out after {self.timeout}s"
            ) from exc

    def _handle_response(self, resp):
        """Parse response and raise on errors."""
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = resp.json()
                msg = detail.get("err_msg") or detail.get("detail") or str(detail)
            except (ValueError, KeyError):
                msg = resp.text[:500]
            raise GalaxyBackendError(f"Galaxy API error ({resp.status_code}): {msg}") from exc

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
            raise GalaxyBackendError(f"File not found: {file_path}")

        payload = {
            "tool_id": "upload1",
            "history_id": history_id,
            "inputs": json.dumps({
                "file_type": file_type,
                "dbkey": dbkey,
                "files_0|type": "upload_dataset",
                "files_0|space_to_tab": None,
                "files_0|to_posix_lines": "Yes",
            }),
        }
        files = {"files_0|file_data": (file_path.name, open(file_path, "rb"))}
        try:
            resp = requests.post(
                self._api_url("tools"),
                headers=self._upload_headers(),
                data=payload,
                files=files,
                timeout=max(self.timeout, 300),  # Upload may be slow
            )
        except requests.exceptions.SSLError as exc:
            raise GalaxyBackendError(
                f"TLS/SSL handshake failed while connecting to Galaxy at {self.url}\n"
                "This usually means your Python runtime has an outdated SSL library.\n"
                "Use a modern Python build, such as uv-managed Python or Homebrew Python 3.10+."
            ) from exc
        except requests.ConnectionError as exc:
            raise GalaxyBackendError(
                f"Cannot connect to Galaxy at {self.url}\n"
                "Ensure the Galaxy server is running and reachable."
            ) from exc
        except requests.Timeout as exc:
            raise GalaxyBackendError(
                f"Request to Galaxy timed out after {max(self.timeout, 300)}s"
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
                f"Failed to download dataset {dataset_id}: {resp.status_code}"
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
