"""Focused tests for deadline-aware Galaxy GET requests."""

import json
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from galaxy_cli.utils.galaxy_backend import (
    EXIT_TIMEOUT,
    GalaxyBackendError,
    GalaxyClient,
    get_with_deadline,
)


def _response(status=200, payload=None, headers=None):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.raise_for_status.return_value = None
    response.json.return_value = payload or {"state": "ok"}
    return response


def test_client_deadline_bounds_request_timeout():
    client = GalaxyClient(
        url="https://galaxy.example.org",
        api_key="test-key",
        connect_timeout=30,
        request_timeout=60,
    )

    with patch(
        "galaxy_cli.utils.galaxy_backend.time.monotonic", return_value=100
    ), patch(
        "galaxy_cli.utils.galaxy_backend.requests.request",
        return_value=_response(payload={"id": "j1", "state": "ok"}),
    ) as request:
        result = client.get_with_deadline("jobs/j1", deadline=105)

    assert result == {"id": "j1", "state": "ok"}
    request_timeout = request.call_args.kwargs["timeout"]
    assert request_timeout.total == 5
    assert request_timeout.connect_timeout == 5


def test_client_deadline_caps_retry_after_and_raises_timeout():
    client = GalaxyClient(
        url="https://galaxy.example.org",
        api_key="test-key",
        max_get_retries=3,
    )
    busy = _response(status=429, headers={"Retry-After": "10"})

    with patch(
        "galaxy_cli.utils.galaxy_backend.time.monotonic", return_value=100
    ), patch(
        "galaxy_cli.utils.galaxy_backend.time.sleep"
    ) as sleep, patch(
        "galaxy_cli.utils.galaxy_backend.requests.request", return_value=busy
    ) as request, pytest.raises(GalaxyBackendError) as exc:
        client.get_with_deadline("jobs/j1", deadline=103)

    assert exc.value.category == "timeout"
    assert exc.value.exit_code == EXIT_TIMEOUT
    assert exc.value.error_kind == "request_deadline"
    assert request.call_count == 1
    sleep.assert_called_once_with(3)


def test_client_deadline_allows_retry_within_remaining_budget():
    client = GalaxyClient(
        url="https://galaxy.example.org",
        api_key="test-key",
        max_get_retries=1,
    )
    busy = _response(status=503, headers={"Retry-After": "2"})
    ok = _response(payload={"id": "j1", "state": "ok"})

    with patch(
        "galaxy_cli.utils.galaxy_backend.time.monotonic", return_value=100
    ), patch(
        "galaxy_cli.utils.galaxy_backend.time.sleep"
    ) as sleep, patch(
        "galaxy_cli.utils.galaxy_backend.requests.request",
        side_effect=[busy, ok],
    ) as request:
        result = client.get_with_deadline("jobs/j1", deadline=110)

    assert result["state"] == "ok"
    assert request.call_count == 2
    sleep.assert_called_once_with(2)


def test_client_rejects_response_completed_after_deadline():
    client = GalaxyClient(url="https://galaxy.example.org", api_key="test-key")

    with patch(
        "galaxy_cli.utils.galaxy_backend.time.monotonic",
        side_effect=[100, 106],
    ), patch(
        "galaxy_cli.utils.galaxy_backend.requests.request",
        return_value=_response(payload={"id": "j1", "state": "ok"}),
    ) as request, pytest.raises(GalaxyBackendError) as exc:
        client.get_with_deadline("jobs/j1", deadline=105)

    assert exc.value.exit_code == EXIT_TIMEOUT
    assert request.call_count == 1


def test_get_with_deadline_preserves_lightweight_fake_clients():
    client = MagicMock()
    client.get.return_value = {"id": "j1", "state": "ok"}

    result = get_with_deadline(client, "jobs/j1", deadline=0)

    assert result["state"] == "ok"
    assert client.method_calls == [call.get("jobs/j1")]


def test_expired_deadline_error_never_contains_api_key():
    secret = "deadline-secret-key"
    client = GalaxyClient(url="https://galaxy.example.org", api_key=secret)

    with patch(
        "galaxy_cli.utils.galaxy_backend.time.monotonic", return_value=100
    ), patch(
        "galaxy_cli.utils.galaxy_backend.requests.request"
    ) as request, pytest.raises(GalaxyBackendError) as exc:
        client.get_with_deadline("jobs/j1", deadline=100)

    rendered = json.dumps(exc.value.to_dict()) + str(exc.value) + repr(exc.value)
    assert secret not in rendered
    assert exc.value.exit_code == EXIT_TIMEOUT
    request.assert_not_called()


def test_bounded_transport_timeout_drops_sensitive_cause():
    secret = "transport-secret-key"
    client = GalaxyClient(url="https://galaxy.example.org", api_key=secret)

    with patch(
        "galaxy_cli.utils.galaxy_backend.time.monotonic", return_value=100
    ), patch(
        "galaxy_cli.utils.galaxy_backend.requests.request",
        side_effect=requests.Timeout(secret),
    ), pytest.raises(GalaxyBackendError) as exc:
        client.get_with_deadline("jobs/j1", deadline=105)

    assert secret not in json.dumps(exc.value.to_dict())
    assert exc.value.__cause__ is None
    assert exc.value.exit_code == EXIT_TIMEOUT
