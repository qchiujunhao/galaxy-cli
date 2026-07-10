"""Tests for file-backed Galaxy API keys and secret redaction."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests


def _environment(**values):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GALAXY_URL", "GALAXY_API_KEY", "GALAXY_API_KEY_FILE"}
    }
    env.update(values)
    return env


def test_explicit_api_key_precedes_environment_and_file(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyClient

    key_file = tmp_path / "key"
    key_file.write_text("file-secret\n")
    env = _environment(
        GALAXY_API_KEY="environment-secret",
        GALAXY_API_KEY_FILE=str(key_file),
    )
    with (
        patch.dict(os.environ, env, clear=True),
        patch("galaxy_cli.utils.galaxy_backend.read_api_key_file") as read_file,
    ):
        client = GalaxyClient(
            url="https://galaxy.example.org", api_key="explicit-secret"
        )

    assert client.api_key == "explicit-secret"
    read_file.assert_not_called()


def test_environment_api_key_precedes_file(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyClient

    missing = tmp_path / "missing"
    env = _environment(
        GALAXY_API_KEY="environment-secret",
        GALAXY_API_KEY_FILE=str(missing),
    )
    with patch.dict(os.environ, env, clear=True):
        client = GalaxyClient(url="https://galaxy.example.org")

    assert client.api_key == "environment-secret"


def test_api_key_file_precedes_profile_and_legacy_config(tmp_path):
    from galaxy_cli.utils import galaxy_backend as backend
    from galaxy_cli.utils.galaxy_backend import GalaxyClient

    key_file = tmp_path / "key"
    key_file.write_text("file-secret\n")
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_profile": "main",
                "profiles": {
                    "main": {
                        "url": "https://profile.example.org",
                        "api_key": "profile-secret",
                    }
                },
                "api_key": "legacy-secret",
            }
        )
    )
    env = _environment(GALAXY_API_KEY_FILE=str(key_file))
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(backend, "DEFAULT_CONFIG_FILE", config_file),
    ):
        client = GalaxyClient(url="https://galaxy.example.org")

    assert client.api_key == "file-secret"


def test_api_key_file_strips_trailing_whitespace(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyClient

    key_file = tmp_path / "key"
    key_file.write_text("file-secret\n \t\n")
    env = _environment(GALAXY_API_KEY_FILE=str(key_file))
    with patch.dict(os.environ, env, clear=True):
        client = GalaxyClient(url="https://galaxy.example.org")

    assert client.api_key == "file-secret"


@pytest.mark.parametrize("kind", ["missing", "directory", "empty"])
def test_api_key_file_rejects_invalid_paths(tmp_path, kind):
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    path = tmp_path / "key"
    if kind == "directory":
        path.mkdir()
    elif kind == "empty":
        path.write_text("\n \t")
    env = _environment(GALAXY_API_KEY_FILE=str(path))
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(GalaxyBackendError) as exc,
    ):
        GalaxyClient(url="https://galaxy.example.org")

    assert exc.value.category == "auth"
    assert "GALAXY_API_KEY_FILE" in str(exc.value)


def test_api_key_file_rejects_unreadable_file(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    key_file = tmp_path / "key"
    key_file.write_text("never-disclose")
    key_file.chmod(0o000)
    env = _environment(GALAXY_API_KEY_FILE=str(key_file))
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(GalaxyBackendError) as exc,
    ):
        GalaxyClient(url="https://galaxy.example.org")

    assert "not readable" in str(exc.value)
    assert "never-disclose" not in str(exc.value)


def test_config_show_masks_file_key_and_reports_source(tmp_path):
    from galaxy_cli.core.config import show_config
    from galaxy_cli.utils import galaxy_backend as backend

    key_file = tmp_path / "key"
    key_file.write_text("file-secret-1234\n")
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    env = _environment(
        GALAXY_URL="https://galaxy.example.org",
        GALAXY_API_KEY_FILE=str(key_file),
    )
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(backend, "DEFAULT_CONFIG_FILE", config_file),
    ):
        result = show_config()

    assert result["api_key"] == "***...1234"
    assert result["api_key_source"] == "env_file"
    assert "file-secret" not in json.dumps(result)


def test_http_errors_redact_api_key():
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    secret = "top-secret-api-key"
    client = GalaxyClient(url="https://galaxy.example.org", api_key=secret)
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("bad request")
    response.json.return_value = {"err_msg": f"invalid input containing {secret}"}
    response.status_code = 400

    with pytest.raises(GalaxyBackendError) as exc:
        client._handle_response(response)

    assert "invalid input" in str(exc.value)
    assert secret not in str(exc.value)
    assert secret not in json.dumps(exc.value.to_dict())
    assert "[REDACTED]" in str(exc.value)
