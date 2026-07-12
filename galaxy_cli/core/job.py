"""Job management — list, show, cancel, rerun Galaxy jobs."""

import time
import re

from galaxy_cli.core.polling import deadline_after, sleep_for_poll
from galaxy_cli.utils.galaxy_backend import (
    EXIT_SERVER_ERROR,
    EXIT_TIMEOUT,
    GalaxyBackendError,
    get_with_deadline,
)


_JOB_SUCCESS_STATES = {"ok"}
_JOB_PENDING_STATES = {
    "",
    "unknown",
    "new",
    "resubmitted",
    "upload",
    "waiting",
    "queued",
    "running",
    "deleting",
    "stop",
}


def _first_present(mapping, keys, default=""):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def list_jobs(client, history_id=None, state=None, tool_id=None, limit=50, offset=0):
    """List jobs, optionally filtered."""
    params = {"limit": limit, "offset": offset, "order_by": "update_time"}
    if history_id:
        params["history_id"] = history_id
    if state:
        params["state"] = state
    if tool_id:
        params["tool_id"] = tool_id
    jobs = client.get("jobs", params=params)
    return [
        {
            "id": j.get("id", ""),
            "tool_id": j.get("tool_id", ""),
            "state": j.get("state", ""),
            "create_time": j.get("create_time", ""),
            "update_time": j.get("update_time", ""),
            "exit_code": j.get("exit_code"),
        }
        for j in jobs
    ]


def show_job(client, job_id, full=False, logs=False):
    """Show details of a job.

    The default response is intentionally compact for agent use. Expensive
    command lines and logs are included only when `logs` is explicitly true.
    """
    params = {"full": True} if full else {}
    info = client.get(f"jobs/{job_id}", params=params)
    result = {
        "id": info.get("id", ""),
        "tool_id": info.get("tool_id", ""),
        "state": info.get("state", ""),
        "create_time": info.get("create_time", ""),
        "update_time": info.get("update_time", ""),
        "exit_code": info.get("exit_code"),
        "history_id": info.get("history_id", ""),
    }
    if full:
        result["inputs"] = info.get("inputs", {})
        result["outputs"] = info.get("outputs", {})
        result["params"] = info.get("params", {})
    if full or logs:
        result["command_line"] = _first_present(info, ("command_line", "command"))
        result["stdout"] = _first_present(info, ("tool_stdout", "stdout"))
        result["stderr"] = _first_present(info, ("tool_stderr", "stderr"))
    return result


def cancel_job(client, job_id):
    """Cancel a running job."""
    client.delete(f"jobs/{job_id}")
    return {"id": job_id, "status": "cancelled"}


def rerun_job(client, job_id):
    """Get parameters to rerun a job."""
    info = client.get(f"jobs/{job_id}/build_for_rerun")
    return {
        "id": job_id,
        "tool_id": info.get("id", ""),
        "state_inputs": info.get("state_inputs", {}),
    }


def _console_output(client, job_id):
    try:
        raw = client.get(f"jobs/{job_id}/console_output")
    except GalaxyBackendError as exc:
        if exc.status_code not in {404, 405}:
            raise
        raw = client.get(f"jobs/{job_id}", params={"full": True})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "stdout": _first_present(raw, ("stdout", "tool_stdout")),
        "stderr": _first_present(raw, ("stderr", "tool_stderr")),
    }


def job_logs(
    client, job_id, tail=100, pattern=None, context=2, max_chars=12000, full=False
):
    """Return bounded logs while retaining total-size and truncation metadata."""
    logs = _console_output(client, job_id)
    result = {"id": job_id, "streams": {}, "max_chars": max_chars}
    budget = max_chars
    for stream in ("stdout", "stderr"):
        text = str(logs.get(stream) or "")
        lines = text.splitlines()
        if pattern:
            regex = re.compile(pattern)
            indexes = {index for index, line in enumerate(lines) if regex.search(line)}
            selected = sorted({
                nearby
                for index in indexes
                for nearby in range(max(0, index - context), min(len(lines), index + context + 1))
            })
            shown = [lines[index] for index in selected]
        elif full:
            shown = lines
        else:
            shown = lines[-tail:]
        rendered = "\n".join(shown)
        clipped = rendered if budget is None else rendered[:budget]
        if budget is not None:
            budget -= len(clipped)
        result["streams"][stream] = {
            "text": clipped,
            "total_chars": len(text),
            "total_lines": len(lines),
            "truncated": len(clipped) < len(rendered) or len(shown) < len(lines),
        }
    result["truncated"] = any(value["truncated"] for value in result["streams"].values())
    return result


def diagnose_job(client, job_id, max_chars=12000):
    """Return compact failure context without exposing full logs by default."""
    info = show_job(client, job_id)
    logs = job_logs(
        client,
        job_id,
        pattern=r"(?i)(error|exception|failed|fatal|traceback|killed|oom)",
        context=2,
        max_chars=max_chars,
    )
    return {
        "id": job_id,
        "state": info.get("state", ""),
        "exit_code": info.get("exit_code"),
        "tool_id": info.get("tool_id", ""),
        "history_id": info.get("history_id", ""),
        "error_summary": logs["streams"],
        "truncated": logs["truncated"],
    }


def _wait_error_details(
    job_ids,
    jobs,
    history_id,
    tool_id,
    request_ids=None,
    output_ids=None,
):
    details = {
        "job_ids": list(job_ids),
        "jobs": jobs,
        "history_id": history_id or "",
        "tool_id": tool_id or "",
    }
    if request_ids:
        details["request_ids"] = list(request_ids)
    if output_ids:
        details["output_ids"] = list(output_ids)
    return details


def _observed_job_results(ordered_ids, observed, waited):
    jobs = []
    for job_id in ordered_ids:
        job = dict(observed[job_id])
        job["waited_seconds"] = waited
        jobs.append(job)
    return jobs


def _enrich_poll_error(
    exc,
    ordered_ids,
    observed,
    waited,
    history_id,
    tool_id,
    request_ids,
    output_ids,
):
    jobs = _observed_job_results(ordered_ids, observed, waited)
    details = dict(getattr(exc, "details", {}) or {})
    details.update(
        _wait_error_details(
            ordered_ids,
            jobs,
            history_id,
            tool_id,
            request_ids=request_ids,
            output_ids=output_ids,
        )
    )
    return GalaxyBackendError(
        str(exc),
        category=exc.category,
        error_kind=exc.error_kind or "job_status_unavailable",
        exit_code=exc.exit_code,
        suggestion=exc.suggestion,
        status_code=exc.status_code,
        submission_state="submitted",
        retry_safe=False,
        details=details,
    )


def _job_timeout_error(
    ordered_ids,
    observed,
    pending,
    waited,
    history_id,
    tool_id,
    request_ids,
    output_ids,
):
    jobs = _observed_job_results(ordered_ids, observed, waited)
    pending_jobs = [job for job in jobs if job["id"] in pending]
    message = ", ".join(
        f"{job['id']} ({job['state']})" for job in pending_jobs
    ) or "deadline expired while polling job states"
    return GalaxyBackendError(
        f"Timed out waiting for Galaxy jobs: {message}",
        category="timeout",
        error_kind="job_timeout",
        exit_code=EXIT_TIMEOUT,
        submission_state="submitted",
        retry_safe=False,
        details=_wait_error_details(
            ordered_ids,
            jobs,
            history_id,
            tool_id,
            request_ids=request_ids,
            output_ids=output_ids,
        ),
    )


def wait_for_jobs(
    client,
    job_ids,
    timeout=600,
    poll_interval=None,
    *,
    history_id="",
    tool_id="",
    request_ids=None,
    output_ids=None,
    deadline=None,
):
    """Wait for all jobs against one monotonic deadline.

    A blocking result is successful only when every known job is ``ok``.
    Terminal failures and timeouts raise ``GalaxyBackendError`` with the last
    observed state of every job so callers never need follow-up status calls.
    """
    ordered_ids = list(dict.fromkeys(job_id for job_id in job_ids if job_id))
    if not ordered_ids:
        return []

    timeout = max(0.0, float(timeout))
    supplied_deadline = deadline is not None
    started = time.monotonic()
    deadline = deadline_after(timeout) if deadline is None else float(deadline)
    pending = set(ordered_ids)
    observed = {
        job_id: {"id": job_id, "state": "unknown", "exit_code": None}
        for job_id in ordered_ids
    }
    first_poll = True
    poll_attempt = 0

    while pending:
        now = time.monotonic()
        if not first_poll and now >= deadline:
            raise _job_timeout_error(
                ordered_ids,
                observed,
                pending,
                round(max(0.0, now - started), 3),
                history_id,
                tool_id,
                request_ids,
                output_ids,
            )
        first_poll = False
        for job_id in ordered_ids:
            if job_id not in pending:
                continue
            try:
                info = get_with_deadline(
                    client,
                    f"jobs/{job_id}",
                    deadline=deadline if timeout > 0 or supplied_deadline else None,
                )
            except GalaxyBackendError as exc:
                waited = round(max(0.0, time.monotonic() - started), 3)
                if getattr(exc, "error_kind", None) == "request_deadline":
                    raise _job_timeout_error(
                        ordered_ids,
                        observed,
                        pending,
                        waited,
                        history_id,
                        tool_id,
                        request_ids,
                        output_ids,
                    ) from exc
                raise _enrich_poll_error(
                    exc,
                    ordered_ids,
                    observed,
                    waited,
                    history_id,
                    tool_id,
                    request_ids,
                    output_ids,
                ) from exc
            if not isinstance(info, dict):
                info = {}
            state = info.get("state") or "unknown"
            observed[job_id] = {
                "id": job_id,
                "state": state,
                "exit_code": info.get("exit_code"),
            }
            if state not in _JOB_PENDING_STATES:
                pending.remove(job_id)

        now = time.monotonic()
        failed = [
            observed[job_id]
            for job_id in ordered_ids
            if observed[job_id]["state"] not in _JOB_PENDING_STATES
            and observed[job_id]["state"] not in _JOB_SUCCESS_STATES
        ]
        if now >= deadline and pending:
            raise _job_timeout_error(
                ordered_ids,
                observed,
                pending,
                round(max(0.0, now - started), 3),
                history_id,
                tool_id,
                request_ids,
                output_ids,
            )
        if not pending:
            if failed:
                waited = round(max(0.0, now - started), 3)
                jobs = _observed_job_results(ordered_ids, observed, waited)
                raise GalaxyBackendError(
                    "Galaxy job failed: "
                    + ", ".join(
                        f"{job['id']} ({job['state']})" for job in failed
                    ),
                    category="job_failed",
                    error_kind="job_failed",
                    exit_code=EXIT_SERVER_ERROR,
                    submission_state="submitted",
                    retry_safe=False,
                    details=_wait_error_details(
                        ordered_ids,
                        jobs,
                        history_id,
                        tool_id,
                        request_ids=request_ids,
                        output_ids=output_ids,
                    ),
                )
            break

        poll_attempt = sleep_for_poll(
            poll_attempt, deadline, poll_interval=poll_interval
        )

    waited = round(max(0.0, time.monotonic() - started), 3)
    jobs = _observed_job_results(ordered_ids, observed, waited)
    return jobs


def wait_for_job(client, job_id, max_wait=600, poll_interval=None):
    """Backward-compatible single-job wrapper around :func:`wait_for_jobs`."""
    return wait_for_jobs(
        client,
        [job_id],
        timeout=max_wait,
        poll_interval=poll_interval,
    )[0]
