"""Galaxy user-defined tool management and execution."""

import json
import os
from pathlib import Path

from galaxy_cli.core.job import wait_for_jobs
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


def _udt_create_submission_error(client, exc, representation):
    status_code = getattr(exc, "status_code", None)
    rejected = status_code in {400, 422}
    api_key = getattr(client, "api_key", None)
    secrets = (api_key,) if isinstance(api_key, str) else ()
    details = _redact(dict(getattr(exc, "details", {}) or {}), secrets)
    details.update(
        {
            "tool_id": representation.get("id", ""),
            "tool_version": representation.get("version", ""),
            "job_ids": [],
            "output_ids": [],
        }
    )
    return GalaxyBackendError(
        _redact(str(exc), secrets),
        category=exc.category,
        error_kind="udt_create_rejected" if rejected else "udt_create_unknown",
        exit_code=exc.exit_code,
        suggestion=_redact(exc.suggestion, secrets),
        status_code=status_code,
        submission_state="not_submitted" if rejected else "unknown",
        retry_safe=rejected,
        details=details,
    )


def create_udt(client, representation, evidence=None):
    """Validate and create one UDT from its inner representation."""
    validate_representation(representation)
    payload = {"src": "representation", "representation": representation}
    if evidence is not None:
        evidence["create-request.json"] = payload
    try:
        response = client.post("unprivileged_tools", json_data=payload)
    except GalaxyBackendError as exc:
        raise _udt_create_submission_error(client, exc, representation) from exc
    if evidence is not None:
        evidence["create-response.json"] = response
    if not isinstance(response, dict) or not response.get("uuid"):
        known_id = response.get("id", "") if isinstance(response, dict) else ""
        raise GalaxyBackendError(
            "Galaxy did not return a UUID for the created UDT.",
            category="api_error",
            error_kind="udt_create_response_invalid",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="submitted" if known_id else "unknown",
            retry_safe=False,
            details={
                "udt_id": known_id,
                "tool_id": representation.get("id", ""),
                "tool_version": representation.get("version", ""),
                "job_ids": [],
                "output_ids": [],
            },
        )
    return _compact_udt(response)


def delete_udt(client, uuid):
    """Deactivate one UDT."""
    client.delete(f"unprivileged_tools/{uuid}")
    return {"uuid": uuid, "active": False, "status": "deactivated"}


def _validate_run_response(response, payload, tool_id):
    malformed = not isinstance(response, dict)
    if isinstance(response, dict):
        for key in ("jobs", "outputs", "output_collections"):
            value = response.get(key)
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(item, dict) for item in value)
            ):
                malformed = True
    if not malformed:
        return

    response = response if isinstance(response, dict) else {}
    jobs = [
        item for item in (response.get("jobs") or []) if isinstance(item, dict)
    ]
    outputs = [
        item
        for key in ("outputs", "output_collections")
        for item in (response.get(key) or [])
        if isinstance(item, dict)
    ]
    job_ids = [job.get("id", "") for job in jobs if job.get("id")]
    output_ids = [output.get("id", "") for output in outputs if output.get("id")]
    raise GalaxyBackendError(
        "Galaxy returned a malformed UDT execution response.",
        category="api_error",
        error_kind="udt_response_invalid",
        exit_code=EXIT_SERVER_ERROR,
        submission_state="submitted" if job_ids or output_ids else "unknown",
        retry_safe=False,
        details={
            "history_id": payload["history_id"],
            "tool_id": tool_id,
            "tool_uuid": payload["tool_uuid"],
            "job_ids": job_ids,
            "jobs": [
                {
                    "id": job.get("id", ""),
                    "state": job.get("state", ""),
                    "exit_code": job.get("exit_code"),
                }
                for job in jobs
                if job.get("id")
            ],
            "output_ids": output_ids,
        },
    )


def _compact_run_response(payload, response):
    jobs = response.get("jobs") or []
    outputs = response.get("outputs") or []
    collections = (
        response.get("output_collections") or []
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
                "output_name": output.get("output_name") or output.get("name", ""),
                "id": output.get("id", ""),
                "src": output.get("src", "hda"),
                "name": output.get("name", ""),
                "state": output.get("state", ""),
                "extension": output.get("extension", ""),
                "file_size": output.get("file_size", 0),
                "history_content_type": output.get("history_content_type", "dataset"),
            }
            for output in outputs
        ]
        + [
            {
                "output_name": output.get("output_name") or output.get("name", ""),
                "id": output.get("id", ""),
                "src": output.get("src", "hdca"),
                "name": output.get("name", ""),
                "state": output.get("populated_state") or output.get("state", ""),
                "collection_type": output.get("collection_type", ""),
                "element_count": output.get("element_count", 0),
                "history_content_type": output.get(
                    "history_content_type", "dataset_collection"
                ),
            }
            for output in collections
        ],
    }


def _udt_submission_error(client, exc, history_id, uuid, tool_id):
    status_code = getattr(exc, "status_code", None)
    rejected = status_code in {400, 422}
    api_key = getattr(client, "api_key", None)
    secrets = (api_key,) if isinstance(api_key, str) else ()
    details = _redact(dict(getattr(exc, "details", {}) or {}), secrets)
    details.update(
        {
            "history_id": history_id,
            "tool_id": tool_id,
            "tool_uuid": uuid,
            "job_ids": [],
            "output_ids": [],
        }
    )
    return GalaxyBackendError(
        _redact(str(exc), secrets),
        category=exc.category,
        error_kind="submission_rejected" if rejected else "submission_unknown",
        exit_code=exc.exit_code,
        suggestion=_redact(exc.suggestion, secrets),
        status_code=status_code,
        submission_state="not_submitted" if rejected else "unknown",
        retry_safe=rejected,
        details=details,
    )


def _udt_execution_error(
    client, exc, history_id, uuid, tool_id, jobs, outputs, error_kind
):
    api_key = getattr(client, "api_key", None)
    secrets = (api_key,) if isinstance(api_key, str) else ()
    details = _redact(dict(getattr(exc, "details", {}) or {}), secrets)
    details.update(
        {
            "history_id": history_id,
            "tool_id": tool_id,
            "tool_uuid": uuid,
            "job_ids": [job.get("id", "") for job in jobs if job.get("id")],
            "jobs": jobs,
            "output_ids": [
                output.get("id", "") for output in outputs if output.get("id")
            ],
            "outputs": outputs,
        }
    )
    return GalaxyBackendError(
        _redact(str(exc), secrets),
        category=exc.category,
        error_kind=getattr(exc, "error_kind", None) or error_kind,
        exit_code=exc.exit_code,
        suggestion=_redact(exc.suggestion, secrets),
        status_code=getattr(exc, "status_code", None),
        submission_state="submitted",
        retry_safe=False,
        details=details,
    )


def _udt_response_errors(response, result, uuid, tool_id):
    errors = response.get("errors") if isinstance(response, dict) else None
    if not errors:
        return
    job_ids = [job.get("id", "") for job in result["jobs"] if job.get("id")]
    output_ids = [
        output.get("id", "") for output in result["outputs"] if output.get("id")
    ]
    submitted = bool(job_ids or output_ids)
    raise GalaxyBackendError(
        f"Galaxy rejected the UDT request ({len(errors) if isinstance(errors, list) else 1} server error(s)).",
        category="tool_request_rejected",
        error_kind="udt_request_rejected",
        exit_code=EXIT_SERVER_ERROR,
        submission_state="submitted" if submitted else "not_submitted",
        retry_safe=not submitted,
        details={
            "history_id": result["history_id"],
            "tool_id": tool_id,
            "tool_uuid": uuid,
            "job_ids": job_ids,
            "jobs": result["jobs"],
            "output_ids": output_ids,
            "outputs": result["outputs"],
            "server_error_count": len(errors) if isinstance(errors, list) else 1,
        },
    )


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
    try:
        tool = client.get(f"unprivileged_tools/{uuid}")
    except GalaxyBackendError as exc:
        api_key = getattr(client, "api_key", None)
        secrets = (api_key,) if isinstance(api_key, str) else ()
        details = _redact(dict(getattr(exc, "details", {}) or {}), secrets)
        details.update(
            {
                "history_id": history_id,
                "tool_uuid": uuid,
                "job_ids": [],
                "output_ids": [],
            }
        )
        raise GalaxyBackendError(
            _redact(str(exc), secrets),
            category=exc.category,
            error_kind=getattr(exc, "error_kind", None) or "udt_lookup_failed",
            exit_code=exc.exit_code,
            suggestion=_redact(exc.suggestion, secrets),
            status_code=getattr(exc, "status_code", None),
            submission_state="not_submitted",
            retry_safe=True,
            details=details,
        ) from exc
    if evidence is not None:
        evidence["run-lookup-response.json"] = tool
    if not isinstance(tool, dict) or not tool.get("uuid"):
        raise GalaxyBackendError(
            f"UDT not found: {uuid}",
            category="not_found",
            error_kind="udt_not_found",
            exit_code=EXIT_USER_ERROR,
            submission_state="not_submitted",
            retry_safe=True,
            details={"history_id": history_id, "tool_uuid": uuid},
        )
    if not tool.get("active"):
        raise GalaxyBackendError(
            f"UDT is inactive: {uuid}",
            category="invalid_request",
            error_kind="udt_inactive",
            exit_code=EXIT_USER_ERROR,
            submission_state="not_submitted",
            retry_safe=True,
            details={"history_id": history_id, "tool_uuid": uuid},
        )
    representation = tool.get("representation") or {}
    tool_version = representation.get("version")
    if not tool_version:
        raise GalaxyBackendError(
            f"UDT {uuid} has no representation version.",
            category="api_error",
            error_kind="udt_representation_invalid",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="not_submitted",
            retry_safe=True,
            details={"history_id": history_id, "tool_uuid": uuid},
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
    tool_id = representation.get("id") or tool.get("tool_id") or uuid
    try:
        response = client.post("tools", json_data=payload)
    except GalaxyBackendError as exc:
        raise _udt_submission_error(client, exc, history_id, uuid, tool_id) from exc
    if evidence is not None:
        evidence["run-response.json"] = response
    _validate_run_response(response, payload, tool_id)
    result = _compact_run_response(payload, response)
    result.update(
        {
            "success": True,
            "state": "submitted",
            "execution_backend": "legacy",
            "tool_id": tool_id,
        }
    )

    if not wait:
        _udt_response_errors(response, result, uuid, tool_id)
        return result

    if wait:
        job_ids = [job["id"] for job in result["jobs"] if job["id"]]
        if not job_ids:
            _udt_response_errors(response, result, uuid, tool_id)
            raise GalaxyBackendError(
                "Galaxy returned no jobs for the blocking UDT submission.",
                category="api_error",
                error_kind="unknown_submission_state",
                exit_code=EXIT_SERVER_ERROR,
                submission_state="unknown",
                retry_safe=False,
                details={
                    "history_id": history_id,
                    "tool_id": tool_id,
                    "tool_uuid": uuid,
                    "job_ids": [],
                    "output_ids": [
                        output.get("id", "")
                        for output in result.get("outputs", [])
                        if output.get("id")
                    ],
                },
            )
        wait_results = wait_for_jobs(
            client,
            job_ids,
            timeout=timeout,
            poll_interval=poll_interval,
            history_id=history_id,
            tool_id=tool_id,
            output_ids=[
                output.get("id", "") for output in result.get("outputs", [])
            ],
        )
        waited_by_id = {job["id"]: job for job in wait_results}
        full_jobs = []
        for job in result["jobs"]:
            waited = waited_by_id.get(job["id"])
            if waited is None:
                continue
            job.update(
                {
                    "state": waited.get("state", job.get("state", "")),
                    "exit_code": waited.get("exit_code"),
                    "waited_seconds": waited.get("waited_seconds", 0),
                }
            )
            if evidence is not None:
                full_jobs.append(client.get(f"jobs/{job['id']}", params={"full": True}))
        result["wait_results"] = wait_results
        if len(wait_results) == 1:
            result["wait_result"] = wait_results[0]
        _udt_response_errors(response, result, uuid, tool_id)
        try:
            result["outputs"] = refresh_output_details(
                client, history_id, result.get("outputs", [])
            )
        except GalaxyBackendError as exc:
            raise _udt_execution_error(
                client,
                exc,
                history_id,
                uuid,
                tool_id,
                result.get("jobs", []),
                result.get("outputs", []),
                "output_refresh_failed",
            ) from exc
        if evidence is not None:
            evidence["jobs.json"] = full_jobs
            evidence["outputs.json"] = _full_output_evidence(
                client, history_id, result["outputs"]
            )
        result["state"] = "ok"
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
    try:
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
    except GalaxyBackendError as exc:
        api_key = getattr(client, "api_key", None)
        secrets = (api_key,) if isinstance(api_key, str) else ()
        created_context = _redact(created, secrets)
        details = dict(getattr(exc, "details", {}) or {})
        details.update(
            {
                "created_tool_uuid": created_context.get("uuid", ""),
                "created_udt": created_context,
                "run_submission_state": getattr(exc, "submission_state", None),
                "run_retry_safe": getattr(exc, "retry_safe", None),
            }
        )
        exc.details = details
        # Retrying the composite command would create another UDT even when
        # the run itself was safely rejected before submission.
        exc.submission_state = "submitted"
        exc.retry_safe = False
        raise
    return {
        "create": created,
        "success": run["success"],
        "state": run["state"],
        "execution_backend": run["execution_backend"],
        "history_id": history_id,
        "tool_id": run["tool_id"],
        "tool_version": run["tool_version"],
        "jobs": run["jobs"],
        "outputs": run["outputs"],
        "wait_results": run.get("wait_results", []),
        **({"wait_result": run["wait_result"]} if "wait_result" in run else {}),
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
