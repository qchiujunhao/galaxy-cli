"""Shared loading for CLI options that accept a JSON object."""

import json
import sys
from pathlib import Path

from galaxy_cli.utils.galaxy_backend import (
    EXIT_USER_ERROR,
    GalaxyBackendError,
)


def _input_error(message, *, reason, received_type=None, correction=None):
    details = {
        "path": "$",
        "expected": "JSON object",
        "reason": reason,
    }
    if received_type is not None:
        details["received_type"] = received_type
    if correction is not None:
        details["correction"] = correction
    raise GalaxyBackendError(
        message,
        category="invalid_request",
        error_kind="invalid_input",
        exit_code=EXIT_USER_ERROR,
        submission_state="not_submitted",
        retry_safe=True,
        details=details,
    )


def _json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def load_json_object(
    value=None,
    legacy_path=None,
    *,
    option_name="--inputs",
    legacy_option_name="--inputs-json",
    required=True,
    stdin=None,
):
    """Load one JSON object from inline text, ``@PATH``, stdin, or a legacy file."""
    if value is not None and legacy_path is not None:
        _input_error(
            f"Use either {option_name} or {legacy_option_name}, not both.",
            reason="conflicting_options",
            correction={
                "use_one_of": [
                    f"{option_name} @inputs.json",
                    f"{legacy_option_name} inputs.json",
                ]
            },
        )

    if value is None and legacy_path is None:
        if not required:
            return {}
        _input_error(
            f"Missing JSON input. Provide {option_name} or {legacy_option_name}.",
            reason="missing_input",
            received_type="missing",
            correction={"command_fragment": f"{option_name} @inputs.json"},
        )

    if legacy_path is not None:
        source = Path(legacy_path)
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            _input_error(
                f"Failed to read {legacy_option_name} file {str(source)!r}: {exc}",
                reason="unreadable_file",
                received_type="file",
                correction={"command_fragment": f"{legacy_option_name} inputs.json"},
            )
    elif value == "-":
        stream = stdin if stdin is not None else sys.stdin
        try:
            text = stream.read()
        except (OSError, UnicodeError) as exc:
            _input_error(
                f"Failed to read JSON from stdin: {exc}",
                reason="unreadable_stdin",
                received_type="stdin",
                correction={"command_fragment": f"{option_name} -"},
            )
    elif value.startswith("@"):
        raw_path = value[1:]
        if not raw_path:
            _input_error(
                f"{option_name} @PATH requires a file path after '@'.",
                reason="missing_file_path",
                received_type="file",
                correction={"command_fragment": f"{option_name} @inputs.json"},
            )
        source = Path(raw_path)
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            _input_error(
                f"Failed to read {option_name} file {str(source)!r}: {exc}",
                reason="unreadable_file",
                received_type="file",
                correction={"command_fragment": f"{option_name} @inputs.json"},
            )
    else:
        text = value

    if not text or not text.strip():
        _input_error(
            "JSON input is empty.",
            reason="empty_input",
            received_type="empty",
            correction={"value": {}},
        )

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        _input_error(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}.",
            reason="invalid_json",
            received_type="invalid_json",
            correction={"value": {}},
        )

    if not isinstance(loaded, dict):
        _input_error(
            "JSON input must contain an object at the top level.",
            reason="non_object",
            received_type=_json_type(loaded),
            correction={"value": {}},
        )
    return loaded
