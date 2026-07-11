"""Mock-based tests for Galaxy user-defined tool commands."""

import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def representation():
    return {
        "class": "GalaxyUserTool",
        "id": "copy-input",
        "version": "1.0.0",
        "name": "Copy input",
        "shell_command": "cat '$(inputs.input.path)' > output.txt",
        "container": "quay.io/biocontainers/coreutils:9.5--hd590300_0",
        "inputs": [{"name": "input", "type": "data"}],
        "outputs": [{"name": "output", "format": "txt"}],
    }


def _tool_response(representation, **updates):
    response = {
        "id": "encoded-id",
        "uuid": "11111111-2222-3333-4444-555555555555",
        "tool_id": representation["id"],
        "active": True,
        "representation": representation,
    }
    response.update(updates)
    return response


def _submission_response():
    return {
        "jobs": [{"id": "job-1", "state": "new", "tool_id": "copy-input"}],
        "outputs": [
            {
                "id": "dataset-1",
                "name": "output",
                "extension": "txt",
                "history_content_type": "dataset",
            }
        ],
        "output_collections": [
            {
                "id": "collection-1",
                "name": "collection output",
                "collection_type": "list",
                "history_content_type": "dataset_collection",
            }
        ],
    }


def test_create_uses_exact_path_envelope_and_compact_output(representation):
    from galaxy_cli.core.udt import create_udt

    client = MagicMock()
    client.post.return_value = _tool_response(representation)

    result = create_udt(client, representation)

    client.post.assert_called_once_with(
        "unprivileged_tools",
        json_data={"src": "representation", "representation": representation},
    )
    assert result == {
        "id": "encoded-id",
        "uuid": "11111111-2222-3333-4444-555555555555",
        "tool_id": "copy-input",
        "name": "Copy input",
        "version": "1.0.0",
        "active": True,
    }


@pytest.mark.parametrize(
    "change, message",
    [
        (("remove", "shell_command"), "missing required"),
        (("set", "class", "GalaxyTool"), "GalaxyUserTool"),
        (("set", "container", ["image"]), "must be a string"),
    ],
)
def test_create_validation_fails_before_request(representation, change, message):
    from galaxy_cli.core.udt import create_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    invalid = dict(representation)
    if change[0] == "remove":
        invalid.pop(change[1])
    else:
        invalid[change[1]] = change[2]
    client = MagicMock()

    with pytest.raises(GalaxyBackendError, match=message):
        create_udt(client, invalid)

    client.post.assert_not_called()


@pytest.mark.parametrize(
    "status_code, expected_state, retry_safe",
    [(422, "not_submitted", True), (503, "unknown", False), (None, "unknown", False)],
)
def test_create_submission_error_reports_retry_safety(
    representation, status_code, expected_state, retry_safe
):
    from galaxy_cli.core.udt import create_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    secret = "udt-create-secret-key"
    client = MagicMock(api_key=secret)
    client.post.side_effect = GalaxyBackendError(
        f"create failed with {secret}",
        category="invalid_request" if status_code == 422 else "server_error",
        status_code=status_code,
    )

    with pytest.raises(GalaxyBackendError) as exc:
        create_udt(client, representation)

    assert secret not in str(exc.value)
    assert exc.value.submission_state == expected_state
    assert exc.value.retry_safe is retry_safe
    assert exc.value.details["tool_id"] == representation["id"]
    assert exc.value.details["tool_version"] == representation["version"]


def test_create_response_without_uuid_is_not_safe_to_retry(representation):
    from galaxy_cli.core.udt import create_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.post.return_value = {"id": "created-without-uuid"}

    with pytest.raises(GalaxyBackendError) as exc:
        create_udt(client, representation)

    assert exc.value.error_kind == "udt_create_response_invalid"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["udt_id"] == "created-without-uuid"


def test_list_show_and_delete_paths(representation):
    from galaxy_cli.core.udt import delete_udt, list_udts, show_udt

    active = _tool_response(representation)
    inactive = _tool_response(
        representation,
        id="inactive-id",
        uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        active=False,
    )
    client = MagicMock()
    client.get.side_effect = [[active], [active], [inactive], active]

    assert list_udts(client) == [
        {
            "id": "encoded-id",
            "uuid": active["uuid"],
            "tool_id": "copy-input",
            "name": "Copy input",
            "version": "1.0.0",
            "active": True,
        }
    ]
    combined = list_udts(client, include_inactive=True)
    shown = show_udt(client, active["uuid"])
    deleted = delete_udt(client, active["uuid"])

    assert len(combined) == 2
    assert shown["representation"] == representation
    assert deleted["status"] == "deactivated"
    assert client.get.call_args_list == [
        call("unprivileged_tools", params={"active": True}),
        call("unprivileged_tools", params={"active": True}),
        call("unprivileged_tools", params={"active": False}),
        call(f"unprivileged_tools/{active['uuid']}"),
    ]
    client.delete.assert_called_once_with(f"unprivileged_tools/{active['uuid']}")


def test_run_looks_up_uuid_then_posts_to_tools(representation):
    from galaxy_cli.core.udt import run_udt

    uuid = _tool_response(representation)["uuid"]
    inputs = {"input": {"src": "hda", "id": "dataset-input"}}
    client = MagicMock()
    client.get.return_value = _tool_response(representation)
    client.post.return_value = _submission_response()

    result = run_udt(client, uuid, "history-1", inputs, wait=False)

    client.get.assert_called_once_with(f"unprivileged_tools/{uuid}")
    client.post.assert_called_once_with(
        "tools",
        json_data={
            "history_id": "history-1",
            "tool_uuid": uuid,
            "tool_version": "1.0.0",
            "inputs": inputs,
            "input_format": "legacy",
        },
    )
    payload = client.post.call_args.kwargs["json_data"]
    assert client.method_calls[:2] == [
        call.get(f"unprivileged_tools/{uuid}"),
        call.post("tools", json_data=payload),
    ]
    assert "tool_id" not in payload
    assert client.post.call_args.args[0] != "jobs"
    assert result["jobs"] == [{"id": "job-1", "state": "new"}]
    assert result["success"] is True
    assert result["state"] == "submitted"
    assert result["execution_backend"] == "legacy"
    assert result["tool_id"] == representation["id"]


@pytest.mark.parametrize(
    "response, message",
    [({}, "not found"), ({"uuid": "u", "active": False}, "inactive")],
)
def test_run_rejects_missing_or_inactive_uuid(response, message):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.get.return_value = response

    with pytest.raises(GalaxyBackendError, match=message):
        run_udt(client, "missing", "history-1", {}, wait=False)

    client.post.assert_not_called()


@pytest.mark.parametrize("contents", ["not json", "[]"])
def test_malformed_inputs_json_is_rejected(tmp_path, contents):
    from galaxy_cli.core.udt import load_json_object
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    path = tmp_path / "inputs.json"
    path.write_text(contents)
    with pytest.raises(GalaxyBackendError, match="JSON object|Failed to read"):
        load_json_object(path, "--inputs-json")


def test_galaxy_input_rejection_message_is_preserved(representation):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.get.return_value = _tool_response(representation)
    client.post.side_effect = GalaxyBackendError(
        "Galaxy API bad request (400): input 'input' is invalid",
        category="invalid_request",
    )

    with pytest.raises(GalaxyBackendError, match="input 'input' is invalid") as exc:
        run_udt(client, _tool_response(representation)["uuid"], "history-1", {})

    assert "schema" not in str(exc.value).lower()


@pytest.mark.parametrize(
    "status_code, submission_state, retry_safe",
    [(400, "not_submitted", True), (422, "not_submitted", True), (500, "unknown", False), (None, "unknown", False)],
)
def test_run_submission_error_records_retry_safety(
    representation, status_code, submission_state, retry_safe
):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.return_value = tool
    client.post.side_effect = GalaxyBackendError(
        "redacted submission failure",
        category="invalid_request" if status_code in {400, 422} else "server_error",
        status_code=status_code,
    )

    with pytest.raises(GalaxyBackendError) as exc:
        run_udt(client, tool["uuid"], "history-1", {}, wait=False)

    assert exc.value.submission_state == submission_state
    assert exc.value.retry_safe is retry_safe
    assert exc.value.details["history_id"] == "history-1"
    assert exc.value.details["tool_id"] == representation["id"]
    assert exc.value.details["tool_uuid"] == tool["uuid"]
    assert exc.value.details["job_ids"] == []
    assert exc.value.details["output_ids"] == []


def test_wait_success_refreshes_dataset_and_collection_outputs(representation):
    from galaxy_cli.core.udt import run_udt

    tool = _tool_response(representation)
    dataset = {
        "id": "dataset-1",
        "name": "output",
        "state": "ok",
        "extension": "txt",
        "file_size": 42,
        "history_content_type": "dataset",
    }
    collection = {
        "id": "collection-1",
        "name": "collection output",
        "populated_state": "ok",
        "history_content_type": "dataset_collection",
        "collection_type": "list",
        "element_count": 1,
    }
    client = MagicMock()
    client.get.side_effect = [
        tool,
        {"state": "ok", "exit_code": 0},
        dataset,
        collection,
    ]
    client.post.return_value = _submission_response()

    result = run_udt(
        client,
        tool["uuid"],
        "history-1",
        {"input": {"src": "hda", "id": "input-1"}},
        poll_interval=0,
    )

    assert result["jobs"][0]["state"] == "ok"
    assert result["success"] is True
    assert result["state"] == "ok"
    assert result["execution_backend"] == "legacy"
    assert result["wait_result"] == result["wait_results"][0]
    assert result["outputs"][0]["output_name"] == "output"
    assert result["outputs"][0]["src"] == "hda"
    assert result["outputs"][0]["file_size"] == 42
    assert result["outputs"][1]["output_name"] == "collection output"
    assert result["outputs"][1]["src"] == "hdca"
    assert result["outputs"][1]["collection_type"] == "list"
    assert call("jobs/job-1") in client.get.call_args_list


def test_wait_failure_raises_clear_error(representation):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.side_effect = [tool, {"state": "error", "exit_code": 1}]
    client.post.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [],
    }

    with pytest.raises(GalaxyBackendError, match=r"job-1 \(error\)"):
        run_udt(client, tool["uuid"], "history-1", {}, poll_interval=0)


def test_run_rejects_200_response_errors_with_partial_submission_context(
    representation,
):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.return_value = tool
    client.post.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [{"id": "dataset-1", "name": "output"}],
        "errors": [{"err_msg": "one mapped element was rejected"}],
    }

    with pytest.raises(GalaxyBackendError) as exc:
        run_udt(client, tool["uuid"], "history-1", {}, wait=False)

    assert exc.value.error_kind == "udt_request_rejected"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["job_ids"] == ["job-1"]
    assert exc.value.details["jobs"][0]["state"] == "new"
    assert exc.value.details["output_ids"] == ["dataset-1"]
    assert exc.value.details["outputs"][0]["output_name"] == "output"


def test_blocking_udt_partial_error_waits_known_job_to_final_state(representation):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.side_effect = [
        tool,
        {"state": "running", "exit_code": None},
        {"state": "ok", "exit_code": 0},
    ]
    client.post.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [{"id": "dataset-1", "name": "output"}],
        "errors": [{"err_msg": "one mapped element was rejected"}],
    }

    with pytest.raises(GalaxyBackendError) as exc:
        run_udt(
            client,
            tool["uuid"],
            "history-1",
            {},
            wait=True,
            timeout=5,
            poll_interval=0,
        )

    assert exc.value.error_kind == "udt_request_rejected"
    assert exc.value.retry_safe is False
    assert exc.value.details["jobs"] == [
        {
            "id": "job-1",
            "state": "ok",
            "exit_code": 0,
            "waited_seconds": 0.0,
        }
    ]


def test_output_refresh_error_keeps_completed_udt_context(representation):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import EXIT_SERVER_ERROR, GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.side_effect = [
        tool,
        {"state": "ok", "exit_code": 0},
        GalaxyBackendError(
            "lost while refreshing output",
            category="connection",
            exit_code=EXIT_SERVER_ERROR,
        ),
    ]
    client.post.return_value = _submission_response()

    with pytest.raises(GalaxyBackendError) as exc:
        run_udt(client, tool["uuid"], "history-1", {}, poll_interval=0)

    assert exc.value.category == "connection"
    assert exc.value.error_kind == "output_refresh_failed"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["history_id"] == "history-1"
    assert exc.value.details["tool_id"] == representation["id"]
    assert exc.value.details["job_ids"] == ["job-1"]
    assert exc.value.details["jobs"][0]["state"] == "ok"
    assert exc.value.details["output_ids"] == ["dataset-1", "collection-1"]
    assert exc.value.details["outputs"][0]["output_name"] == "output"


def test_wait_timeout_raises_structured_error(representation):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.side_effect = [tool, {"state": "running", "exit_code": None}]
    client.post.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [],
    }

    with pytest.raises(GalaxyBackendError) as exc:
        run_udt(client, tool["uuid"], "history-1", {}, timeout=0)

    assert exc.value.exit_code == EXIT_TIMEOUT
    assert exc.value.error_kind == "job_timeout"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["history_id"] == "history-1"
    assert exc.value.details["tool_id"] == representation["id"]
    assert exc.value.details["job_ids"] == ["job-1"]
    assert exc.value.details["jobs"][0]["state"] == "running"


def test_wait_rejects_success_response_without_jobs(representation):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.return_value = tool
    client.post.return_value = {"jobs": [], "outputs": []}

    with pytest.raises(GalaxyBackendError) as exc:
        run_udt(client, tool["uuid"], "history-1", {})

    payload = exc.value.to_dict()
    assert payload["error_kind"] == "unknown_submission_state"
    assert payload["submission_state"] == "unknown"
    assert payload["retry_safe"] is False
    assert payload["job_ids"] == []


def test_blocking_udt_rejects_malformed_response_members(representation):
    from galaxy_cli.core.udt import run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.return_value = tool
    client.post.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}, None],
        "outputs": [{"id": "dataset-1"}],
    }

    with pytest.raises(GalaxyBackendError) as exc:
        run_udt(client, tool["uuid"], "history-1", {})

    assert exc.value.error_kind == "udt_response_invalid"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["job_ids"] == ["job-1"]
    assert exc.value.details["output_ids"] == ["dataset-1"]


def test_wait_uses_one_deadline_for_multiple_udt_jobs(representation):
    from galaxy_cli.core.udt import run_udt

    tool = _tool_response(representation)
    client = MagicMock()
    job_states = {
        "job-1": iter([{"state": "running"}, {"state": "ok", "exit_code": 0}]),
        "job-2": iter([{"state": "queued"}, {"state": "ok", "exit_code": 0}]),
    }

    def get(path, params=None):
        if path == f"unprivileged_tools/{tool['uuid']}":
            return tool
        if path.startswith("jobs/"):
            return next(job_states[path.rsplit("/", 1)[-1]])
        raise AssertionError((path, params))

    client.get.side_effect = get
    client.post.return_value = {
        "jobs": [
            {"id": "job-1", "state": "new"},
            {"id": "job-2", "state": "new"},
        ],
        "outputs": [],
    }

    result = run_udt(
        client,
        tool["uuid"],
        "history-1",
        {},
        timeout=10,
        poll_interval=0,
    )

    assert [job["id"] for job in result["wait_results"]] == ["job-1", "job-2"]
    assert [job["state"] for job in result["jobs"]] == ["ok", "ok"]


def test_create_run_uses_trace_multi_job_and_collection_fixture(representation):
    from galaxy_cli.core.udt import create_run_udt

    fixture_path = (
        Path(__file__).with_name("fixtures") / "udt_create_run_multi_job.json"
    )
    trace = json.loads(fixture_path.read_text())
    created = dict(trace["create_response"], representation=representation)
    poll_indexes = {"job-1": 0, "job-2": 0}

    def get(path, params=None):
        if path == f"unprivileged_tools/{created['uuid']}":
            return created
        if path.startswith("jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            index = poll_indexes[job_id]
            poll_indexes[job_id] += 1
            return trace["job_poll_rounds"][index][job_id]
        if path.endswith("/dataset-1"):
            return trace["output_details"]["dataset-1"]
        if path.endswith("/dataset_collections/collection-1"):
            return trace["output_details"]["collection-1"]
        raise AssertionError((path, params))

    client = MagicMock()
    client.post.side_effect = [created, trace["submission_response"]]
    client.get.side_effect = get

    result = create_run_udt(
        client,
        representation,
        "history-1",
        {},
        timeout=10,
        poll_interval=0,
    )

    assert [job["state"] for job in result["jobs"]] == ["ok", "ok"]
    assert result["outputs"][0]["file_size"] == 12
    assert result["outputs"][1]["collection_type"] == "list"
    assert result["outputs"][1]["element_count"] == 2


def test_no_wait_skips_job_poll_and_output_refresh(representation):
    from galaxy_cli.core.udt import run_udt

    tool = _tool_response(representation)
    client = MagicMock()
    client.get.return_value = tool
    client.post.return_value = _submission_response()

    result = run_udt(client, tool["uuid"], "history-1", {}, wait=False)

    assert "wait_results" not in result
    client.get.assert_called_once_with(f"unprivileged_tools/{tool['uuid']}")


def test_create_run_creates_and_runs_exactly_once(representation):
    from galaxy_cli.core.udt import create_run_udt

    tool = _tool_response(representation)
    client = MagicMock()
    client.post.side_effect = [
        tool,
        {"jobs": [{"id": "job-1", "state": "new"}], "outputs": []},
    ]
    client.get.side_effect = [tool, {"state": "ok", "exit_code": 0}]

    result = create_run_udt(client, representation, "history-1", {}, poll_interval=0)

    assert client.post.call_args_list[0].args[0] == "unprivileged_tools"
    assert client.post.call_args_list[1].args[0] == "tools"
    assert client.post.call_count == 2
    assert result["create"]["uuid"] == tool["uuid"]
    assert result["jobs"][0]["state"] == "ok"
    assert result["success"] is True
    assert result["state"] == "ok"
    assert result["execution_backend"] == "legacy"
    assert result["tool_version"] == representation["version"]
    assert result["wait_result"] == result["wait_results"][0]


def test_create_run_is_not_retry_safe_after_create_succeeds(representation):
    from galaxy_cli.core.udt import create_run_udt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = _tool_response(representation)
    rejected_run = GalaxyBackendError(
        "run inputs rejected",
        category="invalid_request",
        status_code=422,
    )
    client = MagicMock()
    client.post.side_effect = [tool, rejected_run]
    client.get.return_value = tool

    with pytest.raises(GalaxyBackendError) as exc:
        create_run_udt(client, representation, "history-1", {})

    payload = exc.value.to_dict()
    assert payload["submission_state"] == "submitted"
    assert payload["retry_safe"] is False
    assert payload["created_tool_uuid"] == tool["uuid"]
    assert payload["created_udt"]["tool_id"] == representation["id"]
    assert payload["run_submission_state"] == "not_submitted"
    assert payload["run_retry_safe"] is True


def test_evidence_files_are_complete_and_redacted(tmp_path, representation):
    from galaxy_cli.core.udt import create_run_udt, write_evidence

    secret = "credential-must-not-leak"
    tool = _tool_response(representation, debug=secret)
    submission = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [{"id": "dataset-1", "name": "output", "extension": "txt"}],
        "debug": secret,
    }
    dataset = {
        "id": "dataset-1",
        "name": "output",
        "state": "ok",
        "extension": "txt",
        "debug": secret,
    }

    def get(path, params=None):
        if path.startswith("unprivileged_tools/"):
            return tool
        if path == "jobs/job-1" and params is None:
            return {"state": "ok", "exit_code": 0}
        if path == "jobs/job-1" and params == {"full": True}:
            return {"id": "job-1", "state": "ok", "api_key": secret}
        if path == "histories/history-1/contents/dataset-1":
            return dataset
        raise AssertionError((path, params))

    client = MagicMock(api_key=secret)
    client.post.side_effect = [tool, submission]
    client.get.side_effect = get
    evidence = {}

    create_run_udt(
        client,
        representation,
        "history-1",
        {"input": {"src": "hda", "id": "input-1"}},
        poll_interval=0,
        evidence=evidence,
    )
    evidence_dir = tmp_path / "evidence"
    write_evidence(evidence_dir, evidence, secrets=(secret,))

    expected = {
        "create-request.json",
        "create-response.json",
        "run-lookup-response.json",
        "run-request.json",
        "run-response.json",
        "jobs.json",
        "outputs.json",
    }
    assert {path.name for path in evidence_dir.iterdir()} == expected
    assert evidence_dir.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in evidence_dir.iterdir())
    combined = "".join(path.read_text() for path in evidence_dir.iterdir())
    assert secret not in combined
    assert "[REDACTED]" in combined
    assert json.loads((evidence_dir / "jobs.json").read_text())[0]["state"] == "ok"
    assert (
        json.loads((evidence_dir / "outputs.json").read_text())[0]["id"] == "dataset-1"
    )


def test_udt_help_and_cli_json_output_are_stable(tmp_path, representation):
    from galaxy_cli.cli import cli

    inputs_path = tmp_path / "inputs.json"
    inputs_path.write_text(json.dumps({"input": {"src": "hda", "id": "input-1"}}))
    runner = CliRunner()
    root_help = runner.invoke(cli, ["--help"])
    help_result = runner.invoke(cli, ["udt", "--help"])
    compact = {
        "tool_uuid": "uuid-1",
        "tool_version": "1.0.0",
        "history_id": "history-1",
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [],
    }

    with (
        patch("galaxy_cli.cli._get_client", return_value=object()),
        patch("galaxy_cli.cli.udt_mod.run_udt", return_value=compact) as run,
        patch("galaxy_cli.cli.session_mod.track_job"),
    ):
        result = runner.invoke(
            cli,
            [
                "--json",
                "udt",
                "run",
                "uuid-1",
                "--history-id",
                "history-1",
                "--inputs-json",
                str(inputs_path),
                "--no-wait",
            ],
        )

    assert root_help.exit_code == 0
    assert "udt" in root_help.output
    assert help_result.exit_code == 0
    assert all(
        command in help_result.output
        for command in ["list", "show", "create", "delete", "run", "create-run"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == compact
    assert result.output == json.dumps(compact, separators=(",", ":")) + "\n"
    assert run.call_args.kwargs["wait"] is False


def test_udt_timeout_main_returns_nonzero_compact_json(tmp_path):
    from galaxy_cli.cli import main
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    inputs_path = tmp_path / "inputs.json"
    inputs_path.write_text("{}")
    error = GalaxyBackendError(
        "Timed out waiting for Galaxy jobs: job-1 (running)",
        category="timeout",
        error_kind="job_timeout",
        exit_code=EXIT_TIMEOUT,
        submission_state="submitted",
        retry_safe=False,
        details={
            "history_id": "history-1",
            "tool_id": "tool-1",
            "job_ids": ["job-1"],
            "jobs": [{"id": "job-1", "state": "running", "exit_code": None}],
        },
    )

    with (
        patch.object(
            sys,
            "argv",
            [
                "galaxy-cli",
                "--json",
                "udt",
                "run",
                "uuid-1",
                "--history-id",
                "history-1",
                "--inputs-json",
                str(inputs_path),
                "--timeout",
                "0",
            ],
        ),
        patch("galaxy_cli.cli._get_client", return_value=object()),
        patch("galaxy_cli.cli.udt_mod.run_udt", side_effect=error),
        patch("click.echo") as echo,
        pytest.raises(SystemExit) as exc,
    ):
        main()

    assert exc.value.code == EXIT_TIMEOUT
    encoded = echo.call_args.args[0]
    payload = json.loads(encoded)
    assert encoded == json.dumps(payload, separators=(",", ":"), default=str)
    assert payload["success"] is False
    assert payload["error_kind"] == "job_timeout"
    assert payload["submission_state"] == "submitted"
    assert payload["retry_safe"] is False
    assert payload["job_ids"] == ["job-1"]
    assert payload["jobs"][0]["state"] == "running"
