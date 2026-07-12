"""Focused Phase 2 tests for adaptive polling and authoritative recovery."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _patch_clock(clock):
    return (
        patch("galaxy_cli.core.polling.time.monotonic", side_effect=clock.monotonic),
        patch("galaxy_cli.core.polling.time.sleep", side_effect=clock.sleep),
    )


def test_adaptive_wait_sequence_and_fixed_override():
    from galaxy_cli.core.job import wait_for_jobs

    clock = _Clock()
    client = MagicMock()
    client.get.side_effect = [
        {"state": "running"},
        {"state": "running"},
        {"state": "running"},
        {"state": "running"},
        {"state": "ok", "exit_code": 0},
    ]
    monotonic, sleep = _patch_clock(clock)
    with monotonic, sleep:
        result = wait_for_jobs(client, ["j1"], timeout=100)

    assert result[0]["state"] == "ok"
    assert clock.sleeps == [5.0, 10.0, 20.0, 30.0]

    fixed_clock = _Clock()
    client.get.side_effect = [
        {"state": "running"},
        {"state": "running"},
        {"state": "ok", "exit_code": 0},
    ]
    monotonic, sleep = _patch_clock(fixed_clock)
    with monotonic, sleep:
        wait_for_jobs(client, ["j1"], timeout=20, poll_interval=2)
    assert fixed_clock.sleeps == [2.0, 2.0]


def test_adaptive_wait_clips_sleep_at_deadline():
    from galaxy_cli.core.job import wait_for_jobs
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    clock = _Clock()
    client = MagicMock()
    client.get.return_value = {"state": "running"}
    monotonic, sleep = _patch_clock(clock)
    with monotonic, sleep, pytest.raises(GalaxyBackendError) as exc:
        wait_for_jobs(client, ["j1"], timeout=12)

    assert exc.value.error_kind == "job_timeout"
    assert clock.sleeps == [5.0, 7.0]
    assert clock.now == 12.0


def test_supplied_deadline_is_forwarded_even_with_zero_timeout():
    from galaxy_cli.core.job import wait_for_jobs
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    class Client:
        def __init__(self):
            self.deadlines = []

        def get_with_deadline(self, path, params=None, deadline=None):
            self.deadlines.append(deadline)
            raise GalaxyBackendError(
                "deadline",
                category="timeout",
                error_kind="request_deadline",
            )

    client = Client()
    with pytest.raises(GalaxyBackendError):
        wait_for_jobs(client, ["j1"], timeout=0, deadline=123.0)
    assert client.deadlines == [123.0]


def test_tool_request_and_jobs_share_one_absolute_deadline():
    from galaxy_cli.core.tool import _run_strict_after_submit
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    clock = _Clock()
    request_states = iter(["new", "submitted"])
    client = MagicMock()

    def get(path, params=None):
        if path == "tool_requests/r1/state":
            return next(request_states)
        if path == "tool_requests/r1":
            return {"state": "submitted", "jobs": [{"id": "j1"}]}
        if path == "jobs/j1":
            return {"id": "j1", "state": "running", "exit_code": None}
        raise AssertionError((path, params))

    client.get.side_effect = get
    plan = {
        "execution_backend": "strict",
        "post_body": {
            "history_id": "h1",
            "tool_id": "t1",
            "tool_version": "1",
        },
        "history_id": "h1",
        "tool_id": "t1",
        "tool_version": "1",
    }
    monotonic, sleep = _patch_clock(clock)
    with monotonic, sleep, pytest.raises(GalaxyBackendError) as exc:
        _run_strict_after_submit(
            client,
            plan,
            {"tool_request_id": "r1"},
            True,
            timeout=12,
            poll_interval=None,
        )

    assert exc.value.error_kind == "job_timeout"
    assert clock.sleeps == [5.0, 5.0, 2.0]
    assert clock.now == 12.0


def test_retry_after_is_bounded():
    from galaxy_cli.utils.galaxy_backend import GalaxyClient

    client = GalaxyClient(url="https://galaxy.example", api_key="key")
    response = MagicMock(headers={"Retry-After": "999"})
    assert client._retry_after_seconds(response, 0) == 60.0
    response.headers = {"Retry-After": "-5"}
    assert client._retry_after_seconds(response, 0) == 0.0


def test_no_wait_receipt_remains_resumable(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    receipt = create_receipt(
        "tool",
        {"tool_id": "t1", "history_id": "h1"},
        result={
            "success": True,
            "state": "submitted",
            "history_id": "h1",
            "tool_id": "t1",
            "tool_request_id": "r1",
        },
    )
    assert receipt["state"] == "submitted"
    assert receipt["request_ids"] == ["r1"]


def test_complete_receipt_returns_saved_authoritative_result_without_queries(
    tmp_path, monkeypatch
):
    from galaxy_cli.core.operation import create_receipt, resume_operation

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    result = {
        "success": True,
        "state": "ok",
        "history_id": "h1",
        "tool_id": "t1",
        "jobs": [{"id": "j1", "state": "ok", "exit_code": 0}],
        "outputs": [{"id": "d1", "src": "hda", "state": "ok"}],
    }
    receipt = create_receipt(
        "tool", {"tool_id": "t1", "history_id": "h1"}, result=result
    )
    client = MagicMock()

    resumed = resume_operation(client, receipt["id"])

    assert resumed["state"] == "complete"
    assert resumed["final_state"] == "ok"
    assert resumed["result"] == result
    client.get.assert_not_called()
    client.post.assert_not_called()


def test_tool_resume_discovers_all_jobs_and_refreshes_all_outputs(
    tmp_path, monkeypatch
):
    from galaxy_cli.core.operation import create_receipt, resume_operation, show_receipt

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    receipt = create_receipt(
        "tool",
        {"tool_id": "t1", "history_id": "h1"},
        result={
            "success": True,
            "state": "submitted",
            "history_id": "h1",
            "tool_id": "t1",
            "tool_version": "1",
            "execution_backend": "strict",
            "tool_request_id": "r1",
        },
    )
    client = MagicMock()

    def get(path, params=None):
        if path == "tool_requests/r1/state":
            return "submitted"
        if path == "tool_requests/r1":
            return {
                "state": "submitted",
                "jobs": [{"id": "j1"}, {"id": "j2"}],
                "implicit_collections": [
                    {"id": "c1", "src": "hdca", "output_name": "mapped"}
                ],
            }
        if path == "jobs/j1":
            return {
                "id": "j1",
                "state": "ok",
                "exit_code": 0,
                "outputs": {"out": {"id": "d1", "src": "hda"}},
            }
        if path == "jobs/j2":
            return {
                "id": "j2",
                "state": "ok",
                "exit_code": 0,
                "outputs": {"out": {"id": "d2", "src": "hda"}},
            }
        if path.endswith("/dataset_collections/c1"):
            return {
                "id": "c1",
                "name": "mapped",
                "populated_state": "ok",
                "history_content_type": "dataset_collection",
                "collection_type": "list",
                "element_count": 2,
            }
        if path.endswith("/d1") or path.endswith("/d2"):
            output_id = path.rsplit("/", 1)[-1]
            return {
                "id": output_id,
                "name": output_id,
                "state": "ok",
                "extension": "txt",
                "file_size": 10,
            }
        raise AssertionError((path, params))

    client.get.side_effect = get
    resumed = resume_operation(client, receipt["id"], timeout=10, poll_interval=0)

    assert resumed["state"] == "complete"
    assert resumed["result"]["state"] == "ok"
    assert resumed["resumable"] is False
    assert resumed["job_ids"] == ["j1", "j2"]
    assert {(item["src"], item["id"]) for item in resumed["outputs"]} == {
        ("hda", "d1"),
        ("hda", "d2"),
        ("hdca", "c1"),
    }
    assert show_receipt(receipt["id"])["result"]["state"] == "ok"
    client.post.assert_not_called()


@pytest.mark.parametrize("operation_type", ["udt", "workflow", "upload"])
def test_resume_returns_authoritative_results_for_other_operation_types(
    tmp_path, monkeypatch, operation_type
):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    operation_dir = tmp_path / operation_type
    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(operation_dir))
    if operation_type == "workflow":
        receipt = create_receipt(
            "workflow",
            {"workflow_id": "wf1", "history_id": "h1"},
            result={
                "id": "inv1",
                "workflow_id": "wf1",
                "history_id": "h1",
                "state": "new",
            },
        )
    elif operation_type == "udt":
        receipt = create_receipt(
            "udt",
            {"uuid": "u1", "history_id": "h1"},
            result={
                "success": True,
                "state": "submitted",
                "history_id": "h1",
                "tool_id": "ut1",
                "tool_uuid": "u1",
                "tool_version": "1",
                "jobs": [{"id": "j1"}],
                "outputs": [{"id": "d1", "src": "hda"}],
            },
        )
    else:
        error = GalaxyBackendError(
            "upload wait timed out",
            category="timeout",
            error_kind="job_timeout",
            submission_state="submitted",
            retry_safe=False,
            details={
                "history_id": "h1",
                "tool_id": "upload1",
                "job_ids": ["j1"],
                "output_ids": ["d1"],
            },
        )
        receipt = create_receipt(
            "upload", {"history_id": "h1", "local_path": "input.txt"}, error=error
        )

    client = MagicMock()

    def get(path, params=None):
        if path == "invocations/inv1":
            return {
                "state": "scheduled",
                "steps": [{"job_id": "j1", "state": "scheduled"}],
            }
        if path == "jobs/j1":
            outputs = {} if operation_type == "upload" else {
                "out": {"id": "d1", "src": "hda"}
            }
            return {
                "id": "j1",
                "state": "ok",
                "exit_code": 0,
                "outputs": outputs,
            }
        if path.endswith("/d1"):
            return {
                "id": "d1",
                "name": "result.txt",
                "state": "ok",
                "extension": "txt",
                "file_size": 7,
            }
        raise AssertionError((path, params))

    client.get.side_effect = get
    resumed = resume_operation(client, receipt["id"], timeout=10, poll_interval=0)
    result = resumed["result"]

    assert result["state"] == "ok"
    assert result["outputs"][0]["id"] == "d1"
    if operation_type == "workflow":
        assert result["id"] == "inv1"
        assert result["workflow_id"] == "wf1"
    elif operation_type == "udt":
        assert result["tool_uuid"] == "u1"
    else:
        assert result["id"] == "d1"
        assert result["file_size"] == 7
    client.post.assert_not_called()


def test_unknown_receipt_is_explicitly_non_resumable(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    error = GalaxyBackendError(
        "submission response was lost",
        error_kind="unknown_submission_state",
        submission_state="unknown",
        retry_safe=False,
    )
    receipt = create_receipt("tool", {"tool_id": "t1"}, error=error)
    client = MagicMock()
    result = resume_operation(client, receipt["id"])

    assert result["resumable"] is False
    assert result["reason"] == "no_known_request_job_or_upload_session"
    assert result["recommended_action"] == "do_not_resubmit"
    client.get.assert_not_called()
    client.post.assert_not_called()


def test_resume_status_error_does_not_mark_receipt_failed(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation, show_receipt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    initial = GalaxyBackendError(
        "timed out",
        category="timeout",
        error_kind="job_timeout",
        submission_state="submitted",
        retry_safe=False,
        details={"history_id": "h1", "job_ids": ["j1"]},
    )
    receipt = create_receipt("udt", {"history_id": "h1"}, error=initial)
    client = MagicMock()
    client.get.side_effect = GalaxyBackendError("offline", category="connection")

    with pytest.raises(GalaxyBackendError):
        resume_operation(client, receipt["id"], timeout=10, poll_interval=0)

    saved = show_receipt(receipt["id"])
    assert saved["state"] == "submitted"
    assert saved["submission_state"] == "submitted"


def test_tus_fetch_write_ahead_marker_prevents_replay(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation, show_receipt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    source = tmp_path / "input.txt"
    source.write_text("data")
    interrupted = GalaxyBackendError(
        "upload interrupted",
        error_kind="tus_upload_interrupted",
        submission_state="submitted",
        retry_safe=False,
        details={"history_id": "h1", "tus_session_id": "s1", "upload_offset": 4},
    )
    receipt = create_receipt(
        "upload",
        {
            "history_id": "h1",
            "local_path": str(source),
            "file_type": "txt",
            "dbkey": "?",
        },
        error=interrupted,
    )
    client = MagicMock()

    def resume(*args, before_fetch_submit=None, **kwargs):
        before_fetch_submit()
        raise GalaxyBackendError(
            "fetch response lost",
            error_kind="tus_fetch_submission_unknown",
            submission_state="unknown",
            retry_safe=False,
        )

    client.resume_tus_upload_file.side_effect = resume
    with pytest.raises(GalaxyBackendError):
        resume_operation(client, receipt["id"])

    saved = show_receipt(receipt["id"])
    assert saved["resume"]["fetch_submission_state"] == "unknown"
    blocked = resume_operation(client, receipt["id"])
    assert blocked["resumable"] is False
    assert blocked["reason"] == "tus_fetch_submission_state_unknown"
    assert client.resume_tus_upload_file.call_count == 1
    call = client.resume_tus_upload_file.call_args
    assert call.kwargs["expected_size"] == 4
    assert len(call.kwargs["expected_sha256"]) == 64


def test_tus_resume_refuses_same_size_changed_local_file(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path / "operations"))
    source = tmp_path / "input.txt"
    source.write_text("AAAA")
    interrupted = GalaxyBackendError(
        "upload interrupted",
        error_kind="tus_upload_interrupted",
        submission_state="submitted",
        retry_safe=False,
        details={"history_id": "h1", "tus_session_id": "s1"},
    )
    receipt = create_receipt(
        "upload",
        {"history_id": "h1", "local_path": str(source)},
        error=interrupted,
    )
    source.write_text("BBBB")
    client = GalaxyClient(url="https://galaxy.example", api_key="key")
    with patch("galaxy_cli.utils.galaxy_backend.requests.head") as head:
        blocked = resume_operation(client, receipt["id"])

    assert blocked["resumable"] is False
    assert blocked["reason"] == "tus_local_file_changed"
    head.assert_not_called()


def test_tus_backend_checks_file_identity_before_head_or_patch(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    source = tmp_path / "input.txt"
    source.write_text("BBBB")
    client = GalaxyClient(url="https://galaxy.example", api_key="key")

    with patch("galaxy_cli.utils.galaxy_backend.requests.head") as head, pytest.raises(
        GalaxyBackendError
    ) as exc:
        client.resume_tus_upload_file(
            source,
            "h1",
            "session-1",
            expected_size=4,
            expected_sha256="0" * 64,
        )

    assert exc.value.error_kind == "tus_local_file_changed"
    head.assert_not_called()


def test_tus_resume_by_exported_receipt_path_is_read_only_and_untrusted(
    tmp_path, monkeypatch
):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    operation_dir = tmp_path / "operations"
    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(operation_dir))
    interrupted = GalaxyBackendError(
        "upload interrupted",
        error_kind="tus_upload_interrupted",
        submission_state="submitted",
        retry_safe=False,
        details={"history_id": "h1", "tus_session_id": "s1"},
    )
    receipt = create_receipt(
        "upload",
        {"history_id": "h1", "local_path": "input.txt"},
        error=interrupted,
    )
    canonical = operation_dir / f"{receipt['id']}.json"
    exported = tmp_path / "exported-receipt.json"
    exported.write_text(canonical.read_text())
    canonical.unlink()
    client = MagicMock()
    blocked = resume_operation(client, exported)
    assert blocked["reason"] == "external_tus_receipt_untrusted"
    assert blocked["recommended_action"] == "do_not_resubmit"
    client.resume_tus_upload_file.assert_not_called()

    blocked_again = resume_operation(client, receipt["id"])
    assert blocked_again["reason"] == "external_tus_receipt_untrusted"
    client.resume_tus_upload_file.assert_not_called()


def test_resume_rejects_unsafe_receipt_identifier(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path / "operations"))
    exported = tmp_path / "unsafe-receipt.json"
    exported.write_text(json.dumps({
        "id": "../outside",
        "operation_type": "tool",
        "state": "submitted",
        "job_ids": ["j1"],
    }))

    with pytest.raises(GalaxyBackendError) as exc:
        resume_operation(MagicMock(), exported)
    assert exc.value.error_kind == "operation_receipt_invalid"
    assert not (tmp_path / "outside.json").exists()


@pytest.mark.parametrize(
    ("operation_type", "error_kind"),
    [
        ("tool", "tool_request_rejected"),
        ("udt", "udt_request_rejected"),
    ],
)
def test_legacy_terminal_rejection_remains_authoritatively_failed(
    tmp_path, monkeypatch, operation_type, error_kind
):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path / operation_type))
    rejected = GalaxyBackendError(
        "Galaxy rejected the request after creating a job.",
        category="tool_request_rejected",
        error_kind=error_kind,
        submission_state="submitted",
        retry_safe=False,
        details={
            "history_id": "h1",
            "tool_id": "t1",
            "job_ids": ["j1"],
        },
    )
    receipt = create_receipt(operation_type, {"history_id": "h1"}, error=rejected)
    client = MagicMock()
    client.get.return_value = {
        "id": "j1",
        "state": "ok",
        "exit_code": 0,
        "outputs": {},
    }

    resumed = resume_operation(client, receipt["id"], timeout=10, poll_interval=0)

    assert resumed["state"] == "failed"
    assert resumed["final_state"] == "failed"
    assert resumed["error_kind"] == error_kind
    assert resumed["result"]["success"] is False
    assert resumed["result"]["state"] == "failed"
    assert resumed["result"]["jobs"][0]["state"] == "ok"
    assert resumed["result"]["recommended_action"] == "do_not_resubmit"
    client.post.assert_not_called()


def test_strict_failed_request_discovers_all_jobs_and_outputs_before_failing(
    tmp_path, monkeypatch
):
    from galaxy_cli.core.operation import create_receipt, resume_operation

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    receipt = create_receipt(
        "tool",
        {"tool_id": "t1", "history_id": "h1"},
        result={
            "success": True,
            "state": "submitted",
            "history_id": "h1",
            "tool_id": "t1",
            "execution_backend": "strict",
            "tool_request_id": "r1",
        },
    )
    requested = []
    client = MagicMock()

    def get(path, params=None):
        requested.append(path)
        if path == "tool_requests/r1/state":
            return "failed"
        if path == "tool_requests/r1":
            return {
                "state": "failed",
                "jobs": [{"id": "j1"}, {"id": "j2"}],
                "outputs": [{"id": "d0", "src": "hda"}],
            }
        if path == "jobs/j1":
            return {
                "id": "j1",
                "state": "ok",
                "exit_code": 0,
                "outputs": {"out": {"id": "d1", "src": "hda"}},
            }
        if path == "jobs/j2":
            return {
                "id": "j2",
                "state": "ok",
                "exit_code": 0,
                "outputs": {"out": {"id": "d2", "src": "hda"}},
            }
        if path.startswith("histories/h1/contents/d"):
            output_id = path.rsplit("/", 1)[-1]
            return {
                "id": output_id,
                "name": output_id,
                "state": "ok",
                "extension": "txt",
                "file_size": 1,
            }
        raise AssertionError((path, params))

    client.get.side_effect = get
    resumed = resume_operation(client, receipt["id"], timeout=10, poll_interval=0)

    assert resumed["state"] == "failed"
    assert resumed["error_kind"] == "tool_request_rejected"
    assert resumed["result"]["success"] is False
    assert [job["id"] for job in resumed["result"]["jobs"]] == ["j1", "j2"]
    assert {output["id"] for output in resumed["result"]["outputs"]} == {
        "d0",
        "d1",
        "d2",
    }
    assert requested.count("jobs/j1") == 2
    assert requested.count("jobs/j2") == 2
    assert all(f"histories/h1/contents/d{index}" in requested for index in range(3))
    client.post.assert_not_called()


def test_no_wait_upload_preserves_job_and_output_identifiers():
    from galaxy_cli.core.dataset import upload_dataset

    client = MagicMock()
    client.upload_file.return_value = {
        "jobs": [{"id": "j1", "state": "new"}, {"id": "j2", "state": "new"}],
        "outputs": [
            {
                "id": "d1",
                "name": "input.txt",
                "state": "queued",
                "extension": "txt",
            }
        ],
    }

    result = upload_dataset(client, "h1", "input.txt", wait=False)

    assert result["job_ids"] == ["j1", "j2"]
    assert result["output_ids"] == ["d1"]
    assert [job["id"] for job in result["jobs"]] == ["j1", "j2"]
    assert [output["id"] for output in result["outputs"]] == ["d1"]
    client.get.assert_not_called()


@pytest.mark.parametrize(
    ("response", "error_kind"),
    [
        ({"jobs": [], "outputs": []}, "unknown_submission_state"),
        ({"jobs": [{"id": "j1"}, None], "outputs": []}, "upload_response_invalid"),
    ],
)
def test_no_wait_upload_rejects_unrecoverable_or_malformed_response(
    response, error_kind
):
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.upload_file.return_value = response

    with pytest.raises(GalaxyBackendError) as exc:
        upload_dataset(client, "h1", "input.txt", wait=False)

    assert exc.value.error_kind == error_kind
    assert exc.value.retry_safe is False
    assert exc.value.submission_state in {"submitted", "unknown"}


def test_output_only_upload_receipt_can_complete(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    receipt = create_receipt(
        "upload",
        {"history_id": "h1", "local_path": "input.txt"},
        result={
            "success": True,
            "state": "submitted",
            "history_id": "h1",
            "outputs": [{"id": "d1", "src": "hda"}],
        },
    )
    client = MagicMock()
    client.get.return_value = {
        "id": "d1",
        "name": "input.txt",
        "state": "ok",
        "extension": "txt",
        "file_size": 8,
    }

    resumed = resume_operation(client, receipt["id"], timeout=10, poll_interval=0)

    assert resumed["state"] == "complete"
    assert resumed["result"]["success"] is True
    assert resumed["result"]["id"] == "d1"
    assert resumed["result"]["file_size"] == 8
    assert resumed["job_ids"] == []
    client.get.assert_called_once_with("histories/h1/contents/d1")
    client.post.assert_not_called()


def test_upload_outputs_missing_never_becomes_empty_success(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    missing = GalaxyBackendError(
        "Completed upload returned no outputs.",
        error_kind="upload_outputs_missing",
        submission_state="submitted",
        retry_safe=False,
        details={"history_id": "h1", "job_ids": ["j1"], "output_ids": []},
    )
    receipt = create_receipt(
        "upload", {"history_id": "h1", "local_path": "input.txt"}, error=missing
    )
    client = MagicMock()
    client.get.return_value = {
        "id": "j1",
        "state": "ok",
        "exit_code": 0,
        "outputs": {},
    }

    resumed = resume_operation(client, receipt["id"], timeout=10, poll_interval=0)

    assert resumed["state"] == "failed"
    assert resumed["error_kind"] == "upload_outputs_missing"
    assert resumed["result"]["success"] is False
    assert resumed["result"]["outputs"] == []
    assert "id" not in resumed["result"]


def test_tus_prefetch_failure_can_be_resumed_again(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation, show_receipt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    source = tmp_path / "input.txt"
    source.write_text("original")
    interrupted = GalaxyBackendError(
        "upload interrupted",
        error_kind="tus_upload_interrupted",
        submission_state="submitted",
        retry_safe=False,
        details={"history_id": "h1", "tus_session_id": "s1", "upload_offset": 4},
    )
    receipt = create_receipt(
        "upload",
        {
            "history_id": "h1",
            "local_path": str(source),
            "file_type": "txt",
            "dbkey": "?",
        },
        error=interrupted,
    )
    client = MagicMock()
    attempts = {"count": 0}

    def resume(*args, before_fetch_submit=None, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise GalaxyBackendError(
                "HEAD request was unavailable",
                category="connection",
                error_kind="tus_resume_unavailable",
                submission_state="submitted",
                retry_safe=False,
            )
        before_fetch_submit()
        return {
            "jobs": [{"id": "j1"}],
            "outputs": [{"id": "d1", "src": "hda"}],
        }

    def get(path, params=None):
        if path == "jobs/j1":
            return {
                "id": "j1",
                "state": "ok",
                "exit_code": 0,
                "outputs": {"out": {"id": "d1", "src": "hda"}},
            }
        if path == "histories/h1/contents/d1":
            return {
                "id": "d1",
                "name": "input.txt",
                "state": "ok",
                "extension": "txt",
                "file_size": 8,
            }
        raise AssertionError((path, params))

    client.resume_tus_upload_file.side_effect = resume
    client.get.side_effect = get
    with pytest.raises(GalaxyBackendError) as exc:
        resume_operation(client, receipt["id"])
    assert exc.value.error_kind == "tus_resume_unavailable"
    assert show_receipt(receipt["id"])["state"] == "submitted"

    resumed = resume_operation(client, receipt["id"], timeout=10, poll_interval=0)

    assert resumed["state"] == "complete"
    assert resumed["result"]["id"] == "d1"
    assert client.resume_tus_upload_file.call_count == 2


def test_concurrent_tus_resume_calls_fetch_only_once(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    source = tmp_path / "input.txt"
    source.write_text("data")
    interrupted = GalaxyBackendError(
        "upload interrupted",
        error_kind="tus_upload_interrupted",
        submission_state="submitted",
        retry_safe=False,
        details={"history_id": "h1", "tus_session_id": "s1", "upload_offset": 4},
    )
    receipt = create_receipt(
        "upload",
        {"history_id": "h1", "local_path": str(source)},
        error=interrupted,
    )
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    second_started = threading.Event()

    class Client:
        def __init__(self):
            self.fetch_calls = 0
            self.fetch_lock = threading.Lock()

        def resume_tus_upload_file(self, *args, before_fetch_submit=None, **kwargs):
            with self.fetch_lock:
                self.fetch_calls += 1
            before_fetch_submit()
            fetch_started.set()
            if not release_fetch.wait(2):
                raise AssertionError("test did not release the TUS fetch")
            return {
                "jobs": [{"id": "j1"}],
                "outputs": [{"id": "d1", "src": "hda"}],
            }

        def get(self, path, params=None):
            if path == "jobs/j1":
                return {
                    "id": "j1",
                    "state": "ok",
                    "exit_code": 0,
                    "outputs": {"out": {"id": "d1", "src": "hda"}},
                }
            if path == "histories/h1/contents/d1":
                return {
                    "id": "d1",
                    "name": "input.txt",
                    "state": "ok",
                    "extension": "txt",
                    "file_size": 8,
                }
            raise AssertionError((path, params))

    client = Client()
    results = []
    errors = []

    def worker(started=None):
        if started is not None:
            started.set()
        try:
            results.append(
                resume_operation(client, receipt["id"], timeout=10, poll_interval=0)
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker, args=(second_started,))
    first.start()
    assert fetch_started.wait(2)
    second.start()
    assert second_started.wait(2)
    release_fetch.set()
    first.join(3)
    second.join(3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert client.fetch_calls == 1
    assert len(results) == 2
    assert all(result["result"]["id"] == "d1" for result in results)


def test_tus_receipt_redacts_api_key_from_all_details(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path))
    secret = "galaxy-api-key-never-persist"
    interrupted = GalaxyBackendError(
        f"upload failed while using {secret}",
        error_kind="tus_upload_interrupted",
        submission_state="submitted",
        retry_safe=False,
        details={
            "history_id": "h1",
            "api_key": secret,
            "authorization": f"Bearer {secret}",
            "tus_session_id": f"session-{secret}",
            "upload_offset": 4,
            "nested": {"message": f"server echoed {secret}"},
        },
    )
    receipt = create_receipt(
        "upload",
        {
            "history_id": "h1",
            "local_path": "input.txt",
            "metadata": {"api_key": secret},
        },
        error=interrupted,
        secrets=(secret,),
    )

    serialized = (tmp_path / f"{receipt['id']}.json").read_text()
    persisted = json.loads(serialized)
    assert secret not in serialized
    assert persisted["resume"]["tus_session_id"] == "session-[REDACTED]"


def test_blocking_upload_final_get_uses_submission_deadline():
    from galaxy_cli.core.dataset import upload_dataset

    deadline = 1234.5

    class Client:
        def __init__(self):
            self.deadlines = []

        def upload_file(self, *args, **kwargs):
            return {
                "jobs": [{"id": "j1"}],
                "outputs": [{"id": "d1", "name": "input.txt", "src": "hda"}],
            }

        def get_with_deadline(self, path, params=None, deadline=None):
            self.deadlines.append((path, deadline))
            return {
                "id": "d1",
                "name": "input.txt",
                "state": "ok",
                "extension": "txt",
                "file_size": 8,
            }

    client = Client()
    with (
        patch("galaxy_cli.core.dataset.deadline_after", return_value=deadline),
        patch("galaxy_cli.core.dataset.remaining", return_value=10),
        patch(
            "galaxy_cli.core.dataset.wait_for_jobs",
            return_value=[{"id": "j1", "state": "ok", "exit_code": 0}],
        ) as wait,
    ):
        result = upload_dataset(client, "h1", "input.txt", wait=True)

    assert result["state"] == "ok"
    assert client.deadlines == [("histories/h1/contents/d1", deadline)]
    assert wait.call_args.kwargs["deadline"] == deadline


def test_udt_evidence_final_gets_use_submission_deadline():
    from galaxy_cli.core.udt import run_udt

    deadline = 2345.6

    class Client:
        def __init__(self):
            self.deadlines = []

        def get(self, path, params=None):
            if path == "unprivileged_tools/u1":
                return {
                    "uuid": "u1",
                    "active": True,
                    "representation": {"id": "ut1", "version": "1"},
                }
            raise AssertionError((path, params))

        def post(self, path, data=None, json_data=None, params=None):
            assert path == "tools"
            return {
                "jobs": [{"id": "j1", "state": "new"}],
                "outputs": [{"id": "d1", "src": "hda"}],
            }

        def get_with_deadline(self, path, params=None, deadline=None):
            self.deadlines.append((path, deadline))
            if path == "jobs/j1":
                return {"id": "j1", "state": "ok", "exit_code": 0}
            if path == "histories/h1/contents/d1":
                return {
                    "id": "d1",
                    "name": "result.txt",
                    "state": "ok",
                    "extension": "txt",
                    "file_size": 8,
                }
            raise AssertionError((path, params))

    client = Client()
    evidence = {}
    with (
        patch("galaxy_cli.core.udt.deadline_after", return_value=deadline),
        patch("galaxy_cli.core.udt.remaining", return_value=10),
        patch(
            "galaxy_cli.core.udt.wait_for_jobs",
            return_value=[{"id": "j1", "state": "ok", "exit_code": 0}],
        ) as wait,
    ):
        result = run_udt(client, "u1", "h1", {}, evidence=evidence)

    assert result["state"] == "ok"
    assert evidence["jobs.json"][0]["id"] == "j1"
    assert evidence["outputs.json"][0]["id"] == "d1"
    assert client.deadlines == [
        ("jobs/j1", deadline),
        ("histories/h1/contents/d1", deadline),
        ("histories/h1/contents/d1", deadline),
    ]
    assert wait.call_args.kwargs["deadline"] == deadline
