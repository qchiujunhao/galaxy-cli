"""Test-wide isolation for local runtime metadata."""

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_directories(tmp_path, monkeypatch):
    """Prevent cache or receipt state from crossing test replicates."""
    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GALAXY_CLI_OPERATION_DIR", str(tmp_path / "operations"))
