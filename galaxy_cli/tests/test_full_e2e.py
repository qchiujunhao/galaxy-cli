"""End-to-end tests for Galaxy CLI.

These tests require a running Galaxy server.
Set GALAXY_URL and GALAXY_API_KEY environment variables.

Tests marked with @pytest.mark.e2e require a live server;
CLI subprocess tests run against the installed command.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# ── Helpers ──────────────────────────────────────────────────���───────────

def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    import shutil
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module_map = {
        "galaxy-cli": "galaxy_cli.cli",
        "cli-galaxy": "galaxy_cli.cli",
        "cli-anything-galaxy": "galaxy_cli.cli",
    }
    module = module_map.get(name)
    if module is None:
        module = name.replace("cli-anything-", "cli_anything.") + "." + name.split("-")[-1] + "_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


def _has_galaxy_server():
    """Check if a Galaxy server is configured and reachable."""
    url = os.environ.get("GALAXY_URL")
    key = os.environ.get("GALAXY_API_KEY")
    if not url or not key:
        return False
    try:
        import requests
        resp = requests.get(f"{url.rstrip('/')}/api/version", timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def _wait_for_dataset_ready(client, dataset_id, history_id=None, max_wait=180, poll_interval=5):
    """Wait for a dataset to reach a downloadable terminal state."""
    from galaxy_cli.core.dataset import show_dataset

    elapsed = 0
    while elapsed < max_wait:
        info = show_dataset(client, dataset_id, history_id=history_id)
        state = info.get("state", "")
        if state == "ok" and info.get("file_size", 0) > 0:
            return info
        if state in {"error", "failed", "discarded", "deleted"}:
            raise AssertionError(f"Dataset {dataset_id} entered terminal failure state: {state}")
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise AssertionError(f"Dataset {dataset_id} did not become ready within {max_wait}s")


needs_galaxy = pytest.mark.skipif(
    not _has_galaxy_server(),
    reason="No Galaxy server available (set GALAXY_URL and GALAXY_API_KEY)"
)


# ── E2E Tests (require running Galaxy) ────────────────────────────────���─

@needs_galaxy
class TestServerConnection:
    def _client(self):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient
        return GalaxyClient()

    def test_server_version(self):
        client = self._client()
        result = client.get_version()
        assert "version_major" in result
        print(f"\n  Galaxy version: {result['version_major']}")

    def test_whoami(self):
        client = self._client()
        result = client.whoami()
        assert "username" in result
        print(f"\n  User: {result['username']} ({result.get('email', '')})")


@needs_galaxy
class TestHistoryE2E:
    def _client(self):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient
        return GalaxyClient()

    def test_create_list_delete_history(self):
        from galaxy_cli.core.history import create_history, list_histories, delete_history

        client = self._client()

        # Create
        created = create_history(client, name="CLI-Test-E2E")
        assert created["id"]
        assert created["name"] == "CLI-Test-E2E"
        print(f"\n  Created history: {created['id']}")

        # List
        histories = list_histories(client)
        ids = [h["id"] for h in histories]
        assert created["id"] in ids

        # Delete
        deleted = delete_history(client, created["id"])
        assert deleted["status"] == "deleted"
        print(f"  Deleted history: {created['id']}")


@needs_galaxy
class TestDatasetE2E:
    def _client(self):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient
        return GalaxyClient()

    def test_upload_and_download(self):
        from galaxy_cli.core.history import create_history, delete_history
        from galaxy_cli.core.dataset import upload_dataset, download_dataset

        client = self._client()
        test_file = None
        out_path = None

        # Create history
        hist = create_history(client, name="CLI-Upload-Test")
        hid = hist["id"]

        try:
            # Create test file
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
                f.write("chr1\t100\t200\nline2\nline3\n")
                test_file = f.name

            # Upload
            result = upload_dataset(client, hid, test_file, file_type="txt")
            assert result.get("id") or result.get("status")
            print(f"\n  Uploaded: {result}")

            # Download
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as out:
                out_path = out.name

            if result.get("id"):
                ready = _wait_for_dataset_ready(client, result["id"], history_id=hid)
                dl = download_dataset(client, result["id"], out_path)
                assert dl["size"] > 0
                assert dl["size"] == ready["file_size"]
                print(f"  Downloaded: {dl['output']} ({dl['size']} bytes)")
        finally:
            delete_history(client, hid)
            for p in [test_file, out_path]:
                try:
                    os.unlink(p)
                except OSError:
                    pass


@needs_galaxy
class TestToolE2E:
    def _client(self):
        from galaxy_cli.utils.galaxy_backend import GalaxyClient
        return GalaxyClient()

    def test_list_and_search_tools(self):
        from galaxy_cli.core.tool import list_tools, search_tools

        client = self._client()
        tools = list_tools(client)
        assert len(tools) > 0
        print(f"\n  Found {len(tools)} tools")

    def test_show_tool_details(self):
        from galaxy_cli.core.tool import list_tools, show_tool

        client = self._client()
        tools = list_tools(client)
        if tools:
            detail = show_tool(client, tools[0]["id"])
            assert detail["name"]
            print(f"\n  Tool: {detail['name']} ({detail['id']})")
            print(f"  Inputs: {len(detail['inputs'])}, Outputs: {len(detail['outputs'])}")


# ── CLI Subprocess Tests ─────────────────────────────────────────────────

class TestCLISubprocess:
    CLI_BASE = _resolve_cli("galaxy-cli")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True,
            check=check,
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "Galaxy" in result.stdout
        assert "history" in result.stdout

    def test_version(self):
        result = self._run(["--version"])
        assert result.returncode == 0
        assert "1.0.0" in result.stdout

    def test_config_show_json(self):
        result = self._run(["--json", "config", "show"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "url" in data
        assert "api_key" in data

    def test_session_show_json(self):
        result = self._run(["--json", "session", "show"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "current_history_id" in data

    def test_history_subcommand_help(self):
        result = self._run(["history", "--help"])
        assert result.returncode == 0
        assert "list" in result.stdout
        assert "create" in result.stdout

    def test_tool_subcommand_help(self):
        result = self._run(["tool", "--help"])
        assert result.returncode == 0
        assert "search" in result.stdout
        assert "run" in result.stdout

    def test_workflow_subcommand_help(self):
        result = self._run(["workflow", "--help"])
        assert result.returncode == 0
        assert "import" in result.stdout
        assert "run" in result.stdout

    def test_job_subcommand_help(self):
        result = self._run(["job", "--help"])
        assert result.returncode == 0
        assert "show" in result.stdout
        assert "wait" in result.stdout


@needs_galaxy
class TestCLISubprocessE2E:
    """Subprocess E2E tests that hit the real Galaxy server."""
    CLI_BASE = _resolve_cli("galaxy-cli")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True,
            check=check,
        )

    def test_history_list_json(self):
        result = self._run(["--json", "history", "list"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        print(f"\n  Histories via subprocess: {len(data)}")

    def test_tool_list_json(self):
        result = self._run(["--json", "tool", "list", "--limit", "5"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        print(f"\n  Tools via subprocess: {len(data)}")

    def test_full_history_lifecycle(self):
        """Create, show, and delete a history via subprocess."""
        # Create
        create = self._run(["--json", "history", "create", "Subprocess-Test"])
        assert create.returncode == 0
        created = json.loads(create.stdout)
        hid = created["id"]
        print(f"\n  Created history via subprocess: {hid}")

        try:
            # Show
            show = self._run(["--json", "history", "show", hid])
            assert show.returncode == 0
            shown = json.loads(show.stdout)
            assert shown["name"] == "Subprocess-Test"
        finally:
            # Delete
            delete = self._run(["--json", "history", "delete", hid])
            assert delete.returncode == 0
            print(f"  Deleted history via subprocess: {hid}")
