"""CLI behavior tests."""

import json
import sys
from unittest.mock import patch

from click.testing import CliRunner
import pytest


class TestCli:
    def test_json_mode_does_not_leak_between_invocations(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        config = {"url": "https://galaxy.example.org", "api_key": "***...6789"}

        with patch("galaxy_cli.cli.config_mod.show_config", return_value=config):
            json_result = runner.invoke(cli, ["--json", "config", "show"])
            text_result = runner.invoke(cli, ["config", "show"])

        assert json_result.exit_code == 0
        assert '"url": "https://galaxy.example.org"' in json_result.output
        assert text_result.exit_code == 0
        assert text_result.output == "URL: https://galaxy.example.org\nAPI Key: ***...6789\n"

    def test_repl_args_allow_overriding_default_json_mode(self):
        from galaxy_cli.cli import _normalize_repl_args

        assert _normalize_repl_args(["history", "list"], True) == ["--json", "history", "list"]
        assert _normalize_repl_args(["--no-json", "history", "list"], True) == ["--no-json", "history", "list"]
        assert _normalize_repl_args(["--json", "history", "list"], False) == ["--json", "history", "list"]

    def test_tool_run_wait_keeps_stdout_json_clean(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        run_result = {
            "tool_id": "fastp",
            "jobs": [{"id": "job-1"}],
            "outputs": [{"name": "trimmed.fastq.gz"}],
        }
        wait_result = {"id": "job-1", "state": "ok", "waited_seconds": 5}

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result), \
             patch("galaxy_cli.cli.job_mod.wait_for_job", return_value=wait_result), \
             patch("galaxy_cli.cli.session_mod.track_job"):
            result = runner.invoke(
                cli,
                ["--json", "tool", "run", "fastp", "--wait"],
            )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["tool_id"] == "fastp"
        assert data["wait_result"]["state"] == "ok"
        assert result.stderr == "Waiting for job job-1...\n"

    def test_invocation_wait_keeps_stdout_json_clean(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        wait_result = {"id": "inv-1", "state": "scheduled", "waited_seconds": 12}

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli.invocation_mod.wait_for_invocation", return_value=wait_result):
            result = runner.invoke(
                cli,
                ["--json", "invocation", "wait", "inv-1"],
            )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["id"] == "inv-1"
        assert data["state"] == "scheduled"
        assert result.stderr == "Waiting for invocation inv-1...\n"

    def test_main_returns_structured_json_for_collection_usage_errors(self):
        from galaxy_cli.cli import EXIT_USER_ERROR, main

        runner = CliRunner()
        with runner.isolated_filesystem(), \
             patch.object(
                 sys,
                 "argv",
                 [
                     "galaxy-cli",
                     "--json",
                     "collection",
                     "create",
                     "pairs",
                     "--collection-type",
                     "list:paired",
                     "-p",
                     "bad",
                 ],
             ), \
             patch("click.echo") as mock_echo, \
             patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == EXIT_USER_ERROR
        payload = json.loads(mock_echo.call_args.args[0])
        assert payload["error"] is True
        assert payload["category"] == "usage_error"
        assert "Invalid pair format" in payload["message"]
