"""Dataset management — upload, download, show, delete, peek at datasets."""

from pathlib import Path

from galaxy_cli.utils.galaxy_backend import (
    EXIT_USER_ERROR,
    GalaxyBackendError,
)
from galaxy_cli.core.job import wait_for_job


def upload_dataset(
    client,
    history_id,
    file_path,
    file_type="auto",
    dbkey="?",
    wait=False,
    timeout=1800,
    poll_interval=30,
):
    """Upload a local file to a Galaxy history."""
    result = client.upload_file(file_path, history_id, file_type=file_type, dbkey=dbkey)
    outputs = result.get("outputs", [])
    jobs = result.get("jobs", [])
    if outputs:
        ds = outputs[0]
        uploaded = {
            "id": ds.get("id", ""),
            "name": ds.get("name", ""),
            "state": ds.get("state", ""),
            "history_id": history_id,
            "file_type": ds.get("extension", file_type),
        }
        if wait and jobs:
            uploaded["wait_results"] = [
                wait_for_job(
                    client,
                    job.get("id", ""),
                    max_wait=timeout,
                    poll_interval=poll_interval,
                )
                for job in jobs
                if job.get("id")
            ]
            if uploaded["id"]:
                refreshed = show_dataset(client, uploaded["id"], history_id=history_id)
                uploaded.update({
                    "state": refreshed.get("state", uploaded["state"]),
                    "file_type": refreshed.get("extension", uploaded["file_type"]),
                    "file_size": refreshed.get("file_size", 0),
                    "data_type": refreshed.get("data_type", ""),
                    "misc_blurb": refreshed.get("misc_blurb", ""),
                })
        return uploaded
    return {"status": "uploaded", "history_id": history_id, "raw": result}


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


def download_dataset(client, dataset_id, output_path):
    """Download a dataset to a local file."""
    return client.download_dataset(dataset_id, output_path)


def peek_dataset(client, dataset_id, lines=10):
    """Get a preview of dataset contents."""
    info = client.get(f"datasets/{dataset_id}", params={"data_type": "raw_data", "provider": "base"})
    # Some Galaxy deployments expose preview text under `peek`.
    if "peek" in info:
        raw_peek = info["peek"]
        peek_lines = raw_peek.strip().split("\n")[:lines]
        return {"id": dataset_id, "lines": peek_lines, "total_shown": len(peek_lines)}

    # usegalaxy.org commonly returns preview rows under `data`.
    raw_data = info.get("data")
    if isinstance(raw_data, list):
        peek_lines = [str(line).rstrip("\n") for line in raw_data[:lines]]
        return {"id": dataset_id, "lines": peek_lines, "total_shown": len(peek_lines)}
    if isinstance(raw_data, str) and raw_data.strip():
        peek_lines = raw_data.strip().split("\n")[:lines]
        return {"id": dataset_id, "lines": peek_lines, "total_shown": len(peek_lines)}

    # Fallback: try getting raw content directly.
    try:
        preview = client.get(f"datasets/{dataset_id}/display", params={"offset": 0, "limit": lines})
        if isinstance(preview, dict) and "raw" in preview and preview["raw"]:
            peek_lines = preview["raw"].strip().split("\n")[:lines]
            return {"id": dataset_id, "lines": peek_lines, "total_shown": len(peek_lines)}
        if isinstance(preview, dict) and "ck_data" in preview and preview["ck_data"]:
            peek_lines = str(preview["ck_data"]).strip().split("\n")[:lines]
            return {"id": dataset_id, "lines": peek_lines, "total_shown": len(peek_lines)}
    except Exception:
        pass
    return {"id": dataset_id, "lines": [], "total_shown": 0, "note": "Preview not available"}


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
