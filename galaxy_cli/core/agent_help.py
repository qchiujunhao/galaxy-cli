"""Bounded, centralized command metadata for agent-oriented help."""

from copy import deepcopy
from difflib import get_close_matches


GROUP_ALIASES = {
    "tool": {"find": "search", "inputs": "template", "schema": "template"},
    "dataset": {"preview": "peek", "head": "peek"},
    "history": {"ls": "list", "find": "resolve"},
    "collection": {"get": "resolve"},
    "job": {"debug": "diagnose"},
}

COMMAND_ALIASES = {
    f"{group}.{alias}": f"{group}.{canonical}"
    for group, aliases in GROUP_ALIASES.items()
    for alias, canonical in aliases.items()
}


RECEIPT_ID_RESULT = {
    "operation_receipt": {
        "type": "string",
        "format": "receipt_id",
        "description": "Pass this value directly to operation show or operation resume.",
    },
}


COMMAND_HELP = {
    "tool.run": {
        "usage": "galaxy-cli tool run TOOL_ID --history HID --inputs @inputs.json",
        "required": ["tool_id", "history", "inputs"],
        "defaults": {
            "wait": True,
            "execution_backend": "auto",
            "timeout": 1800,
            "polling": "adaptive:5,10,20,30",
        },
        "input_examples": {
            "dataset": {"src": "hda", "id": "DATASET_ID"},
            "collection": {"src": "hdca", "id": "COLLECTION_ID"},
        },
        "result_fields": RECEIPT_ID_RESULT,
        "safety": [
            "do not retry when submission_state is unknown",
            "trust a successful blocking result",
        ],
    },
    "tool.search": {
        "usage": "galaxy-cli tool search QUERY",
        "required": ["query"],
        "defaults": {"limit": 25, "cache": True, "resolve": False},
        "safety": ["search does not execute a tool"],
    },
    "tool.template": {
        "usage": "galaxy-cli tool template TOOL_ID",
        "required": ["tool_id"],
        "defaults": {"cache": True},
        "safety": ["use only when the input contract is unknown"],
    },
    "history.copy": {
        "usage": "galaxy-cli history copy HISTORY_ID --name NAME",
        "required": ["history_id"],
        "defaults": {"wait": True, "timeout": 1800, "poll_interval": 5},
        "result_fields": RECEIPT_ID_RESULT,
        "safety": ["trust the successful blocking readiness result"],
    },
    "history.list": {
        "usage": "galaxy-cli history list [--deleted] [--limit N]",
        "required": [],
        "defaults": {"deleted": False, "limit": 50},
        "safety": ["listing histories is read-only"],
    },
    "history.resolve": {
        "usage": "galaxy-cli history resolve HISTORY_ID --exact-name NAME",
        "required": ["history_id", "one_filter"],
        "defaults": {"limit": 50},
        "safety": ["resolution must identify exactly one content item"],
    },
    "dataset.upload": {
        "usage": "galaxy-cli dataset upload FILE --history HID",
        "required": ["file", "history"],
        "defaults": {
            "wait": True,
            "upload_backend": "auto",
            "file_type": "auto",
            "polling": "adaptive:5,10,20,30",
        },
        "result_fields": RECEIPT_ID_RESULT,
        "safety": ["do not retry an unknown submission"],
    },
    "dataset.peek": {
        "usage": "galaxy-cli dataset peek DATASET_ID --lines 5",
        "required": ["dataset_id"],
        "defaults": {
            "lines": 10,
            "max_chars_per_line": 500,
            "max_fields": 20,
            "max_scan_bytes": 5242880,
        },
        "safety": ["preview is bounded; use download only for an explicit local artifact"],
    },
    "collection.resolve": {
        "usage": "galaxy-cli collection resolve COLLECTION_ID --element PATH",
        "required": ["collection_id", "element"],
        "defaults": {"max_depth": 8, "max_results": 25},
        "safety": ["resolution must identify exactly one collection element"],
    },
    "udt.create-run": {
        "usage": "galaxy-cli udt create-run --representation-json udt.json --history HID --inputs @inputs.json",
        "required": ["representation_json", "history", "inputs"],
        "defaults": {
            "wait": True,
            "timeout": 1800,
            "polling": "adaptive:5,10,20,30",
        },
        "result_fields": RECEIPT_ID_RESULT,
        "safety": ["do not retry an unknown submission; resume its receipt"],
    },
    "workflow.run": {
        "usage": "galaxy-cli workflow run WORKFLOW_ID --history HID -i STEP=hda:DATASET_ID",
        "required": ["workflow_id", "history_or_new_history", "inputs"],
        "defaults": {
            "wait": True,
            "timeout": 1800,
            "polling": "adaptive:5,10,20,30",
        },
        "result_fields": RECEIPT_ID_RESULT,
        "safety": ["trust the successful blocking result"],
    },
    "job.diagnose": {
        "usage": "galaxy-cli job diagnose JOB_ID",
        "required": ["job_id"],
        "defaults": {"max_chars": 12000},
        "safety": ["diagnostics are bounded; request full logs explicitly"],
    },
    "operation.resume": {
        "usage": "galaxy-cli operation resume RECEIPT_ID",
        "required": ["receipt_id"],
        "defaults": {"timeout": 1800, "polling": "adaptive:5,10,20,30"},
        "result_fields": {
            "id": {
                "type": "string",
                "format": "receipt_id",
                "description": "The resumed command returns the full receipt object in data.",
            },
        },
        "safety": ["resume polls known records and never replays an unknown POST"],
    },
    "operation.show": {
        "usage": "galaxy-cli operation show RECEIPT_ID",
        "required": ["receipt_id"],
        "defaults": {},
        "result_fields": {
            "id": {
                "type": "string",
                "format": "receipt_id",
                "description": "This command returns the full receipt object in data.",
            },
        },
        "safety": ["show is read-only"],
    },
}


def _normalize(command):
    command = str(command or "").strip().lower().replace("/", ".")
    command = ".".join(
        part for part in command.replace(" ", ".").split(".") if part
    )
    return command


def canonical_command(command):
    """Return the canonical dotted command name for a canonical name or alias."""
    normalized = _normalize(command)
    return COMMAND_ALIASES.get(normalized, normalized)


def command_help(command):
    """Return one bounded help record with a stable top-level schema."""
    canonical = canonical_command(command)
    if canonical not in COMMAND_HELP:
        raise KeyError(canonical)
    result = {
        "schema_version": "1.0",
        "command": canonical,
        **deepcopy(COMMAND_HELP[canonical]),
    }
    aliases = sorted(
        alias for alias, target in COMMAND_ALIASES.items() if target == canonical
    )
    if aliases:
        result["aliases"] = aliases
    return result


def suggest_commands(command, limit=3):
    """Return bounded canonical suggestions without executing a guessed command."""
    normalized = _normalize(command)
    candidates = sorted(set(COMMAND_HELP) | set(COMMAND_ALIASES))
    suggestions = []
    for match in get_close_matches(normalized, candidates, n=limit, cutoff=0.55):
        canonical = canonical_command(match)
        if canonical not in suggestions:
            suggestions.append(canonical)
    return suggestions[:limit]
