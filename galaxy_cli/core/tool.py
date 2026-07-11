"""Tool management — list, search, show, run Galaxy tools."""

import copy
import re
import time

from galaxy_cli.utils.galaxy_backend import (
    EXIT_SERVER_ERROR,
    EXIT_TIMEOUT,
    EXIT_USER_ERROR,
    GalaxyBackendError,
    get_with_deadline,
)

from galaxy_cli.core import job as job_mod
from galaxy_cli.core import metadata_cache


_GALAXY_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _is_data_ref(value):
    return (
        isinstance(value, dict)
        and value.get("src") in {"hda", "hdca", "ldda", "dce"}
        and "id" in value
    )


def _is_batch(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("values"), list)
        and (value.get("__class__") == "Batch" or value.get("batch") is True)
    )


def _searchable_text(tool):
    return " ".join(
        str(value or "") for value in [
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


def _server_version_key(version):
    return version if isinstance(version, (dict, list)) else str(version)


def _tool_show_cache_context(client):
    """Return the non-secret cache identity, or ``None`` for mock/minimal clients."""
    if not isinstance(getattr(client, "url", None), str):
        return None
    try:
        server_version = _server_version_key(metadata_cache.server_version(client))
    except (AttributeError, GalaxyBackendError):
        return None
    return client.url, server_version


def _read_tool_show_cache(client, server_version, requested_tool_id):
    alias_key = [client.url, server_version, requested_tool_id]
    alias = metadata_cache.read("tool-schema-alias", alias_key)
    if not isinstance(alias, dict):
        return None
    exact_id = alias.get("exact_tool_id")
    tool_version = alias.get("tool_version")
    if not isinstance(exact_id, str) or not isinstance(tool_version, str):
        return None
    result = metadata_cache.read(
        "tool-schema", [client.url, server_version, exact_id, tool_version]
    )
    if not isinstance(result, dict):
        return None
    if result.get("id") != exact_id or result.get("version", "") != tool_version:
        return None
    return result


def _write_tool_show_cache(client, server_version, requested_tool_id, result):
    exact_id = result.get("id", requested_tool_id)
    tool_version = result.get("version", "")
    path = metadata_cache.write(
        "tool-schema", [client.url, server_version, exact_id, tool_version], result
    )
    metadata_cache.write(
        "tool-schema-alias",
        [client.url, server_version, requested_tool_id],
        {"exact_tool_id": exact_id, "tool_version": tool_version},
    )
    return path


def _read_tool_cache(client, server_version):
    tools = metadata_cache.read("tool-search", [client.url, server_version])
    return tools if isinstance(tools, list) else None


def _write_tool_cache(client, server_version, tools):
    return metadata_cache.write("tool-search", [client.url, server_version], tools)


def _load_cached_tools(client, refresh_cache=False):
    server_version = _server_version_key(
        metadata_cache.server_version(client, refresh=refresh_cache)
    )
    if not refresh_cache:
        cached = _read_tool_cache(client, server_version)
        if cached is not None:
            return cached
    tools = list_tools(client, resolve=False)
    _write_tool_cache(client, server_version, tools)
    return tools


def search_tools(
    client,
    query,
    limit=25,
    resolve=False,
    use_cache=False,
    refresh_cache=False,
    exact=False,
    input_extension=None,
    output_extension=None,
    version=None,
    all_versions=False,
):
    """Search tools by name or description."""
    query = (query or "").strip()
    if not query:
        return []

    terms = [term.lower() for term in query.split() if term.strip()]
    if use_cache or refresh_cache:
        all_tools = _load_cached_tools(client, refresh_cache=refresh_cache)
        matches = _rank_tool_matches(all_tools, terms)
        if exact:
            lowered = query.lower()
            matches = [
                item for item in matches
                if item.get("id", "").lower() == lowered
                or item.get("name", "").lower() == lowered
            ]
        if version:
            matches = [item for item in matches if str(item.get("version", "")) == version]
        needs_details = resolve or input_extension or output_extension
        if needs_details:
            detailed = []
            candidates = matches if (input_extension or output_extension) else matches[:limit]
            for item in candidates:
                info = show_tool(client, item.get("id", ""), use_cache=use_cache)
                input_extensions = sorted({
                    extension
                    for inp in info.get("inputs", [])
                    for extension in inp.get("extensions", []) or []
                })
                output_extensions = sorted({
                    output.get("format", "") for output in info.get("outputs", [])
                    if output.get("format")
                })
                if input_extension and input_extension not in input_extensions:
                    continue
                if output_extension and output_extension not in output_extensions:
                    continue
                item = dict(item)
                item.update({
                    "exact_tool_id": info.get("id", item.get("id", "")),
                    "version": info.get("version", item.get("version", "")),
                    "input_extensions": input_extensions,
                    "output_extensions": output_extensions,
                })
                detailed.append(item)
            matches = detailed
        if all_versions:
            expanded = []
            for item in matches:
                versions = client.get(
                    "tools", params={"tool_id": item.get("id", ""), "in_panel": False}
                )
                if isinstance(versions, list) and versions:
                    expanded.extend(
                        _normalize_tool_dict(value) if isinstance(value, dict) else _normalize_tool_id(value)
                        for value in versions
                    )
                else:
                    expanded.append(item)
            matches = expanded
        return matches[:limit]

    # First try Galaxy's native search behavior.
    direct_results = list_tools(client, query=query, resolve=resolve, limit=limit)
    if direct_results:
        matches = direct_results
        if exact:
            lowered = query.lower()
            matches = [
                item for item in matches
                if item.get("id", "").lower() == lowered
                or item.get("name", "").lower() == lowered
            ]
        if version:
            matches = [item for item in matches if str(item.get("version", "")) == version]
        if input_extension or output_extension:
            detailed = []
            for item in matches:
                info = show_tool(client, item.get("id", ""), use_cache=False)
                input_extensions = sorted({
                    extension for inp in info.get("inputs", [])
                    for extension in inp.get("extensions", []) or []
                })
                output_extensions = sorted({
                    output.get("format", "") for output in info.get("outputs", [])
                    if output.get("format")
                })
                if input_extension and input_extension not in input_extensions:
                    continue
                if output_extension and output_extension not in output_extensions:
                    continue
                item = dict(item, exact_tool_id=info.get("id", item.get("id", "")),
                            input_extensions=input_extensions,
                            output_extensions=output_extensions)
                detailed.append(item)
            matches = detailed
        if all_versions:
            expanded = []
            for item in matches:
                versions = client.get(
                    "tools", params={"tool_id": item.get("id", ""), "in_panel": False}
                )
                expanded.extend(
                    _normalize_tool_dict(value) if isinstance(value, dict) else _normalize_tool_id(value)
                    for value in versions or [item]
                )
            matches = expanded
        return matches[:limit]

    if len(terms) <= 1:
        return direct_results

    # Fallback: keyword AND matching across ID, name, description, and section.
    all_tools = list_tools(client, resolve=False)
    return _rank_tool_matches(all_tools, terms)[:limit]


def tool_template(client, tool_id, use_cache=True, refresh_cache=False):
    """Build a machine-fillable nested input skeleton from compact metadata."""
    info = show_tool(
        client, tool_id, use_cache=use_cache, refresh_cache=refresh_cache
    )

    def value_for(inp):
        input_type = inp.get("type")
        if input_type == "data":
            value = {"src": "hda", "id": "DATASET_ID"}
            return [value] if inp.get("multiple") else value
        if input_type == "data_collection":
            return {"src": "hdca", "id": "COLLECTION_ID"}
        if input_type in {"repeat", "section"}:
            nested = {child["name"]: value_for(child) for child in inp.get("inputs", [])}
            return [nested] if input_type == "repeat" else nested
        if input_type == "conditional":
            test = inp.get("test_param") or {}
            result = {test.get("name", "condition"): value_for(test)}
            cases = inp.get("cases", [])
            if cases:
                result.update({child["name"]: value_for(child) for child in cases[0].get("inputs", [])})
            return result
        if inp.get("default") is not None:
            return inp.get("default")
        options = inp.get("options", []) or []
        if options:
            return options[0].get("value")
        return None

    return {
        "tool_id": info.get("id", tool_id),
        "tool_version": info.get("version", ""),
        "inputs": {inp.get("name", "input"): value_for(inp) for inp in info.get("inputs", [])},
    }


def tool_examples(client, tool_id, limit=2, max_chars=12000):
    """Return bounded Galaxy-provided tool test examples."""
    raw = client.get(f"tools/{tool_id}/test_data")
    if isinstance(raw, dict):
        raw = raw.get("tests", raw.get("test_data", []))
    examples = raw if isinstance(raw, list) else []
    bounded = []
    used = 0
    for example in examples[:limit]:
        text = str(example)
        if used + len(text) > max_chars:
            break
        bounded.append(example)
        used += len(text)
    return {
        "tool_id": tool_id,
        "examples": bounded,
        "count": len(bounded),
        "truncated": len(bounded) < min(len(examples), limit),
    }


def validate_tool_on_server(client, tool_id, history_id, inputs):
    """Validate with Galaxy's non-executing tool build endpoint."""
    tool_info, _, strict_inputs = _prepare_tool_inputs(client, tool_id, inputs)
    payload = {
        "history_id": history_id,
        "tool_version": tool_info.get("version", ""),
        "inputs": strict_inputs,
    }
    try:
        result = client.post(f"tools/{tool_id}/build", json_data=payload)
    except GalaxyBackendError as exc:
        if exc.status_code in {404, 405}:
            return {
                "supported": False,
                "valid": None,
                "tool_id": tool_info.get("id", tool_id),
                "tool_version": tool_info.get("version", ""),
                "reason": "server_side_validation_unsupported",
            }
        raise
    errors = result.get("errors", {}) if isinstance(result, dict) else {}
    return {
        "supported": True,
        "valid": not bool(errors),
        "tool_id": tool_info.get("id", tool_id),
        "tool_version": tool_info.get("version", ""),
        "errors": errors,
    }


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


def show_tool(client, tool_id, full=False, use_cache=True, refresh_cache=False):
    """Show detailed info about a tool, including its inputs.

    By default, returns a compact record with the fields agents need to run
    the tool: id, name, version, description, inputs, outputs. Pass
    ``full=True`` to additionally include ``help``, ``edam_topics``,
    ``edam_operations``, ``requirements``, and per-input ``help`` strings.
    These fields are large and rarely needed for tool execution; including
    them by default inflates LLM agent context.
    """
    cache_context = None
    if use_cache and not full:
        cache_context = _tool_show_cache_context(client)
        if cache_context and not refresh_cache:
            cached = _read_tool_show_cache(client, cache_context[1], tool_id)
            if cached is not None:
                return cached

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
        if cache_context:
            _write_tool_show_cache(client, cache_context[1], tool_id, result)
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
    return re.sub(r"_(\d+)(?=\||$)", "", name)


def _normalize_tool_input(name, value, input_types):
    """Convert CLI key=value strings to Galaxy tool input payloads."""
    input_type = input_types.get(name) or input_types.get(_canonical_input_type_key(name), "")
    if isinstance(value, list) and input_type in {"data", "data_collection"}:
        return [_normalize_tool_input(name, item, input_types) for item in value]
    if isinstance(value, str) and input_type in {"data", "data_collection"}:
        if ":" in value:
            src, dataset_id = value.split(":", 1)
            if src in {"hda", "hdca", "ldda", "dce"} and dataset_id:
                return {"src": src, "id": dataset_id}
        if _GALAXY_ID_RE.match(value):
            src = "hdca" if input_type == "data_collection" else "hda"
            return {"src": src, "id": value}
    return value


def _input_json_path(path):
    parts = []
    path_parts = str(path or "").split("|")
    for index, part in enumerate(path_parts):
        match = re.match(r"^(.*)_(\d+)$", part)
        if match and index < len(path_parts) - 1:
            parts.append(f".{match.group(1)}[{match.group(2)}]")
        elif part:
            parts.append(f".{part}")
    return "$.inputs" + "".join(parts)


def _validation_error(
    message,
    suggestion=None,
    *,
    path="$",
    expected="valid input",
    allowed_values=None,
    example=None,
):
    validation = {"path": path, "expected": expected}
    if allowed_values:
        allowed_values = list(allowed_values)
        validation["allowed_values"] = allowed_values[:25]
        if len(allowed_values) > 25:
            validation["allowed_values_truncated"] = True
    if example is not None:
        validation["example"] = example
    raise GalaxyBackendError(
        message,
        category="invalid_request",
        error_kind="validation_error",
        exit_code=EXIT_USER_ERROR,
        submission_state="not_submitted",
        retry_safe=True,
        details={"validation": validation},
    )


def _canonical_input_key(name):
    """Normalize repeat-indexed keys like ``a_0|b_12|c`` for schema lookup."""
    return re.sub(r"_(\d+)(?=\||$)", "", name)


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


def _validate_data_ref(path, value, inp):
    input_type = inp.get("type")
    expected_sources = {
        "data": {"hda", "ldda", "dce"},
        "data_collection": {"hdca", "dce"},
    }.get(input_type)
    if not expected_sources:
        return
    batch = _is_batch(value)
    values = value.get("values", []) if batch else value if isinstance(value, list) else [value]
    if input_type == "data" and (batch or inp.get("multiple")):
        expected_sources = expected_sources | {"hdca"}
    for item in values:
        if isinstance(item, dict):
            src = item.get("src")
            ref_id = item.get("id")
            if not src or not ref_id:
                _validation_error(
                    f"Invalid data reference for input '{path}'. Expected an object with src and id.",
                    path=_input_json_path(path),
                    expected=f"{input_type} reference with src and id",
                    allowed_values=sorted(expected_sources),
                    example={
                        "src": "hdca" if input_type == "data_collection" else "hda",
                        "id": "ID",
                    },
                )
            if src not in expected_sources:
                _validation_error(
                    f"Input '{path}' expects {input_type}, but source '{src}' was provided.",
                    path=_input_json_path(path),
                    expected=f"{input_type} reference",
                    allowed_values=sorted(expected_sources),
                    example={"src": sorted(expected_sources)[0], "id": "ID"},
                )
        elif isinstance(item, str) and ":" in item:
            src, ref_id = item.split(":", 1)
            if src in {"hda", "hdca", "ldda", "dce"} and ref_id:
                if src not in expected_sources:
                    _validation_error(
                        f"Input '{path}' expects {input_type}, but source '{src}' was provided.",
                        path=_input_json_path(path),
                        expected=f"{input_type} reference",
                        allowed_values=sorted(expected_sources),
                        example={"src": sorted(expected_sources)[0], "id": "ID"},
                    )
            elif ref_id:
                _validation_error(
                    f"Unsupported data source prefix '{src}' for input '{path}'.",
                    path=_input_json_path(path),
                    expected=f"{input_type} reference",
                    allowed_values=sorted(expected_sources),
                    example={"src": sorted(expected_sources)[0], "id": "ID"},
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
            f"Invalid select value for input '{path}'.",
            path=_input_json_path(path),
            expected="allowed select value",
            allowed_values=sorted(allowed),
            example=sorted(allowed)[0],
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
            f"Invalid boolean value for input '{path}'.",
            path=_input_json_path(path),
            expected="boolean",
            allowed_values=[True, False],
            example=True,
        )


def _validate_number(path, value, inp):
    if not _value_present(value):
        return
    try:
        parsed = float(value) if inp.get("type") == "float" else int(value)
    except (TypeError, ValueError) as exc:
        _validation_error(
            f"Invalid {inp.get('type')} value for input '{path}'.",
            path=_input_json_path(path),
            expected=inp.get("type", "number"),
            example=1 if inp.get("type") == "integer" else 1.0,
        )
        raise AssertionError("unreachable") from exc
    minimum = inp.get("min")
    maximum = inp.get("max")
    if minimum is not None and parsed < minimum:
        _validation_error(
            f"Input '{path}' must be at least {minimum}.",
            path=_input_json_path(path),
            expected=f"{inp.get('type')} >= {minimum}",
            example=minimum,
        )
    if maximum is not None and parsed > maximum:
        _validation_error(
            f"Input '{path}' must be at most {maximum}.",
            path=_input_json_path(path),
            expected=f"{inp.get('type')} <= {maximum}",
            example=maximum,
        )


def _provided_key_matches(required_path, flat_inputs):
    required_parts = required_path.split("|")
    for key in flat_inputs:
        key_parts = key.split("|")
        if len(key_parts) != len(required_parts):
            continue
        if all(
            candidate == required
            or _canonical_input_key(candidate) == required
            for candidate, required in zip(key_parts, required_parts)
        ):
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
    def _provided_value(path):
        if path in flat_inputs:
            return flat_inputs[path]
        canonical_path = _canonical_input_key(path)
        for key, value in flat_inputs.items():
            if _canonical_input_key(key) == canonical_path:
                return value
        return None

    def _walk(inp, prefix=""):
        name = inp.get("name")
        path = f"{prefix}|{name}" if prefix and name else name
        input_type = inp.get("type")
        if not path:
            return
        if input_type == "conditional":
            test_param = inp.get("test_param") or {}
            test_path = f"{path}|{test_param.get('name')}" if test_param.get("name") else ""
            selected = _provided_value(test_path)
            if selected is None:
                return
            for case in inp.get("cases", []) or []:
                if _conditional_values_match(case.get("value"), selected):
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
                path=_input_json_path(path),
                expected=f"required {inp.get('type', 'value')}",
                example={
                    "src": "hdca" if inp.get("type") == "data_collection" else "hda",
                    "id": "ID",
                },
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
                path=_input_json_path(path),
                expected="known tool input",
                example=next(iter(sorted(input_defs)), "input"),
            )
        input_type = inp.get("type")
        if input_type in {"data", "data_collection"}:
            _validate_data_ref(path, value, inp)
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
        if _is_batch(value):
            flat[prefix] = value
        elif isinstance(value, dict):
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


def _find_nested_input_schema(inputs, segment):
    for inp in inputs or []:
        if inp.get("name") == segment:
            return inp, None
    match = re.match(r"^(.*)_(\d+)$", segment)
    if match:
        base, index = match.groups()
        for inp in inputs or []:
            if inp.get("name") == base and inp.get("type") == "repeat":
                return inp, int(index)
    return None, None


def _nested_child_schemas(inp):
    input_type = inp.get("type")
    if input_type in {"repeat", "section"}:
        return inp.get("inputs", []) or []
    if input_type == "conditional":
        children = []
        test_param = inp.get("test_param") or {}
        if test_param:
            children.append(test_param)
        for case in inp.get("cases", []) or []:
            children.extend(case.get("inputs", []) or [])
        return children
    return []


def _nest_pipe_inputs(inputs, tool_inputs):
    """Convert legacy pipe keys to strict nested objects using the tool schema.

    Schema lookup is important here: a parameter name ending in ``_1`` is not
    necessarily a repeat instance, while ``repeat_name_1`` is.
    """
    nested = {
        key: copy.deepcopy(value)
        for key, value in (inputs or {}).items()
        if "|" not in key
    }
    for flat_key, value in (inputs or {}).items():
        if "|" not in flat_key:
            continue
        current = nested
        schemas = tool_inputs or []
        parts = flat_key.split("|")
        for position, segment in enumerate(parts):
            inp, repeat_index = _find_nested_input_schema(schemas, segment)
            name = inp.get("name") if inp else segment
            last = position == len(parts) - 1
            if repeat_index is not None:
                repeat_values = current.setdefault(name, [])
                if not isinstance(repeat_values, list):
                    repeat_values = []
                    current[name] = repeat_values
                while len(repeat_values) <= repeat_index:
                    repeat_values.append({})
                if last:
                    repeat_values[repeat_index] = copy.deepcopy(value)
                    break
                if not isinstance(repeat_values[repeat_index], dict):
                    repeat_values[repeat_index] = {}
                current = repeat_values[repeat_index]
                schemas = _nested_child_schemas(inp)
                continue
            if last:
                current[name] = copy.deepcopy(value)
                break
            child = current.get(name)
            if not isinstance(child, dict):
                child = {}
                current[name] = child
            current = child
            schemas = _nested_child_schemas(inp) if inp else []
    return nested


def _normalize_data_scalar(value, input_type):
    if isinstance(value, str):
        if ":" in value:
            src, content_id = value.split(":", 1)
            if src in {"hda", "hdca", "ldda", "dce"} and content_id:
                return {"src": src, "id": content_id}
        if _GALAXY_ID_RE.match(value):
            return {
                "src": "hdca" if input_type == "data_collection" else "hda",
                "id": value,
            }
    return copy.deepcopy(value)


def _normalize_strict_value(value, inp):
    input_type = inp.get("type")
    if input_type in {"data", "data_collection"}:
        if _is_batch(value):
            batch = copy.deepcopy(value)
            batch.pop("batch", None)
            batch["__class__"] = "Batch"
            batch["values"] = [
                _normalize_data_scalar(item, input_type)
                for item in value.get("values", [])
            ]
            return batch
        if isinstance(value, list):
            return [_normalize_data_scalar(item, input_type) for item in value]
        return _normalize_data_scalar(value, input_type)
    if input_type == "boolean" and isinstance(value, str):
        lowered = value.lower()
        true_values = {"true", str(inp.get("truevalue", "true")).lower()}
        false_values = {"false", str(inp.get("falsevalue", "false")).lower()}
        if lowered in true_values:
            return True
        if lowered in false_values:
            return False
    if input_type == "integer" and isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    if input_type == "float" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if input_type == "repeat" and isinstance(value, list):
        return [
            _normalize_strict_object(item, inp.get("inputs", []) or [])
            if isinstance(item, dict)
            else copy.deepcopy(item)
            for item in value
        ]
    if input_type == "section" and isinstance(value, dict):
        return _normalize_strict_object(value, inp.get("inputs", []) or [])
    if input_type == "conditional" and isinstance(value, dict):
        test_param = inp.get("test_param") or {}
        normalized = {}
        selected = value.get(test_param.get("name"))
        if test_param.get("name") in value:
            selected = _normalize_strict_value(selected, test_param)
            normalized[test_param["name"]] = selected
        child_schemas = []
        for case in inp.get("cases", []) or []:
            if selected is None or _conditional_values_match(case.get("value"), selected):
                child_schemas.extend(case.get("inputs", []) or [])
        child_lookup = {child.get("name"): child for child in child_schemas}
        for key, item in value.items():
            if key == test_param.get("name"):
                continue
            normalized[key] = (
                _normalize_strict_value(item, child_lookup[key])
                if key in child_lookup
                else copy.deepcopy(item)
            )
        return normalized
    return copy.deepcopy(value)


def _conditional_values_match(case_value, selected):
    if isinstance(case_value, bool) or isinstance(selected, bool):
        return str(case_value).lower() == str(selected).lower()
    return str(case_value) == str(selected)


def _normalize_strict_object(values, schemas):
    lookup = {inp.get("name"): inp for inp in schemas or []}
    return {
        key: _normalize_strict_value(value, lookup[key])
        if key in lookup
        else copy.deepcopy(value)
        for key, value in (values or {}).items()
    }


def _normalize_strict_inputs(inputs, tool_info):
    nested = _nest_pipe_inputs(inputs or {}, tool_info.get("inputs", []))
    return _normalize_strict_object(nested, tool_info.get("inputs", []))


def _normalize_legacy_input(name, value, input_types):
    if _is_batch(value):
        return {
            "batch": True,
            "values": [
                _normalize_tool_input(name, item, input_types)
                for item in value.get("values", [])
            ],
        }
    return _normalize_tool_input(name, value, input_types)


def _prepare_tool_inputs(client, tool_id, inputs):
    flat_inputs = _flatten_nested_inputs(inputs or {})
    tool_info = show_tool(client, tool_id, use_cache=True)
    validate_tool_inputs(tool_info, flat_inputs)
    input_types = _collect_input_types(tool_info.get("inputs", []))
    legacy_inputs = {
        key: _normalize_legacy_input(key, value, input_types)
        for key, value in flat_inputs.items()
    }
    strict_inputs = _normalize_strict_inputs(inputs or {}, tool_info)
    return tool_info, legacy_inputs, strict_inputs


def build_tool_payload(client, tool_id, history_id, inputs=None):
    """Build the legacy ``POST /api/tools`` body.

    This function intentionally retains its pre-1.5 behavior for callers that
    persist or inspect legacy payloads. New code should use
    :func:`build_tool_execution_plan`.
    """
    _, legacy_inputs, _ = _prepare_tool_inputs(client, tool_id, inputs)
    return {
        "tool_id": tool_id,
        "history_id": history_id,
        "inputs": legacy_inputs,
    }


def build_tool_execution_plan(
    client,
    tool_id,
    history_id,
    inputs=None,
    execution_backend="auto",
):
    """Build the exact endpoint and body that ``tool run`` will submit."""
    if execution_backend not in {"auto", "strict", "legacy"}:
        _validation_error(
            f"Invalid execution backend: {execution_backend!r}.",
            path="$.execution_backend",
            expected="execution backend",
            allowed_values=["auto", "strict", "legacy"],
            example="auto",
        )
    tool_info, legacy_inputs, strict_inputs = _prepare_tool_inputs(
        client, tool_id, inputs
    )
    exact_tool_id = tool_info.get("id") or tool_id
    tool_version = tool_info.get("version", "")
    strict_body = {
        "tool_id": exact_tool_id,
        "tool_version": tool_version,
        "history_id": history_id,
        "inputs": strict_inputs,
        "strict": True,
        "send_email_notification": False,
    }
    legacy_body = {
        "tool_id": tool_id,
        "history_id": history_id,
        "inputs": legacy_inputs,
    }
    selected = "legacy" if execution_backend == "legacy" else "strict"
    plan = {
        "requested_execution_backend": execution_backend,
        "execution_backend": selected,
        "endpoint": "/api/tools" if selected == "legacy" else "/api/jobs",
        "post_body": legacy_body if selected == "legacy" else strict_body,
        "tool_id": exact_tool_id,
        "requested_tool_id": tool_id,
        "tool_version": tool_version,
        "history_id": history_id,
    }
    if execution_backend == "auto":
        plan["_legacy_fallback"] = {
            "execution_backend": "legacy",
            "endpoint": "/api/tools",
            "post_body": legacy_body,
        }
    return plan


def execution_plan_for_output(plan):
    """Return the compact, exact dry-run view of an execution plan."""
    return {
        key: copy.deepcopy(plan[key])
        for key in (
            "requested_execution_backend",
            "execution_backend",
            "endpoint",
            "post_body",
        )
    }


def _make_backend_error(message, **kwargs):
    """Construct a rich v1.5 error while tolerating a pre-v1.5 error class."""
    try:
        return GalaxyBackendError(message, **kwargs)
    except TypeError:
        legacy_keys = {key: kwargs[key] for key in ("category", "exit_code", "suggestion") if key in kwargs}
        error = GalaxyBackendError(message, **legacy_keys)
        for key, value in kwargs.items():
            setattr(error, key, value)
        return error


def _enrich_backend_error(
    error,
    *,
    history_id,
    tool_id,
    request_ids=None,
    job_ids=None,
    jobs=None,
    output_ids=None,
    submission_state=None,
    retry_safe=None,
):
    details = dict(getattr(error, "details", None) or {})
    details.setdefault("history_id", history_id)
    details.setdefault("tool_id", tool_id)

    def merge_ids(key, values):
        if not values:
            return
        details[key] = list(
            dict.fromkeys([*(details.get(key) or []), *values])
        )

    if request_ids:
        merge_ids("request_ids", request_ids)
    if job_ids:
        merge_ids("job_ids", job_ids)
    if jobs:
        details["jobs"] = _compact_jobs(jobs)
    if output_ids:
        merge_ids("output_ids", output_ids)
    error.details = details
    if submission_state is not None:
        error.submission_state = submission_state
    if retry_safe is not None:
        error.retry_safe = retry_safe
    if not getattr(error, "error_kind", None):
        error.error_kind = getattr(error, "category", "unknown")
    return error


def _strict_endpoint_unsupported(error):
    """Return true only for an explicit missing POST /api/jobs endpoint."""
    status = getattr(error, "status_code", None)
    if status == 405:
        return True
    if status != 404:
        return False
    details = getattr(error, "details", None) or {}
    if details.get("endpoint_unsupported") is True:
        return True
    if getattr(error, "error_kind", None) == "endpoint_unsupported":
        return True
    message = str(error).lower()
    if any(
        marker in message
        for marker in (
            "endpoint unsupported",
            "method not allowed",
            "no route",
            "route not found",
        )
    ):
        return True
    specific_resource = (
        ("tool" in message or "history" in message)
        and ("not found" in message or "no such" in message or "unknown" in message)
    )
    return not specific_resource and (
        message.strip() in {"not found", "404 not found"}
        or message.rstrip().endswith(": not found")
    )


def _compact_jobs(jobs):
    return [
        {
            "id": job.get("id", ""),
            "state": job.get("state", ""),
            "exit_code": job.get("exit_code"),
        }
        for job in jobs or []
        if isinstance(job, dict) and job.get("id")
    ]


def _normalize_output_ref(output, *, collection=False, output_name=None):
    if not isinstance(output, dict) or not output.get("id"):
        return None
    is_collection = collection or output.get("src") == "hdca" or output.get(
        "history_content_type"
    ) == "dataset_collection"
    item = {
        "output_name": output_name or output.get("output_name") or output.get("name", ""),
        "id": output.get("id", ""),
        "src": "hdca" if is_collection else output.get("src", "hda"),
        "name": output.get("name", ""),
        "state": output.get("populated_state", output.get("state", "")),
        "history_content_type": "dataset_collection" if is_collection else "dataset",
    }
    if is_collection:
        item.update(
            {
                "collection_type": output.get("collection_type", ""),
                "element_count": output.get("element_count", 0),
            }
        )
    else:
        item.update(
            {
                "extension": output.get("extension", ""),
                "file_size": output.get("file_size", 0),
            }
        )
    return item


def _deduplicate_outputs(outputs):
    deduplicated = []
    seen = set()
    for output in outputs:
        if not output:
            continue
        identity = (output.get("src"), output.get("id"), output.get("output_name"))
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(output)
    return deduplicated


def _legacy_outputs(response):
    outputs = [
        _normalize_output_ref(output)
        for output in response.get("outputs", []) or []
    ]
    for key in ("output_collections", "implicit_collections"):
        outputs.extend(
            _normalize_output_ref(output, collection=True)
            for output in response.get(key, []) or []
        )
    return _deduplicate_outputs(outputs)


def _legacy_outputs_compat(response):
    """Preserve the 1.4 non-blocking output shape for direct callers."""
    outputs = [
        {
            "id": output.get("id", ""),
            "name": output.get("name", ""),
            "extension": output.get("extension", ""),
            "history_content_type": output.get(
                "history_content_type", "dataset"
            ),
        }
        for output in response.get("outputs", []) or []
        if isinstance(output, dict)
    ]
    for key in ("output_collections", "implicit_collections"):
        outputs.extend(
            {
                "id": output.get("id", ""),
                "name": output.get("name", ""),
                "extension": "",
                "collection_type": output.get("collection_type", ""),
                "history_content_type": output.get(
                    "history_content_type", "dataset_collection"
                ),
            }
            for output in response.get(key, []) or []
            if isinstance(output, dict)
        )
    return outputs


def _job_outputs(job_details):
    outputs = []
    for job in job_details:
        for output_name, output in (job.get("outputs", {}) or {}).items():
            values = output if isinstance(output, list) else [output]
            outputs.extend(
                _normalize_output_ref(item, output_name=output_name)
                for item in values
            )
        for output_name, output in (job.get("output_collections", {}) or {}).items():
            values = output if isinstance(output, list) else [output]
            outputs.extend(
                _normalize_output_ref(item, collection=True, output_name=output_name)
                for item in values
            )
    return _deduplicate_outputs(outputs)


def _job_details_before_deadline(client, job_ids, deadline):
    details = []
    for job_id in job_ids:
        if time.monotonic() >= deadline:
            break
        try:
            job = get_with_deadline(
                client, f"jobs/{job_id}", deadline=deadline
            )
        except GalaxyBackendError:
            continue
        if isinstance(job, dict):
            details.append(job)
    return details


def _tool_request_state(value):
    if isinstance(value, dict):
        return str(value.get("state", "unknown"))
    return str(value)


def _redacted_message(client, value, limit=300):
    redact = getattr(client, "redact", str)
    redacted = redact(value) if callable(redact) else str(value)
    if not isinstance(redacted, str):
        redacted = str(value)
    return redacted[:limit]


def _tool_request_failure_message(client, detail):
    state_message = detail.get("state_message") if isinstance(detail, dict) else None
    if isinstance(state_message, dict):
        return _redacted_message(
            client, state_message.get("err_msg") or "tool request failed to expand"
        )
    if state_message:
        return _redacted_message(client, state_message)
    return "tool request failed to expand"


def _legacy_error_message(client, errors):
    first = errors[0] if isinstance(errors, list) and errors else errors
    if isinstance(first, dict):
        first = first.get("err_msg") or first.get("detail") or first.get("message") or "request rejected"
    return _redacted_message(client, first or "request rejected")


def _post_failure_state(error):
    status = getattr(error, "status_code", None)
    if status in {400, 404, 405, 422}:
        return "not_submitted", True
    if status in {401, 403}:
        return "not_submitted", False
    return "unknown", False


def _poll_tool_request(client, request_id, deadline, poll_interval):
    while True:
        state = _tool_request_state(
            get_with_deadline(
                client,
                f"tool_requests/{request_id}/state",
                deadline=deadline,
            )
        )
        if state in {"submitted", "failed"}:
            return state
        if state != "new":
            raise _make_backend_error(
                f"Unexpected tool request state for {request_id}: {state}.",
                category="api_error",
                error_kind="unexpected_response",
                exit_code=EXIT_SERVER_ERROR,
                submission_state="submitted",
                retry_safe=False,
                details={"request_ids": [request_id], "request_state": state},
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _make_backend_error(
                f"Timed out waiting for tool request {request_id} to submit.",
                category="timeout",
                error_kind="timeout",
                exit_code=EXIT_TIMEOUT,
                submission_state="submitted",
                retry_safe=False,
                details={"request_ids": [request_id], "request_state": state},
            )
        time.sleep(min(max(0, poll_interval), remaining))


def _base_run_result(plan, backend, state):
    result = {
        "success": True,
        "state": state,
        "execution_backend": backend,
        "history_id": plan.get("history_id", plan.get("post_body", {}).get("history_id", "")),
        "tool_id": plan.get("tool_id", plan.get("post_body", {}).get("tool_id", "")),
        "tool_version": plan.get("tool_version", plan.get("post_body", {}).get("tool_version", "")),
        "jobs": [],
        "outputs": [],
    }
    requested_tool_id = plan.get("requested_tool_id")
    if requested_tool_id and requested_tool_id != result["tool_id"]:
        result["requested_tool_id"] = requested_tool_id
    return result


def _add_wait_results(result, wait_results):
    result["wait_results"] = wait_results
    result["jobs"] = _compact_jobs(wait_results)
    if len(wait_results) == 1:
        result["wait_result"] = wait_results[0]


def _run_legacy(client, plan, wait, timeout, poll_interval):
    body = plan["post_body"]
    resolved_tool_id = plan.get("tool_id", body.get("tool_id", ""))
    response = client.post("tools", json_data=body)
    if not isinstance(response, dict):
        raise _make_backend_error(
            "Galaxy returned an invalid legacy tool response.",
            category="api_error",
            error_kind="unexpected_response",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="unknown",
            retry_safe=False,
        )
    jobs = _compact_jobs(response.get("jobs", []))
    outputs = _legacy_outputs(response)
    job_ids = [job["id"] for job in jobs]
    output_ids = [output["id"] for output in outputs]
    if response.get("errors"):
        server_errors = response.get("errors")
        has_known_side_effects = bool(job_ids or output_ids)
        if wait and job_ids:
            try:
                jobs = job_mod.wait_for_jobs(
                    client,
                    job_ids,
                    timeout=timeout,
                    poll_interval=poll_interval,
                    history_id=body.get("history_id", ""),
                    tool_id=resolved_tool_id,
                    output_ids=output_ids,
                )
            except GalaxyBackendError as wait_error:
                raise _enrich_backend_error(
                    wait_error,
                    history_id=body.get("history_id", ""),
                    tool_id=resolved_tool_id,
                    job_ids=job_ids,
                    output_ids=output_ids,
                    submission_state="submitted",
                    retry_safe=False,
                )
        error = _make_backend_error(
            f"Galaxy rejected the legacy tool request: {_legacy_error_message(client, server_errors)}.",
            category="tool_request_rejected",
            error_kind="tool_request_rejected",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="submitted" if has_known_side_effects else "not_submitted",
            retry_safe=not has_known_side_effects,
            details={
                "server_error_count": len(server_errors)
                if isinstance(server_errors, list)
                else 1
            },
        )
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", ""),
            tool_id=resolved_tool_id,
            job_ids=job_ids,
            jobs=jobs,
            output_ids=output_ids,
        )
    result = _base_run_result(plan, "legacy", "submitted")
    result["jobs"] = jobs
    result["outputs"] = outputs
    if not wait:
        result["outputs"] = _legacy_outputs_compat(response)
        return result
    if not job_ids:
        raise _make_backend_error(
            "Galaxy returned no jobs for a blocking legacy tool request.",
            category="api_error",
            error_kind="unexpected_response",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="unknown",
            retry_safe=False,
            details={
                "history_id": body.get("history_id", ""),
                "tool_id": resolved_tool_id,
                "job_ids": [],
                "output_ids": output_ids,
            },
        )
    try:
        wait_results = job_mod.wait_for_jobs(
            client,
            job_ids,
            timeout=timeout,
            poll_interval=poll_interval,
            history_id=body.get("history_id", ""),
            tool_id=resolved_tool_id,
            output_ids=output_ids,
        )
    except GalaxyBackendError as error:
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", ""),
            tool_id=resolved_tool_id,
            job_ids=job_ids,
            output_ids=output_ids,
            submission_state="submitted",
            retry_safe=False,
        )
    _add_wait_results(result, wait_results)
    try:
        result["outputs"] = refresh_output_details(
            client, body.get("history_id", ""), outputs
        )
    except GalaxyBackendError as error:
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", ""),
            tool_id=resolved_tool_id,
            job_ids=job_ids,
            jobs=wait_results,
            output_ids=output_ids,
            submission_state="submitted",
            retry_safe=False,
        )
    result["state"] = "ok"
    return result


def _run_strict_after_submit(client, plan, response, wait, timeout, poll_interval):
    body = plan["post_body"]
    if not isinstance(response, dict) or not response.get("tool_request_id"):
        raise _make_backend_error(
            "Galaxy accepted the strict request but did not return tool_request_id.",
            category="api_error",
            error_kind="unknown_submission_state",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="unknown",
            retry_safe=False,
            details={"history_id": body.get("history_id", ""), "tool_id": body.get("tool_id", "")},
        )
    request_id = response["tool_request_id"]
    result = _base_run_result(plan, "strict", "submitted")
    result["tool_request_id"] = request_id
    if not wait:
        return result

    deadline = time.monotonic() + max(0, timeout)
    try:
        state = _poll_tool_request(client, request_id, deadline, poll_interval)
        detail = get_with_deadline(
            client, f"tool_requests/{request_id}", deadline=deadline
        )
    except GalaxyBackendError as error:
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", ""),
            tool_id=body.get("tool_id", ""),
            request_ids=[request_id],
            submission_state="submitted",
            retry_safe=False,
        )
    if not isinstance(detail, dict):
        raise _make_backend_error(
            "Galaxy returned an invalid tool request detail.",
            category="api_error",
            error_kind="unexpected_response",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="submitted",
            retry_safe=False,
            details={
                "history_id": body.get("history_id", ""),
                "tool_id": body.get("tool_id", ""),
                "request_ids": [request_id],
            },
        )
    job_ids = [
        ref.get("id")
        for ref in detail.get("jobs", []) or []
        if isinstance(ref, dict) and ref.get("id")
    ]
    implicit_outputs = _deduplicate_outputs(
        [
            _normalize_output_ref(
                ref,
                collection=True,
                output_name=ref.get("output_name", ""),
            )
            for ref in detail.get("implicit_collections", []) or []
            if isinstance(ref, dict)
        ]
    )
    implicit_ids = [output["id"] for output in implicit_outputs]
    if state == "failed":
        failure_message = _tool_request_failure_message(client, detail)
        known_jobs = []
        if job_ids:
            try:
                known_jobs = job_mod.wait_for_jobs(
                    client,
                    job_ids,
                    timeout=max(0, deadline - time.monotonic()),
                    poll_interval=poll_interval,
                    history_id=body.get("history_id", ""),
                    tool_id=body.get("tool_id", ""),
                    request_ids=[request_id],
                    output_ids=implicit_ids,
                )
            except GalaxyBackendError as wait_error:
                known_job_details = (
                    []
                    if wait_error.exit_code == EXIT_TIMEOUT
                    else _job_details_before_deadline(client, job_ids, deadline)
                )
                known_outputs = _deduplicate_outputs(
                    _job_outputs(known_job_details) + implicit_outputs
                )
                raise _enrich_backend_error(
                    wait_error,
                    history_id=body.get("history_id", ""),
                    tool_id=body.get("tool_id", ""),
                    request_ids=[request_id],
                    job_ids=job_ids,
                    output_ids=[output["id"] for output in known_outputs],
                    submission_state="submitted",
                    retry_safe=False,
                )
        known_job_details = _job_details_before_deadline(
            client, job_ids, deadline
        )
        known_output_ids = [
            output["id"]
            for output in _deduplicate_outputs(
                _job_outputs(known_job_details) + implicit_outputs
            )
        ]
        raise _make_backend_error(
            f"Tool request failed: {failure_message}.",
            category="tool_request_rejected",
            error_kind="tool_request_rejected",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="submitted",
            retry_safe=False,
            details={
                "history_id": body.get("history_id", ""),
                "tool_id": body.get("tool_id", ""),
                "request_ids": [request_id],
                "job_ids": job_ids,
                "jobs": _compact_jobs(known_jobs),
                "output_ids": known_output_ids,
            },
        )
    if not job_ids:
        raise _make_backend_error(
            "Galaxy tool request reached submitted state without any spawned jobs.",
            category="api_error",
            error_kind="unexpected_response",
            exit_code=EXIT_SERVER_ERROR,
            submission_state="submitted",
            retry_safe=False,
            details={
                "history_id": body.get("history_id", ""),
                "tool_id": body.get("tool_id", ""),
                "request_ids": [request_id],
                "job_ids": [],
                "output_ids": implicit_ids,
            },
        )
    remaining = max(0, deadline - time.monotonic())
    try:
        wait_results = job_mod.wait_for_jobs(
            client,
            job_ids,
            timeout=remaining,
            poll_interval=poll_interval,
            history_id=body.get("history_id", ""),
            tool_id=body.get("tool_id", ""),
            request_ids=[request_id],
            output_ids=implicit_ids,
        )
    except GalaxyBackendError as error:
        known_outputs = list(implicit_outputs)
        known_job_details = (
            []
            if error.exit_code == EXIT_TIMEOUT
            else _job_details_before_deadline(client, job_ids, deadline)
        )
        known_outputs = _deduplicate_outputs(
            _job_outputs(known_job_details) + known_outputs
        )
        known_output_ids = [output["id"] for output in known_outputs]
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", ""),
            tool_id=body.get("tool_id", ""),
            request_ids=[request_id],
            job_ids=job_ids,
            output_ids=known_output_ids,
            submission_state="submitted",
            retry_safe=False,
        )
    try:
        job_details = [client.get(f"jobs/{job_id}") for job_id in job_ids]
    except GalaxyBackendError as error:
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", ""),
            tool_id=body.get("tool_id", ""),
            request_ids=[request_id],
            job_ids=job_ids,
            jobs=wait_results,
            output_ids=implicit_ids,
            submission_state="submitted",
            retry_safe=False,
        )
    outputs = _deduplicate_outputs(_job_outputs(job_details) + implicit_outputs)
    _add_wait_results(result, wait_results)
    output_ids = [output["id"] for output in outputs]
    try:
        result["outputs"] = refresh_output_details(
            client, body.get("history_id", ""), outputs
        )
    except GalaxyBackendError as error:
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", ""),
            tool_id=body.get("tool_id", ""),
            request_ids=[request_id],
            job_ids=job_ids,
            jobs=wait_results,
            output_ids=output_ids,
            submission_state="submitted",
            retry_safe=False,
        )
    result["state"] = "ok"
    return result


def run_tool(
    client,
    tool_id,
    history_id,
    inputs=None,
    payload=None,
    execution_backend="auto",
    wait=False,
    timeout=1800,
    poll_interval=180,
    plan=None,
):
    """Run a tool through strict, auto-fallback, or legacy execution.

    ``payload`` remains a compatibility alias for a pre-built legacy payload.
    New callers should pass ``plan=build_tool_execution_plan(...)`` when they
    need the submitted body to exactly match a prior dry run.
    """
    timeout = float(timeout)
    poll_interval = float(poll_interval)
    if plan is None:
        if payload is not None:
            plan = {
                "requested_execution_backend": "legacy",
                "execution_backend": "legacy",
                "endpoint": "/api/tools",
                "post_body": payload,
                "tool_id": tool_id,
                "tool_version": payload.get("tool_version", ""),
                "history_id": history_id,
            }
        else:
            plan = build_tool_execution_plan(
                client,
                tool_id,
                history_id,
                inputs=inputs,
                execution_backend=execution_backend,
            )
    backend = plan.get("execution_backend")
    if backend == "legacy":
        try:
            return _run_legacy(client, plan, wait, timeout, poll_interval)
        except GalaxyBackendError as error:
            body = plan.get("post_body", {})
            submission_state = getattr(error, "submission_state", None)
            retry_safe = getattr(error, "retry_safe", None)
            if submission_state is None or retry_safe is None:
                submission_state, retry_safe = _post_failure_state(error)
            raise _enrich_backend_error(
                error,
                history_id=body.get("history_id", history_id),
                tool_id=plan.get("tool_id", body.get("tool_id", tool_id)),
                submission_state=submission_state,
                retry_safe=retry_safe,
            )
    if backend != "strict":
        _validation_error(
            f"Invalid execution plan backend: {backend!r}.",
            path="$.execution_backend",
            expected="execution backend",
            allowed_values=["strict", "legacy"],
            example="strict",
        )

    body = plan["post_body"]
    try:
        response = client.post("jobs", json_data=body)
    except GalaxyBackendError as error:
        if (
            plan.get("requested_execution_backend") == "auto"
            and _strict_endpoint_unsupported(error)
            and plan.get("_legacy_fallback")
        ):
            fallback = plan["_legacy_fallback"]
            fallback_plan = dict(plan)
            fallback_plan.update(fallback)
            fallback_plan.pop("_legacy_fallback", None)
            try:
                return _run_legacy(
                    client, fallback_plan, wait, timeout, poll_interval
                )
            except GalaxyBackendError as fallback_error:
                fallback_state = getattr(fallback_error, "submission_state", None)
                fallback_retry = getattr(fallback_error, "retry_safe", None)
                if fallback_state is None or fallback_retry is None:
                    fallback_state, fallback_retry = _post_failure_state(fallback_error)
                raise _enrich_backend_error(
                    fallback_error,
                    history_id=body.get("history_id", history_id),
                    tool_id=body.get("tool_id", tool_id),
                    submission_state=fallback_state,
                    retry_safe=fallback_retry,
                )
        submission_state, retry_safe = _post_failure_state(error)
        raise _enrich_backend_error(
            error,
            history_id=body.get("history_id", history_id),
            tool_id=body.get("tool_id", tool_id),
            submission_state=submission_state,
            retry_safe=retry_safe,
        )
    return _run_strict_after_submit(
        client,
        plan,
        response,
        wait,
        timeout,
        poll_interval,
    )
def refresh_output_details(client, history_id, outputs, require_complete=True):
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
            if require_complete:
                raise _make_backend_error(
                    "Galaxy returned a tool output without an id.",
                    category="api_error",
                    error_kind="unexpected_response",
                    exit_code=EXIT_SERVER_ERROR,
                    submission_state="submitted",
                    retry_safe=False,
                    details={"history_id": history_id, "output_ids": []},
                )
            refreshed.append(item)
            continue
        history_content_type = item.get("history_content_type", "dataset")
        if item.get("src") == "hdca":
            history_content_type = "dataset_collection"
        if history_content_type == "dataset_collection":
            info = client.get(f"histories/{history_id}/contents/dataset_collections/{content_id}")
            if not isinstance(info, dict):
                if require_complete:
                    raise _make_backend_error(
                        f"Galaxy returned invalid metadata for collection output {content_id}.",
                        category="api_error",
                        error_kind="unexpected_response",
                        exit_code=EXIT_SERVER_ERROR,
                        submission_state="submitted",
                        retry_safe=False,
                        details={"history_id": history_id, "output_ids": [content_id]},
                    )
                refreshed.append(item)
                continue
            item.update({
                "id": info.get("id", content_id),
                "src": "hdca",
                "output_name": item.get("output_name", item.get("name", "")),
                "name": info.get("name", item.get("name", "")),
                "state": info.get("populated_state", info.get("state", "")),
                "history_content_type": info.get("history_content_type", history_content_type),
                "collection_type": info.get("collection_type", item.get("collection_type", "")),
                "element_count": info.get(
                    "element_count",
                    len(info["elements"])
                    if isinstance(info.get("elements"), list)
                    else 0,
                ),
                "populated": info.get("populated", False),
                "elements_datatypes": info.get("elements_datatypes", []),
            })
            refreshed.append(item)
            continue

        info = client.get(f"histories/{history_id}/contents/{content_id}")
        if not isinstance(info, dict):
            if require_complete:
                raise _make_backend_error(
                    f"Galaxy returned invalid metadata for dataset output {content_id}.",
                    category="api_error",
                    error_kind="unexpected_response",
                    exit_code=EXIT_SERVER_ERROR,
                    submission_state="submitted",
                    retry_safe=False,
                    details={"history_id": history_id, "output_ids": [content_id]},
                )
            refreshed.append(item)
            continue
        item.update({
            "id": info.get("id", content_id),
            "src": info.get("src", item.get("src", "hda")),
            "output_name": item.get("output_name", item.get("name", "")),
            "name": info.get("name", item.get("name", "")),
            "state": info.get("state", ""),
            "extension": info.get("extension", item.get("extension", "")),
            "file_size": info.get("file_size", 0),
            "genome_build": info.get("genome_build", "?"),
            "data_type": info.get("data_type", ""),
            "visible": info.get("visible", True),
            "history_content_type": info.get("history_content_type", history_content_type),
            "misc_blurb": info.get("misc_blurb", ""),
        })
        refreshed.append(item)
    return refreshed
