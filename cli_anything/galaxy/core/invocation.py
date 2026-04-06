"""Workflow invocation management — list, show, cancel invocations."""

import time


_INVOCATION_FAILURE_STATES = {"cancelled", "failed", "error"}
_FAILED_STEP_STATES = {"failed", "error", "cancelled"}
_JOB_FAILURE_STATES = {"error", "deleted", "paused"}
_JOB_TERMINAL_STATES = {"ok"} | _JOB_FAILURE_STATES


def list_invocations(client, workflow_id=None, history_id=None, limit=50):
    """List workflow invocations."""
    params = {"limit": limit}
    if workflow_id:
        params["workflow_id"] = workflow_id
    if history_id:
        params["history_id"] = history_id
    invocations = client.get("invocations", params=params)
    return [
        {
            "id": inv.get("id", ""),
            "workflow_id": inv.get("workflow_id", ""),
            "history_id": inv.get("history_id", ""),
            "state": inv.get("state", ""),
            "create_time": inv.get("create_time", ""),
            "update_time": inv.get("update_time", ""),
        }
        for inv in invocations
    ]


def show_invocation(client, invocation_id):
    """Show details of a workflow invocation."""
    info = client.get(f"invocations/{invocation_id}")
    steps = []
    for step in info.get("steps", []):
        steps.append({
            "id": step.get("id", ""),
            "order_index": step.get("order_index", 0),
            "state": step.get("state", ""),
            "job_id": step.get("job_id"),
            "update_time": step.get("update_time", ""),
            "action": step.get("action"),
        })
    return {
        "id": info.get("id", invocation_id),
        "workflow_id": info.get("workflow_id", ""),
        "history_id": info.get("history_id", ""),
        "state": info.get("state", ""),
        "create_time": info.get("create_time", ""),
        "update_time": info.get("update_time", ""),
        "steps": steps,
        "inputs": info.get("inputs", {}),
        "outputs": info.get("outputs", {}),
    }


def cancel_invocation(client, invocation_id):
    """Cancel a running invocation."""
    client.delete(f"invocations/{invocation_id}")
    return {"id": invocation_id, "status": "cancelled"}


def wait_for_invocation(client, invocation_id, max_wait=1800, poll_interval=10):
    """Wait for a workflow invocation to complete."""
    elapsed = 0
    while elapsed < max_wait:
        info = client.get(f"invocations/{invocation_id}")
        state = info.get("state", "unknown")
        steps = info.get("steps", [])
        failed_steps = [
            {
                "id": step.get("id", ""),
                "order_index": step.get("order_index", 0),
                "state": step.get("state", ""),
                "job_id": step.get("job_id"),
            }
            for step in steps
            if step.get("state", "") in _FAILED_STEP_STATES
        ]
        if failed_steps:
            return {
                "id": invocation_id,
                "state": "failed",
                "invocation_state": state,
                "waited_seconds": elapsed,
                "failed_steps": failed_steps,
            }

        if state in _INVOCATION_FAILURE_STATES:
            return {
                "id": invocation_id,
                "state": state,
                "waited_seconds": elapsed,
            }

        job_ids = [step.get("job_id") for step in steps if step.get("job_id")]
        if job_ids:
            jobs = []
            for job_id in job_ids:
                job = client.get(f"jobs/{job_id}")
                jobs.append({
                    "id": job_id,
                    "state": job.get("state", "unknown"),
                    "exit_code": job.get("exit_code"),
                })

            failed_jobs = [job for job in jobs if job["state"] in _JOB_FAILURE_STATES]
            if failed_jobs:
                return {
                    "id": invocation_id,
                    "state": "failed",
                    "invocation_state": state,
                    "waited_seconds": elapsed,
                    "failed_jobs": failed_jobs,
                }

            if all(job["state"] in _JOB_TERMINAL_STATES for job in jobs):
                return {
                    "id": invocation_id,
                    "state": "ok",
                    "invocation_state": state,
                    "waited_seconds": elapsed,
                    "jobs": jobs,
                }
        time.sleep(poll_interval)
        elapsed += poll_interval
    return {
        "id": invocation_id,
        "state": "timeout",
        "waited_seconds": elapsed,
    }
