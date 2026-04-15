"""Collection management — create, list, show dataset collections."""


def create_collection(client, history_id, name, collection_type="list",
                      element_identifiers=None):
    """Create a dataset collection in a history.

    Args:
        client: GalaxyClient instance.
        history_id: Target history ID.
        name: Collection name.
        collection_type: "list" or "list:paired".
        element_identifiers: List of element dicts for the Galaxy API.
            For "list": [{"src": "hda", "id": "...", "name": "..."}]
            For "list:paired": [{"name": "...", "collection_type": "paired",
                "element_identifiers": [
                    {"src": "hda", "id": "...", "name": "forward"},
                    {"src": "hda", "id": "...", "name": "reverse"},
                ]}]
    """
    payload = {
        "type": "dataset_collection",
        "name": name,
        "collection_type": collection_type,
        "element_identifiers": element_identifiers or [],
    }
    result = client.post(f"histories/{history_id}/contents", json_data=payload)
    return {
        "id": result.get("id", ""),
        "name": result.get("name", name),
        "collection_type": result.get("collection_type", collection_type),
        "element_count": result.get("element_count", len(element_identifiers or [])),
        "history_id": history_id,
        "state": result.get("populated_state", ""),
    }


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


def show_collection(client, collection_id):
    """Show details of a dataset collection including its elements."""
    info = client.get(f"dataset_collections/{collection_id}",
                      params={"instance_type": "history"})
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
            "collection_type": "paired",
            "element_identifiers": [
                {"src": "hda", "id": fwd_id, "name": "forward"},
                {"src": "hda", "id": rev_id, "name": "reverse"},
            ],
        })
    return elements
