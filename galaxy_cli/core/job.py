"""Job management — list, show, cancel, rerun Galaxy jobs."""

import time


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
    if logs:
        result["command_line"] = info.get("command_line", "")
        result["stdout"] = info.get("tool_stdout", "")
        result["stderr"] = info.get("tool_stderr", "")
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


def wait_for_job(client, job_id, max_wait=600, poll_interval=5):
    """Wait for a job to complete. Returns final job state."""
    terminal_states = {"ok", "error", "deleted", "paused"}
    elapsed = 0
    while elapsed < max_wait:
        info = client.get(f"jobs/{job_id}")
        state = info.get("state", "unknown")
        if state in terminal_states:
            return {
                "id": job_id,
                "state": state,
                "exit_code": info.get("exit_code"),
                "waited_seconds": elapsed,
            }
        time.sleep(poll_interval)
        elapsed += poll_interval
    return {
        "id": job_id,
        "state": "timeout",
        "waited_seconds": elapsed,
    }
