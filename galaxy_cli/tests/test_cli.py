"""CLI behavior tests."""

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
import pytest


def _cli_runner(separate_stderr=False):
    kwargs = {}
    if separate_stderr and "mix_stderr" in inspect.signature(CliRunner).parameters:
        kwargs["mix_stderr"] = False
    return CliRunner(**kwargs)


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

    def test_get_client_passes_request_timeout_when_configured(self):
        from galaxy_cli.cli import _get_client

        ctx = SimpleNamespace(obj={
            "url": "https://galaxy.example.org",
            "api_key": "abc123",
            "profile": None,
            "request_timeout": 120,
        })

        with patch("galaxy_cli.cli.GalaxyClient", return_value="client") as client_cls:
            client = _get_client(ctx)

        assert client == "client"
        client_cls.assert_called_once_with(
            url="https://galaxy.example.org",
            api_key="abc123",
            profile=None,
            request_timeout=120,
        )

    def test_dataset_upload_timeout_also_sets_upload_timeout(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        uploaded = {"id": "d1", "name": "matrix.tsv", "state": "ok"}

        with patch("galaxy_cli.cli._get_client", return_value=object()) as get_client, \
             patch("galaxy_cli.cli.dataset_mod.upload_dataset", return_value=uploaded) as upload, \
             patch("galaxy_cli.cli.session_mod.track_dataset"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "dataset",
                    "upload",
                    "matrix.tsv",
                    "--history-id",
                    "hist-1",
                    "--timeout",
                    "7200",
                ],
            )

        assert result.exit_code == 0, result.output
        get_client.assert_called_once()
        assert json.loads(result.output)["id"] == "d1"
        assert upload.call_args.kwargs["timeout"] == 7200
        assert upload.call_args.kwargs["upload_timeout"] == 7200

    def test_dataset_upload_explicit_upload_timeout_wins(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        uploaded = {"id": "d1", "name": "matrix.tsv", "state": "ok"}

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli.dataset_mod.upload_dataset", return_value=uploaded) as upload, \
             patch("galaxy_cli.cli.session_mod.track_dataset"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "dataset",
                    "upload",
                    "matrix.tsv",
                    "--history-id",
                    "hist-1",
                    "--timeout",
                    "60",
                    "--upload-timeout",
                    "7200",
                ],
            )

        assert result.exit_code == 0, result.output
        assert upload.call_args.kwargs["timeout"] == 60
        assert upload.call_args.kwargs["upload_timeout"] == 7200.0

    def test_dataset_peek_accepts_history_id_and_compaction_options(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        preview = {"id": "d1", "lines": ["a\tb"], "rows": [], "total_shown": 1}

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli.dataset_mod.peek_dataset", return_value=preview) as peek:
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "dataset",
                    "peek",
                    "d1",
                    "--history-id",
                    "hist-1",
                    "--lines",
                    "1",
                    "--max-fields",
                    "2",
                    "--max-chars-per-line",
                    "80",
                    "--delimiter",
                    "tab",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["lines"] == ["a\tb"]
        assert peek.call_args.kwargs == {
            "lines": 1,
            "history_id": "hist-1",
            "max_chars_per_line": 80,
            "max_fields": 2,
            "delimiter": "tab",
        }

    def test_dataset_download_accepts_history_id(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        downloaded = {"output": "out.tsv", "size": 10}
        client = object()

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli.dataset_mod.download_dataset", return_value=downloaded) as download:
            result = runner.invoke(
                cli,
                ["--json", "dataset", "download", "d1", "out.tsv", "--history-id", "hist-1"],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["output"] == "out.tsv"
        download.assert_called_once_with(client, "d1", "out.tsv", history_id="hist-1")

    def test_tool_search_passes_limit_cache_and_resolve_options(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        matches = [{"id": "tool_a", "name": "Tool A", "version": "1.0", "description": ""}]

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli.tool_mod.search_tools", return_value=matches) as search:
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "tool",
                    "search",
                    "tool",
                    "--limit",
                    "3",
                    "--resolve",
                    "--cache",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)[0]["id"] == "tool_a"
        assert search.call_args.kwargs == {
            "limit": 3,
            "resolve": True,
            "use_cache": True,
            "refresh_cache": False,
        }

    def test_user_whoami_redacts_email_by_default(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        client = SimpleNamespace(
            whoami=lambda: {
                "id": "u1",
                "username": "test@example.org",
                "email": "test@example.org",
                "is_admin": False,
                "total_disk_usage": 0,
                "nice_total_disk_usage": "0 bytes",
            }
        )

        with patch("galaxy_cli.cli._get_client", return_value=client):
            result = runner.invoke(cli, ["--json", "user", "whoami"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["username"] == "t***@example.org"
        assert "email" not in data
        assert data["email_redacted"] is True

    def test_user_whoami_show_email_is_explicit(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        client = SimpleNamespace(
            whoami=lambda: {
                "id": "u1",
                "username": "testuser",
                "email": "test@example.org",
                "is_admin": False,
                "total_disk_usage": 0,
                "nice_total_disk_usage": "0 bytes",
            }
        )

        with patch("galaxy_cli.cli._get_client", return_value=client):
            result = runner.invoke(cli, ["--json", "user", "whoami", "--show-email"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["username"] == "testuser"
        assert data["email"] == "test@example.org"

    def test_skill_path_outputs_packaged_path(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()

        result = runner.invoke(cli, ["--json", "skill", "path"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "galaxy-cli"
        assert data["exists"] is True
        assert data["path"].endswith("SKILL.md")

    def test_skill_show_human_outputs_markdown(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()

        result = runner.invoke(cli, ["--human", "skill", "show"])

        assert result.exit_code == 0, result.output
        assert result.output.startswith("---")
        assert "Use this skill when the task requires Galaxy operations" in result.output

    def test_skill_install_to_target_dir(self, tmp_path):
        from galaxy_cli.cli import cli

        runner = CliRunner()

        result = runner.invoke(
            cli,
            [
                "--json",
                "skill",
                "install",
                "--agent",
                "claude",
                "--target-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        destination = tmp_path / "galaxy-cli" / "SKILL.md"
        assert data["agent"] == "claude"
        assert data["status"] == "installed"
        assert data["destination"] == str(destination)
        assert destination.exists()

    def test_tool_run_wait_keeps_stdout_json_clean(self):
        from galaxy_cli.cli import cli

        runner = _cli_runner(separate_stderr=True)
        run_result = {
            "success": True,
            "state": "ok",
            "execution_backend": "strict",
            "history_id": "hist-1",
            "tool_id": "fastp",
            "tool_version": "1.0",
            "jobs": [{"id": "job-1", "state": "ok", "exit_code": 0}],
            "wait_results": [{"id": "job-1", "state": "ok", "exit_code": 0}],
            "wait_result": {"id": "job-1", "state": "ok", "exit_code": 0},
            "outputs": [
                {
                    "output_name": "trimmed",
                    "name": "trimmed.fastq.gz",
                    "id": "dataset-1",
                    "src": "hda",
                    "state": "ok",
                    "extension": "fastqsanger.gz",
                    "file_size": 10,
                }
            ],
        }
        client = object()

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result) as run_tool, \
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
        assert result.stderr == "Waiting for all jobs from tool fastp...\n"
        run_tool.assert_called_once_with(
            client,
            "fastp",
            "hist-1",
            inputs={},
            execution_backend="auto",
            wait=True,
            timeout=1800,
            poll_interval=180,
            plan=None,
        )

    def test_tool_run_no_wait_skips_wait_and_refresh(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        run_result = {
            "success": True,
            "state": "submitted",
            "execution_backend": "strict",
            "history_id": "hist-1",
            "tool_id": "fastp",
            "jobs": [{"id": "job-1"}],
            "outputs": [{"name": "trimmed.fastq.gz"}],
        }

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result) as run_tool, \
             patch("galaxy_cli.cli.session_mod.track_job"):
            result = runner.invoke(
                cli,
                ["--json", "tool", "run", "fastp", "--no-wait"],
            )

        assert result.exit_code == 0
        assert "wait_result" not in json.loads(result.stdout)
        assert run_tool.call_args.kwargs["wait"] is False

    def test_tool_run_peeks_only_named_dataset_output(self):
        from galaxy_cli.cli import cli

        runner = _cli_runner(separate_stderr=True)
        client = object()
        run_result = {
            "success": True,
            "state": "ok",
            "execution_backend": "strict",
            "history_id": "hist-1",
            "tool_id": "reporter",
            "tool_version": "1.0",
            "jobs": [{"id": "job-1", "state": "ok", "exit_code": 0}],
            "outputs": [
                {"output_name": "report", "id": "dataset-1", "src": "hda", "state": "ok"},
                {"output_name": "other", "id": "dataset-2", "src": "hda", "state": "ok"},
            ],
        }
        preview = {"id": "dataset-1", "lines": ["one", "two"], "total_shown": 2}

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result), \
             patch("galaxy_cli.cli.dataset_mod.peek_dataset", return_value=preview) as peek, \
             patch("galaxy_cli.cli.session_mod.track_job"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "tool",
                    "run",
                    "reporter",
                    "--history-id",
                    "hist-1",
                    "--peek-output",
                    "report",
                    "--peek-lines",
                    "2",
                ],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["output_peek"][0]["preview"]["lines"] == ["one", "two"]
        peek.assert_called_once_with(
            client,
            "dataset-1",
            lines=2,
            history_id="hist-1",
        )

    def test_tool_run_collection_peek_is_explicitly_unsupported(self):
        from galaxy_cli.cli import cli

        runner = _cli_runner(separate_stderr=True)
        run_result = {
            "success": True,
            "state": "ok",
            "execution_backend": "strict",
            "history_id": "hist-1",
            "tool_id": "mapper",
            "tool_version": "1.0",
            "jobs": [{"id": "job-1", "state": "ok", "exit_code": 0}],
            "outputs": [
                {
                    "output_name": "mapped_output",
                    "id": "dataset-1",
                    "src": "hda",
                    "state": "ok",
                },
                {
                    "output_name": "mapped_output",
                    "id": "collection-1",
                    "src": "hdca",
                    "state": "ok",
                    "collection_type": "list",
                    "element_count": 2,
                }
            ],
        }

        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result), \
             patch("galaxy_cli.cli.dataset_mod.peek_dataset") as peek, \
             patch("galaxy_cli.cli.session_mod.track_job"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "tool",
                    "run",
                    "mapper",
                    "--history-id",
                    "hist-1",
                    "--peek-output",
                    "mapped_output",
                ],
            )

        assert result.exit_code == 0, result.output
        item = json.loads(result.stdout)["output_peek"][0]
        assert item["supported"] is False
        assert "unsupported" in item["reason"].lower()
        peek.assert_not_called()

    def test_tool_run_rejects_peek_without_wait(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        with patch("galaxy_cli.cli._get_client", return_value=object()), \
             patch("galaxy_cli.cli.tool_mod.run_tool") as run_tool:
            result = runner.invoke(
                cli,
                [
                    "tool",
                    "run",
                    "reporter",
                    "--history-id",
                    "hist-1",
                    "--no-wait",
                    "--peek-output",
                    "report",
                ],
            )

        assert result.exit_code != 0
        assert "--peek-output requires" in result.output
        run_tool.assert_not_called()

    def test_tool_run_accepts_multiqc_style_inputs_json(self, tmp_path):
        from galaxy_cli.cli import cli

        runner = _cli_runner(separate_stderr=True)
        inputs_path = tmp_path / "multiqc_inputs.json"
        inputs_payload = {
            "results": [
                {
                    "software_cond": {
                        "software": "fastqc",
                        "output": [
                            {
                                "type": "data",
                                "input": [
                                    {"src": "hda", "id": "f9cad7b01a4721358dba0ff950c535fa"},
                                    {"src": "hda", "id": "a6b7c8d9e0f112233445566778899abc"},
                                ],
                            }
                        ],
                    }
                },
            ]
        }
        inputs_path.write_text(json.dumps(inputs_payload))
        run_result = {
            "success": True,
            "state": "ok",
            "execution_backend": "strict",
            "history_id": "hist-1",
            "tool_id": "multiqc",
            "tool_version": "1.33",
            "jobs": [{"id": "job-1", "state": "ok", "exit_code": 0}],
            "outputs": [
                {"id": "html-1", "name": "MultiQC report", "extension": "html"},
                {"id": "raw-1", "name": "MultiQC data", "extension": "zip"},
            ],
        }
        client = object()

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result) as run_tool, \
             patch("galaxy_cli.cli.session_mod.track_job"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "tool",
                    "run",
                    "multiqc",
                    "--history-id",
                    "hist-1",
                    "--inputs-json",
                    str(inputs_path),
                ],
            )

        assert result.exit_code == 0, result.output
        run_tool.assert_called_once_with(
            client,
            "multiqc",
            "hist-1",
            inputs=inputs_payload,
            execution_backend="auto",
            wait=True,
            timeout=1800,
            poll_interval=180,
            plan=None,
        )
        data = json.loads(result.stdout)
        assert data["jobs"][0]["id"] == "job-1"
        assert [output["id"] for output in data["outputs"]] == ["html-1", "raw-1"]

    def test_tool_run_dry_run_payload_outputs_backend_and_post_body(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        client = object()
        payload = {
            "tool_id": "hisat2",
            "tool_version": "2.2.2",
            "history_id": "hist-1",
            "inputs": {"library": {"input_1": {"src": "hda", "id": "dataset-1"}}},
            "strict": True,
            "send_email_notification": False,
        }
        plan = {
            "requested_execution_backend": "auto",
            "execution_backend": "strict",
            "endpoint": "/api/jobs",
            "post_body": payload,
        }

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.tool_mod.build_tool_execution_plan", return_value=plan) as build, \
             patch("galaxy_cli.cli.tool_mod.run_tool") as run_tool:
            result = runner.invoke(
                cli,
                ["--json", "tool", "run", "hisat2", "--dry-run-payload"],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == plan
        build.assert_called_once_with(
            client,
            "hisat2",
            "hist-1",
            inputs={},
            execution_backend="auto",
        )
        run_tool.assert_not_called()

    def test_tool_run_save_payload_writes_post_body_and_submits(self, tmp_path):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        client = object()
        payload_path = tmp_path / "payload.json"
        payload = {
            "tool_id": "hisat2",
            "tool_version": "2.2.2",
            "history_id": "hist-1",
            "inputs": {"library": {"input_1": {"src": "hda", "id": "dataset-1"}}},
            "strict": True,
            "send_email_notification": False,
        }
        plan = {
            "requested_execution_backend": "auto",
            "execution_backend": "strict",
            "endpoint": "/api/jobs",
            "post_body": payload,
        }
        run_result = {
            "success": True,
            "state": "submitted",
            "execution_backend": "strict",
            "tool_id": "hisat2",
            "history_id": "hist-1",
            "jobs": [],
            "outputs": [],
        }

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli._require_history", return_value="hist-1"), \
             patch("galaxy_cli.cli.tool_mod.build_tool_execution_plan", return_value=plan), \
             patch("galaxy_cli.cli.tool_mod.run_tool", return_value=run_result) as run_tool:
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "tool",
                    "run",
                    "hisat2",
                    "--save-payload",
                    str(payload_path),
                    "--no-wait",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(payload_path.read_text()) == payload
        data = json.loads(result.output)
        assert data["saved_payload"] == str(payload_path)
        run_tool.assert_called_once_with(
            client,
            "hisat2",
            "hist-1",
            inputs={},
            execution_backend="auto",
            wait=False,
            timeout=1800,
            poll_interval=180,
            plan=plan,
        )

    def test_workflow_run_dry_run_payload_outputs_post_body(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        client = object()
        payload = {
            "workflow_id": "wf-1",
            "history_id": "hist-1",
            "ds_map": {"0": {"src": "hda", "id": "dataset-1"}},
        }

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli.workflow_mod.build_workflow_payload", return_value=payload) as build, \
             patch("galaxy_cli.cli.workflow_mod.run_workflow") as run_workflow:
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "workflow",
                    "run",
                    "wf-1",
                    "--history-id",
                    "hist-1",
                    "-i",
                    "0=dataset-1",
                    "--dry-run-payload",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == payload
        build.assert_called_once_with(
            client,
            "wf-1",
            history_id="hist-1",
            inputs={"0": "dataset-1"},
            new_history_name=None,
        )
        run_workflow.assert_not_called()

    def test_workflow_run_save_payload_writes_post_body_and_submits(self, tmp_path):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        client = object()
        payload_path = tmp_path / "workflow-payload.json"
        payload = {
            "workflow_id": "wf-1",
            "history_id": "hist-1",
            "ds_map": {"0": {"src": "hda", "id": "dataset-1"}},
        }
        run_result = {
            "id": "inv-1",
            "workflow_id": "wf-1",
            "history_id": "hist-1",
            "state": "new",
            "status": "invoked",
        }

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli.workflow_mod.build_workflow_payload", return_value=payload), \
             patch("galaxy_cli.cli.workflow_mod.run_workflow", return_value=run_result) as run_workflow:
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "workflow",
                    "run",
                    "wf-1",
                    "--history-id",
                    "hist-1",
                    "--save-payload",
                    str(payload_path),
                    "--no-wait",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(payload_path.read_text()) == payload
        data = json.loads(result.output)
        assert data["saved_payload"] == str(payload_path)
        run_workflow.assert_called_once_with(
            client,
            "wf-1",
            history_id="hist-1",
            inputs=None,
            new_history_name=None,
            payload=payload,
        )

    def test_history_update_published_importable_cli(self):
        from galaxy_cli.cli import cli

        runner = CliRunner()
        result_payload = {
            "id": "hist-1",
            "updated": ["published", "importable"],
            "name": "",
            "published": True,
            "importable": True,
        }
        client = object()

        with patch("galaxy_cli.cli._get_client", return_value=client), \
             patch("galaxy_cli.cli.history_mod.update_history", return_value=result_payload) as update:
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "history",
                    "update",
                    "hist-1",
                    "--published",
                    "true",
                    "--importable",
                    "true",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["published"] is True
        update.assert_called_once_with(
            client,
            "hist-1",
            name=None,
            annotation=None,
            tags=None,
            published=True,
            importable=True,
        )

    def test_invocation_wait_keeps_stdout_json_clean(self):
        from galaxy_cli.cli import cli

        runner = _cli_runner(separate_stderr=True)
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

    def test_main_backend_error_is_compact_and_redacts_explicit_api_key(self):
        from galaxy_cli.cli import main
        from galaxy_cli.utils.galaxy_backend import (
            EXIT_TIMEOUT,
            GalaxyBackendError,
        )

        secret = "cli-secret-api-key"
        error = GalaxyBackendError(
            f"request containing {secret} timed out",
            category="timeout",
            error_kind="job_timeout",
            exit_code=EXIT_TIMEOUT,
            submission_state="submitted",
            retry_safe=False,
            details={"job_ids": ["job-1"]},
        )
        with patch.object(
            sys,
            "argv",
            [
                "galaxy-cli",
                "--url",
                "https://galaxy.example.org",
                "--api-key",
                secret,
                "job",
                "wait",
                "job-1",
            ],
        ), patch("galaxy_cli.cli._get_client", return_value=object()), patch(
            "galaxy_cli.cli.job_mod.wait_for_job", side_effect=error
        ), patch("click.echo") as echo, pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == EXIT_TIMEOUT
        rendered = [str(item.args[0]) for item in echo.call_args_list]
        assert secret not in "\n".join(rendered)
        payload_text = rendered[-1]
        payload = json.loads(payload_text)
        assert payload_text == json.dumps(payload, separators=(",", ":"))
        assert payload["message"] == "request containing [REDACTED] timed out"
        assert payload["retry_safe"] is False

    def test_main_recursively_redacts_effective_profile_key_from_error_details(self):
        from galaxy_cli.cli import main
        from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

        secret = "profile-secret-api-key"
        client = SimpleNamespace(api_key=secret)
        error = GalaxyBackendError(
            "job status unavailable",
            category="server_error",
            error_kind="job_status_unavailable",
            submission_state="submitted",
            retry_safe=False,
            details={
                "job_ids": ["job-1"],
                "diagnostic": {"authorization": f"Bearer {secret}"},
            },
        )
        with patch.object(
            sys,
            "argv",
            ["galaxy-cli", "--profile", "main", "job", "wait", "job-1"],
        ), patch("galaxy_cli.cli.GalaxyClient", return_value=client), patch(
            "galaxy_cli.cli.job_mod.wait_for_job", side_effect=error
        ), patch("click.echo") as echo, pytest.raises(SystemExit):
            main()

        rendered = "\n".join(str(item.args[0]) for item in echo.call_args_list)
        assert secret not in rendered
        payload = json.loads(str(echo.call_args.args[0]))
        assert payload["diagnostic"]["authorization"] == "Bearer [REDACTED]"

    @pytest.mark.parametrize("output_mode", ["--json", "--human"])
    def test_success_and_progress_output_redact_effective_api_key(self, output_mode):
        from galaxy_cli.cli import cli

        secret = "success-secret-api-key"
        client = SimpleNamespace(api_key=secret)
        result_payload = {
            "success": True,
            "state": "ok",
            "execution_backend": "strict",
            "history_id": "history-1",
            "tool_id": secret,
            "tool_version": "1.0",
            "jobs": [],
            "outputs": [
                {
                    "output_name": "report",
                    "id": "dataset-1",
                    "src": "hda",
                    "name": f"report-{secret}",
                    "state": "ok",
                    "extension": "txt",
                    "file_size": 1,
                }
            ],
        }

        with patch("galaxy_cli.cli.GalaxyClient", return_value=client), patch(
            "galaxy_cli.cli.tool_mod.run_tool", return_value=result_payload
        ):
            result = _cli_runner(separate_stderr=True).invoke(
                cli,
                [
                    output_mode,
                    "--url",
                    "https://galaxy.example.org",
                    "--api-key",
                    secret,
                    "tool",
                    "run",
                    secret,
                    "--history-id",
                    "history-1",
                ],
            )

        assert result.exit_code == 0
        assert secret not in result.stdout
        assert secret not in result.stderr
        assert "[REDACTED]" in result.stdout
        assert "[REDACTED]" in result.stderr
        if output_mode == "--json":
            payload = json.loads(result.stdout)
            assert payload["tool_id"] == "[REDACTED]"
            assert payload["outputs"][0]["name"] == "report-[REDACTED]"

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
