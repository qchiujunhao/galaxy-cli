"""Stable opt-in output envelope and mechanical follow-up commands."""

import shlex


SCHEMA_VERSION = "1.0"
_FINAL_STATES = {"ok", "complete", "completed", "ready"}


def _command(*parts):
    return " ".join(shlex.quote(str(part)) for part in parts)


def _unique_dicts(values):
    seen = set()
    result = []
    for value in values or []:
        if not isinstance(value, dict) or not value.get("id"):
            continue
        key = (value.get("src"), str(value["id"]))
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _outputs(payload, command):
    outputs = _unique_dicts(payload.get("outputs", []))
    if outputs:
        return outputs
    if command == "dataset.upload" and payload.get("id"):
        return [{"id": payload["id"], "src": "hda"}]
    return []


def _output_is_ready(payload, output):
    """Treat omitted states as compatible, but never suggest transient outputs."""
    for state in (
        payload.get("state"),
        payload.get("final_state"),
        output.get("state"),
        output.get("populated_state"),
    ):
        if state not in (None, "") and str(state).lower() not in _FINAL_STATES:
            return False
    return True


def safe_next_commands(payload, command=""):
    """Build bounded commands from IDs already present in *payload*.

    The helper never reconstructs or recommends a mutating submission.
    """
    if not isinstance(payload, dict):
        return {}

    success = payload.get("success") is not False and not payload.get("error")
    submission_state = payload.get("submission_state")
    retry_safe = payload.get("retry_safe")
    receipt = payload.get("operation_receipt")
    if command == "operation.resume" and not receipt:
        receipt = payload.get("id")
    result = {}

    if success:
        outputs = _outputs(payload, command)
        if len(outputs) == 1 and _output_is_ready(payload, outputs[0]):
            output = outputs[0]
            output_id = str(output["id"])
            is_collection = (
                output.get("src") == "hdca"
                or output.get("history_content_type") == "dataset_collection"
            )
            if is_collection:
                result["show_output_collection"] = _command(
                    "galaxy-cli", "collection", "show", output_id
                )
                result["use_output_as_input"] = f"hdca:{output_id}"
            else:
                result["preview_output"] = _command(
                    "galaxy-cli", "dataset", "preview", output_id, "--lines", 5
                )
                result["use_output_as_input"] = f"hda:{output_id}"
        if (
            payload.get("state") in {"submitted", "running", "queued", "new"}
            and receipt
            and payload.get("resumable") is not False
        ):
            result["resume"] = _command(
                "galaxy-cli", "operation", "resume", receipt
            )
    else:
        jobs = _unique_dicts([
            *(payload.get("jobs", []) or []),
            *(
                {"id": value}
                for value in payload.get("job_ids", []) or []
                if value
            ),
        ])
        if len(jobs) == 1:
            result["diagnose"] = _command(
                "galaxy-cli", "job", "diagnose", jobs[0]["id"]
            )
        if (
            receipt
            and submission_state != "not_submitted"
            and payload.get("resumable") is not False
        ):
            result["resume"] = _command(
                "galaxy-cli", "operation", "resume", receipt
            )

    if (
        submission_state == "unknown"
        or retry_safe is False
        or payload.get("recommended_action") == "do_not_resubmit"
    ):
        result["do_not_resubmit"] = True
    return result


def envelope_v1(command, payload):
    """Wrap a redacted result without changing the legacy payload itself."""
    success = not (
        isinstance(payload, dict)
        and (payload.get("success") is False or payload.get("error") is True)
    )
    warnings = payload.get("warnings", []) if isinstance(payload, dict) else []
    if not isinstance(warnings, list):
        warnings = [warnings]
    warnings = [str(warning) for warning in warnings]
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "success": success,
        "data": payload,
        "warnings": warnings,
        "next_commands": safe_next_commands(payload, command),
    }
