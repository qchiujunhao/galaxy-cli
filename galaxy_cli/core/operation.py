"""Secret-free receipts and read-only recovery of Galaxy operations."""

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":  # pragma: no cover - exercised on Windows runners.
    import msvcrt
else:  # pragma: no cover - branch choice is platform-specific.
    import fcntl

from galaxy_cli.core.job import wait_for_jobs
from galaxy_cli.core.polling import deadline_after, remaining, sleep_for_poll
from galaxy_cli.core.tool import (
    _job_outputs,
    _normalize_output_ref,
    _poll_tool_request,
    refresh_output_details,
)
from galaxy_cli.utils.galaxy_backend import (
    DEFAULT_CONFIG_DIR,
    EXIT_SERVER_ERROR,
    EXIT_USER_ERROR,
    GalaxyBackendError,
    get_with_deadline,
)


_TERMINAL_ERROR_KINDS = {
    "history_copy_failed",
    "job_failed",
    "output_failed",
    "tool_request_rejected",
    "udt_request_rejected",
    "upload_outputs_missing",
    "workflow_failed",
}
_SECRET_KEYS = {"api_key", "x_api_key", "authorization"}
_RECEIPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def operation_dir():
    configured = os.environ.get("GALAXY_CLI_OPERATION_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_CONFIG_DIR / "operations"


def _hash(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _local_file_fingerprint(path):
    """Return a strong identity for a resumable local upload source."""
    try:
        source = Path(path)
        if not source.is_file():
            return None
        digest = hashlib.sha256()
        size = 0
        with open(source, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        return {"local_file_size": size, "local_file_sha256": digest.hexdigest()}
    except (OSError, TypeError, ValueError):
        return None


def _ids(value, singular, plural):
    if not isinstance(value, dict):
        return []
    values = list(value.get(plural, []) or [])
    if singular and value.get(singular):
        values.append(value[singular])
    return list(dict.fromkeys(str(item) for item in values if item))


def _merge_ids(*groups):
    return list(
        dict.fromkeys(
            str(item)
            for group in groups
            for item in (group or [])
            if item
        )
    )


def _redact_known(value, secrets):
    if isinstance(value, dict):
        return {key: _redact_known(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_known(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_known(item, secrets) for item in value)
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def _safe_output_ref(output, *, collection=False):
    if not isinstance(output, dict) or not output.get("id"):
        return None
    is_collection = collection or output.get("src") == "hdca" or output.get(
        "history_content_type"
    ) == "dataset_collection"
    return {
        "id": str(output["id"]),
        "src": "hdca" if is_collection else "hda",
        "history_content_type": (
            "dataset_collection" if is_collection else "dataset"
        ),
    }


def _source_output_refs(source, operation_type=""):
    refs = []
    if not isinstance(source, dict):
        return refs
    for output in source.get("output_refs", []) or []:
        ref = _safe_output_ref(output)
        if ref:
            refs.append(ref)
    for output in source.get("outputs", []) or []:
        ref = _safe_output_ref(output)
        if ref:
            refs.append(ref)
    for key in ("output_collections", "implicit_collections"):
        for output in source.get(key, []) or []:
            ref = _safe_output_ref(output, collection=True)
            if ref:
                refs.append(ref)
    if operation_type == "upload" and source.get("id"):
        refs.append(
            {
                "id": str(source["id"]),
                "src": "hda",
                "history_content_type": "dataset",
            }
        )
    return _merge_output_refs(refs)


def _merge_output_refs(*groups):
    merged = []
    indexes = {}
    for group in groups:
        for output in group or []:
            if not isinstance(output, dict) or not output.get("id"):
                continue
            src = "hdca" if (
                output.get("src") == "hdca"
                or output.get("history_content_type") == "dataset_collection"
            ) else "hda"
            item = dict(output, src=src)
            identity = (src, str(item["id"]))
            if identity in indexes:
                current = merged[indexes[identity]]
                current.update(
                    {key: value for key, value in item.items() if value not in (None, "")}
                )
            else:
                indexes[identity] = len(merged)
                merged.append(item)
    return merged


def _receipt_state(source, error, submission_state, error_kind):
    state = source.get("state", "") if isinstance(source, dict) else ""
    if error is None:
        if state == "ok":
            return "complete"
        return "submitted" if submission_state == "submitted" else "unknown"
    if error_kind == "tus_upload_interrupted":
        return "submitted"
    if submission_state == "unknown":
        return "unknown"
    if submission_state == "not_submitted" or error_kind in _TERMINAL_ERROR_KINDS:
        return "failed"
    return "submitted"


def create_receipt(operation_type, payload, result=None, error=None, secrets=()):
    details = dict(getattr(error, "details", {}) or {}) if error else {}
    source = result if isinstance(result, dict) else details
    source = _redact_known(source, secrets)
    safe_payload = _redact_known(payload, secrets)
    submission_state = (
        getattr(error, "submission_state", None) if error else source.get("submission_state")
    ) or ("submitted" if source else "unknown")
    error_kind = getattr(error, "error_kind", "") if error else ""
    output_refs = _source_output_refs(source, operation_type=operation_type)
    output_ids = _merge_ids(
        _ids(source, "output_id", "output_ids"),
        [item["id"] for item in output_refs],
    )
    created = time.time()
    receipt_id = uuid.uuid4().hex
    receipt = {
        "id": receipt_id,
        "operation_type": operation_type,
        "payload_hash": _hash(safe_payload),
        "submission_state": submission_state,
        "state": _receipt_state(source, error, submission_state, error_kind),
        "retry_safe": bool(getattr(error, "retry_safe", False)) if error else False,
        "history_id": source.get("history_id", "") or (
            source.get("id", "") if operation_type.startswith("history") else ""
        ) or safe_payload.get("history_id", ""),
        "tool_id": source.get("tool_id", "") or safe_payload.get("tool_id", ""),
        "tool_version": source.get("tool_version", ""),
        "tool_uuid": source.get("tool_uuid", "") or safe_payload.get("uuid", ""),
        "workflow_id": source.get("workflow_id", "") or safe_payload.get("workflow_id", ""),
        "execution_backend": source.get("execution_backend", ""),
        "request_ids": _ids(source, "tool_request_id", "request_ids")
        + _ids(source, "id" if operation_type == "workflow" else "", "invocation_ids"),
        "job_ids": _merge_ids(
            _ids(source, "job_id", "job_ids"),
            [
                job.get("id")
                for job in source.get("jobs", [])
                if isinstance(job, dict) and job.get("id")
            ],
        ),
        "output_ids": output_ids,
        "output_refs": output_refs,
        "created_at": created,
        "updated_at": created,
        "error_kind": error_kind,
    }
    create_result = source.get("create") if isinstance(source, dict) else None
    if not receipt["tool_uuid"] and isinstance(create_result, dict):
        receipt["tool_uuid"] = create_result.get("uuid", "")
    if operation_type == "upload":
        receipt["resume"] = {
            key: safe_payload.get(key)
            for key in ("local_path", "file_type", "dbkey")
            if safe_payload.get(key) is not None
        }
        for key in ("tus_session_id", "upload_offset"):
            if source.get(key) is not None:
                receipt["resume"][key] = source[key]
        if receipt["resume"].get("tus_session_id"):
            receipt["resume"]["tus_mutation_allowed"] = True
            fingerprint = {
                "local_file_size": source.get("local_file_size"),
                "local_file_sha256": source.get("local_file_sha256"),
            }
            if (
                isinstance(fingerprint["local_file_size"], bool)
                or not isinstance(fingerprint["local_file_size"], int)
                or fingerprint["local_file_size"] < 0
                or not isinstance(fingerprint["local_file_sha256"], str)
                or not re.fullmatch(
                    r"[0-9a-f]{64}", fingerprint["local_file_sha256"]
                )
            ):
                fingerprint = _local_file_fingerprint(payload.get("local_path"))
            if fingerprint:
                receipt["resume"].update(fingerprint)
        if error_kind == "tus_fetch_submission_unknown":
            receipt["resume"]["fetch_submission_state"] = "unknown"
    receipt["request_ids"] = _merge_ids(receipt["request_ids"])
    receipt["job_ids"] = _merge_ids(receipt["job_ids"])
    if (
        receipt["state"] == "complete"
        and operation_type in {"tool", "udt", "workflow", "upload"}
    ):
        receipt.update(
            {
                "success": True,
                "final_state": "ok",
                "resumable": False,
                "jobs": list(source.get("jobs", []) or []),
                "outputs": list(source.get("outputs", []) or []),
                "exit_code": source.get("exit_code"),
                "result": source,
            }
        )
    _save(receipt)
    return receipt


def _path(reference):
    supplied = Path(reference).expanduser()
    return supplied if supplied.exists() else operation_dir() / f"{reference}.json"


def _receipt_id(receipt):
    receipt_id = receipt.get("id") if isinstance(receipt, dict) else None
    if not isinstance(receipt_id, str) or not _RECEIPT_ID_PATTERN.fullmatch(
        receipt_id
    ):
        raise GalaxyBackendError(
            "Operation receipt has an invalid identifier.",
            category="invalid_request",
            error_kind="operation_receipt_invalid",
            exit_code=EXIT_USER_ERROR,
            submission_state="not_submitted",
            retry_safe=True,
        )
    return receipt_id


def show_receipt(reference):
    return _redact(None, json.loads(_path(reference).read_text()))


def list_receipts(state=None):
    receipts = []
    try:
        paths = operation_dir().glob("*.json")
    except OSError:
        return []
    for path in paths:
        try:
            receipt = _redact(None, json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
        if not state or receipt.get("state") == state:
            receipts.append(receipt)
    return sorted(receipts, key=lambda item: item.get("created_at", 0), reverse=True)


def _save(receipt):
    receipt["updated_at"] = time.time()
    path = operation_dir() / f"{_receipt_id(receipt)}.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=".operation-", delete=False,
        ) as handle:
            json.dump(receipt, handle, separators=(",", ":"), default=str)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(str(temporary), str(path))
        if os.name != "nt":
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


@contextmanager
def _receipt_lock(receipt_id):
    """Serialize the only mutating resume path: completing a TUS upload."""
    _receipt_id({"id": receipt_id})
    directory = operation_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    lock_path = directory / f".{receipt_id}.lock"
    with open(lock_path, "a+", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        if os.name == "nt":
            handle.seek(0)
            if not handle.read(1):
                handle.write("\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _redact(client, value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if str(key).lower().replace("-", "_") in _SECRET_KEYS
            else _redact(client, item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(client, item) for item in value]
    if isinstance(value, str):
        redact = getattr(type(client), "redact", None)
        return redact(client, value) if callable(redact) else value
    return value


def _non_resumable(receipt, reason="no_known_request_job_or_upload_session"):
    result = dict(receipt)
    result.update(
        {
            "resumable": False,
            "reason": reason,
            "recommended_action": "do_not_resubmit",
        }
    )
    return result


def _job_ids_from_detail(detail):
    job_ids = []
    if not isinstance(detail, dict):
        return job_ids
    for item in detail.get("jobs", []) or []:
        job_id = item.get("id") if isinstance(item, dict) else item
        if job_id:
            job_ids.append(job_id)
    for step in detail.get("steps", []) or []:
        if isinstance(step, dict) and step.get("job_id"):
            job_ids.append(step["job_id"])
    return _merge_ids(job_ids)


def _output_values(raw):
    if isinstance(raw, list):
        return [(None, item) for item in raw]
    if isinstance(raw, dict):
        values = []
        for name, value in raw.items():
            for item in value if isinstance(value, list) else [value]:
                values.append((name, item))
        return values
    return []


def _outputs_from_detail(detail):
    if not isinstance(detail, dict):
        return []
    outputs = []
    for name, output in _output_values(detail.get("outputs")):
        item = _normalize_output_ref(output, output_name=name)
        if item:
            outputs.append(item)
    for key in ("output_collections", "implicit_collections"):
        for name, output in _output_values(detail.get(key)):
            item = _normalize_output_ref(output, collection=True, output_name=name)
            if item:
                outputs.append(item)
    return _merge_output_refs(outputs)


def _workflow_detail(client, invocation_id, deadline, poll_interval):
    poll_attempt = 0
    while True:
        detail = get_with_deadline(
            client, f"invocations/{invocation_id}", deadline=deadline
        )
        if not isinstance(detail, dict):
            raise GalaxyBackendError(
                f"Galaxy returned invalid workflow invocation detail for {invocation_id}.",
                category="api_error",
                error_kind="unexpected_response",
                exit_code=EXIT_SERVER_ERROR,
                submission_state="submitted",
                retry_safe=False,
                details={"request_ids": [invocation_id]},
            )
        state = str(detail.get("state", "unknown"))
        steps = detail.get("steps", []) or []
        failed_steps = [
            step
            for step in steps
            if isinstance(step, dict)
            and step.get("state") in {"failed", "error", "cancelled"}
        ]
        if state in {"failed", "error", "cancelled"} or failed_steps:
            return detail, "workflow_failed"
        if state in {"scheduled", "completed"}:
            return detail, ""
        poll_attempt = sleep_for_poll(
            poll_attempt, deadline, poll_interval=poll_interval
        )


def _tool_detail(client, request_id, deadline, poll_interval):
    state = _poll_tool_request(client, request_id, deadline, poll_interval)
    detail = get_with_deadline(
        client, f"tool_requests/{request_id}", deadline=deadline
    )
    if not isinstance(detail, dict):
        raise GalaxyBackendError(
            f"Galaxy returned invalid tool request detail for {request_id}.",
            category="api_error",
            error_kind="unexpected_response",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="submitted",
            retry_safe=False,
            details={"request_ids": [request_id]},
        )
    if state == "failed":
        return detail, "tool_request_rejected"
    return detail, ""


def _discover_requests(client, receipt, deadline, poll_interval):
    job_ids = list(receipt.get("job_ids", []))
    outputs = list(receipt.get("output_refs", []))
    terminal_errors = []
    operation_type = receipt.get("operation_type")
    for request_id in receipt.get("request_ids", []) or []:
        detail, terminal_error = (
            _workflow_detail(client, request_id, deadline, poll_interval)
            if operation_type == "workflow"
            else _tool_detail(client, request_id, deadline, poll_interval)
        )
        job_ids = _merge_ids(job_ids, _job_ids_from_detail(detail))
        outputs = _merge_output_refs(outputs, _outputs_from_detail(detail))
        if terminal_error:
            terminal_errors.append(terminal_error)
    return job_ids, outputs, terminal_errors


def _lookup_output_ref(client, history_id, output_id, deadline):
    try:
        info = get_with_deadline(
            client,
            f"histories/{history_id}/contents/{output_id}",
            deadline=deadline,
        )
    except GalaxyBackendError as exc:
        if exc.status_code != 404:
            raise
        info = get_with_deadline(
            client,
            f"histories/{history_id}/contents/dataset_collections/{output_id}",
            deadline=deadline,
        )
    if not isinstance(info, dict):
        raise GalaxyBackendError(
            f"Galaxy returned invalid metadata for output {output_id}.",
            category="api_error",
            error_kind="unexpected_response",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="submitted",
            retry_safe=False,
            details={"history_id": history_id, "output_ids": [output_id]},
        )
    collection = (
        info.get("history_content_type") == "dataset_collection"
        or bool(info.get("collection_type"))
    )
    return _normalize_output_ref(info, collection=collection)


def _complete_result(receipt, jobs, outputs):
    operation_type = receipt.get("operation_type", "")
    result = {
        "success": True,
        "state": "ok",
        "operation_type": operation_type,
        "history_id": receipt.get("history_id", ""),
        "jobs": jobs,
        "outputs": outputs,
        "wait_results": jobs,
    }
    if len(jobs) == 1:
        result["wait_result"] = jobs[0]
    exit_codes = [job.get("exit_code") for job in jobs if job.get("exit_code") is not None]
    result["exit_code"] = (
        exit_codes[0] if exit_codes and len(set(exit_codes)) == 1 else exit_codes or None
    )
    for key in (
        "execution_backend",
        "tool_id",
        "tool_version",
        "tool_uuid",
        "workflow_id",
    ):
        if receipt.get(key):
            result[key] = receipt[key]
    request_ids = list(receipt.get("request_ids", []))
    if request_ids:
        result["request_ids"] = request_ids
    if operation_type == "tool" and len(request_ids) == 1:
        result["tool_request_id"] = request_ids[0]
    if operation_type == "workflow" and len(request_ids) == 1:
        result["id"] = request_ids[0]
        result.setdefault("execution_backend", "workflow_invocation")
    if operation_type == "upload" and len(outputs) == 1 and outputs[0].get("src") == "hda":
        output = outputs[0]
        result.update(
            {
                "id": output.get("id", ""),
                "name": output.get("name", ""),
                "file_type": output.get("extension", ""),
                "file_size": output.get("file_size", 0),
                "data_type": output.get("data_type", ""),
            }
        )
    return result


def _failure_result(receipt, jobs, outputs, error_kind):
    result = _complete_result(receipt, jobs, outputs)
    result.update(
        {
            "success": False,
            "state": "failed",
            "error_kind": error_kind or "operation_failed",
            "submission_state": "submitted",
            "retry_safe": False,
            "recommended_action": "do_not_resubmit",
        }
    )
    return result


def _output_state(output):
    state = str(output.get("state", "") or "").lower()
    if not state and output.get("src") == "hdca" and output.get("populated") is True:
        return "ok"
    return state


def _refresh_outputs_until_terminal(
    client,
    history_id,
    output_refs,
    deadline,
    poll_interval,
):
    """Refresh known outputs under the operation's single deadline."""
    pending_states = {
        "",
        "unknown",
        "new",
        "upload",
        "waiting",
        "queued",
        "running",
        "setting_metadata",
        "paused",
    }
    failure_states = {"error", "failed", "discarded", "cancelled"}
    attempt = 0
    while True:
        outputs = refresh_output_details(
            client,
            history_id,
            output_refs,
            require_complete=False,
            deadline=deadline,
        )
        states = [_output_state(output) for output in outputs]
        if any(state in failure_states for state in states):
            return outputs, "output_failed"
        if states and all(state == "ok" for state in states):
            return outputs, ""
        unexpected = [
            state for state in states if state not in pending_states and state != "ok"
        ]
        if unexpected:
            return outputs, "output_failed"
        if remaining(deadline) <= 0:
            raise GalaxyBackendError(
                "Timed out waiting for Galaxy output metadata to reach a final state.",
                category="timeout",
                error_kind="output_timeout",
                submission_state="submitted",
                retry_safe=False,
                details={
                    "history_id": history_id,
                    "output_ids": [
                        output.get("id") for output in outputs if output.get("id")
                    ],
                },
            )
        attempt = sleep_for_poll(
            attempt, deadline, poll_interval=poll_interval
        )


def _persist_final(receipt, result, error_kind=""):
    failed = bool(error_kind)
    receipt.update(
        {
            "state": "failed" if failed else "complete",
            "submission_state": "submitted",
            "retry_safe": False,
            "error_kind": error_kind,
            "resumable": False,
            "success": not failed,
            "final_state": "failed" if failed else "ok",
            "exit_code": result.get("exit_code"),
            "jobs": result["jobs"],
            "outputs": result["outputs"],
            "output_ids": _merge_ids(
                receipt.get("output_ids"),
                [output.get("id") for output in result["outputs"]],
            ),
            "result": result,
        }
    )
    if failed:
        receipt["reason"] = "terminal_operation_failure"
        receipt["recommended_action"] = "do_not_resubmit"
    else:
        receipt.pop("reason", None)
        receipt.pop("recommended_action", None)
    _save(receipt)
    return receipt


def _record_error(receipt, error):
    details = dict(getattr(error, "details", {}) or {})
    receipt["job_ids"] = _merge_ids(receipt.get("job_ids"), details.get("job_ids"))
    receipt["output_ids"] = _merge_ids(
        receipt.get("output_ids"), details.get("output_ids")
    )
    if details.get("jobs"):
        receipt["jobs"] = details["jobs"]
    elif receipt.get("jobs"):
        details["jobs"] = receipt["jobs"]
    kind = getattr(error, "error_kind", None) or getattr(error, "category", "unknown")
    submission_state = getattr(error, "submission_state", None)
    prior_kind = receipt.get("error_kind", "")
    prior_terminal = (
        receipt.get("state") == "failed" or prior_kind in _TERMINAL_ERROR_KINDS
    )
    receipt["retry_safe"] = False
    if prior_terminal and kind not in _TERMINAL_ERROR_KINDS:
        receipt["last_resume_error_kind"] = kind
    elif kind in _TERMINAL_ERROR_KINDS:
        receipt["error_kind"] = kind
        receipt["state"] = "failed"
        receipt["submission_state"] = "submitted"
    elif submission_state == "unknown":
        receipt["error_kind"] = kind
        receipt["state"] = "unknown"
        receipt["submission_state"] = "unknown"
    else:
        receipt["error_kind"] = kind
        receipt["state"] = "submitted"
        receipt["submission_state"] = "submitted"
    details["operation_receipt"] = receipt["id"]
    error.details = details
    _save(receipt)


def _tus_file_identity_reason(resume):
    expected_size = resume.get("local_file_size")
    expected_sha256 = resume.get("local_file_sha256")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
        or not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
    ):
        return "tus_local_file_identity_unavailable"
    try:
        source = Path(resume.get("local_path"))
        if not source.is_file():
            return "tus_local_file_unavailable"
        if source.stat().st_size != expected_size:
            return "tus_local_file_changed"
    except (OSError, TypeError, ValueError):
        return "tus_local_file_unavailable"
    return ""


def _resume_tus(client, receipt):
    resume = receipt.get("resume", {})
    phase = resume.get("fetch_submission_state")
    if phase in {"attempting", "unknown"}:
        return _non_resumable(receipt, "tus_fetch_submission_state_unknown")
    if phase == "submitted":
        return None
    identity_reason = _tus_file_identity_reason(resume)
    if identity_reason:
        return _non_resumable(receipt, identity_reason)

    fetch_started = {"value": False}

    def before_fetch_submit():
        fetch_started["value"] = True
        resume["fetch_submission_state"] = "attempting"
        receipt["resume"] = resume
        receipt["state"] = "submitted"
        _save(receipt)

    try:
        result = client.resume_tus_upload_file(
            resume["local_path"],
            receipt.get("history_id", ""),
            resume["tus_session_id"],
            file_type=resume.get("file_type", "auto"),
            dbkey=resume.get("dbkey", "?"),
            before_fetch_submit=before_fetch_submit,
            expected_size=resume["local_file_size"],
            expected_sha256=resume["local_file_sha256"],
        )
    except GalaxyBackendError as exc:
        if exc.error_kind in {
            "tus_local_file_changed",
            "tus_local_file_unavailable",
        }:
            return _non_resumable(receipt, exc.error_kind)
        if fetch_started["value"]:
            resume["fetch_submission_state"] = "unknown"
            receipt["resume"] = resume
            receipt["state"] = "unknown"
            receipt["submission_state"] = "unknown"
            exc.submission_state = "unknown"
            exc.retry_safe = False
        _record_error(receipt, exc)
        raise
    resume["fetch_submission_state"] = "submitted"
    receipt["resume"] = resume
    receipt["state"] = "submitted"
    receipt["submission_state"] = "submitted"
    receipt["error_kind"] = ""
    receipt["job_ids"] = _merge_ids(
        receipt.get("job_ids"),
        [
            job.get("id")
            for job in (result.get("jobs", []) if isinstance(result, dict) else [])
            if isinstance(job, dict) and job.get("id")
        ],
    )
    refs = _source_output_refs(result, operation_type="upload")
    receipt["output_refs"] = _merge_output_refs(receipt.get("output_refs"), refs)
    receipt["output_ids"] = _merge_ids(
        receipt.get("output_ids"), [item["id"] for item in refs]
    )
    _save(receipt)
    return None


def resume_operation(client, reference, timeout=1800, poll_interval=None):
    """Recover an operation using only known read-side identifiers.

    Mutating tool, UDT, workflow, and upload submissions are never replayed.
    The sole special case is completing a recorded interrupted TUS transfer;
    its final fetch submission is protected by a durable write-ahead marker.
    """
    source_path = _path(reference)
    receipt = show_receipt(source_path)
    receipt_id = _receipt_id(receipt)
    canonical_path = operation_dir() / f"{receipt_id}.json"
    if not canonical_path.exists():
        imported = source_path.resolve() != canonical_path.resolve()
        receipt = _redact(client, receipt)
        resume = receipt.get("resume", {})
        if imported and resume.get("tus_session_id"):
            resume["tus_mutation_allowed"] = False
            receipt["resume"] = resume
        _save(receipt)
    reference = receipt_id
    receipt = show_receipt(reference)
    if (
        receipt.get("final_state") in {"ok", "failed"}
        and isinstance(receipt.get("result"), dict)
    ):
        return receipt

    resume = receipt.get("resume", {})
    if receipt.get("operation_type") == "upload" and resume.get("tus_session_id"):
        if resume.get("tus_mutation_allowed") is False:
            return _non_resumable(receipt, "external_tus_receipt_untrusted")
        with _receipt_lock(receipt_id):
            receipt = show_receipt(reference)
            resume = receipt.get("resume", {})
            if resume.get("fetch_submission_state") in {"attempting", "unknown"}:
                return _non_resumable(receipt, "tus_fetch_submission_state_unknown")
            if (
                resume.get("fetch_submission_state") != "submitted"
                and receipt.get("error_kind")
                in {
                    "tus_upload_interrupted",
                    "tus_resume_unavailable",
                    "tus_local_file_changed",
                    "tus_local_file_unavailable",
                }
            ):
                blocked = _resume_tus(client, receipt)
                if blocked is not None:
                    return blocked
            receipt = show_receipt(reference)

    if not any(
        (
            receipt.get("request_ids"),
            receipt.get("job_ids"),
            receipt.get("output_ids"),
            receipt.get("output_refs"),
        )
    ):
        return _non_resumable(receipt)

    deadline = deadline_after(timeout)
    terminal_kind = ""
    if (
        receipt.get("state") == "failed"
        or receipt.get("error_kind") in _TERMINAL_ERROR_KINDS
    ):
        terminal_kind = receipt.get("error_kind") or "operation_failed"
    try:
        job_ids, output_refs, request_errors = _discover_requests(
            client, receipt, deadline, poll_interval
        )
        if request_errors:
            terminal_kind = request_errors[0]
        receipt["job_ids"] = _merge_ids(receipt.get("job_ids"), job_ids)
        receipt["output_refs"] = _merge_output_refs(
            receipt.get("output_refs"), output_refs
        )
        receipt["output_ids"] = _merge_ids(
            receipt.get("output_ids"),
            [item["id"] for item in receipt["output_refs"]],
        )
        _save(receipt)

        if (
            not receipt["job_ids"]
            and not receipt.get("output_refs")
            and not receipt.get("output_ids")
            and receipt.get("operation_type") != "workflow"
            and not terminal_kind
        ):
            raise GalaxyBackendError(
                "No known jobs were discovered for the submitted operation.",
                category="api_error",
                error_kind="unexpected_response",
                exit_code=EXIT_SERVER_ERROR,
                submission_state="submitted",
                retry_safe=False,
                details={
                    "history_id": receipt.get("history_id", ""),
                    "request_ids": receipt.get("request_ids", []),
                    "job_ids": [],
                    "output_ids": receipt.get("output_ids", []),
                },
            )

        try:
            jobs = wait_for_jobs(
                client,
                receipt["job_ids"],
                timeout=remaining(deadline),
                poll_interval=poll_interval,
                history_id=receipt.get("history_id", ""),
                tool_id=receipt.get("tool_id", ""),
                request_ids=receipt.get("request_ids", []),
                output_ids=receipt.get("output_ids", []),
                deadline=deadline,
            ) if receipt["job_ids"] else []
        except GalaxyBackendError as exc:
            if exc.error_kind not in _TERMINAL_ERROR_KINDS:
                raise
            terminal_kind = exc.error_kind
            jobs = list((exc.details or {}).get("jobs", []))
        receipt["jobs"] = jobs
        _save(receipt)
        job_details = [
            get_with_deadline(client, f"jobs/{job_id}", deadline=deadline)
            for job_id in receipt["job_ids"]
        ]
        output_refs = _merge_output_refs(
            receipt.get("output_refs"), _job_outputs(job_details)
        )
        known_output_ids = {str(output["id"]) for output in output_refs}
        history_id = receipt.get("history_id", "")
        for output_id in receipt.get("output_ids", []):
            if str(output_id) not in known_output_ids:
                output_refs = _merge_output_refs(
                    output_refs,
                    [_lookup_output_ref(client, history_id, output_id, deadline)],
                )
                known_output_ids.add(str(output_id))
        outputs = []
        if output_refs:
            outputs, output_error = _refresh_outputs_until_terminal(
                client,
                history_id,
                output_refs,
                deadline,
                poll_interval,
            )
            if output_error:
                terminal_kind = terminal_kind or output_error
        if (
            receipt.get("operation_type") == "upload"
            and not any(output.get("src") == "hda" for output in outputs)
        ):
            terminal_kind = terminal_kind or "upload_outputs_missing"
        result = (
            _failure_result(receipt, jobs, outputs, terminal_kind)
            if terminal_kind
            else _complete_result(receipt, jobs, outputs)
        )
        result = _redact(client, result)
    except GalaxyBackendError as exc:
        _record_error(receipt, exc)
        raise
    return _persist_final(receipt, result, terminal_kind)
