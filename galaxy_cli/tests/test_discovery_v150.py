import json
import time
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


def test_metadata_cache_ttl_corruption_and_isolated_dirs(tmp_path, monkeypatch):
    from galaxy_cli.core import metadata_cache

    first = tmp_path / "run-one"
    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(first))
    path = metadata_cache.write("tools", ["url", "version"], {"ok": True})
    assert metadata_cache.read("tools", ["url", "version"]) == {"ok": True}
    payload = json.loads(path.read_text())
    payload["created_at"] = time.time() - 100
    path.write_text(json.dumps(payload))
    assert metadata_cache.read("tools", ["url", "version"], ttl=10) is None
    assert not path.exists()

    path = metadata_cache.write("tools", ["url", "version"], {"ok": True})
    path.write_text("broken")
    assert metadata_cache.read("tools", ["url", "version"]) is None

    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(tmp_path / "run-two"))
    assert metadata_cache.read("tools", ["url", "version"]) is None


def test_server_version_is_cached_without_secret(tmp_path, monkeypatch):
    from galaxy_cli.core import metadata_cache

    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(tmp_path / "cache"))
    client = MagicMock(url="https://galaxy.example/")
    client.api_key = "super-secret"
    client.get_version.return_value = {"version_major": "26.0"}
    assert metadata_cache.server_version(client) == {"version_major": "26.0"}
    assert metadata_cache.server_version(client) == {"version_major": "26.0"}
    client.get_version.assert_called_once()
    assert "super-secret" not in "".join(path.read_text() for path in (tmp_path / "cache").rglob("*.json"))


def test_history_contents_filters_and_resolves():
    from galaxy_cli.core.history import history_contents, resolve_history_content

    client = MagicMock()
    client.get.return_value = [
        {"hid": 1, "id": "d1", "name": "Reads", "state": "ok", "extension": "fastq"},
        {"hid": 2, "id": "c1", "name": "Pairs", "src": "hdca", "populated_state": "ok", "collection_type": "paired", "element_count": 2},
    ]
    assert history_contents(client, "h1", extension="fastq")[0]["id"] == "d1"
    assert resolve_history_content(client, "h1", exact_name="Pairs")["src"] == "hdca"


def test_tool_template_examples_and_server_validation_unsupported():
    from galaxy_cli.core.tool import tool_examples, tool_template, validate_tool_on_server
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.get.return_value = {
        "id": "nested", "name": "Nested", "version": "1",
        "inputs": [
            {"name": "reads", "type": "data", "multiple": True},
            {"name": "repeat", "type": "repeat", "inputs": [{"name": "column", "type": "integer", "value": 1}]},
        ],
        "outputs": [],
    }
    template = tool_template(client, "nested", use_cache=False)
    assert template["inputs"]["reads"][0]["src"] == "hda"
    assert template["inputs"]["repeat"][0]["column"] == 1

    client.get.return_value = [{"inputs": {"a": 1}}, {"inputs": {"a": 2}}, {"inputs": {"a": 3}}]
    assert tool_examples(client, "nested", limit=2)["count"] == 2

    client.get.return_value = {
        "id": "nested", "name": "Nested", "version": "1", "inputs": [], "outputs": []
    }
    client.post.side_effect = GalaxyBackendError("missing", status_code=404)
    result = validate_tool_on_server(client, "nested", "h1", {})
    assert result == {
        "supported": False, "valid": None, "tool_id": "nested",
        "tool_version": "1", "reason": "server_side_validation_unsupported",
    }


def test_tool_search_filters_io_and_exact_version_without_selecting_best():
    from galaxy_cli.core.tool import search_tools

    client = MagicMock()
    client.get.side_effect = [
        [{"id": "cut", "name": "Cut", "version": "1", "description": "columns"}],
        {
            "id": "cut", "name": "Cut", "version": "1", "description": "columns",
            "inputs": [{"name": "input", "type": "data", "extensions": ["tabular"]}],
            "outputs": [{"name": "out", "format": "tabular"}],
        },
    ]
    result = search_tools(
        client, "Cut", exact=True, version="1", input_extension="tabular",
        output_extension="tabular", use_cache=False,
    )
    assert result[0]["id"] == "cut"
    assert result[0]["input_extensions"] == ["tabular"]


def test_job_logs_are_bounded_and_full_output_is_recoverable():
    from galaxy_cli.core.job import diagnose_job, job_logs

    client = MagicMock()
    client.get.side_effect = [
        {"stdout": "one\ntwo\nthree", "stderr": "warning\nFATAL boom\nafter"},
        {"id": "j1", "state": "error", "exit_code": 1, "tool_id": "tool", "history_id": "h1"},
        {"stdout": "", "stderr": "before\nFATAL boom\nafter"},
    ]
    bounded = job_logs(client, "j1", tail=1, max_chars=20)
    assert bounded["streams"]["stdout"]["text"] == "three"
    assert bounded["truncated"] is True
    diagnosed = diagnose_job(client, "j1", max_chars=100)
    assert "FATAL boom" in diagnosed["error_summary"]["stderr"]["text"]


def test_nested_collection_flatten_and_resolve():
    from galaxy_cli.core.collection import flatten_collection, resolve_collection_element

    client = MagicMock()
    client.get.return_value = {
        "id": "outer", "collection_type": "list:paired", "elements": [{
            "element_identifier": "sample", "element_type": "dataset_collection",
            "object": {"id": "pair", "collection_type": "paired", "elements": [
                {"element_identifier": "forward", "element_type": "hda", "object": {"id": "d1", "state": "ok", "extension": "fastq"}},
                {"element_identifier": "reverse", "element_type": "hda", "object": {"id": "d2", "state": "ok", "extension": "fastq"}},
            ]},
        }],
    }
    flattened = flatten_collection(client, "outer")
    assert [item["element_path"] for item in flattened["elements"]] == ["sample/forward", "sample/reverse"]
    assert resolve_collection_element(client, "outer", "sample/reverse")["id"] == "d2"


def test_nested_collection_cycle_is_rejected():
    from galaxy_cli.core.collection import flatten_collection

    client = MagicMock()
    client.get.return_value = {
        "id": "loop", "collection_type": "list", "elements": [{
            "element_identifier": "again", "element_type": "dataset_collection",
            "object": {"id": "loop", "collection_type": "list"},
        }],
    }
    with pytest.raises(ValueError, match="cycle"):
        flatten_collection(client, "loop")


def test_udt_preflight_does_not_create():
    from galaxy_cli.core.udt import validate_udt

    representation = {
        "class": "GalaxyUserTool", "id": "u", "version": "1", "name": "U",
        "shell_command": "echo ok", "container": "example/image:1",
        "inputs": [], "outputs": [],
    }
    client = MagicMock()
    client.post.side_effect = [{"errors": {}}, {"runtime_model": "local"}]
    result = validate_udt(client, representation, history_id="h1")
    assert result["valid"] is True
    assert [call.args[0] for call in client.post.call_args_list] == [
        "unprivileged_tools/build", "unprivileged_tools/runtime_model"
    ]


def test_operation_receipt_redacts_payload_and_resume_never_posts(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path / "operations"))
    receipt = create_receipt(
        "tool", {"api_key": "secret-value", "inputs": {"text": "science"}},
        result={"state": "running", "history_id": "h1", "tool_id": "t1", "job_ids": ["j1"]},
    )
    text = (tmp_path / "operations" / f"{receipt['id']}.json").read_text()
    assert "secret-value" not in text and "science" not in text
    client = MagicMock()
    client.get.return_value = {"state": "ok", "exit_code": 0}
    resumed = resume_operation(client, receipt["id"], timeout=1, poll_interval=0)
    assert resumed["state"] == "complete"
    client.post.assert_not_called()


def test_unknown_operation_resume_never_replays_submission(tmp_path, monkeypatch):
    from galaxy_cli.core.operation import create_receipt, resume_operation
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path / "operations"))
    error = GalaxyBackendError(
        "lost response", submission_state="unknown", retry_safe=False,
        error_kind="unknown_submission_state",
    )
    receipt = create_receipt("tool", {"tool_id": "t"}, error=error)
    client = MagicMock()
    assert resume_operation(client, receipt["id"])["state"] == "unknown"
    client.post.assert_not_called()
    client.get.assert_not_called()


def test_workflow_template_and_authoritative_wait():
    from galaxy_cli.core.workflow import wait_for_workflow_run, workflow_template

    client = MagicMock()
    client.get.side_effect = [{
        "id": "wf", "version": 2,
        "inputs": {"0": {"label": "reads"}},
        "steps": {"0": {"id": "s0", "type": "data_input", "tool_inputs": {}, "input_connections": {}}},
    }]
    assert workflow_template(client, "wf")["inputs"]["0"]["src"] == "hda"

    client.get.side_effect = [
        {"state": "scheduled", "steps": [{"job_id": "j1", "state": "scheduled"}]},
        {"id": "j1", "state": "ok", "exit_code": 0},
        {"id": "j1", "state": "ok", "exit_code": 0, "outputs": {}},
    ]
    result = wait_for_workflow_run(
        client, {"id": "inv", "workflow_id": "wf", "history_id": "h1", "state": "new"},
        timeout=1, poll_interval=0,
    )
    assert result["success"] is True and result["jobs"][0]["id"] == "j1"


def test_workflow_wait_timeout_is_structured_nonzero():
    from galaxy_cli.core.workflow import wait_for_workflow_run
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    with pytest.raises(GalaxyBackendError) as exc:
        wait_for_workflow_run(
            MagicMock(), {"id": "inv", "workflow_id": "wf", "history_id": "h1"},
            timeout=0, poll_interval=0,
        )
    assert exc.value.exit_code == EXIT_TIMEOUT
    assert exc.value.submission_state == "submitted"


def test_upload_auto_capability_choice_and_safe_fallback():
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    client = MagicMock(spec=GalaxyClient)
    client.url = "https://galaxy.example/"
    unsupported = GalaxyBackendError(
        "missing", status_code=404, submission_state="not_submitted", retry_safe=True
    )
    client.tus_upload_file.side_effect = unsupported
    client.upload_file.return_value = {"outputs": [{"id": "d1", "name": "x", "state": "new"}], "jobs": []}
    with patch("galaxy_cli.core.server.server_capabilities", return_value={"capabilities": {"tus_upload": True}}):
        result = upload_dataset(client, "h1", "x", upload_backend="auto")
    assert result["execution_backend"] == "legacy"
    client.tus_upload_file.assert_called_once()
    client.upload_file.assert_called_once()


def test_upload_tus_unknown_never_falls_back():
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    client = MagicMock(spec=GalaxyClient)
    client.url = "https://galaxy.example/"
    client.tus_upload_file.side_effect = GalaxyBackendError(
        "interrupted", error_kind="tus_upload_interrupted",
        submission_state="submitted", retry_safe=False,
    )
    with patch("galaxy_cli.core.server.server_capabilities", return_value={"capabilities": {"tus_upload": True}}):
        with pytest.raises(GalaxyBackendError):
            upload_dataset(client, "h1", "x", upload_backend="auto")
    client.upload_file.assert_not_called()


def test_tus_protocol_creates_patches_and_fetches_once(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyClient

    source = tmp_path / "reads.txt"
    source.write_text("reads\n")
    client = GalaxyClient(url="https://galaxy.example", api_key="secret")
    created = MagicMock(status_code=201, headers={"Location": "/api/upload/resumable_upload/session-1"})
    patched = MagicMock(status_code=204, headers={"Upload-Offset": str(source.stat().st_size)})
    client.post = MagicMock(return_value={"jobs": [{"id": "j1"}], "outputs": []})
    with patch("galaxy_cli.utils.galaxy_backend.requests.post", return_value=created) as create, patch(
        "galaxy_cli.utils.galaxy_backend.requests.patch", return_value=patched
    ) as upload:
        result = client.tus_upload_file(source, "h1", file_type="txt")
    assert result["jobs"][0]["id"] == "j1"
    assert create.call_args.kwargs["headers"]["Tus-Resumable"] == "1.0.0"
    assert upload.call_count == 1
    fetch = client.post.call_args
    assert fetch.args[0] == "tools/fetch"
    assert fetch.kwargs["json_data"]["files_0|file_data"]["session_id"] == "session-1"


def test_global_output_file_and_bounds(tmp_path):
    from galaxy_cli.cli import cli

    runner = CliRunner()
    output_path = tmp_path / "full.json"
    with patch("galaxy_cli.cli._get_client", return_value=MagicMock()), patch(
        "galaxy_cli.cli.history_mod.list_histories",
        return_value=[{"id": str(index), "name": "x" * 50} for index in range(5)],
    ):
        result = runner.invoke(cli, ["--output-file", str(output_path), "history", "list"])
    summary = json.loads(result.output)
    assert summary["truncated"] is True and summary["bytes"] == output_path.stat().st_size
    assert len(json.loads(output_path.read_text())) == 5
