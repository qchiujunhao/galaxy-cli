"""Explicitly opt-in Galaxy compatibility checks.

Run one target with ``GALAXY_CLI_LIVE=1`` and
``GALAXY_CLI_LIVE_TARGET=usegalaxy|local``. Tests requiring a particular tool,
workflow, collection, or failed job skip unless its documented environment
fixture is supplied. Credentials are never included in assertion messages.
"""

import json
import os

import pytest


pytestmark = pytest.mark.live


@pytest.fixture(
    params=[
        pytest.param("usegalaxy", marks=pytest.mark.usegalaxy),
        pytest.param("local", marks=pytest.mark.local_galaxy),
    ]
)
def live_target(request):
    if os.environ.get("GALAXY_CLI_LIVE") != "1":
        pytest.skip("set GALAXY_CLI_LIVE=1 to enable live compatibility tests")
    configured = os.environ.get("GALAXY_CLI_LIVE_TARGET", "").strip().lower()
    if configured not in {"usegalaxy", "local"}:
        pytest.fail(
            "live mode requires GALAXY_CLI_LIVE_TARGET=usegalaxy or local"
        )
    if configured != request.param:
        pytest.skip(f"configured live target is not {request.param}")
    return request.param


@pytest.fixture
def live_client(live_target):
    from galaxy_cli.utils.galaxy_backend import GalaxyClient

    url = os.environ.get("GALAXY_URL")
    api_key = os.environ.get("GALAXY_API_KEY")
    api_key_file = os.environ.get("GALAXY_API_KEY_FILE")
    if not url or not (api_key or api_key_file):
        pytest.fail("live mode requires GALAXY_URL and a configured API key")
    return GalaxyClient(url=url, api_key=api_key)


@pytest.fixture
def live_history(live_client):
    from galaxy_cli.core.history import create_history, delete_history

    history = create_history(live_client, "galaxy-cli live compatibility")
    try:
        yield history
    finally:
        delete_history(live_client, history["id"], purge=True)


def _json_setting(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"set {name} for this live capability")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        pytest.fail(f"{name} must contain a JSON object")
    return parsed


def _setting(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"set {name} for this live capability")
    return value


def test_server_capabilities_and_authentication(live_client):
    from galaxy_cli.core.server import server_capabilities

    assert live_client.get_version().get("version_major")
    assert live_client.whoami().get("id")
    result = server_capabilities(live_client, use_cache=False, refresh_cache=True)
    assert result["probe_mode"] == "read_only"


def test_history_copy_readiness_and_delete(live_client, live_history):
    from galaxy_cli.core.history import copy_history, delete_history

    copied = copy_history(
        live_client, live_history["id"], name="galaxy-cli live copy",
        wait=True, timeout=180, poll_interval=2,
    )
    try:
        assert copied["id"]
        assert copied.get("readiness", {}).get("ready", True)
    finally:
        delete_history(live_client, copied["id"], purge=True)


@pytest.mark.parametrize("backend", ["auto", "legacy"])
def test_small_upload_backends(live_client, live_history, tmp_path, backend):
    from galaxy_cli.core.dataset import upload_dataset

    source = tmp_path / f"tiny-{backend}.txt"
    source.write_text("one\ntwo\n")
    result = upload_dataset(
        live_client, live_history["id"], source, wait=True, timeout=180,
        poll_interval=2, upload_backend=backend,
    )
    assert result["id"]
    assert result["state"] == "ok"


def test_regular_tool_strict_nested_execution(live_client, live_history):
    from galaxy_cli.core.tool import run_tool

    result = run_tool(
        live_client,
        _setting("GALAXY_CLI_LIVE_TOOL_ID"),
        live_history["id"],
        inputs=_json_setting("GALAXY_CLI_LIVE_TOOL_INPUTS"),
        execution_backend="strict", wait=True, timeout=300, poll_interval=2,
    )
    assert result["state"] == "ok"
    assert result["jobs"]
    assert result["outputs"]


def test_multi_job_or_collection_execution(live_client, live_history):
    from galaxy_cli.core.tool import run_tool

    result = run_tool(
        live_client,
        _setting("GALAXY_CLI_LIVE_MULTI_TOOL_ID"),
        live_history["id"],
        inputs=_json_setting("GALAXY_CLI_LIVE_MULTI_TOOL_INPUTS"),
        execution_backend="strict", wait=True, timeout=300, poll_interval=2,
    )
    assert len(result["jobs"]) > 1 or any(
        item.get("src") == "hdca" for item in result["outputs"]
    )


def test_udt_validate_create_run_deactivate(live_client, live_history):
    from galaxy_cli.core.udt import (
        create_run_udt, delete_udt, load_json_object, validate_udt,
    )
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    representation = load_json_object(
        _setting("GALAXY_CLI_LIVE_UDT_REPRESENTATION"),
        "GALAXY_CLI_LIVE_UDT_REPRESENTATION",
    )
    assert validate_udt(
        live_client, representation, live_history["id"]
    )["valid"] is True
    uuid = None
    try:
        result = create_run_udt(
            live_client, representation, live_history["id"],
            _json_setting("GALAXY_CLI_LIVE_UDT_INPUTS"),
            timeout=300, poll_interval=2,
        )
        uuid = result["create"]["uuid"]
        assert result["state"] == "ok"
    except GalaxyBackendError as exc:
        uuid = exc.details.get("created_tool_uuid")
        raise
    finally:
        if uuid:
            delete_udt(live_client, uuid)


def test_workflow_run_returns_final_outputs(live_client, live_history):
    from galaxy_cli.core.workflow import run_workflow, wait_for_workflow_run

    result = run_workflow(
        live_client, _setting("GALAXY_CLI_LIVE_WORKFLOW_ID"),
        history_id=live_history["id"],
        inputs=_json_setting("GALAXY_CLI_LIVE_WORKFLOW_INPUTS"),
    )
    final = wait_for_workflow_run(
        live_client, result, timeout=300, poll_interval=2
    )
    assert final["state"] == "ok"
    assert final["outputs"]


def test_operation_receipt_resume(live_client, live_history, monkeypatch, tmp_path):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.core.tool import run_tool

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path / "operations"))
    tool_id = _setting("GALAXY_CLI_LIVE_TOOL_ID")
    result = run_tool(
        live_client, tool_id, live_history["id"],
        inputs=_json_setting("GALAXY_CLI_LIVE_TOOL_INPUTS"),
        execution_backend="strict", wait=False,
    )
    receipt = create_receipt(
        "tool", {"tool_id": tool_id, "history_id": live_history["id"]},
        result=result,
    )
    final = resume_operation(live_client, receipt["id"], timeout=300, poll_interval=2)
    assert final["final_state"] == "ok"
    assert final["outputs"]


def test_collection_resolve_and_preview(live_client):
    from galaxy_cli.core.collection import (
        preview_collection_element, resolve_collection_element,
    )

    collection_id = _setting("GALAXY_CLI_LIVE_COLLECTION_ID")
    element_path = _setting("GALAXY_CLI_LIVE_COLLECTION_ELEMENT")
    resolved = resolve_collection_element(live_client, collection_id, element_path)
    preview = preview_collection_element(
        live_client, collection_id, element_path, lines=5
    )
    assert preview["dataset_id"] == resolved["id"]
    assert preview["resolved_path"] == element_path
    assert preview["total_shown"] <= 5


def test_failure_diagnostics_are_bounded(live_client):
    from galaxy_cli.core.job import diagnose_job

    result = diagnose_job(
        live_client, _setting("GALAXY_CLI_LIVE_FAILED_JOB_ID"), max_chars=2000
    )
    assert result["state"] not in {"new", "queued", "running"}
    assert len(json.dumps(result, default=str)) < 10000
