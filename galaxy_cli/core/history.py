"""History management — create, copy, list, show, delete, export Galaxy histories."""


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


def copy_history(client, history_id, name=None, all_datasets=False):
    """Create a new history by copying an existing one."""
    payload = {"source": "history", "history_id": history_id}
    if name is not None:
        payload["name"] = name
    if all_datasets:
        payload["all_datasets"] = True
    result = client.post("histories", json_data=payload)
    return {
        "id": result["id"],
        "name": result.get("name", name or ""),
        "state": result.get("state", ""),
        "create_time": result.get("create_time", ""),
        "copied_from_history_id": history_id,
        "all_datasets": bool(all_datasets),
    }


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
