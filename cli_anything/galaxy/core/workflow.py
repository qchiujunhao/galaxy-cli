"""Workflow management — import, export, list, show, run, delete workflows."""

import json
from pathlib import Path


def list_workflows(client, published=False):
    """List workflows for the current user."""
    params = {}
    if published:
        params["show_published"] = True
    workflows = client.get("workflows", params=params)
    return [
        {
            "id": w.get("id", ""),
            "name": w.get("name", ""),
            "owner": w.get("owner", ""),
            "published": w.get("published", False),
            "deleted": w.get("deleted", False),
            "step_count": w.get("number_of_steps", 0),
            "update_time": w.get("update_time", ""),
            "tags": w.get("tags", []),
        }
        for w in workflows
    ]


def show_workflow(client, workflow_id):
    """Show detailed info about a workflow including input types."""
    info = client.get(f"workflows/{workflow_id}")
    steps = {}
    for step_id, step in info.get("steps", {}).items():
        steps[step_id] = {
            "id": step.get("id", ""),
            "type": step.get("type", ""),
            "tool_id": step.get("tool_id"),
            "label": step.get("label", ""),
            "annotation": step.get("annotation", ""),
            "input_connections": step.get("input_connections", {}),
        }

    # Build rich input descriptions from input steps
    inputs = {}
    raw_inputs = info.get("inputs", {})
    raw_steps = info.get("steps", {})
    for inp_id, inp in raw_inputs.items():
        entry = {
            "label": inp.get("label", ""),
            "value": inp.get("value", ""),
        }
        # Enrich from the corresponding step's type and tool_inputs
        step = raw_steps.get(str(inp_id), {})
        step_type = step.get("type", "")
        tool_inputs = step.get("tool_inputs", {})
        entry["step_type"] = step_type
        entry["annotation"] = step.get("annotation", "")

        if step_type == "data_input":
            entry["input_type"] = "dataset"
        elif step_type == "data_collection_input":
            entry["input_type"] = "collection"
            entry["collection_type"] = tool_inputs.get("collection_type", "list")
        elif step_type == "parameter_input":
            entry["input_type"] = "parameter"
            entry["parameter_type"] = tool_inputs.get("parameter_type", "text")
            if "default" in tool_inputs:
                entry["default"] = tool_inputs["default"]
        else:
            entry["input_type"] = step_type

        entry["optional"] = tool_inputs.get("optional", False)
        inputs[inp_id] = entry

    return {
        "id": info.get("id", workflow_id),
        "name": info.get("name", ""),
        "owner": info.get("owner", ""),
        "annotation": info.get("annotation", ""),
        "published": info.get("published", False),
        "step_count": len(steps),
        "steps": steps,
        "inputs": inputs,
        "tags": info.get("tags", []),
        "version": info.get("version", 0),
        "update_time": info.get("update_time", ""),
    }


def import_workflow(client, workflow_path=None, workflow_dict=None):
    """Import a workflow from a file or dict."""
    if workflow_path:
        path = Path(workflow_path)
        if not path.exists():
            raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
        workflow_dict = json.loads(path.read_text())
    if not workflow_dict:
        raise ValueError("Either workflow_path or workflow_dict must be provided")
    result = client.post("workflows", json_data={"workflow": workflow_dict})
    return {
        "id": result.get("id", ""),
        "name": result.get("name", ""),
        "status": "imported",
    }


def export_workflow(client, workflow_id, output_path=None):
    """Export a workflow to Galaxy format JSON."""
    info = client.get(f"workflows/{workflow_id}/download")
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info, indent=2))
        return {"id": workflow_id, "output": str(path), "status": "exported"}
    return info


def run_workflow(client, workflow_id, history_id=None, inputs=None, params=None,
                 new_history_name=None):
    """Run a workflow.

    Args:
        client: GalaxyClient instance.
        workflow_id: Workflow ID to run.
        history_id: Existing history to put outputs in.
        inputs: Dict mapping step input labels/indices to dataset IDs.
        params: Dict mapping step indices to parameter overrides.
        new_history_name: If set, create a new history with this name for outputs.
    """
    payload = {"workflow_id": workflow_id}
    if new_history_name:
        payload["history"] = f"hist_name={new_history_name}"
    elif history_id:
        payload["history_id"] = history_id

    if inputs:
        # Convert simple {step_index: dataset_id} to Galaxy API format
        ds_map = {}
        for step_key, dataset_id in inputs.items():
            ds_map[str(step_key)] = {"src": "hda", "id": dataset_id}
        payload["ds_map"] = ds_map

    if params:
        payload["parameters"] = params

    result = client.post(f"workflows/{workflow_id}/invocations", json_data=payload)
    return {
        "id": result.get("id", ""),
        "workflow_id": workflow_id,
        "history_id": result.get("history_id", history_id or ""),
        "state": result.get("state", ""),
        "status": "invoked",
    }


def delete_workflow(client, workflow_id):
    """Delete a workflow."""
    client.delete(f"workflows/{workflow_id}")
    return {"id": workflow_id, "status": "deleted"}
