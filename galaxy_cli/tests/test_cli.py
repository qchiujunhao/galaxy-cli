"""CLI behavior tests."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
import pytest


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect the CLI config file to a temp location for each test."""
    config_dir = tmp_path / ".galaxy-cli"
    config_file = config_dir / "config.json"
    monkeypatch.setattr("galaxy_cli.utils.galaxy_backend.DEFAULT_CONFIG_DIR", config_dir)
    monkeypatch.setattr("galaxy_cli.utils.galaxy_backend.DEFAULT_CONFIG_FILE", config_file)
    # Make sure env vars don't leak in
    monkeypatch.delenv("GALAXY_URL", raising=False)
    monkeypatch.delenv("GALAXY_API_KEY", raising=False)
    return config_file


class TestCli:
    def test_json_mode_does_not_leak_between_invocations(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        config = {
            "url": "https://galaxy.example.org",
            "url_source": "env",
            "api_key": "***...6789",
            "api_key_source": "env",
            "active_profile": None,
            "profiles": [],
        }

        with patch("galaxy_cli.cli.config_mod.show_config", return_value=config):
            json_result = runner.invoke(cli, ["--json", "config", "show"])
            text_result = runner.invoke(cli, ["--human", "config", "show"])

        assert json_result.exit_code == 0
        data = json.loads(json_result.output)
        assert data["url"] == "https://galaxy.example.org"
        assert data["api_key"] == "***...6789"
        assert text_result.exit_code == 0
        assert "URL: https://galaxy.example.org" in text_result.output
        assert "API Key: ***...6789" in text_result.output

    def test_json_flag_forces_json_output(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        config = {
            "url": "https://galaxy.example.org",
            "url_source": "env",
            "api_key": "***...6789",
            "api_key_source": "env",
            "active_profile": None,
            "profiles": [],
        }

        with patch("galaxy_cli.cli.config_mod.show_config", return_value=config):
            result = runner.invoke(cli, ["--json", "config", "show"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["url"] == "https://galaxy.example.org"
        assert data["api_key"] == "***...6789"

    def test_human_flag_forces_human_output(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        config = {
            "url": "https://galaxy.example.org",
            "url_source": "env",
            "api_key": "***...6789",
            "api_key_source": "env",
            "active_profile": None,
            "profiles": [],
        }

        with patch("galaxy_cli.cli.config_mod.show_config", return_value=config):
            result = runner.invoke(cli, ["--human", "config", "show"])

        assert result.exit_code == 0
        assert "URL: https://galaxy.example.org" in result.output
        assert "API Key: ***...6789" in result.output

    def test_default_output_is_json(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        config = {
            "url": "https://galaxy.example.org",
            "url_source": "env",
            "api_key": "***...6789",
            "api_key_source": "env",
            "active_profile": None,
            "profiles": [],
        }

        with patch("galaxy_cli.cli.config_mod.show_config", return_value=config):
            result = runner.invoke(cli, ["config", "show"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["url"] == "https://galaxy.example.org"
        assert payload["api_key"] == "***...6789"

    def test_repl_args_allow_overriding_default_json_mode(self):
        from galaxy_cli.cli import _normalize_repl_args

        assert _normalize_repl_args(["history", "list"], True) == ["history", "list"]
        assert _normalize_repl_args(["history", "list"], False) == ["--human", "history", "list"]
        assert _normalize_repl_args(["--human", "history", "list"], False) == ["--human", "history", "list"]
        assert _normalize_repl_args(["--json", "history", "list"], False) == ["--json", "history", "list"]

    def test_json_mode_from_argv_only_considers_root_flags(self):
        from galaxy_cli.cli import _json_mode_from_argv

        assert _json_mode_from_argv(["--json", "tool", "show", "fastqc"]) is True
        assert _json_mode_from_argv(["--profile", "main", "--human", "tool", "show", "fastqc"]) is False
        assert _json_mode_from_argv(["tool", "show", "--json", "fastqc"]) is None
        assert _json_mode_from_argv(["tool", "run", "cutadapt", "-i", "adapter=--json"]) is None

    def test_get_client_caches_lazy_client_on_context(self):
        from galaxy_cli.cli import _get_client

        ctx = SimpleNamespace(obj={"url": "https://galaxy.example.org", "api_key": "abc123", "profile": None})

        with patch("galaxy_cli.cli.GalaxyClient", side_effect=["client-1", "client-2"]) as client_cls:
            first = _get_client(ctx)
            second = _get_client(ctx)

        assert first == "client-1"
        assert second == "client-1"
        assert ctx.obj["client"] == "client-1"
        client_cls.assert_called_once_with(url="https://galaxy.example.org", api_key="abc123", profile=None)

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
             patch("galaxy_cli.cli.tool_mod.refresh_output_details", return_value=[
                 {"name": "trimmed.fastq.gz", "state": "ok", "extension": "fastqsanger.gz"},
             ]), \
             patch("galaxy_cli.cli.job_mod.wait_for_job", return_value=wait_result), \
             patch("galaxy_cli.cli.session_mod.track_job"):
            result = runner.invoke(
                cli,
                ["--json", "tool", "run", "fastp"],
            )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["tool_id"] == "fastp"
        assert data["jobs"][0]["state"] == "ok"
        assert data["wait_result"]["state"] == "ok"
        assert data["outputs"][0]["state"] == "ok"
        assert result.stderr == "Waiting for job job-1...\n"

    def test_tool_run_no_wait_skips_wait_and_refresh(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        run_result = {
            "tool_id": "fastp",
            "jobs": [{"id": "job-1"}],
            "outputs": [{"name": "trimmed.fastq.gz"}],
        }

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result), \
             patch("galaxy_cli.cli.tool_mod.refresh_output_details") as refresh, \
             patch("galaxy_cli.cli.job_mod.wait_for_job") as wait, \
             patch("galaxy_cli.cli.session_mod.track_job"):
            result = runner.invoke(
                cli,
                ["--json", "tool", "run", "fastp", "--no-wait"],
            )

        assert result.exit_code == 0
        assert "wait_result" not in json.loads(result.stdout)
        wait.assert_not_called()
        refresh.assert_not_called()

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

    def test_profile_add_list_use_roundtrip(self, isolated_config):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        # Add two profiles; first becomes active implicitly.
        r1 = runner.invoke(cli, [
            "--json", "profile", "add", "main",
            "--url", "https://usegalaxy.org", "--api-key", "mainkey1234",
        ])
        assert r1.exit_code == 0, r1.output
        assert json.loads(r1.output)["active"] is True

        r2 = runner.invoke(cli, [
            "--json", "profile", "add", "eu",
            "--url", "https://usegalaxy.eu", "--api-key", "eukey5678",
        ])
        assert r2.exit_code == 0
        # Second add should not flip the active profile unless --use passed.
        assert json.loads(r2.output)["active"] is False

        r3 = runner.invoke(cli, ["--json", "profile", "list"])
        assert r3.exit_code == 0
        profiles = json.loads(r3.output)
        names = sorted(p["name"] for p in profiles)
        assert names == ["eu", "main"]
        active = {p["name"]: p["active"] for p in profiles}
        assert active == {"main": True, "eu": False}
        # API keys must be masked.
        for p in profiles:
            assert "***" in p["api_key"]

        r4 = runner.invoke(cli, ["--json", "profile", "use", "eu"])
        assert r4.exit_code == 0
        assert json.loads(r4.output)["active_profile"] == "eu"

    def test_profile_remove_clears_active(self, isolated_config):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        runner.invoke(cli, [
            "--json", "profile", "add", "main",
            "--url", "https://usegalaxy.org", "--api-key", "k1234",
        ])
        r = runner.invoke(cli, ["--json", "profile", "remove", "main"])
        assert r.exit_code == 0

        r2 = runner.invoke(cli, ["--json", "profile", "list"])
        assert json.loads(r2.output) == []

    def test_profile_use_rejects_missing_name(self, isolated_config):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        r = runner.invoke(cli, ["--json", "profile", "use", "nonexistent"])
        assert r.exit_code != 0
        payload = json.loads(r.output)
        assert payload["error"] is True

    def test_resolution_env_beats_profile(self, isolated_config, monkeypatch):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient

        GalaxyClient.save_profile("main", "https://from-profile", "profilekey", use=True)
        monkeypatch.setenv("GALAXY_URL", "https://from-env")
        monkeypatch.setenv("GALAXY_API_KEY", "envkey")

        c = GalaxyClient()
        assert c.url == "https://from-env/"
        assert c.api_key == "envkey"

    def test_resolution_profile_selected_by_flag(self, isolated_config):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient

        GalaxyClient.save_profile("main", "https://main", "mainkey", use=True)
        GalaxyClient.save_profile("eu", "https://eu", "eukey")
        # No env → --profile eu should pick the eu profile.
        c = GalaxyClient(profile="eu")
        assert c.url == "https://eu/"
        assert c.api_key == "eukey"

    def test_resolution_falls_back_to_active_profile(self, isolated_config):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient

        GalaxyClient.save_profile("main", "https://main", "mainkey", use=True)
        c = GalaxyClient()
        assert c.url == "https://main/"
        assert c.api_key == "mainkey"

    def test_resolution_legacy_top_level_still_works(self, isolated_config):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient

        GalaxyClient.save_config("url", "https://legacy")
        GalaxyClient.save_config("api_key", "legacykey")
        c = GalaxyClient()
        assert c.url == "https://legacy/"
        assert c.api_key == "legacykey"

    def test_config_file_permissions_are_user_only(self, isolated_config):
        import stat as stat_mod
        from galaxy_cli.utils.galaxy_backend import GalaxyClient, DEFAULT_CONFIG_FILE

        GalaxyClient.save_profile("main", "https://x", "keykey", use=True)
        mode = DEFAULT_CONFIG_FILE.stat().st_mode & 0o777
        assert mode == (stat_mod.S_IRUSR | stat_mod.S_IWUSR)

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

    def test_main_returns_structured_json_for_invalid_paired_collection_elements(self):
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
                     "pair",
                     "--collection-type",
                     "paired",
                     "-e",
                     "left=d1",
                     "-e",
                     "right=d2",
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
        assert "forward and one reverse element" in payload["message"]

    def test_json_collection_create_requests_resolved_elements(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        created = {
            "id": "hdca456",
            "name": "pair",
            "collection_type": "paired",
            "element_count": 2,
            "history_id": "hist-1",
            "state": "ok",
            "elements": [{"element_identifier": "forward", "id": "fwd123"}],
        }

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.collection_mod.create_collection", return_value=created) as create:
            result = runner.invoke(cli, [
                "--json",
                "collection",
                "create",
                "pair",
                "--collection-type",
                "paired",
                "-e",
                "forward=fwd123",
                "-e",
                "reverse=rev456",
            ])

        assert result.exit_code == 0
        assert json.loads(result.output)["elements"][0]["id"] == "fwd123"
        assert create.call_args.kwargs["include_elements"] is True

    def test_collection_create_accepts_forward_reverse_flags(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        created = {
            "id": "hdca456",
            "name": "pair",
            "collection_type": "paired",
            "element_count": 2,
            "history_id": "hist-1",
            "state": "ok",
        }

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.collection_mod.create_collection", return_value=created) as create:
            result = runner.invoke(cli, [
                "--json",
                "collection",
                "create",
                "pair",
                "--collection-type",
                "paired",
                "--forward",
                "fwd123",
                "--reverse",
                "rev456",
            ])

        assert result.exit_code == 0
        assert create.call_args.kwargs["element_identifiers"] == [
            {"name": "forward", "id": "fwd123", "src": "hda"},
            {"name": "reverse", "id": "rev456", "src": "hda"},
        ]

    def test_collection_create_rejects_mixing_forward_reverse_with_elements(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "collection",
            "create",
            "pair",
            "--collection-type",
            "paired",
            "--forward",
            "fwd123",
            "--reverse",
            "rev456",
            "-e",
            "forward=fwd123",
            "-e",
            "reverse=rev456",
        ])

        assert result.exit_code != 0
        assert "Use either --forward/--reverse or -e for paired collections, not both" in result.output

    def test_collection_create_rejects_incomplete_forward_reverse_flags(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "collection",
            "create",
            "pair",
            "--collection-type",
            "paired",
            "--forward",
            "fwd123",
        ])

        assert result.exit_code != 0
        assert "Paired collections require both --forward and --reverse" in result.output

    def test_collection_create_rejects_forward_reverse_for_non_paired(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "collection",
            "create",
            "samples",
            "--collection-type",
            "list",
            "--forward",
            "fwd123",
            "--reverse",
            "rev456",
        ])

        assert result.exit_code != 0
        assert "--forward/--reverse only apply to paired collections" in result.output
