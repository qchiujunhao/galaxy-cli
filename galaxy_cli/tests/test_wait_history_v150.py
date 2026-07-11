"""v1.5 shared job-wait and history-copy readiness tests."""

import inspect
import json
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner


def _cli_runner():
    kwargs = {}
    if "mix_stderr" in inspect.signature(CliRunner).parameters:
        kwargs["mix_stderr"] = False
    return CliRunner(**kwargs)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_for_jobs_returns_all_jobs_in_submission_order():
    from galaxy_cli.core.job import wait_for_jobs

    client = MagicMock()
    states = {
        "job-1": iter(
            [
                {"state": "running", "exit_code": None},
                {"state": "ok", "exit_code": 0},
            ]
        ),
        "job-2": iter(
            [
                {"state": "queued", "exit_code": None},
                {"state": "ok", "exit_code": 0},
            ]
        ),
    }
    client.get.side_effect = lambda path: next(states[path.rsplit("/", 1)[-1]])

    result = wait_for_jobs(
        client,
        ["job-1", "job-2"],
        timeout=10,
        poll_interval=0,
        history_id="history-1",
        tool_id="mapped-tool",
    )

    assert [job["id"] for job in result] == ["job-1", "job-2"]
    assert [job["state"] for job in result] == ["ok", "ok"]
    assert [job["exit_code"] for job in result] == [0, 0]
    assert client.get.call_args_list == [
        call("jobs/job-1"),
        call("jobs/job-2"),
        call("jobs/job-1"),
        call("jobs/job-2"),
    ]


def test_wait_for_jobs_mixed_terminal_states_raise_with_context():
    from galaxy_cli.core.job import wait_for_jobs
    from galaxy_cli.utils.galaxy_backend import EXIT_SERVER_ERROR, GalaxyBackendError

    client = MagicMock()
    client.get.side_effect = [
        {"state": "ok", "exit_code": 0},
        {"state": "failed_metadata", "exit_code": 1},
    ]

    with pytest.raises(GalaxyBackendError) as exc:
        wait_for_jobs(
            client,
            ["job-ok", "job-bad"],
            timeout=10,
            poll_interval=0,
            history_id="history-1",
            tool_id="tool-1",
            request_ids=["request-1"],
            output_ids=["dataset-1"],
        )

    assert exc.value.exit_code == EXIT_SERVER_ERROR
    assert exc.value.error_kind == "job_failed"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details == {
        "job_ids": ["job-ok", "job-bad"],
        "jobs": [
            {"id": "job-ok", "state": "ok", "exit_code": 0, "waited_seconds": 0.0},
            {
                "id": "job-bad",
                "state": "failed_metadata",
                "exit_code": 1,
                "waited_seconds": 0.0,
            },
        ],
        "history_id": "history-1",
        "tool_id": "tool-1",
        "request_ids": ["request-1"],
        "output_ids": ["dataset-1"],
    }


def test_wait_for_jobs_uses_one_deadline_for_all_jobs():
    from galaxy_cli.core.job import wait_for_jobs
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    client = MagicMock()
    states = {
        "job-1": iter([{"state": "running"}, {"state": "ok", "exit_code": 0}]),
        "job-2": iter([{"state": "queued"}, {"state": "running"}]),
    }
    client.get.side_effect = lambda path: next(states[path.rsplit("/", 1)[-1]])
    clock = _Clock()

    with (
        patch("galaxy_cli.core.job.time.monotonic", side_effect=clock.monotonic),
        patch("galaxy_cli.core.job.time.sleep", side_effect=clock.sleep),
        pytest.raises(GalaxyBackendError) as exc,
    ):
        wait_for_jobs(
            client,
            ["job-1", "job-2"],
            timeout=5,
            poll_interval=5,
            history_id="history-1",
            tool_id="mapped-tool",
        )

    assert exc.value.exit_code == EXIT_TIMEOUT
    assert exc.value.error_kind == "job_timeout"
    assert exc.value.details["jobs"] == [
        {
            "id": "job-1",
            "state": "running",
            "exit_code": None,
            "waited_seconds": 5.0,
        },
        {
            "id": "job-2",
            "state": "queued",
            "exit_code": None,
            "waited_seconds": 5.0,
        },
    ]
    assert client.get.call_args_list == [
        call("jobs/job-1"),
        call("jobs/job-2"),
    ]


def test_wait_for_jobs_deadline_with_failure_and_pending_is_timeout():
    from galaxy_cli.core.job import wait_for_jobs
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    client = MagicMock()
    client.get.side_effect = [
        {"state": "error", "exit_code": 1},
        {"state": "running", "exit_code": None},
    ]

    with pytest.raises(GalaxyBackendError) as exc:
        wait_for_jobs(
            client,
            ["job-failed", "job-running"],
            timeout=0,
            poll_interval=1,
            history_id="history-1",
            tool_id="mapped-tool",
        )

    assert exc.value.exit_code == EXIT_TIMEOUT
    assert exc.value.error_kind == "job_timeout"
    assert exc.value.details["jobs"] == [
        {
            "id": "job-failed",
            "state": "error",
            "exit_code": 1,
            "waited_seconds": 0.0,
        },
        {
            "id": "job-running",
            "state": "running",
            "exit_code": None,
            "waited_seconds": 0.0,
        },
    ]
    assert client.get.call_count == 2


def test_wait_for_jobs_reports_failure_after_other_jobs_reach_final_state():
    from galaxy_cli.core.job import wait_for_jobs
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    states = {
        "job-failed": iter([{"state": "error", "exit_code": 1}]),
        "job-running": iter(
            [
                {"state": "running", "exit_code": None},
                {"state": "ok", "exit_code": 0},
            ]
        ),
    }
    client.get.side_effect = lambda path: next(states[path.rsplit("/", 1)[-1]])

    with pytest.raises(GalaxyBackendError) as exc:
        wait_for_jobs(
            client,
            ["job-failed", "job-running"],
            timeout=10,
            poll_interval=0,
            history_id="history-1",
            tool_id="mapped-tool",
        )

    assert exc.value.error_kind == "job_failed"
    assert [job["state"] for job in exc.value.details["jobs"]] == ["error", "ok"]
    assert client.get.call_args_list == [
        call("jobs/job-failed"),
        call("jobs/job-running"),
        call("jobs/job-running"),
    ]


def test_wait_for_jobs_enriches_poll_transport_error_with_known_context():
    from galaxy_cli.core.job import wait_for_jobs
    from galaxy_cli.utils.galaxy_backend import EXIT_SERVER_ERROR, GalaxyBackendError

    client = MagicMock()
    client.get.side_effect = [
        {"state": "running", "exit_code": None},
        {"state": "queued", "exit_code": None},
        GalaxyBackendError(
            "lost connection",
            category="connection",
            exit_code=EXIT_SERVER_ERROR,
        ),
    ]

    with pytest.raises(GalaxyBackendError) as exc:
        wait_for_jobs(
            client,
            ["job-1", "job-2"],
            timeout=10,
            poll_interval=0,
            history_id="history-1",
            tool_id="tool-1",
            request_ids=["request-1"],
            output_ids=["dataset-1"],
        )

    assert exc.value.category == "connection"
    assert exc.value.exit_code == EXIT_SERVER_ERROR
    assert exc.value.error_kind == "job_status_unavailable"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["job_ids"] == ["job-1", "job-2"]
    assert [job["state"] for job in exc.value.details["jobs"]] == [
        "running",
        "queued",
    ]
    assert exc.value.details["history_id"] == "history-1"
    assert exc.value.details["tool_id"] == "tool-1"
    assert exc.value.details["request_ids"] == ["request-1"]
    assert exc.value.details["output_ids"] == ["dataset-1"]


def test_wait_for_job_wrapper_raises_on_timeout():
    from galaxy_cli.core.job import wait_for_job
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    client = MagicMock()
    client.get.return_value = {"state": "running"}

    with pytest.raises(GalaxyBackendError) as exc:
        wait_for_job(client, "job-1", max_wait=0, poll_interval=1)

    assert exc.value.exit_code == EXIT_TIMEOUT
    assert exc.value.details["job_ids"] == ["job-1"]
    assert exc.value.details["jobs"][0]["state"] == "running"


def _copy_response():
    return {
        "id": "history-copy",
        "name": "Copied History",
        "state": "new",
        "create_time": "2026-07-11",
    }


def test_copy_history_no_wait_preserves_v141_shape():
    from galaxy_cli.core.history import copy_history

    client = MagicMock()
    client.post.return_value = _copy_response()

    result = copy_history(
        client,
        "history-source",
        name="Copied History",
        all_datasets=True,
        wait=False,
    )

    assert result == {
        "id": "history-copy",
        "name": "Copied History",
        "state": "new",
        "create_time": "2026-07-11",
        "copied_from_history_id": "history-source",
        "all_datasets": True,
    }
    client.get.assert_not_called()


def test_history_copy_post_transport_error_is_unknown_and_not_retry_safe():
    from galaxy_cli.core.history import copy_history
    from galaxy_cli.utils.galaxy_backend import EXIT_SERVER_ERROR, GalaxyBackendError

    client = MagicMock()
    client.post.side_effect = GalaxyBackendError(
        "lost during history copy",
        category="connection",
        exit_code=EXIT_SERVER_ERROR,
    )

    with pytest.raises(GalaxyBackendError) as exc:
        copy_history(client, "history-source", wait=True)

    assert exc.value.category == "connection"
    assert exc.value.exit_code == EXIT_SERVER_ERROR
    assert exc.value.error_kind == "history_copy_submission_unknown"
    assert exc.value.submission_state == "unknown"
    assert exc.value.retry_safe is False
    assert exc.value.details["history_id"] == ""
    assert exc.value.details["copied_from_history_id"] == "history-source"


def test_history_copy_response_without_id_is_unknown_and_not_retry_safe():
    from galaxy_cli.core.history import copy_history
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.post.return_value = {"name": "Copy without ID"}

    with pytest.raises(GalaxyBackendError) as exc:
        copy_history(client, "history-source", wait=True)

    assert exc.value.error_kind == "history_copy_response_invalid"
    assert exc.value.submission_state == "unknown"
    assert exc.value.retry_safe is False
    assert exc.value.details["copied_from_history_id"] == "history-source"


def test_copy_history_wait_returns_compact_ready_contents():
    from galaxy_cli.core.history import copy_history

    client = MagicMock()
    client.post.return_value = _copy_response()
    client.get.return_value = [
        {
            "hid": 1,
            "id": "dataset-1",
            "history_content_type": "dataset",
            "name": "matrix.tsv",
            "state": "ok",
            "extension": "tabular",
        },
        {
            "hid": 2,
            "id": "collection-1",
            "history_content_type": "dataset_collection",
            "name": "samples",
            "state": "ok",
            "collection_type": "list",
            "element_count": 3,
        },
    ]

    result = copy_history(
        client,
        "history-source",
        name="Copied History",
        wait=True,
        timeout=10,
        poll_interval=0,
    )

    assert result["contents"] == [
        {
            "hid": 1,
            "id": "dataset-1",
            "src": "hda",
            "name": "matrix.tsv",
            "state": "ok",
            "extension": "tabular",
        },
        {
            "hid": 2,
            "id": "collection-1",
            "src": "hdca",
            "name": "samples",
            "state": "ok",
            "collection_type": "list",
            "element_count": 3,
        },
    ]
    client.get.assert_called_once_with("histories/history-copy/contents")


def test_history_copy_rejects_malformed_content_record():
    from galaxy_cli.core.history import copy_history
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.post.return_value = _copy_response()
    client.get.return_value = [
        {"id": "dataset-1", "hid": 1, "state": "ok", "extension": "txt"},
        None,
    ]

    with pytest.raises(GalaxyBackendError) as exc:
        copy_history(client, "history-source", wait=True)

    assert exc.value.error_kind == "history_copy_response_invalid"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["content_ids"] == ["dataset-1"]


def test_copy_history_waits_for_delayed_dataset_and_collection():
    from galaxy_cli.core.history import copy_history

    client = MagicMock()
    client.post.return_value = _copy_response()
    client.get.side_effect = [
        [
            {
                "hid": 1,
                "id": "dataset-1",
                "state": "setting_metadata",
                "extension": "tabular",
            },
            {
                "hid": 2,
                "id": "collection-1",
                "history_content_type": "dataset_collection",
                "state": "new",
                "collection_type": "list",
                "element_count": 0,
            },
        ],
        [
            {
                "hid": 1,
                "id": "dataset-1",
                "state": "ok",
                "extension": "tabular",
            },
            {
                "hid": 2,
                "id": "collection-1",
                "history_content_type": "dataset_collection",
                "populated_state": "ok",
                "collection_type": "list",
                "element_count": 2,
            },
        ],
    ]

    result = copy_history(
        client,
        "history-source",
        wait=True,
        timeout=10,
        poll_interval=0,
    )

    assert [content["state"] for content in result["contents"]] == ["ok", "ok"]
    assert client.get.call_count == 2


def test_copy_history_all_datasets_accepts_preserved_deleted_and_purged_items():
    from galaxy_cli.core.history import copy_history

    client = MagicMock()
    client.post.return_value = _copy_response()
    client.get.return_value = [
        {
            "hid": 1,
            "id": "dataset-1",
            "state": "discarded",
            "extension": "data",
            "deleted": True,
            "purged": True,
        },
        {
            "hid": 2,
            "id": "collection-1",
            "history_content_type": "dataset_collection",
            "state": "deleted",
            "collection_type": "list",
            "element_count": 1,
            "deleted": True,
        },
    ]

    result = copy_history(
        client,
        "history-source",
        all_datasets=True,
        wait=True,
        timeout=0,
        poll_interval=0,
    )

    assert result["contents"][0]["state"] == "discarded"
    assert result["contents"][0]["deleted"] is True
    assert result["contents"][0]["purged"] is True
    assert result["contents"][1]["state"] == "deleted"
    assert result["contents"][1]["deleted"] is True


@pytest.mark.parametrize(
    "content",
    [
        {"hid": 1, "id": "dataset-1", "state": "failed_metadata"},
        {
            "hid": 1,
            "id": "collection-1",
            "history_content_type": "dataset_collection",
            "state": "failed",
            "collection_type": "list",
        },
    ],
)
def test_copy_history_content_failure_is_structured(content):
    from galaxy_cli.core.history import copy_history
    from galaxy_cli.utils.galaxy_backend import EXIT_SERVER_ERROR, GalaxyBackendError

    client = MagicMock()
    client.post.return_value = _copy_response()
    client.get.return_value = [content]

    with pytest.raises(GalaxyBackendError) as exc:
        copy_history(client, "history-source", wait=True, timeout=10, poll_interval=0)

    assert exc.value.exit_code == EXIT_SERVER_ERROR
    assert exc.value.error_kind == "history_copy_failed"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["history_id"] == "history-copy"
    assert exc.value.details["contents"][0]["state"] == content["state"]


def test_copy_history_timeout_raises_with_last_content_states():
    from galaxy_cli.core.history import copy_history
    from galaxy_cli.utils.galaxy_backend import EXIT_TIMEOUT, GalaxyBackendError

    client = MagicMock()
    client.post.return_value = _copy_response()
    client.get.return_value = [
        {
            "hid": 1,
            "id": "dataset-1",
            "state": "running",
            "extension": "tabular",
        }
    ]

    with pytest.raises(GalaxyBackendError) as exc:
        copy_history(client, "history-source", wait=True, timeout=0, poll_interval=1)

    assert exc.value.exit_code == EXIT_TIMEOUT
    assert exc.value.error_kind == "history_copy_timeout"
    assert exc.value.details["contents"][0]["state"] == "running"


def test_history_copy_cli_wait_default_keeps_progress_off_stdout():
    from galaxy_cli.cli import cli

    copied = {
        "id": "history-copy",
        "name": "Copied History",
        "state": "ok",
        "create_time": "",
        "copied_from_history_id": "history-source",
        "all_datasets": False,
        "contents": [],
    }
    runner = _cli_runner()

    with (
        patch("galaxy_cli.cli._get_client", return_value=object()),
        patch("galaxy_cli.cli.history_mod.copy_history", return_value=copied) as copy,
        patch("galaxy_cli.cli.session_mod.set_current_history"),
    ):
        result = runner.invoke(
            cli,
            ["--json", "history", "copy", "history-source", "Copied History"],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == copied
    assert result.stdout == json.dumps(copied, separators=(",", ":")) + "\n"
    assert result.stderr == "Waiting for copied history contents from history-source...\n"
    assert copy.call_args.kwargs["wait"] is True


def test_history_copy_cli_no_wait_preserves_immediate_mode():
    from galaxy_cli.cli import cli

    copied = {
        "id": "history-copy",
        "name": "Copied History",
        "state": "new",
        "create_time": "",
        "copied_from_history_id": "history-source",
        "all_datasets": False,
    }
    runner = _cli_runner()

    with (
        patch("galaxy_cli.cli._get_client", return_value=object()),
        patch("galaxy_cli.cli.history_mod.copy_history", return_value=copied) as copy,
        patch("galaxy_cli.cli.session_mod.set_current_history"),
    ):
        result = runner.invoke(
            cli,
            [
                "--json",
                "history",
                "copy",
                "history-source",
                "Copied History",
                "--no-wait",
            ],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == copied
    assert result.stderr == ""
    assert copy.call_args.kwargs["wait"] is False


def test_upload_dataset_waits_for_all_submission_jobs():
    from galaxy_cli.core.dataset import upload_dataset

    client = MagicMock()
    client.upload_file.return_value = {
        "jobs": [{"id": "job-1"}, {"id": "job-2"}],
        "outputs": [
            {
                "id": "dataset-1",
                "name": "matrix.tsv",
                "state": "queued",
                "extension": "tabular",
            }
        ],
    }
    client.get.side_effect = [
        {"state": "ok", "exit_code": 0},
        {"state": "ok", "exit_code": 0},
        {
            "id": "dataset-1",
            "name": "matrix.tsv",
            "state": "ok",
            "extension": "tabular",
            "file_size": 10,
        },
    ]

    result = upload_dataset(
        client,
        "history-1",
        "matrix.tsv",
        wait=True,
        timeout=10,
        poll_interval=0,
    )

    assert [job["id"] for job in result["wait_results"]] == ["job-1", "job-2"]
    assert result["state"] == "ok"
    assert client.get.call_args_list[:2] == [call("jobs/job-1"), call("jobs/job-2")]


def test_upload_output_refresh_error_keeps_completed_job_context():
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.upload_file.return_value = {
        "jobs": [{"id": "job-1"}],
        "outputs": [{"id": "dataset-1", "name": "matrix.tsv"}],
    }
    client.get.side_effect = [
        {"state": "ok", "exit_code": 0},
        GalaxyBackendError("refresh failed", category="connection"),
    ]

    with pytest.raises(GalaxyBackendError) as exc:
        upload_dataset(
            client,
            "history-1",
            "matrix.tsv",
            wait=True,
            timeout=10,
            poll_interval=0,
        )

    assert exc.value.error_kind == "upload_output_refresh_failed"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["jobs"][0]["state"] == "ok"
    assert exc.value.details["output_ids"] == ["dataset-1"]


def test_blocking_upload_rejects_empty_submission_response():
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.upload_file.return_value = {"jobs": [], "outputs": []}

    with pytest.raises(GalaxyBackendError) as exc:
        upload_dataset(client, "history-1", "matrix.tsv", wait=True)

    assert exc.value.error_kind == "unknown_submission_state"
    assert exc.value.submission_state == "unknown"
    assert exc.value.retry_safe is False
    assert exc.value.details["history_id"] == "history-1"
    assert "raw" not in exc.value.to_dict()


def test_blocking_upload_rejects_malformed_submission_members():
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.upload_file.return_value = {
        "jobs": [{"id": "job-1"}, None],
        "outputs": [{"id": "dataset-1"}],
    }

    with pytest.raises(GalaxyBackendError) as exc:
        upload_dataset(client, "history-1", "matrix.tsv", wait=True)

    assert exc.value.error_kind == "upload_response_invalid"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["job_ids"] == ["job-1"]
    assert exc.value.details["output_ids"] == ["dataset-1"]


def test_blocking_upload_waits_known_jobs_before_reporting_missing_output():
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import GalaxyBackendError

    client = MagicMock()
    client.upload_file.return_value = {
        "jobs": [{"id": "job-1", "state": "new"}],
        "outputs": [],
    }
    client.get.return_value = {"state": "ok", "exit_code": 0}

    with pytest.raises(GalaxyBackendError) as exc:
        upload_dataset(
            client,
            "history-1",
            "matrix.tsv",
            wait=True,
            timeout=10,
            poll_interval=0,
        )

    assert exc.value.error_kind == "upload_outputs_missing"
    assert exc.value.submission_state == "submitted"
    assert exc.value.retry_safe is False
    assert exc.value.details["job_ids"] == ["job-1"]
    assert exc.value.details["jobs"][0]["state"] == "ok"


def test_upload_post_transport_error_is_unknown_and_not_retry_safe():
    from galaxy_cli.core.dataset import upload_dataset
    from galaxy_cli.utils.galaxy_backend import EXIT_SERVER_ERROR, GalaxyBackendError

    client = MagicMock()
    client.upload_file.side_effect = GalaxyBackendError(
        "lost during upload",
        category="connection",
        exit_code=EXIT_SERVER_ERROR,
    )

    with pytest.raises(GalaxyBackendError) as exc:
        upload_dataset(client, "history-1", "matrix.tsv", wait=True)

    assert exc.value.category == "connection"
    assert exc.value.exit_code == EXIT_SERVER_ERROR
    assert exc.value.error_kind == "upload_submission_unknown"
    assert exc.value.submission_state == "unknown"
    assert exc.value.retry_safe is False
    assert exc.value.details["history_id"] == "history-1"
    assert exc.value.details["tool_id"] == "upload1"


def test_peek_dataset_bounds_the_primary_raw_data_request():
    from galaxy_cli.core.dataset import peek_dataset

    client = MagicMock()
    client.get.return_value = {"data": []}

    peek_dataset(client, "dataset-1", lines=5)

    client.get.assert_called_once_with(
        "datasets/dataset-1",
        params={
            "data_type": "raw_data",
            "provider": "base",
            "offset": 0,
            "limit": 5,
        },
    )
