"""Tool management — list, search, show, run Galaxy tools."""

import hashlib
import json
import re
import time

from galaxy_cli.utils.galaxy_backend import (
    DEFAULT_CONFIG_DIR,
    EXIT_USER_ERROR,
    GalaxyBackendError,
)


_GALAXY_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _is_data_ref(value):
    return (
        isinstance(value, dict)
        and set(value.keys()) <= {"src", "id", "values"}
        and "id" in value
    )


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


def _normalize_tool_id(tool_id):
    return {
        "id": tool_id,
        "name": "",
        "version": "",
        "description": "",
        "section": "",
    }


def _resolve_search_hits(client, tools, limit=None):
    """Resolve string-only search hits into full tool records."""
    if not tools or not all(isinstance(tool, str) for tool in tools):
        return tools
    tool_ids = tools[:limit] if limit else tools
    return [client.get(f"tools/{tool_id}") for tool_id in tool_ids]


def list_tools(client, query=None, section_id=None, resolve=True, limit=None):
    """List available tools, optionally filtered by search or section."""
    params = {"in_panel": False}
    if query:
        params["q"] = query
    tools = client.get("tools", params=params)
    if resolve:
        tools = _resolve_search_hits(client, tools, limit=limit)
    results = []
    for t in tools:
        if isinstance(t, dict):
            normalized = _normalize_tool_dict(t, section_id=section_id)
            if normalized is not None:
                results.append(normalized)
        elif isinstance(t, str) and not resolve:
            results.append(_normalize_tool_id(t))
        if limit and len(results) >= limit:
            break
    return results


def _rank_tool_matches(tools, terms):
    ranked = []
    for tool in tools:
        if isinstance(tool, str):
            tool = _normalize_tool_id(tool)
        if not isinstance(tool, dict):
            continue
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


def _tool_cache_path(client):
    digest = hashlib.sha256(client.url.encode("utf-8")).hexdigest()[:16]
    return DEFAULT_CONFIG_DIR / "tool-cache" / f"{digest}.json"


def _read_tool_cache(client):
    path = _tool_cache_path(client)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return None
    return tools


def _write_tool_cache(client, tools):
    path = _tool_cache_path(client)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "url": client.url,
        "created_at": time.time(),
        "tools": tools,
    }
    path.write_text(json.dumps(payload, separators=(",", ":")))
    return path


def _load_cached_tools(client, refresh_cache=False):
    if not refresh_cache:
        cached = _read_tool_cache(client)
        if cached is not None:
            return cached
    tools = list_tools(client, resolve=False)
    _write_tool_cache(client, tools)
    return tools


def search_tools(
    client,
    query,
    limit=25,
    resolve=False,
    use_cache=False,
    refresh_cache=False,
):
    """Search tools by name or description."""
    query = (query or "").strip()
    if not query:
        return []

    terms = [term.lower() for term in query.split() if term.strip()]
    if use_cache or refresh_cache:
        all_tools = _load_cached_tools(client, refresh_cache=refresh_cache)
        return _rank_tool_matches(all_tools, terms)[:limit]

    # First try Galaxy's native search behavior.
    direct_results = list_tools(client, query=query, resolve=resolve, limit=limit)
    if direct_results:
        return direct_results[:limit]

    if len(terms) <= 1:
        return direct_results

    # Fallback: keyword AND matching across ID, name, description, and section.
    all_tools = list_tools(client, resolve=False)
    return _rank_tool_matches(all_tools, terms)[:limit]


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
    """Map flattened Galaxy input paths to their terminal input types.

    Galaxy form schemas nest data inputs under conditionals, repeats, and
    sections, while the tool execution endpoint accepts flattened keys such as
    ``library|input_1`` and ``results_0|software_cond|input``.  This collector
    walks the normalized ``tool show`` schema recursively so those terminal
    data/data_collection nodes can still be recognized after flattening.
    """
    input_types = {}

    def _add(inp, prefix=""):
        name = inp.get("name")
        input_type = inp.get("type")
        path = f"{prefix}|{name}" if prefix and name else name
        if name and input_type:
            input_types[path] = input_type
        if not path:
            return
        if input_type == "conditional":
            test_param = inp.get("test_param") or {}
            if isinstance(test_param, dict):
                _add(test_param, path)
            for case in inp.get("cases", []) or []:
                for child in case.get("inputs", []) or []:
                    _add(child, path)
        elif input_type in {"repeat", "section"}:
            for child in inp.get("inputs", []) or []:
                _add(child, path)

    for inp in inputs or []:
        _add(inp)
    return input_types


def _collect_input_defs(inputs):
    """Map flattened Galaxy input paths to normalized input definitions."""
    input_defs = {}

    def _add(inp, prefix=""):
        name = inp.get("name")
        path = f"{prefix}|{name}" if prefix and name else name
        if name and path:
            input_defs[path] = inp
        if not path:
            return
        input_type = inp.get("type")
        if input_type == "conditional":
            test_param = inp.get("test_param") or {}
            if isinstance(test_param, dict):
                _add(test_param, path)
            for case in inp.get("cases", []) or []:
                for child in case.get("inputs", []) or []:
                    _add(child, path)
        elif input_type in {"repeat", "section"}:
            for child in inp.get("inputs", []) or []:
                _add(child, path)

    for inp in inputs or []:
        _add(inp)
    return input_defs


def _canonical_input_type_key(name):
    """Normalize repeat-indexed keys like ``results_0|input`` for lookup."""
    return re.sub(r"_(\d+)(?=\|)", "", name)


def _normalize_tool_input(name, value, input_types):
    """Convert CLI key=value strings to Galaxy tool input payloads."""
    input_type = input_types.get(name) or input_types.get(_canonical_input_type_key(name), "")
    if isinstance(value, list) and input_type in {"data", "data_collection"}:
        return [_normalize_tool_input(name, item, input_types) for item in value]
    if isinstance(value, str) and input_type in {"data", "data_collection"}:
        if ":" in value:
            src, dataset_id = value.split(":", 1)
            if src in {"hda", "hdca", "ldda"} and dataset_id:
                return {"src": src, "id": dataset_id}
        if _GALAXY_ID_RE.match(value):
            src = "hdca" if input_type == "data_collection" else "hda"
            return {"src": src, "id": value}
    return value


def _validation_error(message, suggestion):
    raise GalaxyBackendError(
        message,
        category="invalid_request",
        exit_code=EXIT_USER_ERROR,
        suggestion=suggestion,
    )


def _canonical_input_key(name):
    """Normalize repeat-indexed keys like ``a_0|b_12|c`` for schema lookup."""
    return re.sub(r"_(\d+)(?=\|)", "", name)


def _value_present(value):
    return value is not None and value != "" and value != []


def _input_has_default(inp):
    return _value_present(inp.get("default")) or _value_present(inp.get("value"))


def _is_required_input(inp):
    return (
        inp.get("type") in {"data", "data_collection"}
        and not inp.get("optional", False)
        and not _input_has_default(inp)
    )


def _validate_data_ref(path, value, input_type):
    expected_sources = {"data": {"hda", "ldda"}, "data_collection": {"hdca"}}.get(input_type)
    if not expected_sources:
        return
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, dict):
            src = item.get("src")
            ref_id = item.get("id")
            if not src or not ref_id:
                _validation_error(
                    f"Invalid data reference for input '{path}'. Expected an object with src and id.",
                    "Use {'src':'hda','id':'DATASET_ID'} for datasets or {'src':'hdca','id':'COLLECTION_ID'} for collections.",
                )
            if src not in expected_sources:
                expected = ", ".join(sorted(expected_sources))
                _validation_error(
                    f"Input '{path}' expects {input_type}, but source '{src}' was provided.",
                    f"Use one of these source prefixes for '{path}': {expected}.",
                )
        elif isinstance(item, str) and ":" in item:
            src, ref_id = item.split(":", 1)
            if src in {"hda", "hdca", "ldda"} and ref_id:
                if src not in expected_sources:
                    expected = ", ".join(sorted(expected_sources))
                    _validation_error(
                        f"Input '{path}' expects {input_type}, but source '{src}' was provided.",
                        f"Use one of these source prefixes for '{path}': {expected}.",
                    )
            elif ref_id:
                _validation_error(
                    f"Unsupported data source prefix '{src}' for input '{path}'.",
                    "Use hda: or ldda: for datasets, and hdca: for dataset collections.",
                )


def _validate_select(path, value, inp):
    if not _value_present(value):
        return
    options = inp.get("options", []) or []
    allowed = {str(opt.get("value")) for opt in options if "value" in opt}
    if not allowed:
        return
    values = value if isinstance(value, list) else [value]
    invalid = [str(item) for item in values if str(item) not in allowed]
    if invalid:
        _validation_error(
            f"Invalid select value for input '{path}': {', '.join(invalid)}.",
            f"Use one of: {', '.join(sorted(allowed))}.",
        )


def _validate_boolean(path, value, inp):
    if isinstance(value, bool) or not _value_present(value):
        return
    allowed = {
        "true",
        "false",
        str(inp.get("truevalue", "true")),
        str(inp.get("falsevalue", "false")),
    }
    if str(value).lower() not in {item.lower() for item in allowed}:
        _validation_error(
            f"Invalid boolean value for input '{path}': {value!r}.",
            "Use true or false.",
        )


def _validate_number(path, value, inp):
    if not _value_present(value):
        return
    try:
        parsed = float(value) if inp.get("type") == "float" else int(value)
    except (TypeError, ValueError) as exc:
        _validation_error(
            f"Invalid {inp.get('type')} value for input '{path}': {value!r}.",
            f"Provide a valid {inp.get('type')} value for '{path}'.",
        )
        raise AssertionError("unreachable") from exc
    minimum = inp.get("min")
    maximum = inp.get("max")
    if minimum is not None and parsed < minimum:
        _validation_error(
            f"Input '{path}' must be at least {minimum}; got {value!r}.",
            f"Choose a value >= {minimum}.",
        )
    if maximum is not None and parsed > maximum:
        _validation_error(
            f"Input '{path}' must be at most {maximum}; got {value!r}.",
            f"Choose a value <= {maximum}.",
        )


def _provided_key_matches(required_path, flat_inputs):
    required_parts = required_path.split("|")
    for key in flat_inputs:
        key_parts = key.split("|")
        if len(key_parts) != len(required_parts):
            continue
        if all(_canonical_input_key(kp) == rp for kp, rp in zip(key_parts, required_parts)):
            return True
    return False


def _has_provided_input_under(path, flat_inputs):
    canonical_path = _canonical_input_key(path)
    for key in flat_inputs:
        canonical_key = _canonical_input_key(key)
        if canonical_key == canonical_path or canonical_key.startswith(f"{canonical_path}|"):
            return True
    return False


def _repeat_minimum(inp):
    try:
        return int(inp.get("min", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _validate_required_inputs(inputs, flat_inputs):
    def _walk(inp, prefix=""):
        name = inp.get("name")
        path = f"{prefix}|{name}" if prefix and name else name
        input_type = inp.get("type")
        if not path:
            return
        if input_type == "conditional":
            test_param = inp.get("test_param") or {}
            test_path = f"{path}|{test_param.get('name')}" if test_param.get("name") else ""
            selected = flat_inputs.get(test_path)
            if selected is None:
                return
            for case in inp.get("cases", []) or []:
                if str(case.get("value")) == str(selected):
                    for child in case.get("inputs", []) or []:
                        _walk(child, path)
                    return
        elif input_type == "repeat":
            if _repeat_minimum(inp) <= 0 and not _has_provided_input_under(path, flat_inputs):
                return
            for child in inp.get("inputs", []) or []:
                _walk(child, path)
        elif input_type == "section":
            for child in inp.get("inputs", []) or []:
                _walk(child, path)
        elif _is_required_input(inp) and not _provided_key_matches(path, flat_inputs):
            _validation_error(
                f"Missing required input '{path}'.",
                "Run `galaxy-cli tool show TOOL_ID` to inspect required input names, then pass them with -i or --inputs-json.",
            )

    for inp in inputs or []:
        _walk(inp)


def validate_tool_inputs(tool_info, flat_inputs):
    """Validate obvious tool input mistakes before submitting to Galaxy."""
    input_defs = _collect_input_defs(tool_info.get("inputs", []))
    for path, value in flat_inputs.items():
        canonical = _canonical_input_key(path)
        inp = input_defs.get(path) or input_defs.get(canonical)
        if inp is None:
            _validation_error(
                f"Unknown input '{path}' for tool '{tool_info.get('id', '')}'.",
                "Run `galaxy-cli tool show TOOL_ID` and use the listed input names.",
            )
        input_type = inp.get("type")
        if input_type in {"data", "data_collection"}:
            _validate_data_ref(path, value, input_type)
        elif input_type == "select":
            _validate_select(path, value, inp)
        elif input_type == "boolean":
            _validate_boolean(path, value, inp)
        elif input_type in {"integer", "float"}:
            _validate_number(path, value, inp)
    _validate_required_inputs(tool_info.get("inputs", []), flat_inputs)


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
            if _is_data_ref(value):
                flat[prefix] = value
                return
            for k, v in value.items():
                child = f"{prefix}|{k}" if prefix else k
                _walk(child, v)
        elif isinstance(value, list) and value and all(_is_data_ref(v) for v in value):
            # Multiple=true data inputs use a list of dataset refs under one key.
            flat[prefix] = value
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            # Repeat block: index each item.
            for idx, item in enumerate(value):
                _walk(f"{prefix}_{idx}", item)
        else:
            flat[prefix] = value

    for key, value in (inputs or {}).items():
        _walk(key, value)
    return flat


def build_tool_payload(client, tool_id, history_id, inputs=None):
    """Build the exact POST body for a Galaxy tool execution."""
    flat_inputs = _flatten_nested_inputs(inputs or {})
    tool_info = show_tool(client, tool_id)
    validate_tool_inputs(tool_info, flat_inputs)
    input_types = _collect_input_types(tool_info.get("inputs", []))
    normalized_inputs = {
        key: _normalize_tool_input(key, value, input_types)
        for key, value in flat_inputs.items()
    }
    return {
        "tool_id": tool_id,
        "history_id": history_id,
        "inputs": normalized_inputs,
    }


def _submit_tool_payload(client, payload):
    result = client.post("tools", json_data=payload)
    jobs = result.get("jobs", [])
    outputs = result.get("outputs", [])
    output_collections = result.get("output_collections", [])
    return {
        "tool_id": payload.get("tool_id", ""),
        "history_id": payload.get("history_id", ""),
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


def run_tool(client, tool_id, history_id, inputs=None, payload=None):
    """Run a tool with given inputs in a history.

    Args:
        client: GalaxyClient instance.
        tool_id: Tool identifier.
        history_id: Target history for outputs. Inputs may be a flat dict
            using Galaxy's pipe-encoded keys (``operations_0|op_name``) or
            a nested dict/list structure that will be flattened automatically.
        payload: Pre-built tool execution payload from ``build_tool_payload``.
    """
    if payload is None:
        payload = build_tool_payload(client, tool_id, history_id, inputs=inputs)
    return _submit_tool_payload(client, payload)


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
