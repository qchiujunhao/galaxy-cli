"""CLI behavior tests."""

from unittest.mock import patch

from click.testing import CliRunner


class TestCli:
    def test_json_mode_does_not_leak_between_invocations(self):
        from cli_anything.galaxy.galaxy_cli import cli

        runner = CliRunner()
        config = {"url": "https://galaxy.example.org", "api_key": "***...6789"}

        with patch("cli_anything.galaxy.galaxy_cli.config_mod.show_config", return_value=config):
            json_result = runner.invoke(cli, ["--json", "config", "show"])
            text_result = runner.invoke(cli, ["config", "show"])

        assert json_result.exit_code == 0
        assert '"url": "https://galaxy.example.org"' in json_result.output
        assert text_result.exit_code == 0
        assert text_result.output == "URL: https://galaxy.example.org\nAPI Key: ***...6789\n"

    def test_repl_args_allow_overriding_default_json_mode(self):
        from cli_anything.galaxy.galaxy_cli import _normalize_repl_args

        assert _normalize_repl_args(["history", "list"], True) == ["--json", "history", "list"]
        assert _normalize_repl_args(["--no-json", "history", "list"], True) == ["--no-json", "history", "list"]
        assert _normalize_repl_args(["--json", "history", "list"], False) == ["--json", "history", "list"]
