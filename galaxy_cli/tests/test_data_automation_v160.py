import json
import stat
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_cache_stats_counters_clear_and_secret_free_output(tmp_path, monkeypatch):
    from galaxy_cli.core import metadata_cache

    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(cache_dir))
    metadata_cache.invocation_counters(reset=True)
    secret = "never-print-this-schema-body"
    key = ["https://galaxy.example/api", "private-tool-id"]

    path = metadata_cache.write("tool-schema", key, {"body": secret})
    metadata_cache.write("server-version", ["https://galaxy.example"], {"version": "26"})
    assert metadata_cache.read("tool-schema", key) == {"body": secret}
    assert metadata_cache.read("tool-schema", ["https://galaxy.example", "missing"]) is None

    result = metadata_cache.stats()
    rendered = json.dumps(result)
    assert result["entry_count"] == 2
    assert result["fresh"] == 2
    assert result["invocation_counters"]["hit"] == 1
    assert result["invocation_counters"]["miss"] == 1
    assert secret not in rendered
    assert "private-tool-id" not in rendered
    assert "https://galaxy.example" not in rendered
    assert result["server_identity_hashes"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700

    cleared = metadata_cache.clear("tool-schema")
    assert cleared["removed_entries"] == 1
    assert metadata_cache.stats()["entry_count"] == 1


def test_cache_expired_corrupt_counters_and_atomic_writes(tmp_path, monkeypatch):
    from galaxy_cli.core import metadata_cache

    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(tmp_path / "cache"))
    metadata_cache.invocation_counters(reset=True)

    expired = metadata_cache.write("tools", ["server", "expired"], {"ok": True})
    payload = json.loads(expired.read_text())
    payload["created_at"] = time.time() - 60
    expired.write_text(json.dumps(payload))
    assert metadata_cache.read("tools", ["server", "expired"], ttl=1) is None

    corrupt = metadata_cache.write("tools", ["server", "corrupt"], {"ok": True})
    corrupt.write_text("not json")
    assert metadata_cache.read("tools", ["server", "corrupt"]) is None
    counters = metadata_cache.invocation_counters()
    assert counters["expired"] == 1
    assert counters["corrupt"] == 1

    shared = metadata_cache.write("tools", ["server", "shared"], {"writer": -1})
    threads = [
        threading.Thread(
            target=metadata_cache.write,
            args=("tools", ["server", "shared"], {"writer": index}),
        )
        for index in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert isinstance(json.loads(shared.read_text())["value"]["writer"], int)
    assert not list(shared.parent.glob(".cache-*"))

    monkeypatch.setenv("GALAXY_CLI_CACHE_TTL", "nan")
    assert metadata_cache.cache_ttl() == metadata_cache.DEFAULT_CACHE_TTL


def test_cache_server_identity_removes_url_credentials_query_and_fragment(
    tmp_path, monkeypatch
):
    from galaxy_cli.core.server import server_capabilities

    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(tmp_path / "cache"))
    secret = "query-api-key"
    client = MagicMock()
    client.url = (
        f"https://user:password@galaxy.example/galaxy/?key={secret}#fragment"
    )
    client.api_key = secret
    client.get_version.return_value = {
        "version_major": "26.0",
        "server_echo": secret,
    }
    client.get.side_effect = [
        {},
        [],
        [],
        [],
        [],
        [],
        [],
    ]

    result = server_capabilities(client, refresh_cache=True)
    persisted = "".join(
        path.read_text() for path in (tmp_path / "cache").rglob("*.json")
    )

    assert result["galaxy_url"] == "https://galaxy.example/galaxy/"
    for forbidden in ("user", "password", secret, "fragment", "?key="):
        assert forbidden not in persisted


def test_cache_serialization_failure_removes_private_temporary_file(
    tmp_path, monkeypatch
):
    from galaxy_cli.core import metadata_cache

    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("GALAXY_CLI_CACHE_DIR", str(cache_dir))
    unserializable = {frozenset({"invalid-json-key"}): "value"}

    assert metadata_cache.write(
        "tools", ["server", "unserializable"], unserializable
    ) is None
    assert not list(cache_dir.rglob(".cache-*"))


def _download_client(contents):
    encoded = contents.encode("utf-8")
    client = MagicMock()
    client.get.return_value = {"id": "d1", "file_size": len(encoded)}

    def download(_dataset_id, output_path):
        Path(output_path).write_bytes(encoded)
        return {"output": output_path, "size": len(encoded)}

    client.download_dataset.side_effect = download
    return client


def test_dataset_head_and_field_selector_remain_remote_and_bounded():
    from galaxy_cli.core.dataset import MAX_PREVIEW_LINES, peek_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.get.return_value = {"data": ["a\tb\tc\n", "d\te\tf\n", "g\th\ti\n"]}
    result = peek_dataset(client, "d1", head=2, fields="1,3", delimiter="tab")

    assert result["lines"] == ["a\tc", "d\tf"]
    assert result["selected_fields"] == [1, 3]
    assert result["selector"] == {"mode": "head", "limit": 2}
    client.download_dataset.assert_not_called()
    client.get.assert_called_once_with(
        "datasets/d1",
        params={"data_type": "raw_data", "provider": "base", "offset": 0, "limit": 2},
    )

    with pytest.raises(GalaxyBackendError) as exc:
        peek_dataset(client, "d1", head=MAX_PREVIEW_LINES + 1)
    assert exc.value.error_kind == "preview_limit_invalid"


def test_dataset_tail_and_grep_scan_known_small_file_and_cleanup(tmp_path, monkeypatch):
    from galaxy_cli.core.dataset import peek_dataset

    preview_dir = tmp_path / "preview"
    monkeypatch.setenv("GALAXY_CLI_PREVIEW_TMPDIR", str(preview_dir))
    contents = "a\tone\t1\nb\tERROR\t2\nc\tthree\t3\nd\tERROR\t4\ne\tfive\t5\n"

    tail_client = _download_client(contents)
    tail = peek_dataset(tail_client, "d1", tail=2, delimiter="tab")
    assert tail["lines"] == ["d\tERROR\t4", "e\tfive\t5"]
    assert [row["line_number"] for row in tail["rows"]] == [4, 5]
    assert tail["scan"]["temporary_file_removed"] is True
    assert not list(preview_dir.iterdir())

    grep_client = _download_client(contents)
    matched = peek_dataset(
        grep_client,
        "d1",
        lines=3,
        grep="ERROR",
        context=1,
        fields="1,3",
        delimiter="tab",
    )
    assert matched["lines"] == ["a\t1", "b\t2", "c\t3"]
    assert [row["line_number"] for row in matched["rows"]] == [1, 2, 3]
    assert matched["truncated"] is True
    assert not list(preview_dir.iterdir())


def test_dataset_tail_streams_many_lines_without_whole_file_read(
    tmp_path, monkeypatch
):
    from galaxy_cli.core.dataset import peek_dataset

    preview_dir = tmp_path / "preview"
    monkeypatch.setenv("GALAXY_CLI_PREVIEW_TMPDIR", str(preview_dir))
    client = _download_client("\n" * 10000)

    def reject_whole_file_read(*_args, **_kwargs):
        raise AssertionError("preview must stream the bounded copy")

    monkeypatch.setattr(Path, "read_text", reject_whole_file_read)
    result = peek_dataset(client, "d1", tail=2)

    assert result["total_shown"] == 2
    assert result["scan"]["scanned_lines"] == 10000
    assert result["truncated"] is True


@pytest.mark.parametrize("file_size", [None, 11])
def test_dataset_scan_refuses_unknown_or_oversized_download(file_size, tmp_path, monkeypatch):
    from galaxy_cli.core.dataset import peek_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    monkeypatch.setenv("GALAXY_CLI_PREVIEW_TMPDIR", str(tmp_path / "preview"))
    client = MagicMock()
    client.get.return_value = {"id": "d1", "file_size": file_size}

    with pytest.raises(GalaxyBackendError) as exc:
        peek_dataset(client, "d1", tail=1, max_download_bytes=10)
    assert exc.value.error_kind in {"preview_size_unknown", "preview_download_too_large"}
    assert "dataset download" in exc.value.suggestion
    client.download_dataset.assert_not_called()


def test_real_client_stream_aborts_before_preview_limit_is_written(tmp_path):
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [b"1234", b"5678", b"never-read"]
    client = GalaxyClient(url="https://galaxy.example", api_key="key")
    client._request = MagicMock(return_value=response)
    destination = tmp_path / "bounded-preview.txt"

    with pytest.raises(GalaxyBackendError) as exc:
        client.download_dataset("d1", destination, max_bytes=5)

    assert exc.value.error_kind == "preview_download_too_large"
    assert not destination.exists()
    response.close.assert_called_once()


def test_scan_preserves_stream_limit_error_after_backend_removes_partial_file(
    tmp_path, monkeypatch
):
    from galaxy_cli.core.dataset import peek_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError, GalaxyClient

    preview_dir = tmp_path / "preview"
    monkeypatch.setenv("GALAXY_CLI_PREVIEW_TMPDIR", str(preview_dir))
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [b"1234", b"5678"]
    client = GalaxyClient(url="https://galaxy.example", api_key="key")
    client.get = MagicMock(return_value={"id": "d1", "file_size": 4})
    client._request = MagicMock(return_value=response)

    with pytest.raises(GalaxyBackendError) as exc:
        peek_dataset(client, "d1", tail=1, max_download_bytes=5)

    assert exc.value.error_kind == "preview_download_too_large"
    assert not list(preview_dir.iterdir())


def test_preview_cleanup_failure_is_explicit(tmp_path, monkeypatch):
    from galaxy_cli.core.dataset import peek_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    preview_dir = tmp_path / "preview"
    monkeypatch.setenv("GALAXY_CLI_PREVIEW_TMPDIR", str(preview_dir))
    client = _download_client("one\ntwo\n")

    def deny_cleanup(_path):
        raise OSError("cleanup denied")

    monkeypatch.setattr(Path, "unlink", deny_cleanup)

    with pytest.raises(GalaxyBackendError) as exc:
        peek_dataset(client, "d1", tail=1)

    assert exc.value.error_kind == "preview_cleanup_failed"


def _collection_with_elements(elements):
    return {
        "id": "collection-1",
        "collection_type": "list",
        "elements": elements,
    }


def _dataset_element(identifier, dataset_id):
    return {
        "element_identifier": identifier,
        "element_type": "hda",
        "object": {"id": dataset_id, "state": "ok", "extension": "txt"},
    }


def test_collection_element_preview_is_explicit_and_bounded():
    from galaxy_cli.core.collection import preview_collection_element

    client = MagicMock()
    client.get.side_effect = [
        _collection_with_elements([_dataset_element("sample", "dataset-1")]),
        {"data": ["first\n", "second\n", "third\n"]},
    ]
    result = preview_collection_element(
        client, "collection-1", "sample", head=2
    )

    assert result["dataset_id"] == "dataset-1"
    assert result["resolved_path"] == "sample"
    assert result["extension"] == "txt"
    assert result["lines"] == ["first", "second"]


@pytest.mark.parametrize(
    "elements,path,max_results,error_kind",
    [
        ([_dataset_element("one", "d1")], "missing", 10000, "collection_element_missing"),
        (
            [_dataset_element("same", "d1"), _dataset_element("same", "d2")],
            "same",
            10000,
            "collection_element_ambiguous",
        ),
        (
            [_dataset_element("one", "d1"), _dataset_element("two", "d2")],
            "two",
            1,
            "collection_result_limit",
        ),
    ],
)
def test_collection_resolution_errors_are_structured(
    elements, path, max_results, error_kind
):
    from galaxy_cli.core.collection import resolve_collection_element
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.get.return_value = _collection_with_elements(elements)
    with pytest.raises(GalaxyBackendError) as exc:
        resolve_collection_element(
            client, "collection-1", path, max_results=max_results
        )
    assert exc.value.error_kind == error_kind


def test_collection_cycle_and_depth_errors_are_structured():
    from galaxy_cli.core.collection import flatten_collection
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    cycle = {
        "id": "loop",
        "collection_type": "list",
        "elements": [{
            "element_identifier": "again",
            "element_type": "dataset_collection",
            "object": {"id": "loop", "collection_type": "list"},
        }],
    }
    client = MagicMock()
    client.get.return_value = cycle
    with pytest.raises(GalaxyBackendError) as cycle_exc:
        flatten_collection(client, "loop")
    assert cycle_exc.value.error_kind == "collection_cycle"

    nested = _collection_with_elements([{
        "element_identifier": "nested",
        "element_type": "dataset_collection",
        "object": {
            "id": "inner",
            "collection_type": "list",
            "elements": [_dataset_element("item", "d1")],
        },
    }])
    client.get.return_value = nested
    with pytest.raises(GalaxyBackendError) as depth_exc:
        flatten_collection(client, "collection-1", max_depth=0)
    assert depth_exc.value.error_kind == "collection_depth_exceeded"


def test_collection_fetches_partial_embedded_metadata_authoritatively():
    from galaxy_cli.core.collection import flatten_collection

    partial = _collection_with_elements([{
        "element_identifier": "nested",
        "element_type": "dataset_collection",
        "object": {
            "id": "inner",
            "collection_type": "list",
            "element_count": 2,
            "elements": [_dataset_element("one", "d1")],
        },
    }])
    complete = {
        "id": "inner",
        "collection_type": "list",
        "element_count": 2,
        "elements": [
            _dataset_element("one", "d1"),
            _dataset_element("two", "d2"),
        ],
    }
    client = MagicMock()
    client.get.side_effect = [partial, complete]

    result = flatten_collection(client, "collection-1")

    assert [item["element_path"] for item in result["elements"]] == [
        "nested/one",
        "nested/two",
    ]
    assert client.get.call_count == 2


@pytest.mark.parametrize(
    ("kwargs", "error_kind"),
    [
        ({"max_requests": 1}, "collection_request_limit"),
        ({"max_nodes": 1}, "collection_node_limit"),
    ],
)
def test_collection_traversal_has_request_and_node_budgets(kwargs, error_kind):
    from galaxy_cli.core.collection import flatten_collection
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    root = _collection_with_elements([{
        "element_identifier": "nested",
        "element_type": "dataset_collection",
        "object": {"id": "inner", "collection_type": "list"},
    }])
    client = MagicMock()
    client.get.side_effect = [root, {
        "id": "inner",
        "collection_type": "list",
        "elements": [],
    }]

    with pytest.raises(GalaxyBackendError) as exc:
        flatten_collection(client, "collection-1", **kwargs)
    assert exc.value.error_kind == error_kind
    assert client.get.call_count <= kwargs.get("max_requests", 2)
