"""Focused v1.5 tests for strict/auto tool execution."""

import json
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _client_with_tool(tool):
    client = MagicMock()
    client.get.return_value = tool
    return client


def test_execution_plan_preserves_nested_repeat_conditional_and_types():
    from galaxy_cli.core.tool import build_tool_execution_plan, execution_plan_for_output

    trace = _fixture("nested_repeat_conditional_tool.json")
    client = _client_with_tool(trace["tool"])

    plan = build_tool_execution_plan(
        client,
        trace["tool"]["id"],
        "history-1",
        trace["inputs"],
        execution_backend="auto",
    )

    assert execution_plan_for_output(plan) == {
        "requested_execution_backend": "auto",
        "execution_backend": "strict",
        "endpoint": "/api/jobs",
        "post_body": plan["post_body"],
    }
    body = plan["post_body"]
    assert body["tool_version"] == "1.2.3"
    assert body["strict"] is True
    assert body["send_email_notification"] is False
    assert "|" not in json.dumps(body["inputs"])
    first = body["inputs"]["groups"][0]["mode_cond"]
    assert first["reads"] == [
        {"src": "hda", "id": "dataset-a"},
        {"src": "hda", "id": "dataset-b"},
    ]
    assert first["trim"] == 5
    assert first["enabled"] is True
    assert body["inputs"]["groups"][1]["mode_cond"]["reads_collection"] == {
        "src": "hdca",
        "id": "collection-a",
    }
    assert plan["_legacy_fallback"]["post_body"]["inputs"][
        "groups_0|mode_cond|reads"
    ][0]["id"] == "dataset-a"


def test_execution_plan_re_nests_pipe_keys_schema_safely():
    from galaxy_cli.core.tool import build_tool_execution_plan

    trace = _fixture("nested_repeat_conditional_tool.json")
    client = _client_with_tool(trace["tool"])
    inputs = {
        "groups_0|mode_cond|mode": "datasets",
        "groups_0|mode_cond|reads": [{"src": "hda", "id": "dataset-a"}],
        "groups_0|mode_cond|trim": "7",
        "groups_0|mode_cond|enabled": "false",
    }

    plan = build_tool_execution_plan(client, trace["tool"]["id"], "history-1", inputs)

    strict = plan["post_body"]["inputs"]["groups"][0]["mode_cond"]
    assert strict == {
        "mode": "datasets",
        "reads": [{"src": "hda", "id": "dataset-a"}],
        "trim": 7,
        "enabled": False,
    }


def test_strict_repeat_required_data_is_recognized_after_indexing():
    from galaxy_cli.core.tool import build_tool_execution_plan

    tool = {
        "id": "repeat-data",
        "name": "Repeat data",
        "version": "1.0",
        "inputs": [
            {
                "name": "items",
                "type": "repeat",
                "min": 1,
                "inputs": [
                    {"name": "input", "type": "data", "optional": False}
                ],
            }
        ],
        "outputs": [],
    }
    client = _client_with_tool(tool)

    plan = build_tool_execution_plan(
        client,
        "repeat-data",
        "history-1",
        {"items": [{"input": {"src": "hda", "id": "dataset-1"}}]},
    )

    assert plan["post_body"]["inputs"] == {
        "items": [{"input": {"src": "hda", "id": "dataset-1"}}]
    }


def test_strict_boolean_conditional_normalizes_selected_branch():
    from galaxy_cli.core.tool import build_tool_execution_plan

    tool = {
        "id": "boolean-cond",
        "name": "Boolean conditional",
        "version": "1.0",
        "inputs": [
            {
                "name": "settings",
                "type": "conditional",
                "test_param": {"name": "enabled", "type": "boolean"},
                "cases": [
                    {
                        "value": "true",
                        "inputs": [
                            {"name": "count", "type": "integer", "optional": False}
                        ],
                    },
                    {"value": "false", "inputs": []},
                ],
            }
        ],
        "outputs": [],
    }
    client = _client_with_tool(tool)

    plan = build_tool_execution_plan(
        client,
        "boolean-cond",
        "history-1",
        {"settings": {"enabled": "true", "count": "4"}},
    )

    assert plan["post_body"]["inputs"]["settings"] == {
        "enabled": True,
        "count": 4,
    }


def test_mapped_batch_plan_uses_strict_batch_and_legacy_batch():
    from galaxy_cli.core.tool import build_tool_execution_plan

    trace = _fixture("mapped_tool_multi_job.json")
    client = _client_with_tool(trace["tool"])

    plan = build_tool_execution_plan(
        client, trace["tool"]["id"], "history-1", trace["inputs"]
    )

    assert plan["post_body"]["inputs"]["input"] == trace["inputs"]["input"]
    assert plan["_legacy_fallback"]["post_body"]["inputs"]["input"] == {
        "batch": True,
        "values": [{"src": "hdca", "id": "input-collection"}],
    }


def test_strict_mapped_run_waits_all_jobs_and_normalizes_all_outputs():
    from galaxy_cli.core.tool import run_tool

    trace = _fixture("mapped_tool_multi_job.json")
    client = MagicMock()

    def get(path, params=None):
        if path == f"tools/{trace['tool']['id']}":
            return trace["tool"]
        if path == "tool_requests/request-1/state":
            return "submitted"
        if path == "tool_requests/request-1":
            return trace["request_detail"]
        if path.startswith("jobs/"):
            return trace["jobs"][path.split("/", 1)[1]]
        if path.startswith("histories/history-1/contents/dataset_collections/"):
            return trace["output_details"][path.rsplit("/", 1)[1]]
        if path.startswith("histories/history-1/contents/"):
            return trace["output_details"][path.rsplit("/", 1)[1]]
        raise AssertionError((path, params))

    client.get.side_effect = get
    client.post.return_value = trace["submit_response"]

    result = run_tool(
        client,
        trace["tool"]["id"],
        "history-1",
        trace["inputs"],
        wait=True,
        timeout=5,
        poll_interval=0,
    )

    assert result["success"] is True
    assert result["state"] == "ok"
    assert result["execution_backend"] == "strict"
    assert result["tool_request_id"] == "request-1"
    assert result["tool_version"] == "2.0.0"
    assert result["jobs"] == [
        {"id": "job-1", "state": "ok", "exit_code": 0},
        {"id": "job-2", "state": "ok", "exit_code": 0},
    ]
    assert "wait_result" not in result
    assert len(result["wait_results"]) == 2
    by_id = {output["id"]: output for output in result["outputs"]}
    assert by_id["dataset-1"] | {
        "output_name": "out",
        "src": "hda",
        "state": "ok",
        "extension": "txt",
        "file_size": 11,
    } == by_id["dataset-1"]
    assert by_id["dataset-2"]["file_size"] == 12
    assert by_id["output-collection"] | {
        "output_name": "out",
        "src": "hdca",
        "state": "ok",
        "collection_type": "list",
        "element_count": 2,
    } == by_id["output-collection"]
    assert client.get.call_args_list.count(call("jobs/job-1")) == 2
    assert client.get.call_args_list.count(call("jobs/job-2")) == 2


def test_strict_mixed_job_failure_reports_all_states_and_known_outputs():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    trace = _fixture("mapped_tool_multi_job.json")
    client = MagicMock()
    jobs = {
        "job-1": trace["jobs"]["job-1"],
        "job-2": dict(trace["jobs"]["job-2"], state="error", exit_code=1),
    }

    def get(path, params=None):
        if path == f"tools/{trace['tool']['id']}":
            return trace["tool"]
        if path == "tool_requests/request-1/state":
            return "submitted"
        if path == "tool_requests/request-1":
            return trace["request_detail"]
        if path.startswith("jobs/"):
            return jobs[path.split("/", 1)[1]]
        raise AssertionError((path, params))

    client.get.side_effect = get
    client.post.return_value = trace["submit_response"]

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(
            client,
            trace["tool"]["id"],
            "history-1",
            trace["inputs"],
            wait=True,
            timeout=5,
            poll_interval=0,
        )

    error = exc.value.to_dict()
    assert error["error_kind"] == "job_failed"
    assert error["submission_state"] == "submitted"
    assert error["retry_safe"] is False
    assert error["job_ids"] == ["job-1", "job-2"]
    assert [job["state"] for job in error["jobs"]] == ["ok", "error"]
    assert set(error["output_ids"]) == {
        "dataset-1",
        "dataset-2",
        "output-collection",
    }
    assert client.post.call_count == 1


def test_strict_job_timeout_is_nonzero_structured_and_never_resubmits():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    trace = _fixture("mapped_tool_multi_job.json")
    client = MagicMock()
    running_jobs = {
        job_id: dict(job, state="running", exit_code=None)
        for job_id, job in trace["jobs"].items()
    }

    def get(path, params=None):
        if path == f"tools/{trace['tool']['id']}":
            return trace["tool"]
        if path == "tool_requests/request-1/state":
            return "submitted"
        if path == "tool_requests/request-1":
            return trace["request_detail"]
        if path.startswith("jobs/"):
            return running_jobs[path.split("/", 1)[1]]
        raise AssertionError((path, params))

    client.get.side_effect = get
    client.post.return_value = trace["submit_response"]

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(
            client,
            trace["tool"]["id"],
            "history-1",
            trace["inputs"],
            wait=True,
            timeout=0,
            poll_interval=0,
        )

    error = exc.value.to_dict()
    assert exc.value.exit_code == EXIT_TIMEOUT
    assert error["category"] == "timeout"
    assert error["submission_state"] == "submitted"
    assert [job["state"] for job in error["jobs"]] == ["running", "running"]
    assert client.post.call_count == 1


def test_strict_no_wait_returns_request_id_without_polling():
    from galaxy_cli.core.tool import build_tool_execution_plan, run_tool

    trace = _fixture("mapped_tool_multi_job.json")
    client = _client_with_tool(trace["tool"])
    plan = build_tool_execution_plan(
        client, trace["tool"]["id"], "history-1", trace["inputs"]
    )
    client.get.reset_mock()
    client.post.return_value = trace["submit_response"]

    result = run_tool(
        client,
        trace["tool"]["id"],
        "history-1",
        wait=False,
        plan=plan,
    )

    assert result["state"] == "submitted"
    assert result["tool_request_id"] == "request-1"
    assert result["jobs"] == []
    assert result["outputs"] == []
    client.get.assert_not_called()


def test_strict_blocking_request_with_no_spawned_jobs_is_not_success():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    trace = _fixture("mapped_tool_multi_job.json")
    client = MagicMock()

    def get(path, params=None):
        if path == f"tools/{trace['tool']['id']}":
            return trace["tool"]
        if path == "tool_requests/request-1/state":
            return "submitted"
        if path == "tool_requests/request-1":
            return {"id": "request-1", "state": "submitted", "jobs": [], "implicit_collections": []}
        raise AssertionError((path, params))

    client.get.side_effect = get
    client.post.return_value = trace["submit_response"]

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(
            client,
            trace["tool"]["id"],
            "history-1",
            trace["inputs"],
            wait=True,
            timeout=5,
            poll_interval=0,
        )

    assert exc.value.error_kind == "unexpected_response"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False


def test_legacy_blocking_response_with_no_jobs_is_unknown_not_success():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = {"id": "empty", "name": "Empty", "version": "1", "inputs": [], "outputs": []}
    client = _client_with_tool(tool)
    client.post.return_value = {"jobs": [], "outputs": []}

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(
            client,
            "empty",
            "history-1",
            execution_backend="legacy",
            wait=True,
        )

    assert exc.value.error_kind == "unexpected_response"
    assert exc.value.submission_state == "unknown"
    assert exc.value.retry_safe is False


@pytest.mark.parametrize("status", [404, 405])
def test_auto_safely_falls_back_only_for_unsupported_endpoint(status):
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    trace = _fixture("mapped_tool_multi_job.json")
    client = _client_with_tool(trace["tool"])
    endpoint_error = GalaxyBackendError(
        f"Galaxy API error ({status}): Not Found" if status == 404 else "Method Not Allowed",
        category="not_found" if status == 404 else "method_not_allowed",
        status_code=status,
    )
    client.post.side_effect = [
        endpoint_error,
        {
            "jobs": [{"id": "legacy-job", "state": "new"}],
            "outputs": [
                {"id": "legacy-output", "output_name": "out", "name": "output", "extension": "txt"}
            ],
        },
    ]

    result = run_tool(
        client, trace["tool"]["id"], "history-1", trace["inputs"], wait=False
    )

    assert result["execution_backend"] == "legacy"
    assert result["state"] == "submitted"
    assert client.post.call_args_list[0].args[0] == "jobs"
    assert client.post.call_args_list[1].args[0] == "tools"
    assert client.post.call_count == 2


@pytest.mark.parametrize(
    "status,category",
    [(400, "invalid_request"), (422, "invalid_request"), (500, "server_error"), (None, "timeout")],
)
def test_auto_never_resubmits_on_rejection_timeout_or_server_error(status, category):
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    trace = _fixture("mapped_tool_multi_job.json")
    client = _client_with_tool(trace["tool"])
    client.post.side_effect = GalaxyBackendError(
        "strict submission failed",
        category=category,
        status_code=status,
    )

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(client, trace["tool"]["id"], "history-1", trace["inputs"])

    assert client.post.call_count == 1
    if status in {400, 422}:
        assert exc.value.submission_state == "not_submitted"
        assert exc.value.retry_safe is True
    else:
        assert exc.value.submission_state == "unknown"
        assert exc.value.retry_safe is False


def test_auto_does_not_fallback_for_tool_specific_404():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    trace = _fixture("mapped_tool_multi_job.json")
    client = _client_with_tool(trace["tool"])
    client.post.side_effect = GalaxyBackendError(
        "Galaxy API not found (404): tool not found",
        category="not_found",
        status_code=404,
    )

    with pytest.raises(GalaxyBackendError):
        run_tool(client, trace["tool"]["id"], "history-1", trace["inputs"])

    assert client.post.call_count == 1


def test_forced_strict_never_falls_back_for_endpoint_404():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    trace = _fixture("mapped_tool_multi_job.json")
    client = _client_with_tool(trace["tool"])
    client.post.side_effect = GalaxyBackendError(
        "Galaxy API not found (404): Not Found",
        category="not_found",
        status_code=404,
    )

    with pytest.raises(GalaxyBackendError):
        run_tool(
            client,
            trace["tool"]["id"],
            "history-1",
            trace["inputs"],
            execution_backend="strict",
        )

    assert client.post.call_count == 1


def test_legacy_blocking_result_includes_implicit_collection_and_single_wait_result():
    from galaxy_cli.core.tool import run_tool

    tool = {
        "id": "legacy-tool",
        "name": "Legacy tool",
        "version": "1.0",
        "description": "",
        "inputs": [],
        "outputs": [],
    }
    client = _client_with_tool(tool)
    client.post.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [{"id": "dataset-1", "output_name": "out", "name": "out", "extension": "txt"}],
        "implicit_collections": [
            {"id": "collection-1", "output_name": "mapped", "name": "mapped", "collection_type": "list"}
        ],
    }

    def get(path, params=None):
        if path == "jobs/job-1":
            return {"id": "job-1", "state": "ok", "exit_code": 0}
        if path.endswith("dataset-1"):
            return {"id": "dataset-1", "name": "out", "state": "ok", "extension": "txt", "file_size": 4}
        if path.endswith("collection-1"):
            return {"id": "collection-1", "name": "mapped", "populated_state": "ok", "collection_type": "list", "element_count": 1}
        return tool

    client.get.side_effect = get

    result = run_tool(
        client,
        "legacy-tool",
        "history-1",
        execution_backend="legacy",
        wait=True,
        timeout=5,
        poll_interval=0,
    )

    assert result["state"] == "ok"
    assert result["wait_result"]["id"] == "job-1"
    assert {(output["src"], output["id"]) for output in result["outputs"]} == {
        ("hda", "dataset-1"),
        ("hdca", "collection-1"),
    }


def test_legacy_error_with_known_output_is_not_retry_safe():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = {
        "id": "legacy-tool",
        "name": "Legacy tool",
        "version": "1.0",
        "inputs": [],
        "outputs": [],
    }
    client = _client_with_tool(tool)
    client.post.return_value = {
        "jobs": [],
        "outputs": [{"id": "dataset-1", "name": "partial"}],
        "errors": [{"err_msg": "expansion failed"}],
    }

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(
            client,
            "legacy-tool",
            "history-1",
            execution_backend="legacy",
        )

    payload = exc.value.to_dict()
    assert payload["submission_state"] == "submitted"
    assert payload["retry_safe"] is False
    assert payload["output_ids"] == ["dataset-1"]


def test_legacy_partial_error_waits_known_jobs_to_final_state():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = {
        "id": "legacy-tool",
        "name": "Legacy tool",
        "version": "1.0",
        "inputs": [],
        "outputs": [],
    }
    states = iter(
        [
            {"id": "job-1", "state": "running", "exit_code": None},
            {"id": "job-1", "state": "ok", "exit_code": 0},
        ]
    )
    client = MagicMock()
    client.get.side_effect = lambda path, params=None: (
        tool if path == "tools/legacy-tool" else next(states)
    )
    client.post.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [{"id": "dataset-1", "name": "partial"}],
        "errors": [{"err_msg": "one mapped element was rejected"}],
    }

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(
            client,
            "legacy-tool",
            "history-1",
            execution_backend="legacy",
            wait=True,
            timeout=5,
            poll_interval=0,
        )

    payload = exc.value.to_dict()
    assert payload["error_kind"] == "tool_request_rejected"
    assert payload["jobs"] == [
        {"id": "job-1", "state": "ok", "exit_code": 0}
    ]
    assert payload["retry_safe"] is False


def test_strict_failed_request_reports_known_jobs_and_outputs():
    from galaxy_cli.core.tool import run_tool
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = {
        "id": "strict-tool",
        "name": "Strict tool",
        "version": "1.0",
        "inputs": [],
        "outputs": [],
    }
    client = MagicMock()

    def get(path, params=None):
        if path == "tools/strict-tool":
            return tool
        if path == "tool_requests/request-1/state":
            return "failed"
        if path == "tool_requests/request-1":
            return {
                "state_message": "expansion failed",
                "jobs": [{"id": "job-1"}],
                "implicit_collections": [
                    {"id": "collection-1", "src": "hdca", "output_name": "out"}
                ],
            }
        if path == "jobs/job-1":
            return {
                "id": "job-1",
                "state": "error",
                "exit_code": 1,
                "outputs": {"out": {"id": "dataset-1", "src": "hda"}},
            }
        raise AssertionError((path, params))

    client.get.side_effect = get
    client.post.return_value = {"tool_request_id": "request-1"}

    with pytest.raises(GalaxyBackendError) as exc:
        run_tool(
            client,
            "strict-tool",
            "history-1",
            wait=True,
            timeout=5,
            poll_interval=0,
        )

    payload = exc.value.to_dict()
    assert payload["submission_state"] == "submitted"
    assert payload["retry_safe"] is False
    assert payload["request_ids"] == ["request-1"]
    assert payload["job_ids"] == ["job-1"]
    assert payload["jobs"] == [
        {
            "id": "job-1",
            "state": "error",
            "exit_code": 1,
            "waited_seconds": 0.0,
        }
    ]
    assert set(payload["output_ids"]) == {"dataset-1", "collection-1"}


def test_tool_show_compact_cache_is_versioned_refreshable_and_secret_free(tmp_path, monkeypatch):
    from galaxy_cli.core import tool as tool_mod

    monkeypatch.setattr(tool_mod, "DEFAULT_CONFIG_DIR", tmp_path)
    client = MagicMock()
    client.url = "https://galaxy.example.org/"
    client.api_key = "secret-api-key"
    client.get_version.return_value = {"version_major": "26.0", "version_minor": "1"}
    first = {
        "id": "exact/tool/1.0",
        "name": "Cached tool",
        "version": "1.0",
        "description": "",
        "inputs": [],
        "outputs": [],
    }
    second = dict(first, id="exact/tool/1.1", version="1.1")
    client.get.return_value = first

    assert tool_mod.show_tool(client, "tool") == tool_mod.show_tool(client, "tool")
    assert client.get.call_count == 1
    client.get.return_value = second
    refreshed = tool_mod.show_tool(client, "tool", refresh_cache=True)
    assert refreshed["version"] == "1.1"
    assert client.get.call_count == 2
    uncached = tool_mod.show_tool(client, "tool", use_cache=False)
    assert uncached["version"] == "1.1"
    assert client.get.call_count == 3

    plan = tool_mod.build_tool_execution_plan(client, "tool", "history-1")
    assert client.get.call_count == 4
    assert plan["post_body"]["tool_id"] == "exact/tool/1.1"
    assert plan["post_body"]["tool_version"] == "1.1"
    assert plan["_legacy_fallback"]["post_body"]["tool_id"] == "tool"

    client.post.return_value = {"tool_request_id": "request-1"}
    result = tool_mod.run_tool(
        client,
        "tool",
        "history-1",
        plan=plan,
        wait=False,
    )
    assert result["tool_id"] == "exact/tool/1.1"
    assert result["requested_tool_id"] == "tool"
    assert result["tool_version"] == "1.1"

    cache_files = list((tmp_path / "tool-cache" / "show").glob("*.json"))
    assert len(cache_files) == 2
    cache_text = "".join(path.read_text() for path in cache_files)
    assert "secret-api-key" not in cache_text
    assert '"tool_version":"1.0"' in cache_text
    assert '"tool_version":"1.1"' in cache_text


def test_local_validation_error_is_compact_and_structured():
    from galaxy_cli.core.tool import build_tool_execution_plan
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    tool = {
        "id": "select-tool",
        "name": "Select tool",
        "version": "1.0",
        "description": "",
        "inputs": [
            {
                "name": "mode",
                "label": "Mode",
                "type": "select",
                "options": [["A", "a", True], ["B", "b", False]]
            }
        ],
        "outputs": [],
    }
    client = _client_with_tool(tool)

    with pytest.raises(GalaxyBackendError) as exc:
        build_tool_execution_plan(client, "select-tool", "history-1", {"mode": "wrong"})

    payload = exc.value.to_dict()
    assert payload["validation"] == {
        "path": "$.inputs.mode",
        "expected": "allowed select value",
        "allowed_values": ["a", "b"],
        "example": "a",
    }
    assert "schema" not in json.dumps(payload).lower()
    assert "wrong" not in json.dumps(payload)


def test_blocking_output_metadata_must_be_a_mapping():
    from galaxy_cli.core.tool import refresh_output_details
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.get.return_value = []

    with pytest.raises(GalaxyBackendError) as exc:
        refresh_output_details(
            client,
            "history-1",
            [{"output_name": "out", "id": "dataset-1", "src": "hda"}],
        )

    assert exc.value.error_kind == "unexpected_response"
    assert exc.value.submission_state == "submitted"
    assert exc.value.details["output_ids"] == ["dataset-1"]
