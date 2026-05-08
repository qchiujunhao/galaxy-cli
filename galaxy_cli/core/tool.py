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


def _normalize_input(inp):
    """Normalize a single Galaxy tool input into a structured dict.

    Handles all Galaxy parameter types: data, select, boolean, text, integer,
    float, data_column, conditional, repeat, section, and others.
    """
    ptype = inp.get("type", "")
    entry = {
        "name": inp.get("name", ""),
        "label": inp.get("label", ""),
        "type": ptype,
        "optional": inp.get("optional", False),
        "help": inp.get("help", ""),
    }

    if ptype == "data":
        entry["extensions"] = inp.get("extensions", [])
        entry["multiple"] = inp.get("multiple", False)

    elif ptype == "data_collection":
        entry["extensions"] = inp.get("extensions", [])
        entry["collection_type"] = inp.get("collection_type", "")

    elif ptype == "select":
        # options is [[label, value, selected], ...]
        raw_opts = inp.get("options", [])
        entry["options"] = [
            {"label": o[0], "value": o[1]}
            for o in raw_opts if isinstance(o, list) and len(o) >= 2
        ]
        entry["default"] = inp.get("value")
        entry["multiple"] = inp.get("multiple", False)

    elif ptype == "boolean":
        entry["default"] = inp.get("value", False)
        entry["truevalue"] = inp.get("truevalue", "true")
        entry["falsevalue"] = inp.get("falsevalue", "false")

    elif ptype in ("integer", "float"):
        entry["default"] = inp.get("value")
        entry["min"] = inp.get("min")
        entry["max"] = inp.get("max")

    elif ptype == "text":
        entry["default"] = inp.get("value")

    elif ptype == "data_column":
        entry["default"] = inp.get("value")
        entry["numerical"] = inp.get("numerical", False)
        entry["multiple"] = inp.get("multiple", False)

    elif ptype == "conditional":
        test_param = inp.get("test_param", {})
        entry["test_param"] = _normalize_input(test_param) if test_param else {}
        entry["cases"] = []
        for case in inp.get("cases", []):
            case_entry = {
                "value": case.get("value", ""),
                "inputs": [_normalize_input(ci) for ci in case.get("inputs", [])],
            }
            entry["cases"].append(case_entry)

    elif ptype == "repeat":
        entry["inputs"] = [_normalize_input(ri) for ri in inp.get("inputs", [])]
        entry["min"] = inp.get("min", 0)
        entry["max"] = inp.get("max")
        entry["default"] = inp.get("default", 0)

    elif ptype == "section":
        entry["inputs"] = [_normalize_input(si) for si in inp.get("inputs", [])]
        entry["expanded"] = inp.get("expanded", False)

    else:
        # Unknown type — include default value if present
        entry["default"] = inp.get("value")

    return entry


def _strip_input_help(inp):
    """Recursively drop the verbose ``help`` field from a normalized input."""
    if not isinstance(inp, dict):
        return inp
    inp.pop("help", None)
    for nested_key in ("inputs", "cases"):
        for child in inp.get(nested_key, []) or []:
            if isinstance(child, dict):
                _strip_input_help(child)
                # conditional cases also carry their own input lists
                for ci in child.get("inputs", []) or []:
                    _strip_input_help(ci)
    test_param = inp.get("test_param")
    if isinstance(test_param, dict):
        _strip_input_help(test_param)
    return inp


def show_tool(client, tool_id, full=False):
    """Show detailed info about a tool, including its inputs.

    By default, returns a compact record with the fields agents need to run
    the tool: id, name, version, description, inputs, outputs. Pass
    ``full=True`` to additionally include ``help``, ``edam_topics``,
    ``edam_operations``, ``requirements``, and per-input ``help`` strings.
    These fields are large and rarely needed for tool execution; including
    them by default inflates LLM agent context.
    """
    info = client.get(f"tools/{tool_id}", params={"io_details": True})
    inputs = [_normalize_input(inp) for inp in info.get("inputs", [])]
    outputs = []
    for out in info.get("outputs", []):
        outputs.append({
            "name": out.get("name", ""),
            "format": out.get("format", ""),
            "label": out.get("label", ""),
        })
    result = {
        "id": info.get("id", tool_id),
        "name": info.get("name", ""),
        "version": info.get("version", ""),
        "description": info.get("description", ""),
        "inputs": inputs,
        "outputs": outputs,
    }
    if full:
        result["edam_topics"] = info.get("edam_topics", [])
        result["edam_operations"] = info.get("edam_operations", [])
        result["requirements"] = [
            {"name": r.get("name", ""), "version": r.get("version", "")}
            for r in info.get("requirements", [])
        ]
        result["help"] = info.get("help", "")
    else:
        for inp in inputs:
            _strip_input_help(inp)
    return result


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
    if isinstance(value, str) and input_type in {"data", "data_collection"}:
        if ":" in value:
            src, dataset_id = value.split(":", 1)
            if src in {"hda", "hdca", "ldda"} and dataset_id:
                return {"src": src, "id": dataset_id}
        if _GALAXY_ID_RE.match(value):
            src = "hdca" if input_type == "data_collection" else "hda"
            return {"src": src, "id": value}
    return value


def _flatten_nested_inputs(inputs):
    """Flatten nested dict/list inputs into Galaxy's pipe-encoded flat keys.

    Galaxy's ``tools`` POST endpoint accepts repeats and conditionals as
    flat keys joined by ``|`` with a 0-based index per repeat item, e.g.::

        operations_0|op_name = mean
        operations_0|op_column = 2
        operations_1|op_name = sum

    This helper accepts a JSON-friendly nested representation and produces
    that flat form. Keys that already contain ``|`` are passed through.
    Scalar values are returned as-is.
    """
    flat = {}

    def _walk(prefix, value):
        if isinstance(value, dict):
            # Pass through dataset/collection refs unchanged.
            if set(value.keys()) <= {"src", "id", "values"} and "id" in value:
                flat[prefix] = value
                return
            for k, v in value.items():
                child = f"{prefix}|{k}" if prefix else k
                _walk(child, v)
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            # Repeat block: index each item.
            for idx, item in enumerate(value):
                _walk(f"{prefix}_{idx}", item)
        else:
            flat[prefix] = value

    for key, value in (inputs or {}).items():
        _walk(key, value)
    return flat


def run_tool(client, tool_id, history_id, inputs=None):
    """Run a tool with given inputs in a history.

    Args:
        client: GalaxyClient instance.
        tool_id: Tool identifier.
        history_id: Target history for outputs. Inputs may be a flat dict
            using Galaxy's pipe-encoded keys (``operations_0|op_name``) or
            a nested dict/list structure that will be flattened automatically.
    """
    flat_inputs = _flatten_nested_inputs(inputs or {})
    tool_info = show_tool(client, tool_id)
    input_types = _collect_input_types(tool_info.get("inputs", []))
    normalized_inputs = {
        key: _normalize_tool_input(key, value, input_types)
        for key, value in flat_inputs.items()
    }

    payload = {
        "tool_id": tool_id,
        "history_id": history_id,
        "inputs": normalized_inputs,
    }
    result = client.post("tools", json_data=payload)
    jobs = result.get("jobs", [])
    outputs = result.get("outputs", [])
    output_collections = result.get("output_collections", [])
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
                "history_content_type": o.get("history_content_type", "dataset"),
            }
            for o in outputs
        ] + [
            {
                "id": o.get("id", ""),
                "name": o.get("name", ""),
                "extension": "",
                "collection_type": o.get("collection_type", ""),
                "history_content_type": o.get("history_content_type", "dataset_collection"),
            }
            for o in output_collections
        ],
    }


def refresh_output_details(client, history_id, outputs):
    """Fetch compact state/type details for tool output datasets.

    Tool submission responses often have incomplete output metadata because the
    datasets are still being created. After a job finishes, this helper lets the
    CLI return the final state, datatype, and size in the original `tool run`
    JSON instead of forcing callers to issue one `dataset show` per output.
    """
    refreshed = []
    for output in outputs or []:
        item = dict(output)
        content_id = item.get("id")
        if not content_id:
            refreshed.append(item)
            continue
        history_content_type = item.get("history_content_type", "dataset")
        if history_content_type == "dataset_collection":
            info = client.get(f"histories/{history_id}/contents/dataset_collections/{content_id}")
            if not isinstance(info, dict):
                refreshed.append(item)
                continue
            item.update({
                "id": info.get("id", content_id),
                "name": info.get("name", item.get("name", "")),
                "state": info.get("populated_state", info.get("state", "")),
                "history_content_type": info.get("history_content_type", history_content_type),
                "collection_type": info.get("collection_type", item.get("collection_type", "")),
                "element_count": info.get("element_count", 0),
                "populated": info.get("populated", False),
                "elements_datatypes": info.get("elements_datatypes", []),
            })
            refreshed.append(item)
            continue

        info = client.get(f"histories/{history_id}/contents/{content_id}")
        if not isinstance(info, dict):
            refreshed.append(item)
            continue
        item.update({
            "id": info.get("id", content_id),
            "name": info.get("name", item.get("name", "")),
            "state": info.get("state", ""),
            "extension": info.get("extension", item.get("extension", "")),
            "file_size": info.get("file_size", 0),
            "genome_build": info.get("genome_build", "?"),
            "data_type": info.get("data_type", ""),
            "visible": info.get("visible", True),
            "history_content_type": info.get("history_content_type", ""),
            "misc_blurb": info.get("misc_blurb", ""),
        })
        refreshed.append(item)
    return refreshed
