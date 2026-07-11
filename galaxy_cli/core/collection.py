"""Collection management — create, list, show dataset collections."""


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


def flatten_collection(client, collection_id, limit=100, max_depth=20):
    """Recursively flatten nested collections with cycle and depth guards."""
    flattened = []
    active = set()

    def walk(info, path, depth):
        current_id = info.get("id", "")
        if depth > max_depth:
            raise ValueError(f"Collection nesting exceeds maximum depth {max_depth}")
        if current_id and current_id in active:
            raise ValueError(f"Collection cycle detected at {current_id}")
        if current_id:
            active.add(current_id)
        for element in info.get("elements", []) or []:
            if len(flattened) >= limit:
                break
            identifier = str(element.get("element_identifier", ""))
            element_path = "/".join(filter(None, [path, identifier]))
            obj = element.get("object") or {}
            element_type = element.get("element_type", "")
            if element_type in {"dataset_collection", "hdca"}:
                nested = obj
                if not nested.get("elements") and nested.get("id"):
                    nested = _collection_info(client, nested["id"])
                walk(nested, element_path, depth + 1)
            else:
                flattened.append({
                    "element_path": element_path,
                    "id": obj.get("id", ""),
                    "src": "hda",
                    "state": obj.get("state", ""),
                    "extension": obj.get("extension", ""),
                    "collection_type": info.get("collection_type", ""),
                })
        if current_id:
            active.remove(current_id)

    walk(_collection_info(client, collection_id), "", 0)
    return {
        "id": collection_id,
        "elements": flattened,
        "limit": limit,
        "truncated": len(flattened) >= limit,
    }


def resolve_collection_element(client, collection_id, element_path, max_depth=20):
    result = flatten_collection(client, collection_id, limit=10000, max_depth=max_depth)
    matches = [item for item in result["elements"] if item["element_path"] == element_path]
    if len(matches) != 1:
        raise ValueError(
            f"Collection element path {element_path!r} matched {len(matches)} elements"
        )
    return matches[0]


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
