"""Dataset management — upload, download, show, delete, peek at datasets."""

import csv

from galaxy_cli.utils.galaxy_backend import (
    EXIT_USER_ERROR,
    GalaxyClient,
    GalaxyBackendError,
)
from galaxy_cli.core.job import wait_for_jobs


def _upload_submission_error(exc, history_id):
    status_code = getattr(exc, "status_code", None)
    rejected = status_code in {400, 422}
    details = dict(getattr(exc, "details", {}) or {})
    details.update(
        {
            "history_id": history_id,
            "tool_id": "upload1",
            "job_ids": [],
            "output_ids": [],
        }
    )
    known_submission = getattr(exc, "submission_state", None)
    known_retry = getattr(exc, "retry_safe", None)
    return GalaxyBackendError(
        str(exc),
        category=exc.category,
        error_kind=getattr(exc, "error_kind", None) or (
            "upload_submission_rejected" if rejected else "upload_submission_unknown"
        ),
        exit_code=exc.exit_code,
        suggestion=exc.suggestion,
        status_code=status_code,
        submission_state=known_submission or ("not_submitted" if rejected else "unknown"),
        retry_safe=known_retry if known_retry is not None else rejected,
        details=details,
    )


def upload_dataset(
    client,
    history_id,
    file_path,
    file_type="auto",
    dbkey="?",
    wait=False,
    timeout=1800,
    upload_timeout=None,
    poll_interval=30,
    upload_backend="auto",
):
    """Upload a local file to a Galaxy history."""
    if upload_backend not in {"auto", "tus", "legacy"}:
        raise GalaxyBackendError(
            f"Invalid upload backend: {upload_backend}", category="invalid_request",
            exit_code=EXIT_USER_ERROR, submission_state="not_submitted", retry_safe=True,
        )
    selected_backend = upload_backend
    if upload_backend == "auto":
        if isinstance(client, GalaxyClient):
            from galaxy_cli.core.server import server_capabilities
            capabilities = server_capabilities(client)
            selected_backend = "tus" if capabilities["capabilities"].get("tus_upload") else "legacy"
        else:
            selected_backend = "legacy"
    try:
        uploader = client.tus_upload_file if selected_backend == "tus" else client.upload_file
        result = uploader(
            file_path,
            history_id,
            file_type=file_type,
            dbkey=dbkey,
            upload_timeout=upload_timeout,
        )
    except GalaxyBackendError as exc:
        if (
            upload_backend == "auto" and selected_backend == "tus"
            and exc.status_code in {404, 405}
            and getattr(exc, "submission_state", None) in {None, "not_submitted"}
        ):
            selected_backend = "legacy"
            try:
                result = client.upload_file(
                    file_path, history_id, file_type=file_type, dbkey=dbkey,
                    upload_timeout=upload_timeout,
                )
            except GalaxyBackendError as fallback_exc:
                raise _upload_submission_error(fallback_exc, history_id) from fallback_exc
        else:
            raise _upload_submission_error(exc, history_id) from exc
    malformed = not isinstance(result, dict)
    if not isinstance(result, dict):
        result = {}
    raw_outputs = result.get("outputs")
    raw_jobs = result.get("jobs")
    for value in (raw_outputs, raw_jobs):
        if value is not None and (
            not isinstance(value, list)
            or any(not isinstance(item, dict) for item in value)
        ):
            malformed = True
    outputs = [
        item for item in (raw_outputs or []) if isinstance(item, dict)
    ]
    jobs = [item for item in (raw_jobs or []) if isinstance(item, dict)]
    job_ids = [job.get("id", "") for job in jobs if job.get("id")]
    output_ids = [output.get("id", "") for output in outputs if output.get("id")]
    if wait and malformed:
        raise GalaxyBackendError(
            "Galaxy returned a malformed upload submission response.",
            category="api_error",
            error_kind="upload_response_invalid",
            submission_state="submitted" if job_ids or output_ids else "unknown",
            retry_safe=False,
            details={
                "history_id": history_id,
                "tool_id": "upload1",
                "job_ids": job_ids,
                "output_ids": output_ids,
            },
        )
    wait_results = None
    if wait and not job_ids:
        raise GalaxyBackendError(
            "Galaxy returned no jobs for the blocking upload submission.",
            category="api_error",
            error_kind="unknown_submission_state",
            submission_state="unknown",
            retry_safe=False,
            details={
                "history_id": history_id,
                "tool_id": "upload1",
                "job_ids": [],
                "output_ids": output_ids,
            },
        )
    if wait:
        wait_results = wait_for_jobs(
            client,
            job_ids,
            timeout=timeout,
            poll_interval=poll_interval,
            history_id=history_id,
            tool_id="upload1",
            output_ids=output_ids,
        )
        if not outputs:
            raise GalaxyBackendError(
                "Galaxy returned no dataset outputs for the completed upload.",
                category="api_error",
                error_kind="upload_outputs_missing",
                submission_state="submitted",
                retry_safe=False,
                details={
                    "history_id": history_id,
                    "tool_id": "upload1",
                    "job_ids": job_ids,
                    "jobs": wait_results,
                    "output_ids": [],
                },
            )
    if outputs:
        ds = outputs[0]
        uploaded = {
            "id": ds.get("id", ""),
            "name": ds.get("name", ""),
            "state": ds.get("state", ""),
            "history_id": history_id,
            "file_type": ds.get("extension", file_type),
            "execution_backend": selected_backend,
        }
        if wait:
            uploaded["wait_results"] = wait_results
            if uploaded["id"]:
                try:
                    refreshed = show_dataset(
                        client, uploaded["id"], history_id=history_id
                    )
                except GalaxyBackendError as exc:
                    details = dict(getattr(exc, "details", {}) or {})
                    details.update(
                        {
                            "history_id": history_id,
                            "tool_id": "upload1",
                            "job_ids": job_ids,
                            "jobs": wait_results,
                            "output_ids": output_ids,
                        }
                    )
                    exc.details = details
                    exc.error_kind = (
                        getattr(exc, "error_kind", None)
                        or "upload_output_refresh_failed"
                    )
                    exc.submission_state = "submitted"
                    exc.retry_safe = False
                    raise
                uploaded.update({
                    "state": refreshed.get("state", uploaded["state"]),
                    "file_type": refreshed.get("extension", uploaded["file_type"]),
                    "file_size": refreshed.get("file_size", 0),
                    "data_type": refreshed.get("data_type", ""),
                    "misc_blurb": refreshed.get("misc_blurb", ""),
                })
            uploaded.update({
                "success": True,
                "execution_backend": selected_backend,
                "tool_id": "upload1",
                "jobs": wait_results,
                "outputs": [{
                    "output_name": "output",
                    "id": uploaded.get("id", ""),
                    "src": "hda",
                    "state": uploaded.get("state", ""),
                    "extension": uploaded.get("file_type", ""),
                    "file_size": uploaded.get("file_size", 0),
                }],
            })
        return uploaded
    return {"status": "uploaded", "history_id": history_id, "execution_backend": selected_backend, "raw": result}


def show_dataset(client, dataset_id, history_id=None):
    """Show details of a dataset."""
    if not dataset_id:
        raise GalaxyBackendError(
            "Dataset ID is required.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
            suggestion="Set DATASET_ID first or pass a non-empty dataset ID.",
        )
    if history_id:
        info = client.get(f"histories/{history_id}/contents/{dataset_id}")
    else:
        info = client.get(f"datasets/{dataset_id}")
    if not isinstance(info, dict):
        raise GalaxyBackendError(
            f"Unexpected response while looking up dataset '{dataset_id}'.",
            category="api_error",
            suggestion="Verify the dataset ID is correct and non-empty.",
        )
    return {
        "id": info.get("id", ""),
        "name": info.get("name", ""),
        "state": info.get("state", ""),
        "extension": info.get("extension", ""),
        "file_size": info.get("file_size", 0),
        "genome_build": info.get("genome_build", "?"),
        "data_type": info.get("data_type", ""),
        "create_time": info.get("create_time", ""),
        "update_time": info.get("update_time", ""),
        "deleted": info.get("deleted", False),
        "visible": info.get("visible", True),
        "metadata": info.get("metadata", {}),
        "history_id": info.get("history_id", history_id or ""),
        "history_content_type": info.get("history_content_type", ""),
        "misc_info": info.get("misc_info", ""),
        "misc_blurb": info.get("misc_blurb", ""),
    }


def download_dataset(client, dataset_id, output_path, history_id=None):
    """Download a dataset to a local file."""
    return client.download_dataset(dataset_id, output_path)


def _normalize_delimiter(delimiter):
    if delimiter is None:
        return None
    aliases = {
        "tab": "\t",
        "\\t": "\t",
        "t": "\t",
        "comma": ",",
        "csv": ",",
        "space": " ",
    }
    return aliases.get(delimiter, delimiter)


def _detect_delimiter(line):
    if "\t" in line:
        return "\t"
    if "," in line:
        return ","
    return None


def _preview_rows(raw_lines, max_chars_per_line=500, max_fields=20, delimiter=None):
    normalized_delimiter = _normalize_delimiter(delimiter)
    compact_lines = []
    rows = []
    for index, raw_line in enumerate(raw_lines, start=1):
        line = str(raw_line).rstrip("\n")
        row = {"line_number": index}
        preview = line
        active_delimiter = normalized_delimiter or _detect_delimiter(line)

        if active_delimiter:
            try:
                fields = next(csv.reader([line], delimiter=active_delimiter))
            except csv.Error:
                fields = line.split(active_delimiter)
            field_count = len(fields)
            if max_fields and field_count > max_fields:
                preview_fields = fields[:max_fields]
                preview = active_delimiter.join(preview_fields)
                row["truncated_fields"] = True
            else:
                preview_fields = fields if not max_fields else fields[:max_fields]
                row["truncated_fields"] = False
            row["delimiter"] = active_delimiter
            row["field_count"] = field_count
            row["fields"] = preview_fields

        if max_chars_per_line and len(preview) > max_chars_per_line:
            preview = preview[:max_chars_per_line]
            row["truncated_chars"] = True
        else:
            row["truncated_chars"] = False

        row["text"] = preview
        compact_lines.append(preview)
        rows.append(row)
    return compact_lines, rows


def _peek_result(dataset_id, raw_lines, lines, max_chars_per_line, max_fields, delimiter):
    selected = [str(line).rstrip("\n") for line in raw_lines[:lines]]
    preview_lines, rows = _preview_rows(
        selected,
        max_chars_per_line=max_chars_per_line,
        max_fields=max_fields,
        delimiter=delimiter,
    )
    return {
        "id": dataset_id,
        "lines": preview_lines,
        "rows": rows,
        "total_shown": len(preview_lines),
        "max_chars_per_line": max_chars_per_line,
        "max_fields": max_fields,
    }


def peek_dataset(
    client,
    dataset_id,
    lines=10,
    history_id=None,
    max_chars_per_line=500,
    max_fields=20,
    delimiter=None,
):
    """Get a preview of dataset contents."""
    info = client.get(
        f"datasets/{dataset_id}",
        params={
            "data_type": "raw_data",
            "provider": "base",
            "offset": 0,
            "limit": lines,
        },
    )
    # Some Galaxy deployments expose preview text under `peek`.
    if "peek" in info:
        raw_peek = info["peek"]
        return _peek_result(
            dataset_id,
            raw_peek.strip().split("\n"),
            lines,
            max_chars_per_line,
            max_fields,
            delimiter,
        )

    # usegalaxy.org commonly returns preview rows under `data`.
    raw_data = info.get("data")
    if isinstance(raw_data, list):
        return _peek_result(
            dataset_id,
            raw_data,
            lines,
            max_chars_per_line,
            max_fields,
            delimiter,
        )
    if isinstance(raw_data, str) and raw_data.strip():
        return _peek_result(
            dataset_id,
            raw_data.strip().split("\n"),
            lines,
            max_chars_per_line,
            max_fields,
            delimiter,
        )

    return {
        "id": dataset_id,
        "lines": [],
        "rows": [],
        "total_shown": 0,
        "max_chars_per_line": max_chars_per_line,
        "max_fields": max_fields,
        "note": "Preview not available",
    }


def delete_dataset(client, dataset_id, history_id, purge=False):
    """Delete a dataset from a history."""
    payload = {"deleted": True}
    if purge:
        payload["purged"] = True
    client.put(f"histories/{history_id}/contents/{dataset_id}", json_data=payload)
    return {"id": dataset_id, "history_id": history_id, "status": "deleted", "purged": purge}


def list_datasets(client, history_id, limit=50, offset=0, deleted=False):
    """List datasets in a history."""
    params = {"limit": limit, "offset": offset}
    if deleted:
        params["deleted"] = True
    items = client.get(f"histories/{history_id}/contents", params=params)
    return [
        {
            "id": item["id"],
            "name": item.get("name", ""),
            "type": item.get("type", ""),
            "state": item.get("state", ""),
            "extension": item.get("extension", ""),
            "file_size": item.get("file_size", 0),
            "deleted": item.get("deleted", False),
            "visible": item.get("visible", True),
        }
        for item in items
        if item.get("type") in ("file", "dataset")
    ]
