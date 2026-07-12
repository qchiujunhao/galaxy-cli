"""Collection management — create, list, show dataset collections."""

from galaxy_cli.core.dataset import (
    MAX_PREVIEW_DOWNLOAD_BYTES,
    peek_dataset,
)
from galaxy_cli.utils.galaxy_backend import (
    EXIT_USER_ERROR,
    GalaxyBackendError,
)


MAX_COLLECTION_FLATTEN_RESULTS = 10000
MAX_COLLECTION_DEPTH = 100
MAX_COLLECTION_REQUESTS = 1000
MAX_COLLECTION_NODES = 50000


class CollectionResolutionError(GalaxyBackendError, ValueError):
    """Structured collection error retaining ValueError compatibility."""


def _collection_error(message, error_kind, *, category="invalid_request", details=None):
    return CollectionResolutionError(
        message,
        category=category,
        error_kind=error_kind,
        exit_code=EXIT_USER_ERROR,
        details=details,
    )


def create_collection(client, history_id, name, collection_type="list",
                      element_identifiers=None, include_elements=False):
    """Create a dataset collection in a history.

    Args:
        client: GalaxyClient instance.
        history_id: Target history ID.
        name: Collection name.
        collection_type: "list", "paired", or "list:paired".
        element_identifiers: List of element dicts for the Galaxy API.
            For "list": [{"src": "hda", "id": "...", "name": "..."}]
            For "paired": [
                {"src": "hda", "id": "...", "name": "forward"},
                {"src": "hda", "id": "...", "name": "reverse"},
            ]
            For "list:paired": [{"name": "...", "src": "new_collection",
                "collection_type": "paired",
                "element_identifiers": [
                    {"src": "hda", "id": "...", "name": "forward"},
                    {"src": "hda", "id": "...", "name": "reverse"},
                ]}]
        include_elements: If true, fetch and include resolved collection
            elements in the returned summary.
    """
    payload = {
        "type": "dataset_collection",
        "name": name,
        "collection_type": collection_type,
        "element_identifiers": element_identifiers or [],
    }
    result = client.post(f"histories/{history_id}/contents", json_data=payload)
    created = {
        "id": result.get("id", ""),
        "name": result.get("name", name),
        "collection_type": result.get("collection_type", collection_type),
        "element_count": result.get("element_count", len(element_identifiers or [])),
        "history_id": history_id,
        "state": result.get("populated_state", ""),
    }
    if include_elements and created["id"]:
        details = show_collection(client, created["id"])
        created["state"] = details.get("populated_state", created["state"])
        created["element_count"] = details.get("element_count", created["element_count"])
        created["elements"] = details.get("elements", [])
    return created


def list_collections(client, history_id, limit=50):
    """List dataset collections in a history."""
    items = client.get(f"histories/{history_id}/contents",
                       params={"limit": limit})
    return [
        {
            "id": item["id"],
            "name": item.get("name", ""),
            "collection_type": item.get("collection_type", ""),
            "element_count": item.get("element_count", 0),
            "state": item.get("populated_state", item.get("state", "")),
        }
        for item in items
        if item.get("history_content_type") == "dataset_collection"
    ]


def _collection_info(client, collection_id):
    return client.get(
        f"dataset_collections/{collection_id}", params={"instance_type": "history"}
    )


def flatten_collection(
    client,
    collection_id,
    limit=100,
    max_depth=20,
    max_requests=MAX_COLLECTION_REQUESTS,
    max_nodes=MAX_COLLECTION_NODES,
):
    """Recursively flatten with result, depth, request, node, and cycle guards."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_COLLECTION_FLATTEN_RESULTS
    ):
        raise _collection_error(
            f"Collection result limit must be between 1 and {MAX_COLLECTION_FLATTEN_RESULTS}.",
            "collection_limit_invalid",
            details={"max_results": MAX_COLLECTION_FLATTEN_RESULTS},
        )
    if (
        isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or not 0 <= max_depth <= MAX_COLLECTION_DEPTH
    ):
        raise _collection_error(
            f"Collection max depth must be between 0 and {MAX_COLLECTION_DEPTH}.",
            "collection_depth_invalid",
            details={"max_depth": MAX_COLLECTION_DEPTH},
        )
    for value, label, maximum, error_kind, details_key in (
        (
            max_requests,
            "request limit",
            MAX_COLLECTION_REQUESTS,
            "collection_request_limit_invalid",
            "max_requests",
        ),
        (
            max_nodes,
            "node limit",
            MAX_COLLECTION_NODES,
            "collection_node_limit_invalid",
            "max_nodes",
        ),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise _collection_error(
                f"Collection {label} must be between 1 and {maximum}.",
                error_kind,
                details={details_key: maximum},
            )
    flattened = []
    active = set()
    fetched = {}
    request_count = 0
    node_count = 0
    truncated = False
    stopped = False

    def fetch(nested_id):
        nonlocal request_count
        nested_id = str(nested_id)
        if nested_id in fetched:
            return fetched[nested_id]
        if request_count >= max_requests:
            raise _collection_error(
                f"Collection traversal exceeded the request limit {max_requests}.",
                "collection_request_limit",
                details={
                    "collection_id": collection_id,
                    "max_requests": max_requests,
                },
            )
        request_count += 1
        info = _collection_info(client, nested_id)
        fetched[nested_id] = info
        return info

    def consume_node():
        nonlocal node_count
        if node_count >= max_nodes:
            raise _collection_error(
                f"Collection traversal exceeded the node limit {max_nodes}.",
                "collection_node_limit",
                details={
                    "collection_id": collection_id,
                    "max_nodes": max_nodes,
                },
            )
        node_count += 1

    def complete_embedded_collection(nested):
        elements = nested.get("elements")
        if not isinstance(elements, list):
            return False
        raw_count = nested.get("element_count")
        if raw_count is None:
            return bool(elements)
        try:
            expected = int(raw_count)
            return expected >= 0 and len(elements) >= expected
        except (TypeError, ValueError):
            return False

    def walk(info, path, depth):
        nonlocal stopped, truncated
        if stopped:
            return
        if not isinstance(info, dict):
            raise _collection_error(
                "Galaxy returned malformed collection metadata.",
                "collection_response_invalid",
                category="api_error",
            )
        consume_node()
        current_id = info.get("id", "")
        if depth > max_depth:
            raise _collection_error(
                f"Collection nesting exceeds maximum depth {max_depth}.",
                "collection_depth_exceeded",
                details={"collection_id": collection_id, "max_depth": max_depth},
            )
        if current_id and current_id in active:
            raise _collection_error(
                f"Collection cycle detected at {current_id}.",
                "collection_cycle",
                details={"collection_id": collection_id},
            )
        if current_id:
            active.add(current_id)
        try:
            for element in info.get("elements", []) or []:
                if stopped:
                    break
                consume_node()
                if not isinstance(element, dict):
                    raise _collection_error(
                        "Galaxy returned malformed collection element metadata.",
                        "collection_response_invalid",
                        category="api_error",
                    )
                identifier = str(element.get("element_identifier", ""))
                element_path = "/".join(filter(None, [path, identifier]))
                obj = element.get("object") or {}
                if not isinstance(obj, dict):
                    obj = {}
                element_type = element.get("element_type", "")
                if element_type in {"dataset_collection", "hdca"}:
                    nested = obj
                    if (
                        nested.get("id")
                        and not complete_embedded_collection(nested)
                    ):
                        nested = fetch(nested["id"])
                    walk(nested, element_path, depth + 1)
                else:
                    if len(flattened) >= limit:
                        truncated = True
                        stopped = True
                        break
                    flattened.append({
                        "element_path": element_path,
                        "id": obj.get("id", ""),
                        "src": "hda",
                        "state": obj.get("state", ""),
                        "extension": obj.get("extension", ""),
                        "collection_type": info.get("collection_type", ""),
                    })
        finally:
            if current_id:
                active.discard(current_id)

    walk(fetch(collection_id), "", 0)
    return {
        "id": collection_id,
        "elements": flattened,
        "limit": limit,
        "truncated": truncated,
    }


def resolve_collection_element(
    client,
    collection_id,
    element_path,
    max_depth=20,
    max_results=MAX_COLLECTION_FLATTEN_RESULTS,
    max_requests=MAX_COLLECTION_REQUESTS,
    max_nodes=MAX_COLLECTION_NODES,
):
    """Resolve one exact element path without guessing through truncated data."""
    if not isinstance(element_path, str) or not element_path:
        raise _collection_error(
            "Collection element path is required.",
            "collection_element_missing",
            category="not_found",
            details={"collection_id": collection_id},
        )
    result = flatten_collection(
        client,
        collection_id,
        limit=max_results,
        max_depth=max_depth,
        max_requests=max_requests,
        max_nodes=max_nodes,
    )
    if result["truncated"]:
        raise _collection_error(
            f"Collection resolution exceeded the result limit {max_results}.",
            "collection_result_limit",
            details={
                "collection_id": collection_id,
                "element_path": element_path,
                "max_results": max_results,
            },
        )
    matches = [item for item in result["elements"] if item["element_path"] == element_path]
    if not matches:
        raise _collection_error(
            f"Collection element path {element_path!r} was not found.",
            "collection_element_missing",
            category="not_found",
            details={"collection_id": collection_id, "element_path": element_path},
        )
    if len(matches) > 1:
        raise _collection_error(
            f"Collection element path {element_path!r} is ambiguous.",
            "collection_element_ambiguous",
            details={
                "collection_id": collection_id,
                "element_path": element_path,
                "match_count": len(matches),
            },
        )
    if not matches[0].get("id"):
        raise _collection_error(
            f"Collection element path {element_path!r} has no dataset ID.",
            "collection_response_invalid",
            category="api_error",
            details={"collection_id": collection_id, "element_path": element_path},
        )
    return matches[0]


def preview_collection_element(
    client,
    collection_id,
    element_path,
    *,
    lines=10,
    max_depth=20,
    max_results=MAX_COLLECTION_FLATTEN_RESULTS,
    max_requests=MAX_COLLECTION_REQUESTS,
    max_nodes=MAX_COLLECTION_NODES,
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
    """Resolve and preview one explicit dataset element from a collection."""
    resolved = resolve_collection_element(
        client,
        collection_id,
        element_path,
        max_depth=max_depth,
        max_results=max_results,
        max_requests=max_requests,
        max_nodes=max_nodes,
    )
    preview = peek_dataset(
        client,
        resolved["id"],
        lines=lines,
        max_chars_per_line=max_chars_per_line,
        max_fields=max_fields,
        delimiter=delimiter,
        head=head,
        tail=tail,
        grep=grep,
        context=context,
        fields=fields,
        max_download_bytes=max_download_bytes,
    )
    return {
        **preview,
        "collection_id": collection_id,
        "dataset_id": resolved["id"],
        "element_path": resolved["element_path"],
        "resolved_path": resolved["element_path"],
        "extension": resolved.get("extension", ""),
        "src": resolved.get("src", "hda"),
    }


def show_collection(client, collection_id, flatten=False, limit=100, max_depth=20):
    """Show details of a dataset collection including its elements."""
    if flatten:
        return flatten_collection(client, collection_id, limit=limit, max_depth=max_depth)
    info = _collection_info(client, collection_id)
    elements = []
    for elem in info.get("elements", []):
        entry = {
            "element_index": elem.get("element_index", 0),
            "element_identifier": elem.get("element_identifier", ""),
            "element_type": elem.get("element_type", ""),
        }
        obj = elem.get("object", {})
        if elem.get("element_type") == "hda":
            entry.update({
                "id": obj.get("id", ""),
                "name": obj.get("name", ""),
                "extension": obj.get("extension", ""),
                "state": obj.get("state", ""),
                "file_size": obj.get("file_size", 0),
            })
        elif elem.get("element_type") == "dataset_collection":
            # Nested collection (e.g., paired inside list:paired)
            entry.update({
                "id": obj.get("id", ""),
                "collection_type": obj.get("collection_type", ""),
                "element_count": obj.get("element_count", 0),
                "elements": [
                    {
                        "element_identifier": sub.get("element_identifier", ""),
                        "id": sub.get("object", {}).get("id", ""),
                        "name": sub.get("object", {}).get("name", ""),
                        "extension": sub.get("object", {}).get("extension", ""),
                    }
                    for sub in obj.get("elements", [])
                ],
            })
        elements.append(entry)

    return {
        "id": info.get("id", collection_id),
        "name": info.get("name", ""),
        "collection_type": info.get("collection_type", ""),
        "element_count": info.get("element_count", 0),
        "populated_state": info.get("populated_state", ""),
        "elements": elements,
    }


def build_list_elements(element_specs):
    """Build element_identifiers for a list collection from CLI specs.

    Each spec is either:
        "DATASET_ID"          -> auto-named by index
        "name=DATASET_ID"     -> explicitly named

    Returns list of dicts for the Galaxy API.
    """
    elements = []
    for i, spec in enumerate(element_specs):
        if "=" in spec:
            name, dataset_id = spec.split("=", 1)
        else:
            name = f"element_{i}"
            dataset_id = spec
        elements.append({"src": "hda", "id": dataset_id, "name": name})
    return elements


def build_paired_elements(pair_specs):
    """Build element_identifiers for a list:paired collection from CLI specs.

    Each spec is "pair_name:forward_id:reverse_id".

    Returns list of dicts for the Galaxy API.
    """
    elements = []
    for spec in pair_specs:
        parts = spec.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"Invalid pair format: '{spec}'. "
                "Expected 'pair_name:forward_dataset_id:reverse_dataset_id'"
            )
        pair_name, fwd_id, rev_id = parts
        elements.append({
            "name": pair_name,
            "src": "new_collection",
            "collection_type": "paired",
            "element_identifiers": [
                {"src": "hda", "id": fwd_id, "name": "forward"},
                {"src": "hda", "id": rev_id, "name": "reverse"},
            ],
        })
    return elements


def build_pair_collection_elements(element_specs):
    """Build element_identifiers for a top-level paired collection.

    Accepts exactly two --element specs. These may be given as:
        "DATASET_ID"               -> ordered as forward, reverse
        "forward=DATASET_ID"
        "reverse=DATASET_ID"

    Returns list of dicts for the Galaxy API.
    """
    if len(element_specs) != 2:
        raise ValueError(
            "paired collections require exactly two --element/-e arguments.\n"
            "Format: -e forward=DATASET_ID -e reverse=DATASET_ID"
        )

    parsed = []
    for index, spec in enumerate(element_specs):
        if "=" in spec:
            name, dataset_id = spec.split("=", 1)
        else:
            name = "forward" if index == 0 else "reverse"
            dataset_id = spec
        parsed.append({"src": "hda", "id": dataset_id, "name": name})

    names = [item["name"] for item in parsed]
    if sorted(names) != ["forward", "reverse"]:
        raise ValueError(
            "paired collections require one forward and one reverse element.\n"
            "Format: -e forward=DATASET_ID -e reverse=DATASET_ID"
        )
    return sorted(parsed, key=lambda item: 0 if item["name"] == "forward" else 1)
