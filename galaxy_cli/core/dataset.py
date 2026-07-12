"""Dataset management — upload, download, show, delete, peek at datasets."""

import csv
import os
import re
import tempfile
from collections import deque
from pathlib import Path

from galaxy_cli.utils.galaxy_backend import (
    DEFAULT_CONFIG_DIR,
    EXIT_USER_ERROR,
    GalaxyClient,
    GalaxyBackendError,
    get_with_deadline,
)
from galaxy_cli.core.job import wait_for_jobs
from galaxy_cli.core.polling import deadline_after, remaining


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
    poll_interval=None,
    upload_backend="auto",
    progress=None,
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
        upload_kwargs = {
            "file_type": file_type,
            "dbkey": dbkey,
            "upload_timeout": upload_timeout,
        }
        if selected_backend == "tus":
            upload_kwargs["progress"] = progress
        result = uploader(file_path, history_id, **upload_kwargs)
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
    if malformed:
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
    if not wait and not job_ids and not output_ids:
        raise GalaxyBackendError(
            "Galaxy returned no recoverable identifiers for the upload submission.",
            category="api_error",
            error_kind="unknown_submission_state",
            submission_state="unknown",
            retry_safe=False,
            details={
                "history_id": history_id,
                "tool_id": "upload1",
                "job_ids": [],
                "output_ids": [],
            },
        )
    deadline = deadline_after(timeout) if wait else None
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
            timeout=remaining(deadline),
            poll_interval=poll_interval,
            history_id=history_id,
            tool_id="upload1",
            output_ids=output_ids,
            deadline=deadline,
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
            "job_ids": job_ids,
            "output_ids": output_ids,
            "jobs": jobs,
            "outputs": outputs,
        }
        if wait:
            uploaded["wait_results"] = wait_results
            if uploaded["id"]:
                try:
                    refreshed = show_dataset(
                        client,
                        uploaded["id"],
                        history_id=history_id,
                        deadline=deadline,
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
    return {
        "status": "uploaded",
        "history_id": history_id,
        "execution_backend": selected_backend,
        "job_ids": job_ids,
        "output_ids": output_ids,
        "jobs": jobs,
        "outputs": outputs,
        "raw": result,
    }


def show_dataset(client, dataset_id, history_id=None, deadline=None):
    """Show details of a dataset."""
    if not dataset_id:
        raise GalaxyBackendError(
            "Dataset ID is required.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
            suggestion="Set DATASET_ID first or pass a non-empty dataset ID.",
        )
    if history_id:
        info = get_with_deadline(
            client,
            f"histories/{history_id}/contents/{dataset_id}",
            deadline=deadline,
        )
    else:
        info = get_with_deadline(
            client, f"datasets/{dataset_id}", deadline=deadline
        )
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


def download_dataset(
    client, dataset_id, output_path, history_id=None, max_bytes=None
):
    """Download a dataset to a local file."""
    if max_bytes is not None and isinstance(client, GalaxyClient):
        return client.download_dataset(
            dataset_id, output_path, max_bytes=max_bytes
        )
    return client.download_dataset(dataset_id, output_path)


MAX_PREVIEW_LINES = 1000
MAX_PREVIEW_FIELDS = 100
MAX_PREVIEW_FIELD_INDEX = 1000
MAX_PREVIEW_CHARS_PER_LINE = 10000
MAX_PREVIEW_TOTAL_CHARS = 100000
MAX_PREVIEW_SOURCE_LINE_CHARS = 65536
MAX_PREVIEW_CONTEXT = 50
MAX_PREVIEW_PATTERN_CHARS = 500
MAX_PREVIEW_DOWNLOAD_BYTES = 5 * 1024 * 1024


def _preview_error(message, error_kind="preview_invalid", suggestion=None, details=None):
    return GalaxyBackendError(
        message,
        category="invalid_request",
        error_kind=error_kind,
        exit_code=EXIT_USER_ERROR,
        suggestion=suggestion,
        details=details,
    )


def _bounded_integer(value, label, maximum, *, zero_allowed=True, zero_as_max=False):
    if isinstance(value, bool):
        raise _preview_error(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _preview_error(f"{label} must be an integer.") from exc
    if parsed == 0 and zero_as_max:
        return maximum
    minimum = 0 if zero_allowed else 1
    if parsed < minimum or parsed > maximum:
        raise _preview_error(
            f"{label} must be between {minimum} and {maximum}.",
            error_kind="preview_limit_invalid",
            details={"limit": maximum},
        )
    return parsed


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
    normalized = aliases.get(delimiter, delimiter)
    if not isinstance(normalized, str) or len(normalized) != 1:
        raise _preview_error(
            "delimiter must be tab, comma, space, or one character.",
            error_kind="preview_delimiter_invalid",
        )
    return normalized


def _detect_delimiter(line):
    if "\t" in line:
        return "\t"
    if "," in line:
        return ","
    return None


def _normalize_fields(fields, max_fields):
    if fields is None:
        return None
    values = fields.split(",") if isinstance(fields, str) else list(fields)
    if not values or any(str(value).strip() == "" for value in values):
        raise _preview_error(
            "fields must be a comma-separated list of one-based field numbers.",
            error_kind="preview_fields_invalid",
        )
    selected = []
    for value in values:
        index = _bounded_integer(
            str(value).strip(),
            "field index",
            MAX_PREVIEW_FIELD_INDEX,
            zero_allowed=False,
        )
        if index not in selected:
            selected.append(index)
    if len(selected) > max_fields:
        raise _preview_error(
            f"fields selects {len(selected)} columns; the active limit is {max_fields}.",
            error_kind="preview_fields_limit",
            details={"field_limit": max_fields},
        )
    return selected


def _preview_rows(
    raw_lines,
    max_chars_per_line=500,
    max_fields=20,
    delimiter=None,
    fields=None,
    line_numbers=None,
):
    normalized_delimiter = _normalize_delimiter(delimiter)
    compact_lines = []
    rows = []
    total_chars = 0
    truncated_total = False
    numbers = line_numbers or range(1, len(raw_lines) + 1)
    for line_number, raw_line in zip(numbers, raw_lines):
        line = str(raw_line).rstrip("\r\n")
        source_line = line[:MAX_PREVIEW_SOURCE_LINE_CHARS]
        row = {
            "line_number": line_number,
            "source_truncated": len(source_line) < len(line),
        }
        preview = source_line
        active_delimiter = normalized_delimiter or _detect_delimiter(source_line)

        if active_delimiter:
            try:
                parsed_fields = next(csv.reader([source_line], delimiter=active_delimiter))
            except (csv.Error, TypeError):
                parsed_fields = source_line.split(active_delimiter)
            field_count = len(parsed_fields)
            if fields is not None:
                preview_fields = [
                    parsed_fields[index - 1]
                    for index in fields
                    if index <= field_count
                ]
                row["selected_fields"] = list(fields)
                row["missing_fields"] = [index for index in fields if index > field_count]
                preview = active_delimiter.join(preview_fields)
                row["truncated_fields"] = field_count > len(preview_fields)
            elif field_count > max_fields:
                preview_fields = parsed_fields[:max_fields]
                preview = active_delimiter.join(preview_fields)
                row["truncated_fields"] = True
            else:
                preview_fields = parsed_fields
                row["truncated_fields"] = False
            row["delimiter"] = active_delimiter
            row["field_count"] = field_count

        if len(preview) > max_chars_per_line:
            preview = preview[:max_chars_per_line]
            row["truncated_chars"] = True
        else:
            row["truncated_chars"] = False

        remaining = MAX_PREVIEW_TOTAL_CHARS - total_chars
        if remaining <= 0:
            truncated_total = True
            break
        if len(preview) > remaining:
            preview = preview[:remaining]
            row["truncated_chars"] = True
            row["truncated_total"] = True
            truncated_total = True

        row["text"] = preview
        if active_delimiter:
            try:
                row["fields"] = next(
                    csv.reader([preview], delimiter=active_delimiter)
                ) if preview else []
            except (csv.Error, TypeError):
                row["fields"] = preview.split(active_delimiter) if preview else []
        compact_lines.append(preview)
        rows.append(row)
        total_chars += len(preview) + 1
    if len(rows) < len(raw_lines):
        truncated_total = True
    return compact_lines, rows, {
        "total_chars": min(total_chars, MAX_PREVIEW_TOTAL_CHARS),
        "max_total_chars": MAX_PREVIEW_TOTAL_CHARS,
        "truncated_total": truncated_total,
    }


def _peek_result(
    dataset_id,
    raw_lines,
    lines,
    max_chars_per_line,
    max_fields,
    delimiter,
    *,
    fields=None,
    line_numbers=None,
    selector=None,
    selection_truncated=False,
    scan=None,
):
    selected = [str(line).rstrip("\r\n") for line in raw_lines[:lines]]
    selected_numbers = list(line_numbers[:lines]) if line_numbers is not None else None
    preview_lines, rows, bounds = _preview_rows(
        selected,
        max_chars_per_line=max_chars_per_line,
        max_fields=max_fields,
        delimiter=delimiter,
        fields=fields,
        line_numbers=selected_numbers,
    )
    result = {
        "id": dataset_id,
        "lines": preview_lines,
        "rows": rows,
        "total_shown": len(preview_lines),
        "max_chars_per_line": max_chars_per_line,
        "max_fields": max_fields,
        "selector": selector or {"mode": "head", "limit": lines},
        "truncated": bool(
            selection_truncated
            or bounds["truncated_total"]
            or any(
                row.get("source_truncated")
                or row.get("truncated_chars")
                or row.get("truncated_fields")
                for row in rows
            )
        ),
        "truncation": bounds,
    }
    if fields is not None:
        result["selected_fields"] = list(fields)
    if scan is not None:
        result["scan"] = scan
    return result


def _compile_grep(pattern):
    if pattern is None:
        return None
    pattern = str(pattern)
    if len(pattern) > MAX_PREVIEW_PATTERN_CHARS:
        raise _preview_error(
            f"grep pattern exceeds {MAX_PREVIEW_PATTERN_CHARS} characters.",
            error_kind="preview_pattern_limit",
        )
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise _preview_error(
            f"Invalid grep pattern: {exc}.",
            error_kind="preview_pattern_invalid",
        ) from exc


def _preview_temp_dir():
    configured = os.environ.get("GALAXY_CLI_PREVIEW_TMPDIR")
    directory = Path(configured).expanduser() if configured else DEFAULT_CONFIG_DIR / "tmp"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise GalaxyBackendError(
            "Unable to create the private dataset preview temporary directory.",
            category="file_error",
            error_kind="preview_temp_unavailable",
            suggestion="Set GALAXY_CLI_PREVIEW_TMPDIR to a writable private directory.",
        ) from exc
    return directory


def _stream_selected_lines(path, regex, context, limit, tail):
    """Select bounded line windows without retaining the whole downloaded file."""
    if regex is None:
        selected = deque(maxlen=limit)
        scanned_lines = 0
        with open(path, encoding="utf-8", errors="replace") as handle:
            for scanned_lines, line in enumerate(handle, start=1):
                selected.append((scanned_lines, line))
        chosen = list(selected)
        return (
            [line for _, line in chosen],
            [number for number, _ in chosen],
            scanned_lines > len(chosen),
            scanned_lines,
        )

    before = deque(maxlen=context)
    selected = deque(maxlen=limit) if tail else []
    emitted_count = 0
    last_emitted = 0
    after_until = 0
    scanned_lines = 0
    truncated = False

    def emit(candidates):
        nonlocal emitted_count, last_emitted, truncated
        for number, line in candidates:
            if number <= last_emitted:
                continue
            emitted_count += 1
            last_emitted = number
            if tail:
                selected.append((number, line))
            elif len(selected) < limit:
                selected.append((number, line))
            else:
                truncated = True
                return True
        return False

    with open(path, encoding="utf-8", errors="replace") as handle:
        for scanned_lines, line in enumerate(handle, start=1):
            item = (scanned_lines, line)
            matches = regex.search(line[:MAX_PREVIEW_SOURCE_LINE_CHARS])
            candidates = []
            if matches:
                candidates.extend(before)
                candidates.append(item)
                after_until = max(after_until, scanned_lines + context)
            elif scanned_lines <= after_until:
                candidates.append(item)
            if candidates and emit(candidates) and not tail:
                break
            before.append(item)

    chosen = list(selected)
    if tail:
        truncated = emitted_count > len(chosen)
    return (
        [line for _, line in chosen],
        [number for number, _ in chosen],
        truncated,
        scanned_lines,
    )


def _scan_dataset(
    client,
    dataset_id,
    history_id,
    max_download_bytes,
    regex,
    context,
    limit,
    tail,
):
    path = (
        f"histories/{history_id}/contents/{dataset_id}"
        if history_id else f"datasets/{dataset_id}"
    )
    info = client.get(path)
    raw_size = info.get("file_size") if isinstance(info, dict) else None
    try:
        file_size = int(raw_size)
    except (TypeError, ValueError):
        file_size = -1
    if file_size < 0:
        raise _preview_error(
            "Galaxy did not provide a trustworthy dataset size for this selector.",
            error_kind="preview_size_unknown",
            suggestion="Use 'galaxy-cli dataset download' for an explicit full download.",
            details={"max_download_bytes": max_download_bytes},
        )
    if file_size > max_download_bytes:
        raise _preview_error(
            f"Dataset size {file_size} exceeds the preview scan limit {max_download_bytes}.",
            error_kind="preview_download_too_large",
            suggestion="Use 'galaxy-cli dataset download' for an explicit full download.",
            details={"file_size": file_size, "max_download_bytes": max_download_bytes},
        )

    directory = _preview_temp_dir()
    temporary_path = None
    selected = []
    line_numbers = []
    selection_truncated = False
    scanned_lines = 0
    downloaded_size = 0
    primary_error = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="galaxy-cli-preview-", dir=str(directory), delete=False
        ) as handle:
            temporary_path = Path(handle.name)
        download_dataset(
            client,
            dataset_id,
            str(temporary_path),
            history_id=history_id,
            max_bytes=max_download_bytes,
        )
        downloaded_size = temporary_path.stat().st_size
        if downloaded_size > max_download_bytes:
            raise _preview_error(
                f"Downloaded dataset exceeds the preview scan limit {max_download_bytes}.",
                error_kind="preview_download_too_large",
                suggestion="Use 'galaxy-cli dataset download' for an explicit full download.",
                details={
                    "downloaded_bytes": downloaded_size,
                    "max_download_bytes": max_download_bytes,
                },
            )
        try:
            (
                selected,
                line_numbers,
                selection_truncated,
                scanned_lines,
            ) = _stream_selected_lines(
                temporary_path, regex, context, limit, tail
            )
        except OSError as exc:
            raise GalaxyBackendError(
                "Failed to read the bounded dataset preview copy.",
                category="file_error",
                error_kind="preview_scan_failed",
                exit_code=EXIT_USER_ERROR,
            ) from exc
    except GalaxyBackendError as exc:
        primary_error = exc
        raise
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                if primary_error is not None:
                    details = dict(getattr(primary_error, "details", {}) or {})
                    details["temporary_file_removed"] = False
                    details["preview_cleanup_failed"] = True
                    primary_error.details = details
                else:
                    raise GalaxyBackendError(
                        "Failed to remove the private dataset preview temporary file.",
                        category="file_error",
                        error_kind="preview_cleanup_failed",
                        exit_code=EXIT_USER_ERROR,
                        details={"temporary_file_removed": False},
                    ) from exc
    return (
        selected,
        line_numbers,
        selection_truncated,
        {
            "downloaded_bytes": downloaded_size,
            "max_download_bytes": max_download_bytes,
            "scanned_lines": scanned_lines,
            "temporary_directory": str(directory),
            "temporary_file_removed": (
                temporary_path is None or not temporary_path.exists()
            ),
        },
    )


def peek_dataset(
    client,
    dataset_id,
    lines=10,
    history_id=None,
    max_chars_per_line=500,
    max_fields=20,
    delimiter=None,
    head=None,
    tail=None,
    grep=None,
    context=0,
    fields=None,
    max_download_bytes=MAX_PREVIEW_DOWNLOAD_BYTES,
):
    """Get a strictly bounded preview, downloading only known-small scans."""
    if head is not None and tail is not None:
        raise _preview_error(
            "head and tail cannot be used together.",
            error_kind="preview_selector_conflict",
        )
    line_limit = _bounded_integer(
        tail if tail is not None else head if head is not None else lines,
        "preview line count",
        MAX_PREVIEW_LINES,
    )
    max_chars_per_line = _bounded_integer(
        max_chars_per_line,
        "max_chars_per_line",
        MAX_PREVIEW_CHARS_PER_LINE,
        zero_as_max=True,
    )
    max_fields = _bounded_integer(
        max_fields,
        "max_fields",
        MAX_PREVIEW_FIELDS,
        zero_as_max=True,
    )
    context = _bounded_integer(context, "context", MAX_PREVIEW_CONTEXT)
    max_download_bytes = _bounded_integer(
        max_download_bytes,
        "max_download_bytes",
        MAX_PREVIEW_DOWNLOAD_BYTES,
        zero_allowed=False,
    )
    normalized_delimiter = _normalize_delimiter(delimiter)
    selected_fields = _normalize_fields(fields, max_fields)
    regex = _compile_grep(grep)
    if context and regex is None:
        raise _preview_error(
            "context requires grep.", error_kind="preview_selector_conflict"
        )

    if tail is not None or regex is not None:
        (
            selected,
            line_numbers,
            selection_truncated,
            scan,
        ) = _scan_dataset(
            client,
            dataset_id,
            history_id,
            max_download_bytes,
            regex,
            context,
            line_limit,
            tail is not None,
        )
        mode = "grep" if regex is not None else "tail"
        selector = {"mode": mode, "limit": line_limit}
        if regex is not None:
            selector["context"] = context
            if tail is not None:
                selector["direction"] = "tail"
        return _peek_result(
            dataset_id,
            selected,
            line_limit,
            max_chars_per_line,
            max_fields,
            normalized_delimiter,
            fields=selected_fields,
            line_numbers=line_numbers,
            selector=selector,
            selection_truncated=selection_truncated,
            scan=scan,
        )

    info = client.get(
        f"datasets/{dataset_id}",
        params={
            "data_type": "raw_data",
            "provider": "base",
            "offset": 0,
            "limit": line_limit,
        },
    )
    if not isinstance(info, dict):
        raise GalaxyBackendError(
            f"Unexpected response while previewing dataset '{dataset_id}'.",
            category="api_error",
            error_kind="preview_response_invalid",
        )
    # Some Galaxy deployments expose preview text under `peek`.
    if "peek" in info:
        raw_peek = info["peek"]
        return _peek_result(
            dataset_id,
            raw_peek.strip().split("\n"),
            line_limit,
            max_chars_per_line,
            max_fields,
            normalized_delimiter,
            fields=selected_fields,
            selector={"mode": "head", "limit": line_limit},
            selection_truncated=len(raw_peek.strip().split("\n")) > line_limit,
        )

    # usegalaxy.org commonly returns preview rows under `data`.
    raw_data = info.get("data")
    if isinstance(raw_data, list):
        return _peek_result(
            dataset_id,
            raw_data,
            line_limit,
            max_chars_per_line,
            max_fields,
            normalized_delimiter,
            fields=selected_fields,
            selector={"mode": "head", "limit": line_limit},
            selection_truncated=len(raw_data) > line_limit,
        )
    if isinstance(raw_data, str) and raw_data.strip():
        return _peek_result(
            dataset_id,
            raw_data.strip().split("\n"),
            line_limit,
            max_chars_per_line,
            max_fields,
            normalized_delimiter,
            fields=selected_fields,
            selector={"mode": "head", "limit": line_limit},
            selection_truncated=len(raw_data.strip().split("\n")) > line_limit,
        )

    return {
        "id": dataset_id,
        "lines": [],
        "rows": [],
        "total_shown": 0,
        "max_chars_per_line": max_chars_per_line,
        "max_fields": max_fields,
        "selector": {"mode": "head", "limit": line_limit},
        "truncated": False,
        "truncation": {
            "total_chars": 0,
            "max_total_chars": MAX_PREVIEW_TOTAL_CHARS,
            "truncated_total": False,
        },
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
