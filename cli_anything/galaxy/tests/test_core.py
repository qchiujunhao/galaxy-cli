"""Unit tests for Galaxy CLI core modules.

All tests use mock HTTP responses — no Galaxy server required.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# ── Config Tests ─────────────────────────────────────────────────────────

class TestConfig:
    def test_set_url(self, tmp_path):
        from cli_anything.galaxy.core.config import set_url
        from cli_anything.galaxy.utils import galaxy_backend as gb

        cfg_file = tmp_path / "config.json"
        with patch.object(gb, "DEFAULT_CONFIG_FILE", cfg_file), \
             patch.object(gb, "DEFAULT_CONFIG_DIR", tmp_path):
            result = set_url("https://galaxy.example.org")
            assert result["url"] == "https://galaxy.example.org"
            data = json.loads(cfg_file.read_text())
            assert data["url"] == "https://galaxy.example.org"

    def test_set_key(self, tmp_path):
        from cli_anything.galaxy.core.config import set_key
        from cli_anything.galaxy.utils import galaxy_backend as gb

        cfg_file = tmp_path / "config.json"
        with patch.object(gb, "DEFAULT_CONFIG_FILE", cfg_file), \
             patch.object(gb, "DEFAULT_CONFIG_DIR", tmp_path):
            result = set_key("abc123def456")
            assert result["status"] == "saved"
            data = json.loads(cfg_file.read_text())
            assert data["api_key"] == "abc123def456"

    def test_show_config(self, tmp_path):
        from cli_anything.galaxy.core.config import show_config
        from cli_anything.galaxy.utils import galaxy_backend as gb

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"url": "https://g.org", "api_key": "abcdef123456789"}))
        with patch.object(gb, "DEFAULT_CONFIG_FILE", cfg_file):
            result = show_config()
            assert result["url"] == "https://g.org"
            assert result["api_key"] == "***...6789"

    def test_save_config_restricts_permissions(self, tmp_path):
        from cli_anything.galaxy.core.config import set_key
        from cli_anything.galaxy.utils import galaxy_backend as gb

        cfg_file = tmp_path / "config.json"
        with patch.object(gb, "DEFAULT_CONFIG_FILE", cfg_file), \
             patch.object(gb, "DEFAULT_CONFIG_DIR", tmp_path):
            set_key("abcdef1234567890")

        assert cfg_file.stat().st_mode & 0o777 == 0o600
        assert tmp_path.stat().st_mode & 0o777 == 0o700

    def test_show_config_empty(self, tmp_path):
        from cli_anything.galaxy.core.config import show_config
        from cli_anything.galaxy.utils import galaxy_backend as gb

        cfg_file = tmp_path / "nonexistent.json"
        with patch.object(gb, "DEFAULT_CONFIG_FILE", cfg_file):
            result = show_config()
            assert result["url"] == "(not set)"
            assert result["api_key"] == "(not set)"

    def test_test_connection(self):
        from cli_anything.galaxy.core.config import test_connection

        mock_client = MagicMock()
        mock_client.get_version.return_value = {"version_major": "24.1", "version_minor": "dev0"}
        mock_client.whoami.return_value = {"username": "testuser", "email": "test@example.com", "id": "abc123"}
        result = test_connection(mock_client)
        assert result["status"] == "connected"
        assert result["galaxy_version"] == "24.1"
        assert result["user"] == "testuser"

    def test_ssl_error_is_normalized(self):
        from cli_anything.galaxy.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

        client = GalaxyClient(url="https://usegalaxy.org", api_key="abc123")
        with patch("cli_anything.galaxy.utils.galaxy_backend.requests.request") as mock_request:
            mock_request.side_effect = requests.exceptions.SSLError("tls failure")
            with pytest.raises(GalaxyBackendError) as exc:
                client.get_version()

        assert "TLS/SSL handshake failed" in str(exc.value)
        assert "modern Python build" in str(exc.value)


# ── History Tests ────────────────────────────────────────────────────────

class TestHistory:
    def _mock_client(self):
        return MagicMock()

    def test_list_histories(self):
        from cli_anything.galaxy.core.history import list_histories

        client = self._mock_client()
        client.get.return_value = [
            {"id": "h1", "name": "History 1", "state": "ok", "size": 1024, "update_time": "2024-01-01"},
            {"id": "h2", "name": "History 2", "state": "running", "size": 0, "update_time": "2024-01-02"},
        ]
        result = list_histories(client)
        assert len(result) == 2
        assert result[0]["id"] == "h1"
        assert result[1]["name"] == "History 2"
        client.get.assert_called_once()

    def test_create_history(self):
        from cli_anything.galaxy.core.history import create_history

        client = self._mock_client()
        client.post.return_value = {"id": "h_new", "name": "My Analysis", "state": "new", "create_time": "2024-01-01"}
        result = create_history(client, name="My Analysis")
        assert result["id"] == "h_new"
        assert result["name"] == "My Analysis"

    def test_show_history(self):
        from cli_anything.galaxy.core.history import show_history

        client = self._mock_client()
        client.get.return_value = {
            "id": "h1", "name": "Test", "state": "ok", "size": 2048,
            "create_time": "2024-01-01", "update_time": "2024-01-02",
            "annotation": "test annotation", "tags": ["tag1"],
            "deleted": False, "importable": False, "published": False,
        }
        result = show_history(client, "h1")
        assert result["id"] == "h1"
        assert result["annotation"] == "test annotation"

    def test_show_history_with_contents(self):
        from cli_anything.galaxy.core.history import show_history

        client = self._mock_client()
        client.get.side_effect = [
            {"id": "h1", "name": "Test", "state": "ok", "size": 0,
             "create_time": "", "update_time": "", "annotation": "", "tags": [],
             "deleted": False, "importable": False, "published": False},
            [{"id": "d1", "name": "data.txt", "type": "file", "state": "ok", "extension": "txt",
              "deleted": False, "visible": True}],
        ]
        result = show_history(client, "h1", contents=True)
        assert len(result["contents"]) == 1
        assert result["contents"][0]["name"] == "data.txt"

    def test_delete_history(self):
        from cli_anything.galaxy.core.history import delete_history

        client = self._mock_client()
        client.delete.return_value = {}
        result = delete_history(client, "h1")
        assert result["id"] == "h1"
        assert result["status"] == "deleted"
        assert result["purged"] is False

    def test_delete_history_purge(self):
        from cli_anything.galaxy.core.history import delete_history

        client = self._mock_client()
        client.delete.return_value = {}
        result = delete_history(client, "h1", purge=True)
        assert result["purged"] is True

    def test_update_history(self):
        from cli_anything.galaxy.core.history import update_history

        client = self._mock_client()
        client.put.return_value = {"id": "h1", "name": "Renamed"}
        result = update_history(client, "h1", name="Renamed")
        assert "name" in result["updated"]
        assert result["name"] == "Renamed"

    def test_export_history(self):
        from cli_anything.galaxy.core.history import export_history

        client = self._mock_client()
        client.put.return_value = {"download_url": "/api/histories/h1/exports/ready"}
        result = export_history(client, "h1")
        assert result["status"] == "export_started"


# ── Dataset Tests ────────────────────────────────────────────────────────

class TestDataset:
    def _mock_client(self):
        return MagicMock()

    def test_upload_dataset(self):
        from cli_anything.galaxy.core.dataset import upload_dataset

        client = self._mock_client()
        client.upload_file.return_value = {
            "outputs": [{"id": "d1", "name": "reads.fastq", "state": "queued", "extension": "fastqsanger"}]
        }
        result = upload_dataset(client, "h1", "/tmp/reads.fastq")
        assert result["id"] == "d1"
        assert result["name"] == "reads.fastq"

    def test_show_dataset(self):
        from cli_anything.galaxy.core.dataset import show_dataset

        client = self._mock_client()
        client.get.return_value = {
            "id": "d1", "name": "data.txt", "state": "ok", "extension": "txt",
            "file_size": 1024, "genome_build": "hg38", "data_type": "tabular",
            "create_time": "", "update_time": "", "deleted": False, "visible": True,
        }
        result = show_dataset(client, "d1")
        assert result["extension"] == "txt"
        assert result["file_size"] == 1024

    def test_show_dataset_with_history(self):
        from cli_anything.galaxy.core.dataset import show_dataset

        client = self._mock_client()
        client.get.return_value = {"id": "d1", "name": "x", "state": "ok", "extension": "bed"}
        result = show_dataset(client, "d1", history_id="h1")
        client.get.assert_called_with("histories/h1/contents/d1")

    def test_download_dataset(self, tmp_path):
        from cli_anything.galaxy.core.dataset import download_dataset

        client = self._mock_client()
        out_file = tmp_path / "output.txt"
        client.download_dataset.return_value = {"output": str(out_file), "size": 100}
        result = download_dataset(client, "d1", str(out_file))
        assert result["output"] == str(out_file)
        assert result["size"] == 100

    def test_peek_dataset(self):
        from cli_anything.galaxy.core.dataset import peek_dataset

        client = self._mock_client()
        client.get.return_value = {"peek": "line1\nline2\nline3\nline4\nline5"}
        result = peek_dataset(client, "d1", lines=3)
        assert len(result["lines"]) == 3
        assert result["lines"][0] == "line1"

    def test_delete_dataset(self):
        from cli_anything.galaxy.core.dataset import delete_dataset

        client = self._mock_client()
        client.put.return_value = {}
        result = delete_dataset(client, "d1", "h1")
        assert result["status"] == "deleted"

    def test_list_datasets(self):
        from cli_anything.galaxy.core.dataset import list_datasets

        client = self._mock_client()
        client.get.return_value = [
            {"id": "d1", "name": "a.txt", "type": "file", "state": "ok", "extension": "txt",
             "file_size": 100, "deleted": False, "visible": True},
            {"id": "d2", "name": "b.bed", "type": "dataset", "state": "ok", "extension": "bed",
             "file_size": 200, "deleted": False, "visible": True},
        ]
        result = list_datasets(client, "h1")
        assert len(result) == 2


# ── Tool Tests ───────────────────────────────────────────────────────────

class TestTool:
    def _mock_client(self):
        return MagicMock()

    def test_list_tools(self):
        from cli_anything.galaxy.core.tool import list_tools

        client = self._mock_client()
        client.get.return_value = [
            {"id": "fastqc", "name": "FastQC", "version": "0.73", "description": "Quality control"},
            {"id": "bowtie2", "name": "Bowtie2", "version": "2.5", "description": "Aligner"},
        ]
        result = list_tools(client)
        assert len(result) == 2
        assert result[0]["id"] == "fastqc"

    def test_list_tools_resolves_string_search_hits(self):
        from cli_anything.galaxy.core.tool import list_tools

        client = self._mock_client()
        client.get.side_effect = [
            [
                "toolshed.g2.bx.psu.edu/repos/devteam/bowtie2/bowtie2/2.5.5+galaxy0",
            ],
            {
                "id": "toolshed.g2.bx.psu.edu/repos/devteam/bowtie2/bowtie2/2.5.5+galaxy0",
                "name": "Bowtie2",
                "version": "2.5.5",
                "description": "Map with Bowtie2",
                "panel_section_name": "NGS Mapping",
            },
        ]

        result = list_tools(client, query="bowtie")
        assert len(result) == 1
        assert result[0]["name"] == "Bowtie2"
        assert client.get.call_args_list == [
            (("tools",), {"params": {"in_panel": False, "q": "bowtie"}}),
            (("tools/toolshed.g2.bx.psu.edu/repos/devteam/bowtie2/bowtie2/2.5.5+galaxy0",), {}),
        ]

    def test_search_tools(self):
        from cli_anything.galaxy.core.tool import search_tools

        client = self._mock_client()
        client.get.return_value = [
            {"id": "bowtie2", "name": "Bowtie2", "version": "2.5", "description": "Aligner"},
        ]
        result = search_tools(client, "bowtie")
        assert len(result) == 1

    def test_search_tools_keyword_fallback(self):
        from cli_anything.galaxy.core.tool import search_tools

        client = self._mock_client()
        client.get.side_effect = [
            [],
            [
                {
                    "id": "sklearn_train_regression",
                    "name": "Tabular Machine Learning Trainer",
                    "version": "1.0",
                    "description": "Train machine learning models on tabular datasets",
                    "panel_section_name": "Machine Learning",
                },
                {
                    "id": "tabular_filter",
                    "name": "Filter Tabular",
                    "version": "1.0",
                    "description": "Filter rows in a tabular file",
                    "panel_section_name": "Text Manipulation",
                },
            ],
        ]

        result = search_tools(client, "tabular machine learning")
        assert len(result) == 1
        assert result[0]["id"] == "sklearn_train_regression"
        assert client.get.call_args_list == [
            (("tools",), {"params": {"in_panel": False, "q": "tabular machine learning"}}),
            (("tools",), {"params": {"in_panel": False}}),
        ]

    def test_show_tool(self):
        from cli_anything.galaxy.core.tool import show_tool

        client = self._mock_client()
        client.get.return_value = {
            "id": "fastqc", "name": "FastQC", "version": "0.73",
            "description": "Read quality reports",
            "inputs": [{"name": "input_file", "label": "Raw data", "type": "data",
                        "value": None, "optional": False, "help": "FASTQ file"}],
            "outputs": [{"name": "html_file", "format": "html", "label": "Report"}],
        }
        result = show_tool(client, "fastqc")
        assert result["name"] == "FastQC"
        assert len(result["inputs"]) == 1
        assert len(result["outputs"]) == 1

    def test_run_tool(self):
        from cli_anything.galaxy.core.tool import run_tool

        client = self._mock_client()
        client.post.return_value = {
            "jobs": [{"id": "j1", "state": "new", "tool_id": "fastqc"}],
            "outputs": [{"id": "d2", "name": "FastQC Report", "extension": "html"}],
        }
        result = run_tool(client, "fastqc", "h1", inputs={"input_file": "d1"})
        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["id"] == "j1"
        assert len(result["outputs"]) == 1


# ── Job Tests ────────────────────────────────────────────────────────────

class TestJob:
    def _mock_client(self):
        return MagicMock()

    def test_list_jobs(self):
        from cli_anything.galaxy.core.job import list_jobs

        client = self._mock_client()
        client.get.return_value = [
            {"id": "j1", "tool_id": "fastqc", "state": "ok", "create_time": "", "update_time": "", "exit_code": 0},
        ]
        result = list_jobs(client)
        assert len(result) == 1
        assert result[0]["state"] == "ok"

    def test_show_job(self):
        from cli_anything.galaxy.core.job import show_job

        client = self._mock_client()
        client.get.return_value = {
            "id": "j1", "tool_id": "fastqc", "state": "ok",
            "create_time": "", "update_time": "", "exit_code": 0,
            "history_id": "h1", "command_line": "fastqc input.fastq",
            "tool_stdout": "Done", "tool_stderr": "",
        }
        result = show_job(client, "j1")
        assert result["state"] == "ok"
        assert result["exit_code"] == 0

    def test_show_job_full(self):
        from cli_anything.galaxy.core.job import show_job

        client = self._mock_client()
        client.get.return_value = {
            "id": "j1", "tool_id": "fastqc", "state": "ok",
            "create_time": "", "update_time": "", "exit_code": 0,
            "history_id": "h1", "command_line": "", "tool_stdout": "", "tool_stderr": "",
            "inputs": {"input_file": {"id": "d1"}},
            "outputs": {"html_file": {"id": "d2"}},
            "params": {"input_file": "d1"},
        }
        result = show_job(client, "j1", full=True)
        assert "inputs" in result
        assert "outputs" in result

    def test_cancel_job(self):
        from cli_anything.galaxy.core.job import cancel_job

        client = self._mock_client()
        client.delete.return_value = {}
        result = cancel_job(client, "j1")
        assert result["status"] == "cancelled"

    def test_wait_for_job_ok(self):
        from cli_anything.galaxy.core.job import wait_for_job

        client = self._mock_client()
        client.get.return_value = {"state": "ok", "exit_code": 0}
        result = wait_for_job(client, "j1", poll_interval=0)
        assert result["state"] == "ok"

    def test_wait_for_job_timeout(self):
        from cli_anything.galaxy.core.job import wait_for_job

        client = self._mock_client()
        client.get.return_value = {"state": "running"}
        result = wait_for_job(client, "j1", max_wait=0, poll_interval=1)
        assert result["state"] == "timeout"


# ── Workflow Tests ───────────────────────────────────────────────────────

class TestWorkflow:
    def _mock_client(self):
        return MagicMock()

    def test_list_workflows(self):
        from cli_anything.galaxy.core.workflow import list_workflows

        client = self._mock_client()
        client.get.return_value = [
            {"id": "w1", "name": "RNA-seq", "owner": "admin", "published": False,
             "deleted": False, "number_of_steps": 5, "update_time": "", "tags": []},
        ]
        result = list_workflows(client)
        assert len(result) == 1
        assert result[0]["step_count"] == 5

    def test_show_workflow(self):
        from cli_anything.galaxy.core.workflow import show_workflow

        client = self._mock_client()
        client.get.return_value = {
            "id": "w1", "name": "RNA-seq", "owner": "admin", "annotation": "",
            "published": False, "version": 1, "update_time": "", "tags": [],
            "steps": {
                "0": {"id": "s0", "type": "data_input", "tool_id": None, "label": "Input",
                      "annotation": "", "input_connections": {}},
                "1": {"id": "s1", "type": "tool", "tool_id": "fastqc", "label": "QC",
                      "annotation": "", "input_connections": {"input_file": {"id": 0}}},
            },
            "inputs": {"0": {"label": "Input Dataset", "value": ""}},
        }
        result = show_workflow(client, "w1")
        assert result["step_count"] == 2
        assert "0" in result["steps"]

    def test_import_workflow_from_file(self, tmp_path):
        from cli_anything.galaxy.core.workflow import import_workflow

        client = self._mock_client()
        client.post.return_value = {"id": "w_new", "name": "Imported WF"}
        wf_file = tmp_path / "workflow.ga"
        wf_file.write_text(json.dumps({"a]_galaxy_workflow": "true", "name": "Test WF"}))
        result = import_workflow(client, workflow_path=str(wf_file))
        assert result["status"] == "imported"

    def test_export_workflow(self, tmp_path):
        from cli_anything.galaxy.core.workflow import export_workflow

        client = self._mock_client()
        client.get.return_value = {"name": "WF", "steps": {}}
        out_file = tmp_path / "exported.ga"
        result = export_workflow(client, "w1", output_path=str(out_file))
        assert result["status"] == "exported"
        assert out_file.exists()

    def test_run_workflow(self):
        from cli_anything.galaxy.core.workflow import run_workflow

        client = self._mock_client()
        client.post.return_value = {"id": "inv1", "state": "new", "history_id": "h1"}
        result = run_workflow(client, "w1", history_id="h1", inputs={"0": "d1"})
        assert result["status"] == "invoked"
        assert result["id"] == "inv1"

    def test_delete_workflow(self):
        from cli_anything.galaxy.core.workflow import delete_workflow

        client = self._mock_client()
        client.delete.return_value = {}
        result = delete_workflow(client, "w1")
        assert result["status"] == "deleted"


# ── Invocation Tests ─────────────────────────────────────────────────────

class TestInvocation:
    def _mock_client(self):
        return MagicMock()

    def test_list_invocations(self):
        from cli_anything.galaxy.core.invocation import list_invocations

        client = self._mock_client()
        client.get.return_value = [
            {"id": "inv1", "workflow_id": "w1", "history_id": "h1",
             "state": "scheduled", "create_time": "", "update_time": ""},
        ]
        result = list_invocations(client)
        assert len(result) == 1

    def test_show_invocation(self):
        from cli_anything.galaxy.core.invocation import show_invocation

        client = self._mock_client()
        client.get.return_value = {
            "id": "inv1", "workflow_id": "w1", "history_id": "h1",
            "state": "scheduled", "create_time": "", "update_time": "",
            "steps": [{"id": "s1", "order_index": 0, "state": "ok", "job_id": "j1",
                       "update_time": "", "action": None}],
            "inputs": {}, "outputs": {},
        }
        result = show_invocation(client, "inv1")
        assert result["state"] == "scheduled"
        assert len(result["steps"]) == 1

    def test_cancel_invocation(self):
        from cli_anything.galaxy.core.invocation import cancel_invocation

        client = self._mock_client()
        client.delete.return_value = {}
        result = cancel_invocation(client, "inv1")
        assert result["status"] == "cancelled"


# ── Library Tests ────────────────────────────────────────────────────────

class TestLibrary:
    def _mock_client(self):
        return MagicMock()

    def test_list_libraries(self):
        from cli_anything.galaxy.core.library import list_libraries

        client = self._mock_client()
        client.get.return_value = [
            {"id": "lib1", "name": "Shared Data", "description": "Team data", "deleted": False, "create_time": ""},
        ]
        result = list_libraries(client)
        assert len(result) == 1
        assert result[0]["name"] == "Shared Data"

    def test_create_library(self):
        from cli_anything.galaxy.core.library import create_library

        client = self._mock_client()
        client.post.return_value = {"id": "lib_new", "name": "New Lib"}
        result = create_library(client, "New Lib", description="Test lib")
        assert result["status"] == "created"

    def test_show_library(self):
        from cli_anything.galaxy.core.library import show_library

        client = self._mock_client()
        client.get.return_value = {
            "id": "lib1", "name": "Shared", "description": "desc",
            "synopsis": "", "deleted": False, "create_time": "",
        }
        result = show_library(client, "lib1")
        assert result["name"] == "Shared"

    def test_list_library_contents(self):
        from cli_anything.galaxy.core.library import list_library_contents

        client = self._mock_client()
        client.get.return_value = [
            {"id": "lc1", "name": "/", "type": "folder", "url": "/api/libraries/lib1/contents/lc1"},
        ]
        result = list_library_contents(client, "lib1")
        assert len(result) == 1
        assert result[0]["type"] == "folder"

    def test_delete_library(self):
        from cli_anything.galaxy.core.library import delete_library

        client = self._mock_client()
        client.delete.return_value = {}
        result = delete_library(client, "lib1")
        assert result["status"] == "deleted"


# ── Session Tests ────────────────────────────────────────────────────────

class TestSession:
    def test_load_session_new(self, tmp_path):
        from cli_anything.galaxy.core.session import load_session

        result = load_session(session_path=str(tmp_path / "nonexistent.json"))
        assert result["current_history_id"] is None
        assert result["current_history_name"] is None

    def test_save_and_load_session(self, tmp_path):
        from cli_anything.galaxy.core.session import save_session, load_session

        sf = str(tmp_path / "session.json")
        save_session({"current_history_id": "h1", "current_history_name": "Test"}, session_path=sf)
        result = load_session(session_path=sf)
        assert result["current_history_id"] == "h1"
        assert result["current_history_name"] == "Test"

    def test_set_current_history(self, tmp_path):
        from cli_anything.galaxy.core.session import set_current_history, load_session

        sf = str(tmp_path / "session.json")
        set_current_history("h42", "My History", session_path=sf)
        result = load_session(session_path=sf)
        assert result["current_history_id"] == "h42"

    def test_track_job(self, tmp_path):
        from cli_anything.galaxy.core.session import track_job, load_session

        sf = str(tmp_path / "session.json")
        track_job("j99", session_path=sf)
        result = load_session(session_path=sf)
        assert result["last_job_id"] == "j99"

    def test_clear_session(self, tmp_path):
        from cli_anything.galaxy.core.session import save_session, clear_session, load_session

        sf = str(tmp_path / "session.json")
        save_session({"current_history_id": "h1"}, session_path=sf)
        clear_session(session_path=sf)
        result = load_session(session_path=sf)
        assert result["current_history_id"] is None


# ── Backend Tests ────────────────────────────────────────────────────────

class TestGalaxyBackend:
    def test_client_no_url(self):
        from cli_anything.galaxy.utils.galaxy_backend import GalaxyClient, GalaxyBackendError

        with patch.dict(os.environ, {}, clear=True):
            # Remove env vars and ensure no config file
            env = {k: v for k, v in os.environ.items() if k not in ("GALAXY_URL", "GALAXY_API_KEY")}
            with patch.dict(os.environ, env, clear=True):
                from cli_anything.galaxy.utils import galaxy_backend as gb
                with patch.object(gb, "DEFAULT_CONFIG_FILE", Path("/nonexistent/config.json")):
                    with pytest.raises(GalaxyBackendError, match="URL not configured"):
                        GalaxyClient()

    def test_client_no_key(self):
        from cli_anything.galaxy.utils.galaxy_backend import GalaxyClient, GalaxyBackendError

        env = {k: v for k, v in os.environ.items() if k not in ("GALAXY_URL", "GALAXY_API_KEY")}
        env["GALAXY_URL"] = "https://galaxy.example.org"
        with patch.dict(os.environ, env, clear=True):
            from cli_anything.galaxy.utils import galaxy_backend as gb
            with patch.object(gb, "DEFAULT_CONFIG_FILE", Path("/nonexistent/config.json")):
                with pytest.raises(GalaxyBackendError, match="API key not configured"):
                    GalaxyClient()

    def test_client_from_env(self):
        from cli_anything.galaxy.utils.galaxy_backend import GalaxyClient

        env = dict(os.environ)
        env["GALAXY_URL"] = "https://galaxy.example.org"
        env["GALAXY_API_KEY"] = "testkey123"
        with patch.dict(os.environ, env, clear=True):
            client = GalaxyClient()
            assert "galaxy.example.org" in client.url
            assert client.api_key == "testkey123"

    def test_api_url_construction(self):
        from cli_anything.galaxy.utils.galaxy_backend import GalaxyClient

        env = dict(os.environ)
        env["GALAXY_URL"] = "https://galaxy.example.org"
        env["GALAXY_API_KEY"] = "testkey123"
        with patch.dict(os.environ, env, clear=True):
            client = GalaxyClient()
            assert client._api_url("histories") == "https://galaxy.example.org/api/histories"
            assert client._api_url("/tools") == "https://galaxy.example.org/api/tools"

    def test_upload_file_closes_handle_on_request_error(self, tmp_path):
        from cli_anything.galaxy.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

        upload_file = tmp_path / "reads.fastq"
        upload_file.write_text("@r1\nACGT\n+\n!!!!\n")
        client = GalaxyClient(url="https://galaxy.example.org", api_key="testkey123")
        observed = {}

        def fake_post(*args, **kwargs):
            observed["closed_during_request"] = kwargs["files"]["files_0|file_data"][1].closed
            raise requests.Timeout("slow upload")

        with patch("cli_anything.galaxy.utils.galaxy_backend.requests.post", side_effect=fake_post):
            with pytest.raises(GalaxyBackendError, match="timed out"):
                client.upload_file(str(upload_file), "h1")

        assert observed["closed_during_request"] is False
