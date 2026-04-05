"""Tool management — list, search, show, run Galaxy tools."""

import re


_GALAXY_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _searchable_text(tool):
    return " ".join(
        [
            tool.get("id", ""),
            tool.get("name", ""),
            tool.get("description", ""),
            tool.get("section", ""),
        ]
    ).lower()


def _normalize_tool_dict(tool, section_id=None):
    if section_id and tool.get("panel_section_id") != section_id:
        return None
    return {
        "id": tool.get("id", ""),
        "name": tool.get("name", ""),
        "version": tool.get("version", ""),
        "description": tool.get("description", ""),
        "section": tool.get("panel_section_name", ""),
    }


def _resolve_search_hits(client, tools):
    """Resolve string-only search hits into full tool records."""
    if not tools or not all(isinstance(tool, str) for tool in tools):
        return tools
    return [client.get(f"tools/{tool_id}") for tool_id in tools]


def list_tools(client, query=None, section_id=None):
    """List available tools, optionally filtered by search or section."""
    params = {"in_panel": False}
    if query:
        params["q"] = query
    tools = client.get("tools", params=params)
    tools = _resolve_search_hits(client, tools)
    results = []
    for t in tools:
        if isinstance(t, dict):
            normalized = _normalize_tool_dict(t, section_id=section_id)
            if normalized is not None:
                results.append(normalized)
    return results


def search_tools(client, query):
    """Search tools by name or description."""
    query = (query or "").strip()
    if not query:
        return []

    # First try Galaxy's native search behavior.
    direct_results = list_tools(client, query=query)
    if direct_results:
        return direct_results

    terms = [term.lower() for term in query.split() if term.strip()]
    if len(terms) <= 1:
        return direct_results

    # Fallback: keyword AND matching across ID, name, description, and section.
    all_tools = list_tools(client)
    ranked = []
    for tool in all_tools:
        haystack = _searchable_text(tool)
        if all(term in haystack for term in terms):
            score = 0
            name = tool.get("name", "").lower()
            description = tool.get("description", "").lower()
            tool_id = tool.get("id", "").lower()
            for term in terms:
                if term in name:
                    score += 3
                if term in description:
                    score += 2
                if term in tool_id:
                    score += 2
            ranked.append((score, tool))

    ranked.sort(key=lambda item: (-item[0], item[1].get("name", ""), item[1].get("id", "")))
    return [tool for _, tool in ranked]


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


def _collect_input_types(inputs):
    """Map input names to their Galaxy input types."""
    input_types = {}
    for inp in inputs or []:
        name = inp.get("name")
        input_type = inp.get("type")
        if name and input_type:
            input_types[name] = input_type
    return input_types


def _normalize_tool_input(name, value, input_types):
    """Convert CLI key=value strings to Galaxy tool input payloads."""
    input_type = input_types.get(name, "")
    if isinstance(value, str):
        if ":" in value:
            src, dataset_id = value.split(":", 1)
            if src in {"hda", "hdca", "ldda"} and dataset_id:
                return {"src": src, "id": dataset_id}
        if input_type in {"data", "data_collection"} and _GALAXY_ID_RE.match(value):
            src = "hdca" if input_type == "data_collection" else "hda"
            return {"src": src, "id": value}
    return value


def run_tool(client, tool_id, history_id, inputs=None):
    """Run a tool with given inputs in a history.

    Args:
        client: GalaxyClient instance.
        tool_id: Tool identifier.
        history_id: Target history for outputs.
        inputs: Dict of tool parameter name -> value.
    """
    tool_info = show_tool(client, tool_id)
    input_types = _collect_input_types(tool_info.get("inputs", []))
    normalized_inputs = {
        key: _normalize_tool_input(key, value, input_types)
        for key, value in (inputs or {}).items()
    }

    payload = {
        "tool_id": tool_id,
        "history_id": history_id,
        "inputs": normalized_inputs,
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
