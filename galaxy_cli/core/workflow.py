"""Workflow management — import, export, list, show, run, delete workflows."""

import json
import re
from pathlib import Path

from galaxy_cli.utils.galaxy_backend import (
    EXIT_USER_ERROR,
    GalaxyBackendError,
)

_GALAXY_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_WORKFLOW_INPUT_SRCS = {"hda", "hdca", "ldda"}


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


def _validation_error(message, suggestion):
    raise GalaxyBackendError(
        message,
        category="invalid_request",
        exit_code=EXIT_USER_ERROR,
        suggestion=suggestion,
    )


def _workflow_input_lookup(workflow_info):
    lookup = {}
    for step_key, inp in (workflow_info.get("inputs") or {}).items():
        lookup[str(step_key)] = inp
        label = inp.get("label")
        if label:
            lookup[str(label)] = inp
    return lookup


def _workflow_input_id_lookup(workflow_info):
    lookup = {}
    for step_key, inp in (workflow_info.get("inputs") or {}).items():
        lookup[str(step_key)] = str(step_key)
        label = inp.get("label")
        if label:
            lookup[str(label)] = str(step_key)
    return lookup


def _normalize_workflow_input_ref(step_key, dataset_ref):
    if isinstance(dataset_ref, dict):
        return dataset_ref
    if isinstance(dataset_ref, str) and ":" in dataset_ref:
        src, dataset_id = dataset_ref.split(":", 1)
        if src in _WORKFLOW_INPUT_SRCS and dataset_id:
            return {"src": src, "id": dataset_id}
        if dataset_id:
            raise GalaxyBackendError(
                f"Unsupported dataset source prefix '{src}' for workflow input '{step_key}'.",
                category="invalid_request",
                exit_code=EXIT_USER_ERROR,
                suggestion="Use hda:, hdca:, or ldda: prefixes for explicit workflow inputs.",
            )
        return {"src": "hda", "id": dataset_ref}
    if isinstance(dataset_ref, str) and _GALAXY_ID_RE.match(dataset_ref):
        return {"src": "hda", "id": dataset_ref}
    return {"src": "hda", "id": dataset_ref}


def validate_workflow_inputs(workflow_info, inputs):
    """Validate obvious workflow input mistakes before invocation."""
    inputs = inputs or {}
    expected = _workflow_input_lookup(workflow_info)
    if not expected and inputs:
        return
    for step_key, dataset_ref in inputs.items():
        input_info = expected.get(str(step_key))
        if input_info is None:
            available = ", ".join(sorted(expected)) or "(none)"
            _validation_error(
                f"Unknown workflow input '{step_key}' for workflow '{workflow_info.get('id', '')}'.",
                f"Run `galaxy-cli workflow show {workflow_info.get('id', 'WORKFLOW_ID')}` and use one of: {available}.",
            )
        normalized = _normalize_workflow_input_ref(step_key, dataset_ref)
        src = normalized.get("src")
        input_type = input_info.get("input_type")
        if input_type == "dataset" and src not in {"hda", "ldda"}:
            _validation_error(
                f"Workflow input '{step_key}' expects a dataset, but source '{src}' was provided.",
                "Use hda:DATASET_ID or ldda:LIBRARY_DATASET_ID for dataset workflow inputs.",
            )
        if input_type == "collection" and src != "hdca":
            _validation_error(
                f"Workflow input '{step_key}' expects a dataset collection, but source '{src}' was provided.",
                "Use hdca:COLLECTION_ID for collection workflow inputs.",
            )
    for step_key, input_info in (workflow_info.get("inputs") or {}).items():
        if input_info.get("input_type") not in {"dataset", "collection"}:
            continue
        if input_info.get("optional", False):
            continue
        label = input_info.get("label")
        if str(step_key) not in inputs and (not label or label not in inputs):
            _validation_error(
                f"Missing required workflow input '{step_key}' ({label or 'unlabeled'}).",
                "Run `galaxy-cli workflow show WORKFLOW_ID` to inspect required inputs, then pass them with -i.",
            )


def build_workflow_payload(client, workflow_id, history_id=None, inputs=None, params=None,
                           new_history_name=None):
    """Build and validate the exact POST body for a Galaxy workflow invocation."""
    payload = {"workflow_id": workflow_id}
    if new_history_name:
        payload["history"] = f"hist_name={new_history_name}"
    elif history_id:
        payload["history_id"] = history_id

    workflow_info = show_workflow(client, workflow_id)
    validate_workflow_inputs(workflow_info, inputs or {})

    if inputs:
        input_id_lookup = _workflow_input_id_lookup(workflow_info)
        ds_map = {}
        for step_key, dataset_ref in inputs.items():
            resolved_step_key = input_id_lookup.get(str(step_key), str(step_key))
            ds_map[resolved_step_key] = _normalize_workflow_input_ref(step_key, dataset_ref)
        payload["ds_map"] = ds_map

    if params:
        payload["parameters"] = params

    return payload


def run_workflow(client, workflow_id, history_id=None, inputs=None, params=None,
                 new_history_name=None, payload=None):
    """Run a workflow.

    Args:
        client: GalaxyClient instance.
        workflow_id: Workflow ID to run.
        history_id: Existing history to put outputs in.
        inputs: Dict mapping step input labels/indices to dataset IDs.
        params: Dict mapping step indices to parameter overrides.
        new_history_name: If set, create a new history with this name for outputs.
        payload: Pre-built workflow invocation payload.
    """
    if payload is None:
        payload = build_workflow_payload(
            client,
            workflow_id,
            history_id=history_id,
            inputs=inputs,
            params=params,
            new_history_name=new_history_name,
        )

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
