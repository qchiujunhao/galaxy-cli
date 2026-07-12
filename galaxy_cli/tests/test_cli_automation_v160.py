"""CLI integration for the v1.6 automation surface."""

import json
import sys
from unittest.mock import patch

import click
from click.testing import CliRunner
import pytest
import requests


def _run_main(args):
    from galaxy_cli.cli import main

    rendered = []
    with patch.object(sys, "argv", ["galaxy-cli", *args]), patch(
        "click.echo", side_effect=lambda value="", **kwargs: rendered.append(str(value))
    ), pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code, json.loads(rendered[-1]), rendered[-1]


def test_envelope_is_opt_in_and_has_canonical_command():
    from galaxy_cli.cli import cli

    value = {"url": "https://galaxy.example", "api_key": "not set"}
    with patch("galaxy_cli.cli.config_mod.show_config", return_value=value):
        legacy = CliRunner().invoke(cli, ["config", "show"])
        wrapped = CliRunner().invoke(cli, ["--envelope", "config", "show"])

    assert json.loads(legacy.output) == value
    envelope = json.loads(wrapped.output)
    assert envelope["schema_version"] == "1.0"
    assert envelope["command"] == "config.show"
    assert envelope["success"] is True
    assert envelope["data"] == value


def test_agent_mode_uses_canonical_alias_identity_and_bounds():
    from galaxy_cli.cli import cli

    preview = {"id": "d1", "lines": ["x"], "rows": [], "total_shown": 1}
    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.dataset_mod.peek_dataset", return_value=preview
    ):
        result = CliRunner().invoke(cli, ["--agent", "dataset", "preview", "d1"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "dataset.peek"
    assert payload["data"] == preview


def test_envelope_environment_switch(monkeypatch):
    from galaxy_cli.cli import cli

    monkeypatch.setenv("GALAXY_CLI_OUTPUT", "envelope-v1")
    with patch("galaxy_cli.cli.session_mod.load_session", return_value={}):
        result = CliRunner().invoke(cli, ["session", "show"])
    assert json.loads(result.output)["schema_version"] == "1.0"


@pytest.mark.parametrize("root_flags", [["--agent"], ["--envelope", "--max-items", "1"]])
def test_envelope_bounds_preserve_schema_top_level(root_flags):
    from galaxy_cli.cli import cli

    value = {"values": list(range(101))}
    with patch("galaxy_cli.cli.config_mod.show_config", return_value=value):
        result = CliRunner().invoke(cli, [*root_flags, "config", "show"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert list(payload) == [
        "schema_version", "command", "success", "data", "warnings", "next_commands"
    ]
    assert "output_truncated" in payload["warnings"]


def test_agent_bounds_apply_to_error_payloads():
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    error = GalaxyBackendError(
        "x" * 50000,
        category="server_error",
        details={"items": list(range(101))},
    )
    with patch("galaxy_cli.cli.config_mod.show_config", side_effect=error):
        code, payload, rendered = _run_main([
            "--agent", "--max-chars", "50", "config", "show"
        ])

    assert code != 0
    assert len(payload["data"]["message"]) == 50
    assert len(payload["data"]["items"]) == 100
    assert "output_truncated" in payload["warnings"]
    assert len(rendered) < 1000


def test_agent_enforces_global_node_and_serialized_byte_budgets():
    from galaxy_cli.cli import _AGENT_MAX_OUTPUT_BYTES, cli

    leaf = "x" * 12001
    value = {"matrix": [[leaf] * 100 for _ in range(100)]}
    with patch("galaxy_cli.cli.config_mod.show_config", return_value=value):
        result = CliRunner().invoke(cli, ["--agent", "config", "show"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(result.output.encode("utf-8")) <= _AGENT_MAX_OUTPUT_BYTES
    assert payload["data"] == {
        "truncated": True,
        "reason": "output_byte_budget_exceeded",
    }
    assert "output_node_budget_exceeded" in payload["warnings"]
    assert "output_byte_budget_exceeded" in payload["warnings"]
    assert "output_truncated" in payload["warnings"]


def test_agent_stdout_budget_covers_deep_unicode_warnings_and_next_commands():
    from galaxy_cli.cli import _AGENT_MAX_OUTPUT_BYTES, cli

    nested = "叶" * 50000
    for _ in range(120):
        nested = {"child": nested}
    envelope = {
        "schema_version": "1.0",
        "command": "config.show",
        "success": True,
        "data": nested,
        "warnings": ["警" * 50000 for _ in range(100)],
        "next_commands": {
            f"command_{index}": "命" * 50000 for index in range(100)
        },
    }
    with patch("galaxy_cli.cli.config_mod.show_config", return_value={}), patch(
        "galaxy_cli.cli.output_contract_mod.envelope_v1", return_value=envelope
    ):
        result = CliRunner().invoke(cli, ["--agent", "config", "show"])

    assert result.exit_code == 0, result.output
    assert len(result.output.encode("utf-8")) <= _AGENT_MAX_OUTPUT_BYTES
    payload = json.loads(result.output)
    assert list(payload) == [
        "schema_version", "command", "success", "data", "warnings",
        "next_commands",
    ]
    assert payload["data"]["reason"] == "output_byte_budget_exceeded"
    assert "output_byte_budget_exceeded" in payload["warnings"]
    assert "output_truncated" in payload["warnings"]
    assert payload["next_commands"] == {}


def test_agent_usage_error_stdout_respects_hard_byte_budget():
    from galaxy_cli.cli import _AGENT_MAX_OUTPUT_BYTES

    code, payload, rendered = _run_main(["--agent", "不存在" * 50000])

    assert code != 0
    assert payload["success"] is False
    assert "output_byte_budget_exceeded" in payload["warnings"]
    assert "output_truncated" in payload["warnings"]
    assert len((rendered + "\n").encode("utf-8")) <= _AGENT_MAX_OUTPUT_BYTES


def test_envelope_omits_overlong_next_command_instead_of_truncating_it():
    from galaxy_cli.cli import cli

    output_id = "dataset-" + "x" * 200
    value = {
        "success": True,
        "state": "ok",
        "outputs": [{"id": output_id, "src": "hda", "state": "ok"}],
    }
    with patch("galaxy_cli.cli.config_mod.show_config", return_value=value):
        result = CliRunner().invoke(cli, [
            "--envelope", "--max-chars", "40", "config", "show"
        ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["next_commands"] == {}
    assert "output_truncated" in payload["warnings"]
    assert output_id not in result.output


def test_output_file_summary_redacts_known_api_key(tmp_path):
    from galaxy_cli.cli import cli

    secret = "output-path-api-key"
    output_path = tmp_path / f"result-{secret}.json"
    with patch(
        "galaxy_cli.cli.config_mod.show_config",
        return_value={"url": "https://galaxy.example", "api_key": "masked"},
    ):
        result = CliRunner().invoke(cli, [
            "--api-key", secret, "--output-file", str(output_path),
            "config", "show",
        ])

    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert "[REDACTED]" in json.loads(result.output)["output_file"]


def test_agent_output_file_is_complete_redacted_and_stdout_is_bounded(tmp_path):
    from galaxy_cli.cli import _AGENT_MAX_OUTPUT_BYTES, cli

    secret = "agent-output-file-secret"
    output_path = tmp_path / f"result-{secret}.json"
    value = {
        "success": True,
        "state": "ok",
        "history_id": "history-1",
        "payload": "x" * 200000,
        "api_key": secret,
    }
    with patch("galaxy_cli.cli.config_mod.show_config", return_value=value):
        result = CliRunner().invoke(cli, [
            "--agent", "--api-key", secret, "--output-file", str(output_path),
            "config", "show",
        ])

    assert result.exit_code == 0, result.output
    assert len(result.output.encode("utf-8")) <= _AGENT_MAX_OUTPUT_BYTES
    assert secret not in result.output
    saved_text = output_path.read_text()
    assert secret not in saved_text
    saved = json.loads(saved_text)
    assert len(saved["data"]["payload"]) == 200000
    assert saved["data"]["api_key"] == "[REDACTED]"
    summary = json.loads(result.output)
    assert summary["command"] == "config.show"
    assert summary["data"]["history_id"] == "history-1"
    assert summary["data"]["bytes"] == output_path.stat().st_size
    assert "[REDACTED]" in summary["data"]["output_file"]
    assert not list(tmp_path.glob("*.tmp"))


def test_usage_error_writes_complete_output_file_and_keeps_exit_code(tmp_path):
    output_path = tmp_path / "usage-error.json"

    code, summary, _rendered = _run_main([
        "--output-file", str(output_path), "tool", "run",
    ])

    assert code == 1
    saved = json.loads(output_path.read_text())
    assert saved["success"] is False
    assert saved["error_kind"] == "missing_parameter"
    assert summary["success"] is False
    assert summary["state"] == "failed"
    assert summary["bytes"] == output_path.stat().st_size
    assert summary["output_file"] == str(output_path)


def test_click_error_writes_complete_output_file_and_keeps_exit_code(tmp_path):
    output_path = tmp_path / "click-error.json"
    message = "click failure " + "x" * 200000

    with patch(
        "galaxy_cli.cli.config_mod.show_config",
        side_effect=click.ClickException(message),
    ):
        code, summary, _rendered = _run_main([
            "--envelope", "--output-file", str(output_path), "config", "show",
        ])

    assert code == 1
    saved = json.loads(output_path.read_text())
    assert saved["success"] is False
    assert saved["data"]["category"] == "click_error"
    assert len(saved["data"]["message"]) > 200000
    assert summary["data"]["bytes"] == output_path.stat().st_size


def test_backend_error_writes_complete_redacted_file_and_keeps_exit_code(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    secret = "backend-output-file-secret"
    output_path = tmp_path / f"backend-{secret}.json"
    error = GalaxyBackendError(
        f"connection failed with {secret}: " + "x" * 200000,
        category="connection",
        error_kind="connection_failed",
        exit_code=2,
        submission_state="unknown",
        retry_safe=False,
    )
    with patch("galaxy_cli.cli.config_mod.show_config", side_effect=error):
        code, summary, rendered = _run_main([
            "--agent", "--api-key", secret, "--output-file", str(output_path),
            "config", "show",
        ])

    assert code == 2
    saved_text = output_path.read_text()
    assert secret not in saved_text
    assert secret not in rendered
    saved = json.loads(saved_text)
    assert saved["success"] is False
    assert saved["data"]["error_kind"] == "connection_failed"
    assert len(saved["data"]["message"]) > 200000
    assert summary["data"]["submission_state"] == "unknown"
    assert summary["data"]["retry_safe"] is False
    assert "[REDACTED]" in summary["data"]["output_file"]


def test_output_file_help_describes_complete_redacted_result():
    from galaxy_cli.cli import cli

    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "complete redacted JSON" in result.output
    assert "bounded summary" in result.output


def test_tus_fingerprint_progress_is_stderr_only(tmp_path):
    from galaxy_cli.cli import cli

    source = tmp_path / "reads.txt"
    source.write_text("reads")

    def upload(_client, _history_id, _file_path, progress=None, **_kwargs):
        progress("Fingerprinting interrupted upload: 5 / 5 bytes")
        return {
            "id": "dataset-1",
            "state": "ok",
            "success": True,
            "history_id": "history-1",
            "jobs": [],
            "outputs": [],
        }

    emitted = []
    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.dataset_mod.upload_dataset", side_effect=upload
    ), patch("galaxy_cli.cli._operation_receipt"), patch(
        "galaxy_cli.cli.click.echo",
        side_effect=lambda value="", **kwargs: emitted.append((str(value), kwargs)),
    ):
        result = CliRunner().invoke(cli, [
            "--agent", "dataset", "upload", str(source),
            "--history", "history-1", "--upload-backend", "tus",
        ])

    assert result.exit_code == 0, result.output
    progress_events = [value for value, kwargs in emitted if kwargs.get("err")]
    stdout_events = [value for value, kwargs in emitted if not kwargs.get("err")]
    assert any("Fingerprinting interrupted upload" in value for value in progress_events)
    assert all("Fingerprinting interrupted upload" not in value for value in stdout_events)
    json.loads(stdout_events[-1])


def test_envelope_unknown_submission_has_resume_and_no_rerun():
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    secret = "api-key-must-not-leak"
    error = GalaxyBackendError(
        f"transport lost {secret}",
        category="connection",
        error_kind="unknown_submission_state",
        submission_state="unknown",
        retry_safe=False,
        details={"job_ids": ["job 1"]},
    )

    def receipt(_kind, _payload, result=None, error=None):
        error.details = dict(error.details, operation_receipt="receipt 1")

    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.tool_mod.run_tool", side_effect=error
    ), patch("galaxy_cli.cli._operation_receipt", side_effect=receipt):
        code, payload, rendered = _run_main([
            "--envelope", "--api-key", secret,
            "tool", "run", "tool-1", "--history", "history-1", "--no-wait",
        ])

    assert code != 0
    assert payload["success"] is False
    assert payload["command"] == "tool.run"
    assert payload["next_commands"]["do_not_resubmit"] is True
    assert "resume" in payload["next_commands"]
    assert "diagnose" in payload["next_commands"]
    assert "tool run" not in json.dumps(payload["next_commands"])
    assert secret not in rendered


def test_backend_validation_fields_are_additive_and_mechanical():
    from galaxy_cli.cli import _backend_error_payload
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    error = GalaxyBackendError(
        "invalid",
        category="invalid_request",
        details={"validation": {
            "path": "$.inputs.mode",
            "expected": "allowed select value",
            "allowed_values": ["a", "b"],
            "example": "a",
        }},
    )
    payload = _backend_error_payload(error, {"command": "tool.run"})
    assert payload["error"] is True
    assert payload["path"] == "$.inputs.mode"
    assert payload["correction"] == {"value": "a"}
    assert payload["retry_safe"] is True


def test_http_validation_preserves_path_type_and_bounded_allowed_values():
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    response = type("Response", (), {})()
    response.status_code = 422
    response.raise_for_status = lambda: (_ for _ in ()).throw(requests.HTTPError())
    response.json = lambda: {"detail": [{
        "loc": ["body", "inputs", "mode"],
        "msg": "choose an allowed value",
        "type": "enum",
        "input": "private-input-value",
        "ctx": {"allowed_values": [f"value-{index}" for index in range(30)]},
    }]}
    client = GalaxyClient(url="https://galaxy.example", api_key="key")
    with pytest.raises(GalaxyBackendError) as exc:
        client._handle_response(response)
    validation = exc.value.details["validation"]
    assert validation["path"] == "$.inputs.mode"
    assert validation["received_type"] == "str"
    assert len(validation["allowed_values"]) == 25
    assert validation["allowed_values_truncated"] is True
    assert "private-input-value" not in json.dumps(validation)


def test_cache_stats_and_clear_never_return_cached_content(tmp_path, monkeypatch):
    from galaxy_cli.cli import cli
    from galaxy_cli.core import metadata_cache

    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(tmp_path))
    secret = "cached-secret-value"
    metadata_cache.write("tool-schema", ["https://galaxy.example", "v"], {"x": secret})

    stats = CliRunner().invoke(cli, ["cache", "stats"])
    assert stats.exit_code == 0, stats.output
    assert secret not in stats.output
    assert json.loads(stats.output)["entry_count"] == 1

    cleared = CliRunner().invoke(
        cli, ["cache", "clear", "--namespace", "tool-schema"]
    )
    assert json.loads(cleared.output)["removed_entries"] == 1


def test_receipt_and_normal_metadata_cache_do_not_persist_api_key(tmp_path, monkeypatch):
    from galaxy_cli.core import metadata_cache
    from galaxy_cli.core.operation import create_receipt

    secret = "api-key-never-persist"
    cache_dir = tmp_path / "cache"
    operation_dir = tmp_path / "operations"
    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(operation_dir))

    client = type("Client", (), {
        "url": "https://galaxy.example",
        "api_key": secret,
        "get_version": lambda self: {"version_major": "26.0"},
    })()
    metadata_cache.server_version(client, refresh=True)
    receipt = create_receipt(
        "tool", {"tool_id": "t1", "history_id": "h1", "token": secret},
        result={
            "success": True, "state": "submitted", "tool_request_id": "r1",
            "tool_id": f"tool-{secret}",
        },
        secrets=(secret,),
    )
    persisted = "".join(path.read_text() for path in cache_dir.rglob("*.json"))
    persisted += (operation_dir / f"{receipt['id']}.json").read_text()
    assert secret not in persisted


def test_operation_show_redacts_sensitive_fields_in_external_receipt(tmp_path):
    from galaxy_cli.cli import cli

    secret = "external-receipt-secret"
    receipt = tmp_path / "external.json"
    receipt.write_text(json.dumps({
        "id": "external-receipt",
        "state": "unknown",
        "api_key": secret,
        "nested": {"authorization": f"Bearer {secret}"},
    }))

    result = CliRunner().invoke(cli, ["operation", "show", str(receipt)])

    assert result.exit_code == 0, result.output
    assert secret not in result.output
    payload = json.loads(result.output)
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["authorization"] == "[REDACTED]"


def test_cache_warm_uses_only_read_only_metadata_paths():
    from galaxy_cli.cli import cli

    client = object()
    with patch("galaxy_cli.cli._get_client", return_value=client), patch(
        "galaxy_cli.cli.server_mod.server_capabilities", return_value={}
    ) as server, patch(
        "galaxy_cli.cli.tool_mod._load_cached_tools", return_value=[{}, {}]
    ) as tools, patch(
        "galaxy_cli.cli.metadata_cache_mod.stats", return_value={"entry_count": 3}
    ):
        result = CliRunner().invoke(cli, ["cache", "warm", "--server", "--tools"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["warmed"] == {"server": True, "tools": 2}
    server.assert_called_once_with(client, use_cache=True, refresh_cache=True)
    tools.assert_called_once_with(client, refresh_cache=True)


def test_documented_history_copy_name_option_is_compatible():
    from galaxy_cli.cli import cli

    copied = {
        "id": "copy-1", "name": "analysis", "copied_from_history_id": "seed-1"
    }
    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.history_mod.copy_history", return_value=copied
    ) as copy, patch("galaxy_cli.cli._operation_receipt"), patch(
        "galaxy_cli.cli.session_mod.set_current_history"
    ):
        result = CliRunner().invoke(cli, [
            "history", "copy", "seed-1", "--name", "analysis", "--no-wait"
        ])
    assert result.exit_code == 0, result.output
    assert copy.call_args.kwargs["name"] == "analysis"


def test_workflow_wait_failure_records_a_resumable_receipt():
    from galaxy_cli.cli import cli
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    error = GalaxyBackendError(
        "workflow wait timed out",
        category="timeout",
        error_kind="workflow_timeout",
        submission_state="submitted",
        retry_safe=False,
        details={
            "history_id": "history-1",
            "request_ids": ["invocation-1"],
            "job_ids": ["job-1"],
        },
    )
    submitted = {
        "id": "invocation-1",
        "workflow_id": "workflow-1",
        "history_id": "history-1",
        "state": "new",
    }
    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.workflow_mod.run_workflow", return_value=submitted
    ), patch(
        "galaxy_cli.cli.workflow_mod.wait_for_workflow_run", side_effect=error
    ), patch("galaxy_cli.cli._operation_receipt") as receipt:
        result = CliRunner().invoke(cli, [
            "workflow", "run", "workflow-1", "--history", "history-1"
        ])

    assert result.exit_code != 0
    receipt.assert_called_once()
    assert receipt.call_args.args[0] == "workflow"
    assert receipt.call_args.kwargs["error"] is error


def test_dataset_selectors_and_collection_preview_are_forwarded():
    from galaxy_cli.cli import cli

    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.dataset_mod.peek_dataset",
        return_value={"id": "d1", "lines": [], "rows": []},
    ) as peek:
        result = CliRunner().invoke(cli, [
            "dataset", "preview", "d1", "--tail", "3", "--grep", "error",
            "--context", "1", "--fields", "1,3",
        ])
    assert result.exit_code == 0, result.output
    assert peek.call_args.kwargs["tail"] == 3
    assert peek.call_args.kwargs["grep"] == "error"
    assert peek.call_args.kwargs["context"] == 1
    assert peek.call_args.kwargs["fields"] == "1,3"

    collection_result = {
        "collection_id": "c1", "dataset_id": "d1", "resolved_path": "sample/report",
        "lines": ["ok"], "rows": [],
    }
    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.collection_mod.preview_collection_element",
        return_value=collection_result,
    ) as preview:
        result = CliRunner().invoke(cli, [
            "collection", "preview", "c1", "--element", "sample/report", "--lines", "5",
        ])
    assert result.exit_code == 0, result.output
    assert preview.call_args.args[1:3] == ("c1", "sample/report")
    assert preview.call_args.kwargs["lines"] == 5


def test_tool_collection_peek_requires_and_uses_explicit_element():
    from galaxy_cli.cli import cli

    run_result = {
        "success": True, "state": "ok", "history_id": "h1", "tool_id": "t1",
        "jobs": [],
        "outputs": [{"output_name": "results", "id": "c1", "src": "hdca"}],
    }
    preview = {"collection_id": "c1", "dataset_id": "d1", "resolved_path": "s/r"}
    client = object()
    with patch("galaxy_cli.cli._get_client", return_value=client), patch(
        "galaxy_cli.cli.tool_mod.run_tool", return_value=run_result
    ), patch("galaxy_cli.cli._operation_receipt"), patch(
        "galaxy_cli.cli.collection_mod.preview_collection_element", return_value=preview
    ) as collection_preview:
        result = CliRunner().invoke(cli, [
            "tool", "run", "t1", "--history", "h1", "--peek-output", "results",
            "--peek-element", "s/r", "--peek-lines", "5",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output.splitlines()[-1])["output_peek"][0]["supported"] is True
    collection_preview.assert_called_once_with(
        client, "c1", "s/r", lines=5
    )


def test_post_submission_preview_error_cannot_suggest_tool_rerun():
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    run_result = {
        "success": True, "state": "ok", "history_id": "h1", "tool_id": "t1",
        "jobs": [{"id": "j1", "state": "ok"}],
        "outputs": [{"output_name": "results", "id": "c1", "src": "hdca"}],
    }
    preview_error = GalaxyBackendError(
        "missing element", category="invalid_request",
        error_kind="collection_element_missing",
    )

    def receipt(_kind, _payload, result=None, error=None):
        result["operation_receipt"] = "receipt-1"

    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.tool_mod.run_tool", return_value=run_result
    ), patch("galaxy_cli.cli._operation_receipt", side_effect=receipt), patch(
        "galaxy_cli.cli.collection_mod.preview_collection_element",
        side_effect=preview_error,
    ):
        code, payload, _ = _run_main([
            "--envelope", "tool", "run", "t1", "--history", "h1",
            "--peek-output", "results", "--peek-element", "missing",
        ])
    assert code != 0
    assert payload["data"]["submission_state"] == "submitted"
    assert payload["data"]["retry_safe"] is False
    assert payload["next_commands"]["do_not_resubmit"] is True
    assert "resume" in payload["next_commands"]
    assert "tool run" not in json.dumps(payload["next_commands"])


def test_blocking_cli_poll_defaults_are_adaptive_and_override_remains_fixed():
    from galaxy_cli.cli import cli

    contexts = {
        "operation": "resume",
        "dataset": "upload",
        "udt": "run",
        "tool": "run",
        "job": "wait",
        "workflow": "run",
    }
    root = click.Context(cli)
    for group_name, command_name in contexts.items():
        group = cli.get_command(root, group_name)
        group_ctx = click.Context(group, parent=root)
        command = group.get_command(group_ctx, command_name)
        option = next(param for param in command.params if param.name == "poll_interval")
        assert option.default is None

    tool = cli.get_command(root, "tool")
    tool_ctx = click.Context(tool, parent=root)
    run = tool.get_command(tool_ctx, "run")
    poll = next(param for param in run.params if param.name == "poll_interval")
    assert poll.type.convert("7", poll, tool_ctx) == 7.0
