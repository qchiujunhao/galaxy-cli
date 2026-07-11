"""Secret-free receipts for mutating Galaxy operations."""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from galaxy_cli.core.job import wait_for_jobs
from galaxy_cli.utils.galaxy_backend import DEFAULT_CONFIG_DIR, GalaxyBackendError


def operation_dir():
    configured = os.environ.get("GALAXY_CLI_OPERATION_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_CONFIG_DIR / "operations"


def _hash(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ids(value, singular, plural):
    if not isinstance(value, dict):
        return []
    values = list(value.get(plural, []) or [])
    if value.get(singular):
        values.append(value[singular])
    return list(dict.fromkeys(str(item) for item in values if item))


def create_receipt(operation_type, payload, result=None, error=None):
    details = dict(getattr(error, "details", {}) or {}) if error else {}
    source = result if isinstance(result, dict) else details
    submission_state = (
        getattr(error, "submission_state", None) if error else source.get("submission_state")
    ) or ("submitted" if source else "unknown")
    state = source.get("state", "") if isinstance(source, dict) else ""
    error_kind = getattr(error, "error_kind", "") if error else ""
    receipt_state = (
        "complete" if state == "ok" or source.get("success") is True
        else "submitted" if error_kind == "tus_upload_interrupted"
        else "failed" if error and submission_state != "unknown"
        else "submitted" if submission_state == "submitted"
        else "unknown"
    )
    receipt_id = uuid.uuid4().hex
    receipt = {
        "id": receipt_id,
        "operation_type": operation_type,
        "payload_hash": _hash(payload),
        "submission_state": submission_state,
        "state": receipt_state,
        "retry_safe": bool(getattr(error, "retry_safe", False)) if error else False,
        "history_id": source.get("history_id", "") or (
            source.get("id", "") if operation_type.startswith("history") else ""
        ),
        "tool_id": source.get("tool_id", ""),
        "request_ids": _ids(source, "tool_request_id", "request_ids") + _ids(source, "id" if operation_type == "workflow" else "", "invocation_ids"),
        "job_ids": _ids(source, "job_id", "job_ids") or [job.get("id") for job in source.get("jobs", []) if isinstance(job, dict) and job.get("id")],
        "output_ids": _ids(source, "output_id", "output_ids") or [output.get("id") for output in source.get("outputs", []) if isinstance(output, dict) and output.get("id")],
        "created_at": time.time(),
        "updated_at": time.time(),
        "error_kind": error_kind,
    }
    if operation_type == "upload":
        receipt["resume"] = {
            key: payload.get(key)
            for key in ("local_path", "file_type", "dbkey")
            if payload.get(key) is not None
        }
        for key in ("tus_session_id", "upload_offset"):
            if details.get(key) is not None:
                receipt["resume"][key] = details[key]
    receipt["request_ids"] = list(dict.fromkeys(item for item in receipt["request_ids"] if item))
    path = operation_dir() / f"{receipt_id}.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, separators=(",", ":")))
    os.chmod(path, 0o600)
    return receipt


def _path(reference):
    supplied = Path(reference).expanduser()
    return supplied if supplied.exists() else operation_dir() / f"{reference}.json"


def show_receipt(reference):
    return json.loads(_path(reference).read_text())


def list_receipts(state=None):
    receipts = []
    try:
        paths = operation_dir().glob("*.json")
    except OSError:
        return []
    for path in paths:
        try:
            receipt = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not state or receipt.get("state") == state:
            receipts.append(receipt)
    return sorted(receipts, key=lambda item: item.get("created_at", 0), reverse=True)


def _save(receipt):
    receipt["updated_at"] = time.time()
    path = operation_dir() / f"{receipt['id']}.json"
    path.write_text(json.dumps(receipt, separators=(",", ":")))


def resume_operation(client, reference, timeout=1800, poll_interval=5):
    """Resume status polling only; this function never replays a POST."""
    receipt = show_receipt(reference)
    if receipt.get("state") in {"complete", "failed"}:
        return receipt
    if (
        receipt.get("operation_type") == "upload"
        and receipt.get("error_kind") == "tus_upload_interrupted"
        and receipt.get("resume", {}).get("tus_session_id")
    ):
        resume = receipt["resume"]
        result = client.resume_tus_upload_file(
            resume["local_path"], receipt.get("history_id", ""),
            resume["tus_session_id"], file_type=resume.get("file_type", "auto"),
            dbkey=resume.get("dbkey", "?"),
        )
        receipt["state"] = "submitted"
        receipt["submission_state"] = "submitted"
        receipt["job_ids"] = [job.get("id") for job in result.get("jobs", []) if job.get("id")]
        receipt["output_ids"] = [item.get("id") for item in result.get("outputs", []) if item.get("id")]
        _save(receipt)
    job_ids = list(receipt.get("job_ids", []))
    if not job_ids and receipt.get("request_ids"):
        request_id = receipt["request_ids"][0]
        path = (
            f"invocations/{request_id}"
            if receipt.get("operation_type") == "workflow"
            else f"tool_requests/{request_id}"
        )
        try:
            detail = client.get(path)
        except GalaxyBackendError:
            return receipt
        job_ids = [
            item.get("id") if isinstance(item, dict) else item
            for item in detail.get("jobs", [])
        ]
        if receipt.get("operation_type") == "workflow":
            job_ids.extend(step.get("job_id") for step in detail.get("steps", []) if step.get("job_id"))
        receipt["job_ids"] = list(dict.fromkeys(item for item in job_ids if item))
    if not receipt.get("job_ids"):
        return receipt
    try:
        jobs = wait_for_jobs(
            client, receipt["job_ids"], timeout=timeout, poll_interval=poll_interval,
            history_id=receipt.get("history_id", ""), tool_id=receipt.get("tool_id", ""),
            request_ids=receipt.get("request_ids", []), output_ids=receipt.get("output_ids", []),
        )
    except GalaxyBackendError:
        receipt["state"] = "failed"
        receipt["submission_state"] = "submitted"
        _save(receipt)
        raise
    receipt.update({"state": "complete", "submission_state": "submitted", "jobs": jobs})
    _save(receipt)
    return receipt
