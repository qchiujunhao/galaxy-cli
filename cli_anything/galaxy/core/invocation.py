"""Workflow invocation management — list, show, cancel invocations."""

import time


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
    terminal_states = {"scheduled", "cancelled", "failed", "error"}
    elapsed = 0
    while elapsed < max_wait:
        info = client.get(f"invocations/{invocation_id}")
        state = info.get("state", "unknown")
        # "scheduled" means all steps are scheduled (essentially done from invocation perspective)
        if state in terminal_states:
            return {
                "id": invocation_id,
                "state": state,
                "waited_seconds": elapsed,
            }
        time.sleep(poll_interval)
        elapsed += poll_interval
    return {
        "id": invocation_id,
        "state": "timeout",
        "waited_seconds": elapsed,
    }
