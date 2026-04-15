"""Library management — create, list, show, manage shared data libraries."""


def list_libraries(client, deleted=False):
    """List data libraries."""
    params = {}
    if deleted:
        params["deleted"] = True
    libraries = client.get("libraries", params=params)
    return [
        {
            "id": lib.get("id", ""),
            "name": lib.get("name", ""),
            "description": lib.get("description", ""),
            "deleted": lib.get("deleted", False),
            "create_time": lib.get("create_time", ""),
        }
        for lib in libraries
    ]


def create_library(client, name, description="", synopsis=""):
    """Create a new data library."""
    payload = {"name": name, "description": description, "synopsis": synopsis}
    result = client.post("libraries", json_data=payload)
    return {
        "id": result.get("id", ""),
        "name": result.get("name", name),
        "status": "created",
    }


def show_library(client, library_id):
    """Show details of a library."""
    info = client.get(f"libraries/{library_id}")
    return {
        "id": info.get("id", library_id),
        "name": info.get("name", ""),
        "description": info.get("description", ""),
        "synopsis": info.get("synopsis", ""),
        "deleted": info.get("deleted", False),
        "create_time": info.get("create_time", ""),
    }


def list_library_contents(client, library_id):
    """List contents of a library."""
    contents = client.get(f"libraries/{library_id}/contents")
    return [
        {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "type": item.get("type", ""),
            "url": item.get("url", ""),
        }
        for item in contents
    ]


def delete_library(client, library_id):
    """Delete a library."""
    client.delete(f"libraries/{library_id}")
    return {"id": library_id, "status": "deleted"}
