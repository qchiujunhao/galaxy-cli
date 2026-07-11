"""History management — create, copy, list, show, delete, export Galaxy histories."""

import time

from galaxy_cli.utils.galaxy_backend import (
    EXIT_SERVER_ERROR,
    EXIT_TIMEOUT,
    EXIT_USER_ERROR,
    GalaxyBackendError,
    get_with_deadline,
)


_DATASET_READY_STATES = {"ok", "empty", "deferred"}
_DATASET_FAILED_STATES = {
    "error",
    "paused",
    "failed_metadata",
    "discarded",
    "deleted",
}
_COLLECTION_READY_STATES = {"ok"}
_COLLECTION_FAILED_STATES = {"failed", "error", "deleted"}


def _history_copy_submission_error(exc, source_history_id):
    status_code = getattr(exc, "status_code", None)
    rejected = status_code in {400, 422}
    details = dict(getattr(exc, "details", {}) or {})
    details.update(
        {
            "history_id": "",
            "copied_from_history_id": source_history_id,
            "content_ids": [],
            "contents": [],
        }
    )
    return GalaxyBackendError(
        str(exc),
        category=exc.category,
        error_kind=(
            "history_copy_submission_rejected"
            if rejected
            else "history_copy_submission_unknown"
        ),
        exit_code=exc.exit_code,
        suggestion=exc.suggestion,
        status_code=status_code,
        submission_state="not_submitted" if rejected else "unknown",
        retry_safe=rejected,
        details=details,
    )


def list_histories(client, deleted=False, limit=50, offset=0):
    """List histories for the current user."""
    params = {"limit": limit, "offset": offset}
    if deleted:
        params["deleted"] = True
    histories = client.get("histories", params=params)
    return [
        {
            "id": h["id"],
            "name": h.get("name", ""),
            "state": h.get("state", ""),
            "size": h.get("size", 0),
            "update_time": h.get("update_time", ""),
            "deleted": h.get("deleted", False),
            "dataset_count": h.get("count", 0),
        }
        for h in histories
    ]


def create_history(client, name="Unnamed history"):
    """Create a new history."""
    result = client.post("histories", json_data={"name": name})
    return {
        "id": result["id"],
        "name": result.get("name", name),
        "state": result.get("state", ""),
        "create_time": result.get("create_time", ""),
    }


def history_contents(
    client,
    history_id,
    *,
    name=None,
    exact_name=None,
    hid=None,
    content_type=None,
    state=None,
    extension=None,
    limit=50,
):
    """Return compact, locally filtered history contents."""
    params = {"limit": max(1, int(limit))}
    raw = client.get(f"histories/{history_id}/contents", params=params)
    if isinstance(raw, dict):
        raw = raw.get("contents", [])
    contents = [_compact_history_content(item) for item in raw if isinstance(item, dict)]
    if name:
        needle = name.lower()
        contents = [item for item in contents if needle in item["name"].lower()]
    if exact_name is not None:
        contents = [item for item in contents if item["name"] == exact_name]
    if hid is not None:
        contents = [item for item in contents if str(item["hid"]) == str(hid)]
    if content_type:
        aliases = {"dataset": "hda", "collection": "hdca"}
        wanted = aliases.get(content_type, content_type)
        contents = [item for item in contents if item["src"] == wanted]
    if state:
        contents = [item for item in contents if item["state"] == state]
    if extension:
        contents = [item for item in contents if item.get("extension") == extension]
    return contents[:limit]


def resolve_history_content(client, history_id, **filters):
    """Resolve exactly one history item or return structured ambiguity details."""
    matches = history_contents(client, history_id, **filters)
    if len(matches) == 1:
        return matches[0]
    raise GalaxyBackendError(
        "No matching history content." if not matches else "History content reference is ambiguous.",
        category="invalid_request",
        error_kind="history_content_not_found" if not matches else "history_content_ambiguous",
        exit_code=EXIT_USER_ERROR,
        submission_state="not_submitted",
        retry_safe=True,
        details={"history_id": history_id, "match_count": len(matches), "matches": matches},
    )


def _compact_history_content(item):
    content_type = item.get("history_content_type", "")
    is_collection = (
        content_type == "dataset_collection"
        or item.get("type") in {"collection", "dataset_collection"}
        or item.get("src") == "hdca"
        or bool(item.get("collection_type"))
    )
    if is_collection:
        state = item.get("populated_state") or item.get("state") or "unknown"
    else:
        state = item.get("state") or "unknown"
    compact = {
        "hid": item.get("hid", ""),
        "id": item.get("id", ""),
        "src": "hdca" if is_collection else "hda",
        "name": item.get("name", ""),
        "state": state,
    }
    if item.get("deleted"):
        compact["deleted"] = True
    if item.get("purged"):
        compact["purged"] = True
    if is_collection:
        elements = item.get("elements")
        compact.update(
            {
                "collection_type": item.get("collection_type", ""),
                "element_count": item.get(
                    "element_count", len(elements) if isinstance(elements, list) else 0
                ),
            }
        )
    else:
        compact["extension"] = item.get("extension", "")
    return compact


def _history_content_status(content, allow_deleted=False):
    state = content["state"]
    if allow_deleted and (content.get("deleted") or content.get("purged")):
        return "ready"
    if content["src"] == "hdca":
        if state in _COLLECTION_READY_STATES:
            return "ready"
        if state in _COLLECTION_FAILED_STATES:
            return "failed"
        return "pending"
    if state in _DATASET_READY_STATES:
        return "ready"
    if state in _DATASET_FAILED_STATES:
        return "failed"
    return "pending"


def _history_copy_error_details(history_id, source_history_id, contents):
    return {
        "history_id": history_id,
        "copied_from_history_id": source_history_id,
        "content_ids": [content["id"] for content in contents if content["id"]],
        "contents": contents,
    }


def _history_copy_timeout_error(
    history_id, source_history_id, contents, allow_deleted=False
):
    pending = [
        content
        for content in contents
        if _history_content_status(content, allow_deleted=allow_deleted) == "pending"
    ]
    message = ", ".join(
        f"{content['id']} ({content['state']})" for content in pending
    ) or "deadline expired while checking copied contents"
    return GalaxyBackendError(
        f"Timed out waiting for copied history contents: {message}",
        category="timeout",
        error_kind="history_copy_timeout",
        exit_code=EXIT_TIMEOUT,
        submission_state="submitted",
        retry_safe=False,
        details=_history_copy_error_details(
            history_id, source_history_id, contents
        ),
    )


def wait_for_history_contents(
    client,
    history_id,
    timeout=1800,
    poll_interval=5,
    *,
    source_history_id="",
    allow_deleted=False,
):
    """Wait until every copied dataset and collection is ready."""
    timeout = max(0.0, float(timeout))
    poll_interval = max(0.0, float(poll_interval))
    started = time.monotonic()
    deadline = started + timeout
    contents = []

    while True:
        try:
            raw_contents = get_with_deadline(
                client,
                f"histories/{history_id}/contents",
                deadline=deadline if timeout > 0 else None,
            )
        except GalaxyBackendError as error:
            if getattr(error, "error_kind", None) == "request_deadline":
                raise _history_copy_timeout_error(
                    history_id, source_history_id, contents, allow_deleted
                ) from error
            details = dict(getattr(error, "details", None) or {})
            details.update(
                _history_copy_error_details(
                    history_id,
                    source_history_id,
                    contents,
                )
            )
            error.details = details
            error.error_kind = (
                getattr(error, "error_kind", None)
                or "history_copy_status_unavailable"
            )
            error.submission_state = "submitted"
            error.retry_safe = False
            raise
        if isinstance(raw_contents, dict) and isinstance(
            raw_contents.get("contents"), list
        ):
            raw_contents = raw_contents["contents"]
        if not isinstance(raw_contents, list):
            raise GalaxyBackendError(
                "Galaxy returned an invalid copied-history contents response.",
                category="api_error",
                error_kind="history_copy_response_invalid",
                exit_code=EXIT_SERVER_ERROR,
                submission_state="submitted",
                retry_safe=False,
                details=_history_copy_error_details(
                    history_id, source_history_id, []
                ),
            )
        if any(not isinstance(item, dict) for item in raw_contents):
            known_contents = [
                _compact_history_content(item)
                for item in raw_contents
                if isinstance(item, dict)
            ]
            raise GalaxyBackendError(
                "Galaxy returned a malformed copied-history content record.",
                category="api_error",
                error_kind="history_copy_response_invalid",
                exit_code=EXIT_SERVER_ERROR,
                submission_state="submitted",
                retry_safe=False,
                details=_history_copy_error_details(
                    history_id, source_history_id, known_contents
                ),
            )
        contents = [_compact_history_content(item) for item in raw_contents]
        failed = [
            content
            for content in contents
            if _history_content_status(content, allow_deleted=allow_deleted) == "failed"
        ]
        if failed:
            raise GalaxyBackendError(
                "Copied history content failed: "
                + ", ".join(
                    f"{content['id']} ({content['state']})" for content in failed
                ),
                category="content_failed",
                error_kind="history_copy_failed",
                exit_code=EXIT_SERVER_ERROR,
                submission_state="submitted",
                retry_safe=False,
                details=_history_copy_error_details(
                    history_id, source_history_id, contents
                ),
            )

        pending = [
            content
            for content in contents
            if _history_content_status(content, allow_deleted=allow_deleted) == "pending"
        ]
        if not pending:
            return contents

        now = time.monotonic()
        if now >= deadline:
            raise _history_copy_timeout_error(
                history_id, source_history_id, contents, allow_deleted
            )
        time.sleep(min(poll_interval, max(0.0, deadline - now)))


def copy_history(
    client,
    history_id,
    name=None,
    all_datasets=False,
    wait=False,
    timeout=1800,
    poll_interval=5,
):
    """Create a new history by copying an existing one."""
    payload = {"source": "history", "history_id": history_id}
    if name is not None:
        payload["name"] = name
    if all_datasets:
        payload["all_datasets"] = True
    try:
        result = client.post("histories", json_data=payload)
    except GalaxyBackendError as exc:
        raise _history_copy_submission_error(exc, history_id) from exc
    if not isinstance(result, dict) or not result.get("id"):
        raise GalaxyBackendError(
            "Galaxy did not return an ID for the copied history.",
            category="api_error",
            error_kind="history_copy_response_invalid",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="unknown",
            retry_safe=False,
            details={
                "history_id": "",
                "copied_from_history_id": history_id,
                "content_ids": [],
                "contents": [],
            },
        )
    copied = {
        "id": result["id"],
        "name": result.get("name", name or ""),
        "state": result.get("state", ""),
        "create_time": result.get("create_time", ""),
        "copied_from_history_id": history_id,
        "all_datasets": bool(all_datasets),
    }
    if wait:
        copied["contents"] = wait_for_history_contents(
            client,
            copied["id"],
            timeout=timeout,
            poll_interval=poll_interval,
            source_history_id=history_id,
            allow_deleted=all_datasets,
        )
    return copied


def show_history(client, history_id, contents=False):
    """Show details of a history."""
    info = client.get(f"histories/{history_id}")
    result = {
        "id": info["id"],
        "name": info.get("name", ""),
        "state": info.get("state", ""),
        "size": info.get("size", 0),
        "create_time": info.get("create_time", ""),
        "update_time": info.get("update_time", ""),
        "annotation": info.get("annotation", ""),
        "tags": info.get("tags", []),
        "deleted": info.get("deleted", False),
        "importable": info.get("importable", False),
        "published": info.get("published", False),
        "state_details": info.get("state_details", {}),
    }
    if contents:
        items = client.get(f"histories/{history_id}/contents")
        result["contents"] = [
            {
                "id": item["id"],
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "state": item.get("state", ""),
                "extension": item.get("extension", ""),
                "deleted": item.get("deleted", False),
                "visible": item.get("visible", True),
            }
            for item in items
        ]
    return result


def delete_history(client, history_id, purge=False):
    """Delete a history."""
    payload = {}
    if purge:
        payload["purge"] = True
    client.delete(f"histories/{history_id}", json_data=payload)
    return {"id": history_id, "status": "deleted", "purged": purge}


def update_history(
    client,
    history_id,
    name=None,
    annotation=None,
    tags=None,
    published=None,
    importable=None,
):
    """Update a history's metadata."""
    payload = {}
    if name is not None:
        payload["name"] = name
    if annotation is not None:
        payload["annotation"] = annotation
    if tags is not None:
        payload["tags"] = tags
    if published is not None:
        payload["published"] = published
    if importable is not None:
        payload["importable"] = importable
    result = client.put(f"histories/{history_id}", json_data=payload)
    return {
        "id": history_id,
        "updated": list(payload.keys()),
        "name": result.get("name", ""),
        "published": result.get("published", published),
        "importable": result.get("importable", importable),
    }


def export_history(client, history_id):
    """Start a history export (archive creation)."""
    result = client.put(f"histories/{history_id}/exports")
    return {
        "id": history_id,
        "status": "export_started",
        "download_url": result.get("download_url", ""),
    }
