"""Galaxy user-defined tool management and execution."""

import json
import os
from pathlib import Path

from galaxy_cli.core.job import wait_for_job
from galaxy_cli.core.tool import refresh_output_details
from galaxy_cli.utils.galaxy_backend import (
    EXIT_SERVER_ERROR,
    EXIT_USER_ERROR,
    GalaxyBackendError,
)


def load_json_object(path, option_name):
    """Load a top-level JSON object for a UDT command option."""
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GalaxyBackendError(
            f"Failed to read {option_name} file: {exc}",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        ) from exc
    if not isinstance(value, dict):
        raise GalaxyBackendError(
            f"{option_name} file must contain a JSON object.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        )
    return value


def validate_representation(representation):
    """Validate the stable minimum required to create a Galaxy UDT."""
    if not isinstance(representation, dict):
        raise GalaxyBackendError(
            "UDT representation must be a JSON object.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        )
    missing = [
        field
        for field in ("class", "id", "version", "name", "shell_command", "container")
        if field not in representation
    ]
    if missing:
        raise GalaxyBackendError(
            f"UDT representation is missing required field(s): {', '.join(missing)}.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        )
    if representation["class"] != "GalaxyUserTool":
        raise GalaxyBackendError(
            "UDT representation field 'class' must be 'GalaxyUserTool'.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        )
    if not isinstance(representation["container"], str):
        raise GalaxyBackendError(
            "UDT representation field 'container' must be a string.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        )
    return representation


def _compact_udt(info, include_representation=False):
    representation = info.get("representation") or {}
    result = {
        "id": info.get("id", ""),
        "uuid": info.get("uuid", ""),
        "tool_id": info.get("tool_id") or representation.get("id", ""),
        "name": representation.get("name", ""),
        "version": representation.get("version", ""),
        "active": info.get("active", False),
    }
    if include_representation:
        result["representation"] = representation
    return result


def list_udts(client, include_inactive=False):
    """List active UDTs and optionally append inactive UDTs."""
    tools = client.get("unprivileged_tools", params={"active": True})
    if include_inactive:
        tools = list(tools or []) + list(
            client.get("unprivileged_tools", params={"active": False}) or []
        )
    return [_compact_udt(tool) for tool in tools or []]


def show_udt(client, uuid):
    """Show one UDT, including its representation for inspection."""
    tool = client.get(f"unprivileged_tools/{uuid}")
    if not isinstance(tool, dict) or not tool.get("uuid"):
        raise GalaxyBackendError(
            f"UDT not found: {uuid}",
            category="not_found",
            exit_code=EXIT_USER_ERROR,
        )
    return _compact_udt(tool, include_representation=True)


def create_udt(client, representation, evidence=None):
    """Validate and create one UDT from its inner representation."""
    validate_representation(representation)
    payload = {"src": "representation", "representation": representation}
    if evidence is not None:
        evidence["create-request.json"] = payload
    response = client.post("unprivileged_tools", json_data=payload)
    if evidence is not None:
        evidence["create-response.json"] = response
    if not isinstance(response, dict) or not response.get("uuid"):
        raise GalaxyBackendError(
            "Galaxy did not return a UUID for the created UDT.",
            category="api_error",
            exit_code=EXIT_SERVER_ERROR,
        )
    return _compact_udt(response)


def delete_udt(client, uuid):
    """Deactivate one UDT."""
    client.delete(f"unprivileged_tools/{uuid}")
    return {"uuid": uuid, "active": False, "status": "deactivated"}


def _compact_run_response(payload, response):
    jobs = response.get("jobs", []) if isinstance(response, dict) else []
    outputs = response.get("outputs", []) if isinstance(response, dict) else []
    collections = (
        response.get("output_collections", []) if isinstance(response, dict) else []
    )
    return {
        "tool_uuid": payload["tool_uuid"],
        "tool_version": payload["tool_version"],
        "history_id": payload["history_id"],
        "jobs": [
            {"id": job.get("id", ""), "state": job.get("state", "")} for job in jobs
        ],
        "outputs": [
            {
                "id": output.get("id", ""),
                "name": output.get("name", ""),
                "extension": output.get("extension", ""),
                "history_content_type": output.get("history_content_type", "dataset"),
            }
            for output in outputs
        ]
        + [
            {
                "id": output.get("id", ""),
                "name": output.get("name", ""),
                "collection_type": output.get("collection_type", ""),
                "history_content_type": output.get(
                    "history_content_type", "dataset_collection"
                ),
            }
            for output in collections
        ],
    }


def _full_output_evidence(client, history_id, outputs):
    details = []
    for output in outputs:
        output_id = output.get("id")
        if not output_id:
            continue
        if output.get("history_content_type") == "dataset_collection":
            path = f"histories/{history_id}/contents/dataset_collections/{output_id}"
        else:
            path = f"histories/{history_id}/contents/{output_id}"
        details.append(client.get(path))
    return details


def run_udt(
    client,
    uuid,
    history_id,
    inputs,
    wait=True,
    timeout=1800,
    poll_interval=180,
    evidence=None,
):
    """Resolve a UDT UUID, submit it through /api/tools, and optionally wait."""
    tool = client.get(f"unprivileged_tools/{uuid}")
    if evidence is not None:
        evidence["run-lookup-response.json"] = tool
    if not isinstance(tool, dict) or not tool.get("uuid"):
        raise GalaxyBackendError(
            f"UDT not found: {uuid}",
            category="not_found",
            exit_code=EXIT_USER_ERROR,
        )
    if not tool.get("active"):
        raise GalaxyBackendError(
            f"UDT is inactive: {uuid}",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        )
    representation = tool.get("representation") or {}
    tool_version = representation.get("version")
    if not tool_version:
        raise GalaxyBackendError(
            f"UDT {uuid} has no representation version.",
            category="api_error",
            exit_code=EXIT_SERVER_ERROR,
        )

    payload = {
        "history_id": history_id,
        "tool_uuid": uuid,
        "tool_version": tool_version,
        "inputs": inputs,
        "input_format": "legacy",
    }
    if evidence is not None:
        evidence["run-request.json"] = payload
    response = client.post("tools", json_data=payload)
    if evidence is not None:
        evidence["run-response.json"] = response
    result = _compact_run_response(payload, response)

    if wait:
        wait_results = []
        full_jobs = []
        for job in result["jobs"]:
            if not job["id"]:
                continue
            waited = wait_for_job(
                client,
                job["id"],
                max_wait=timeout,
                poll_interval=poll_interval,
            )
            job.update(
                {
                    "state": waited.get("state", job.get("state", "")),
                    "exit_code": waited.get("exit_code"),
                    "waited_seconds": waited.get("waited_seconds", 0),
                }
            )
            wait_results.append(waited)
            if evidence is not None:
                full_jobs.append(client.get(f"jobs/{job['id']}", params={"full": True}))
        result["wait_results"] = wait_results
        result["outputs"] = refresh_output_details(
            client, history_id, result.get("outputs", [])
        )
        if evidence is not None:
            evidence["jobs.json"] = full_jobs
            evidence["outputs.json"] = _full_output_evidence(
                client, history_id, result["outputs"]
            )

        failed = [
            job
            for job in result["jobs"]
            if job.get("state") in {"error", "deleted", "paused"}
        ]
        if failed:
            summary = ", ".join(
                f"{job.get('id', 'unknown')} ({job.get('state', 'unknown')})"
                for job in failed
            )
            raise GalaxyBackendError(
                f"UDT job failed: {summary}.",
                category="job_failed",
                exit_code=EXIT_SERVER_ERROR,
            )
    return result


def create_run_udt(
    client,
    representation,
    history_id,
    inputs,
    timeout=1800,
    poll_interval=180,
    evidence=None,
):
    """Create exactly one UDT, then run its returned UUID and wait."""
    created = create_udt(client, representation, evidence=evidence)
    run = run_udt(
        client,
        created["uuid"],
        history_id,
        inputs,
        wait=True,
        timeout=timeout,
        poll_interval=poll_interval,
        evidence=evidence,
    )
    return {
        "create": created,
        "history_id": history_id,
        "jobs": run["jobs"],
        "outputs": run["outputs"],
    }


def _redact(value, secrets):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower().replace("-", "_")
            in {"api_key", "x_api_key", "authorization"}
            else _redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def write_evidence(directory, evidence, secrets=()):
    """Write redacted full UDT evidence records as private JSON files."""
    path = Path(directory)
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        for filename, payload in evidence.items():
            output = path / filename
            output.write_text(
                json.dumps(_redact(payload, secrets), indent=2, default=str) + "\n"
            )
            os.chmod(output, 0o600)
    except OSError as exc:
        raise GalaxyBackendError(
            f"Failed to write UDT evidence: {exc}",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
        ) from exc
