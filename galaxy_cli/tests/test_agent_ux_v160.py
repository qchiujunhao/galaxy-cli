"""Phase 1 agent UX compatibility tests."""

import io
import json
import re
import sys
from unittest.mock import patch

from click.testing import CliRunner
import pytest


def _run_main(args, stdin_text=None):
    from galaxy_cli.cli import main

    rendered = []
    contexts = [
        patch.object(sys, "argv", ["galaxy-cli", *args]),
        patch("click.echo", side_effect=lambda value="", **kwargs: rendered.append(str(value))),
    ]
    if stdin_text is not None:
        contexts.append(
            patch("galaxy_cli.cli.click.get_text_stream", return_value=io.StringIO(stdin_text))
        )
    with contexts[0], contexts[1]:
        if stdin_text is None:
            with pytest.raises(SystemExit) as exc:
                main()
        else:
            with contexts[2], pytest.raises(SystemExit) as exc:
                main()
    assert rendered
    return exc.value.code, rendered[-1], json.loads(rendered[-1])


@pytest.mark.parametrize("source_kind", ["inline", "at_file", "stdin", "legacy"])
def test_tool_run_unified_json_inputs_and_history_alias(tmp_path, source_kind):
    from galaxy_cli.cli import cli

    payload = {"input": {"src": "hda", "id": "dataset-1"}, "flag": False}
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(payload))
    args = ["tool", "run", "tool-1", "--history", "history-1", "--no-wait"]
    stdin = None
    if source_kind == "inline":
        args.extend(["--inputs", json.dumps(payload)])
    elif source_kind == "at_file":
        args.extend(["--inputs", f"@{path}"])
    elif source_kind == "stdin":
        args.extend(["--inputs", "-"])
        stdin = json.dumps(payload)
    else:
        args.extend(["--inputs-json", str(path)])

    result_payload = {
        "success": True,
        "state": "submitted",
        "tool_id": "tool-1",
        "history_id": "history-1",
        "jobs": [],
        "outputs": [],
    }
    client = object()
    with patch("galaxy_cli.cli._get_client", return_value=client), patch(
        "galaxy_cli.cli.tool_mod.run_tool", return_value=result_payload
    ) as run_tool, patch("galaxy_cli.cli._operation_receipt"):
        result = CliRunner().invoke(cli, args, input=stdin)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["state"] == "submitted"
    assert run_tool.call_args.args[:3] == (client, "tool-1", "history-1")
    assert run_tool.call_args.kwargs["inputs"] == payload


def test_tool_run_i_overrides_loaded_json(tmp_path):
    from galaxy_cli.cli import cli

    path = tmp_path / "inputs.json"
    path.write_text('{"mode":"from-file"}')
    result_payload = {
        "success": True,
        "state": "submitted",
        "tool_id": "tool-1",
        "history_id": "history-1",
        "jobs": [],
        "outputs": [],
    }
    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        "galaxy_cli.cli.tool_mod.run_tool", return_value=result_payload
    ) as run_tool, patch("galaxy_cli.cli._operation_receipt"):
        result = CliRunner().invoke(
            cli,
            [
                "tool", "run", "tool-1", "--history-id", "history-1",
                "--inputs", f"@{path}", "-i", "mode=override", "--no-wait",
            ],
        )

    assert result.exit_code == 0, result.output
    assert run_tool.call_args.kwargs["inputs"] == {"mode": "override"}


def test_unified_loader_is_used_by_validate_and_udt_commands(tmp_path):
    from galaxy_cli.cli import cli

    payload = {"input": {"src": "hda", "id": "dataset-1"}}
    representation_path = tmp_path / "udt.json"
    representation_path.write_text('{"class":"GalaxyUserTool"}')
    client = object()

    with patch("galaxy_cli.cli._get_client", return_value=client), patch(
        "galaxy_cli.cli.tool_mod.validate_tool_on_server",
        return_value={"supported": True, "valid": True},
    ) as validate:
        result = CliRunner().invoke(
            cli,
            [
                "tool", "validate", "tool-1", "--history", "history-1",
                "--inputs", json.dumps(payload),
            ],
        )
    assert result.exit_code == 0, result.output
    validate.assert_called_once_with(client, "tool-1", "history-1", payload)

    with patch("galaxy_cli.cli._get_client", return_value=client), patch(
        "galaxy_cli.cli.udt_mod.run_udt",
        return_value={"success": True, "state": "submitted", "jobs": []},
    ) as run_udt, patch("galaxy_cli.cli._operation_receipt"):
        result = CliRunner().invoke(
            cli,
            [
                "udt", "run", "udt-1", "--history", "history-1",
                "--inputs", json.dumps(payload), "--no-wait",
            ],
        )
    assert result.exit_code == 0, result.output
    assert run_udt.call_args.args[3] == payload

    with patch("galaxy_cli.cli._get_client", return_value=client), patch(
        "galaxy_cli.cli.udt_mod.load_json_object",
        return_value={"class": "GalaxyUserTool"},
    ), patch(
        "galaxy_cli.cli.udt_mod.create_run_udt",
        return_value={"success": True, "state": "ok", "jobs": []},
    ) as create_run, patch("galaxy_cli.cli._operation_receipt"):
        result = CliRunner().invoke(
            cli,
            [
                "udt", "create-run", "--representation-json", str(representation_path),
                "--history", "history-1", "--inputs", json.dumps(payload),
            ],
        )
    assert result.exit_code == 0, result.output
    assert create_run.call_args.args[3] == payload


def test_input_errors_are_compact_safe_and_pre_submission(tmp_path):
    secret = "do-not-disclose-api-key"
    path = tmp_path / "missing-inputs.json"
    cases = [
        (["tool", "run", "tool-1", "--history", "h", "--inputs", json.dumps([secret])], None, "non_object"),
        (["tool", "run", "tool-1", "--history", "h", "--inputs", "-"], " \n", "empty_input"),
        (
            [
                "tool", "run", "tool-1", "--history", "h",
                "--inputs", json.dumps({"token": secret}),
                "--inputs-json", str(path),
            ],
            None,
            "conflicting_options",
        ),
        (["tool", "validate", "tool-1", "--history", "h"], None, "missing_input"),
    ]
    for args, stdin_text, reason in cases:
        code, text, payload = _run_main(
            ["--api-key", secret, *args], stdin_text=stdin_text
        )
        assert code != 0
        assert payload["success"] is False
        assert payload["error_kind"] == "invalid_input"
        assert payload["reason"] == reason
        assert payload["submission_state"] == "not_submitted"
        assert payload["retry_safe"] is True
        assert secret not in text


@pytest.mark.parametrize(
    ("canonical", "alias", "args", "target", "payload"),
    [
        (("tool", "search"), ("tool", "find"), ["query"], "tool_mod.search_tools", {"command": "tool.search"}),
        (("tool", "template"), ("tool", "inputs"), ["t1"], "tool_mod.tool_template", {"command": "tool.template"}),
        (("tool", "template"), ("tool", "schema"), ["t1"], "tool_mod.tool_template", {"command": "tool.template"}),
        (("dataset", "peek"), ("dataset", "preview"), ["d1"], "dataset_mod.peek_dataset", {"command": "dataset.peek"}),
        (("dataset", "peek"), ("dataset", "head"), ["d1"], "dataset_mod.peek_dataset", {"command": "dataset.peek"}),
        (("history", "list"), ("history", "ls"), [], "history_mod.list_histories", {"command": "history.list"}),
        (("history", "resolve"), ("history", "find"), ["h1", "--exact-name", "x"], "history_mod.resolve_history_content", {"command": "history.resolve"}),
        (("collection", "resolve"), ("collection", "get"), ["c1", "--element", "sample/read"], "collection_mod.resolve_collection_element", {"command": "collection.resolve"}),
        (("job", "diagnose"), ("job", "debug"), ["j1"], "job_mod.diagnose_job", {"command": "job.diagnose"}),
    ],
)
def test_aliases_reuse_canonical_callbacks(canonical, alias, args, target, payload):
    from galaxy_cli.cli import cli

    with patch("galaxy_cli.cli._get_client", return_value=object()), patch(
        f"galaxy_cli.cli.{target}", return_value=payload
    ) as callback:
        canonical_result = CliRunner().invoke(cli, [*canonical, *args])
        alias_result = CliRunner().invoke(cli, [*alias, *args])

    assert canonical_result.exit_code == 0, canonical_result.output
    assert alias_result.exit_code == 0, alias_result.output
    assert json.loads(alias_result.output) == json.loads(canonical_result.output)
    assert callback.call_args_list[0] == callback.call_args_list[1]


def test_aliases_are_discoverable_in_normal_group_help():
    from galaxy_cli.cli import cli
    from galaxy_cli.core.agent_help import GROUP_ALIASES

    runner = CliRunner()
    for group, aliases in GROUP_ALIASES.items():
        result = runner.invoke(cli, [group, "--help"])
        assert result.exit_code == 0
        for alias, canonical in aliases.items():
            assert re.search(
                rf"^  {re.escape(alias)}\s+Alias for "
                rf"'{re.escape(canonical)}'\.$",
                result.output,
                re.MULTILINE,
            )


def test_structured_help_is_bounded_stable_and_canonical():
    from galaxy_cli.cli import cli
    from galaxy_cli.core.agent_help import COMMAND_ALIASES, COMMAND_HELP

    runner = CliRunner()
    with patch("galaxy_cli.cli._get_client") as get_client:
        for command in COMMAND_HELP:
            result = runner.invoke(cli, ["--human", "help", command, "--json"])
            assert result.exit_code == 0, result.output
            assert len(result.output) < 4096
            payload = json.loads(result.output)
            assert payload["schema_version"] == "1.0"
            assert payload["command"] == command
            assert {"usage", "required", "defaults", "safety"} <= payload.keys()
        for alias, canonical in COMMAND_ALIASES.items():
            result = runner.invoke(cli, ["help", alias, "--json"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["command"] == canonical
    get_client.assert_not_called()


def test_usage_errors_add_guidance_without_executing_a_guess():
    with patch("galaxy_cli.cli.tool_mod.run_tool") as run_tool:
        code, _, unknown = _run_main(
            ["tool", "run", "tool-1", "--histroy", "history-1"]
        )
    assert code != 0
    assert unknown["error"] is True
    assert unknown["category"] == "usage_error"
    assert unknown["error_kind"] == "unknown_option"
    assert unknown["command"] == "tool.run"
    assert unknown["did_you_mean"][:1] == ["--history"]
    assert len(unknown["did_you_mean"]) <= 3
    run_tool.assert_not_called()

    code, _, missing = _run_main(["tool", "run"])
    assert code != 0
    assert missing["error_kind"] == "missing_parameter"
    assert missing["path"] == "$.tool_id"
    assert missing["usage"] == "Usage: galaxy-cli tool run [OPTIONS] TOOL_ID"
