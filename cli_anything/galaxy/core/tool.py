"""Tool management — list, search, show, run Galaxy tools."""


def list_tools(client, query=None, section_id=None):
    """List available tools, optionally filtered by search or section."""
    params = {"in_panel": False}
    if query:
        params["q"] = query
    tools = client.get("tools", params=params)
    results = []
    for t in tools:
        if isinstance(t, dict):
            if section_id and t.get("panel_section_id") != section_id:
                continue
            results.append({
                "id": t.get("id", ""),
                "name": t.get("name", ""),
                "version": t.get("version", ""),
                "description": t.get("description", ""),
                "section": t.get("panel_section_name", ""),
            })
    return results


def search_tools(client, query):
    """Search tools by name or description."""
    return list_tools(client, query=query)


def show_tool(client, tool_id):
    """Show detailed info about a tool, including its inputs."""
    info = client.get(f"tools/{tool_id}", params={"io_details": True})
    inputs = []
    for inp in info.get("inputs", []):
        inputs.append({
            "name": inp.get("name", ""),
            "label": inp.get("label", ""),
            "type": inp.get("type", ""),
            "value": inp.get("value", ""),
            "optional": inp.get("optional", False),
            "help": inp.get("help", ""),
        })
    outputs = []
    for out in info.get("outputs", []):
        outputs.append({
            "name": out.get("name", ""),
            "format": out.get("format", ""),
            "label": out.get("label", ""),
        })
    return {
        "id": info.get("id", tool_id),
        "name": info.get("name", ""),
        "version": info.get("version", ""),
        "description": info.get("description", ""),
        "edam_topics": info.get("edam_topics", []),
        "edam_operations": info.get("edam_operations", []),
        "requirements": [
            {"name": r.get("name", ""), "version": r.get("version", "")}
            for r in info.get("requirements", [])
        ],
        "inputs": inputs,
        "outputs": outputs,
        "help": info.get("help", ""),
    }


def run_tool(client, tool_id, history_id, inputs=None):
    """Run a tool with given inputs in a history.

    Args:
        client: GalaxyClient instance.
        tool_id: Tool identifier.
        history_id: Target history for outputs.
        inputs: Dict of tool parameter name -> value.
    """
    payload = {
        "tool_id": tool_id,
        "history_id": history_id,
        "inputs": inputs or {},
    }
    result = client.post("tools", json_data=payload)
    jobs = result.get("jobs", [])
    outputs = result.get("outputs", [])
    return {
        "tool_id": tool_id,
        "history_id": history_id,
        "jobs": [
            {
                "id": j.get("id", ""),
                "state": j.get("state", ""),
                "tool_id": j.get("tool_id", ""),
            }
            for j in jobs
        ],
        "outputs": [
            {
                "id": o.get("id", ""),
                "name": o.get("name", ""),
                "extension": o.get("extension", ""),
            }
            for o in outputs
        ],
    }
