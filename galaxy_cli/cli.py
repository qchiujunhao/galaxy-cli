"""galaxy-cli: CLI harness for Galaxy bioinformatics platform.

Provides stateful CLI and REPL access to a running Galaxy server via its REST API.
"""

import json
import os
import sys
import shlex

import click
from click.exceptions import Exit

from galaxy_cli import __version__
from galaxy_cli.utils.galaxy_backend import (
    GalaxyClient,
    GalaxyBackendError,
    EXIT_USER_ERROR,
)
from galaxy_cli.core import (
    collection as collection_mod,
    config as config_mod,
    history as history_mod,
    dataset as dataset_mod,
    udt as udt_mod,
    tool as tool_mod,
    job as job_mod,
    workflow as workflow_mod,
    invocation as invocation_mod,
    library as library_mod,
    session as session_mod,
    skill as skill_mod,
    server as server_mod,
    operation as operation_mod,
)

# ── Helpers ──────────────────────────────────────────────────────────────

_ROOT_OPTIONS_WITH_VALUES = {
    "--url",
    "--api-key",
    "--profile",
    "--history-id",
    "--request-timeout",
    "--output-file",
    "--max-items",
    "--max-chars",
}

def _resolve_json_mode(json_mode):
    """Resolve tri-state JSON mode to a boolean."""
    if json_mode is None:
        return True
    return bool(json_mode)


def _json_mode_from_argv(args):
    """Parse the last explicit root-level JSON mode flag from argv."""
    json_mode = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg == "--json":
            json_mode = True
        elif arg == "--human":
            json_mode = False
        elif arg in _ROOT_OPTIONS_WITH_VALUES:
            index += 1
        elif any(arg.startswith(f"{opt}=") for opt in _ROOT_OPTIONS_WITH_VALUES):
            pass
        elif not arg.startswith("-"):
            break
        index += 1
    return json_mode


def _json_mode_enabled():
    """Return whether the current Click context should emit JSON."""
    ctx = click.get_current_context(silent=True)
    while ctx is not None:
        if ctx.obj and "json_mode" in ctx.obj:
            return _resolve_json_mode(ctx.obj["json_mode"])
        ctx = ctx.parent
    return _resolve_json_mode(None)


def _normalize_repl_args(args, default_json_mode):
    """Apply REPL default JSON mode unless the command overrides it."""
    if not default_json_mode and "--json" not in args and "--human" not in args:
        return ["--human"] + args
    return args


def _output(data, human_func=None):
    """Output data as JSON or human-readable.

    JSON mode emits compact single-line JSON to minimize tokens for LLM
    agents that re-read this output on every turn.
    """
    root_obj = _current_root_obj()
    data = _redact_cli_value(data, root_obj)
    output_file = root_obj.get("output_file")
    if output_file:
        _write_json_file(output_file, data)
        summary = {
            "success": data.get("success", True) if isinstance(data, dict) else True,
            "state": data.get("state", "complete") if isinstance(data, dict) else "complete",
            "output_file": output_file,
            "bytes": os.path.getsize(output_file),
            "truncated": True,
        }
        if isinstance(data, dict):
            for key in ("id", "history_id", "tool_id", "workflow_id"):
                if data.get(key):
                    summary[key] = data[key]
        click.echo(json.dumps(summary, separators=(",", ":"), default=str))
        return
    data, truncated = _limit_output(
        data, root_obj.get("max_items"), root_obj.get("max_chars")
    )
    if truncated and isinstance(data, dict):
        data["truncated"] = True
    if _json_mode_enabled():
        click.echo(json.dumps(data, separators=(",", ":"), default=str))
    elif human_func:
        human_func(data)
    else:
        click.echo(json.dumps(data, separators=(",", ":"), default=str))


def _limit_output(value, max_items=None, max_chars=None):
    """Apply simple recursive bounds without an extra query-language dependency."""
    truncated = False
    if isinstance(value, dict):
        result = {}
        items = list(value.items())
        if max_items is not None and len(items) > max_items:
            items = items[:max_items]
            truncated = True
        for key, item in items:
            result[key], child_truncated = _limit_output(item, max_items, max_chars)
            truncated = truncated or child_truncated
        return result, truncated
    if isinstance(value, (list, tuple)):
        items = list(value)
        if max_items is not None and len(items) > max_items:
            items = items[:max_items]
            truncated = True
        result = []
        for item in items:
            bounded, child_truncated = _limit_output(item, max_items, max_chars)
            result.append(bounded)
            truncated = truncated or child_truncated
        return result, truncated
    if isinstance(value, str) and max_chars is not None and len(value) > max_chars:
        return value[:max_chars], True
    return value, False


def _compact_json(data):
    return json.dumps(data, separators=(",", ":"), default=str)


def _current_root_obj():
    ctx = click.get_current_context(silent=True)
    while ctx is not None and ctx.parent is not None:
        ctx = ctx.parent
    return ctx.obj if ctx is not None and ctx.obj else {}


def _cli_secrets(root_obj):
    if not root_obj:
        return ()
    client = root_obj.get("client")
    candidates = (root_obj.get("api_key"), getattr(client, "api_key", None))
    return tuple(
        dict.fromkeys(secret for secret in candidates if isinstance(secret, str) and secret)
    )


def _redact_cli_value(value, root_obj):
    if isinstance(value, dict):
        return {
            _redact_cli_value(key, root_obj): _redact_cli_value(item, root_obj)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_cli_value(item, root_obj) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_cli_value(item, root_obj) for item in value)
    if not isinstance(value, str):
        return value
    for secret in _cli_secrets(root_obj):
        value = value.replace(secret, "[REDACTED]")
    return value


def _redact_cli_text(value, root_obj):
    return _redact_cli_value(str(value), root_obj)


def _progress(message):
    """Emit progress text without corrupting JSON stdout."""
    click.echo(_redact_cli_text(message, _current_root_obj()), err=True)


def _operation_receipt(operation_type, payload, result=None, error=None):
    receipt = operation_mod.create_receipt(
        operation_type, payload, result=result, error=error
    )
    if error is not None:
        details = dict(getattr(error, "details", {}) or {})
        details["operation_receipt"] = receipt["id"]
        error.details = details
    elif isinstance(result, dict):
        result["operation_receipt"] = receipt["id"]
    return receipt


def _write_json_file(path, payload):
    try:
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
            fh.write("\n")
    except OSError as exc:
        raise click.UsageError(f"Failed to write JSON file {path!r}: {exc}")


def _mask_email(email):
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _get_client(ctx):
    """Get or create a GalaxyClient from context."""
    if ctx.obj and ctx.obj.get("client"):
        return ctx.obj["client"]
    url = ctx.obj.get("url") if ctx.obj else None
    api_key = ctx.obj.get("api_key") if ctx.obj else None
    profile = ctx.obj.get("profile") if ctx.obj else None
    request_timeout = ctx.obj.get("request_timeout") if ctx.obj else None
    kwargs = {"url": url, "api_key": api_key, "profile": profile}
    if request_timeout is not None:
        kwargs["request_timeout"] = request_timeout
    client = GalaxyClient(**kwargs)
    if ctx.obj is not None:
        ctx.obj["client"] = client
    return client


def _require_history(ctx):
    """Get the current history ID, fail if not set."""
    hid = ctx.obj.get("history_id") if ctx.obj else None
    if not hid:
        sess = session_mod.get_current_history()
        hid = sess.get("current_history_id")
    if not hid:
        raise click.UsageError(
            "No history selected. Use:\n"
            "  galaxy history use <history-id>\n"
            "  or pass --history-id"
        )
    return hid


def _attach_output_peek(client, result, output_name, lines):
    """Attach bounded previews for one explicitly named dataset output."""
    matches = [
        output
        for output in result.get("outputs", [])
        if output.get("output_name") == output_name
        or (not output.get("output_name") and output.get("name") == output_name)
    ]
    if not matches:
        available = sorted(
            {
                output.get("output_name") or output.get("name")
                for output in result.get("outputs", [])
                if output.get("output_name") or output.get("name")
            }
        )
        raise GalaxyBackendError(
            f"Output '{output_name}' was not produced by this tool run.",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
            error_kind="output_not_found",
            submission_state="submitted",
            retry_safe=False,
            details={
                "history_id": result.get("history_id", ""),
                "tool_id": result.get("tool_id", ""),
                "job_ids": [job.get("id", "") for job in result.get("jobs", [])],
                "available_output_names": available,
            },
        )

    collection_matches = [
        output
        for output in matches
        if output.get("src") == "hdca"
        or output.get("history_content_type") == "dataset_collection"
    ]
    if collection_matches:
        result["output_peek"] = [
            {
                "output_name": output_name,
                "id": output.get("id", ""),
                "src": "hdca",
                "supported": False,
                "reason": "Collection output preview is unsupported; no elements were expanded.",
            }
            for output in collection_matches
        ]
        return result

    previews = []
    for output in matches:
        preview = dataset_mod.peek_dataset(
            client,
            output.get("id", ""),
            lines=lines,
            history_id=result.get("history_id") or None,
        )
        previews.append({
            "output_name": output_name,
            "id": output.get("id", ""),
            "src": "hda",
            "supported": True,
            "preview": preview,
        })
    result["output_peek"] = previews
    return result


# ── Main CLI Group ───────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.option("--url", envvar="GALAXY_URL", help="Galaxy server URL")
@click.option("--api-key", envvar="GALAXY_API_KEY", help="Galaxy API key")
@click.option("--profile", default=None,
              help="Use a named profile from ~/.galaxy-cli/config.json for this command")
@click.option("--history-id", default=None, help="Override current history ID")
@click.option(
    "--request-timeout",
    type=click.FloatRange(min=0.001),
    default=None,
    envvar="GALAXY_CLI_REQUEST_TIMEOUT",
    help="Read timeout in seconds for regular Galaxy API requests.",
)
@click.option(
    "--json/--human",
    "json_mode",
    default=None,
    help="JSON output (default) or human-readable output",
)
@click.option(
    "--output-file", type=click.Path(dir_okay=False, writable=True), default=None,
    help="Write the complete redacted JSON result to PATH and print only a summary.",
)
@click.option("--max-items", type=click.IntRange(min=1), default=None, help="Bound items in returned lists and objects.")
@click.option("--max-chars", type=click.IntRange(min=1), default=None, help="Bound characters in returned strings.")
@click.version_option(__version__, prog_name="galaxy-cli")
@click.pass_context
def cli(
    ctx, url, api_key, profile, history_id, request_timeout, json_mode,
    output_file, max_items, max_chars,
):
    """CLI harness for Galaxy bioinformatics platform.

    Connect to a running Galaxy server and manage histories, datasets,
    tools, workflows, and jobs from the command line.

    \b
    Output mode:
      Compact JSON is the default.
      Use --human for human-readable terminal output.

    \b
    Quick start (typical tool task):
      source .env                      # set GALAXY_URL + GALAXY_API_KEY
      galaxy-cli history create "my run"
      galaxy-cli dataset upload data.tsv --history-id HID
      galaxy-cli tool search "cut columns"
      galaxy-cli tool show Cut1
      galaxy-cli tool run Cut1 --history-id HID -i input=DSID --wait

    A successful blocking tool result already includes final job and output
    metadata; follow-up show calls are only needed for explicit diagnostics.

    \b
    Quick start (workflow task):
      galaxy-cli workflow import workflow.ga
      galaxy-cli workflow show WF_ID
      galaxy-cli workflow run WF_ID --history-id HID -i 0=DSID --wait
    """
    ctx.ensure_object(dict)
    json_mode = _resolve_json_mode(json_mode)
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key
    ctx.obj["profile"] = profile
    ctx.obj["history_id"] = history_id
    ctx.obj["request_timeout"] = request_timeout
    ctx.obj["json_mode"] = json_mode
    ctx.obj["output_file"] = output_file
    ctx.obj["max_items"] = max_items
    ctx.obj["max_chars"] = max_chars
    # Lazily create client — only when a subcommand needs it
    ctx.obj["client"] = None
    if url or api_key or profile:
        try:
            kwargs = {"url": url, "api_key": api_key, "profile": profile}
            if request_timeout is not None:
                kwargs["request_timeout"] = request_timeout
            ctx.obj["client"] = GalaxyClient(**kwargs)
        except GalaxyBackendError:
            pass  # Will fail when command actually needs the client

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ── Config Commands ──────────────────────────────────────────────────────

@cli.group("server")
def server_group():
    """Inspect Galaxy server metadata."""


@server_group.command("capabilities")
@click.option("--refresh-cache", is_flag=True)
@click.option("--cache/--no-cache", "use_cache", default=True)
@click.pass_context
def server_capabilities(ctx, refresh_cache, use_cache):
    """Report cached read-only capability detection."""
    result = server_mod.server_capabilities(
        _get_client(ctx), use_cache=use_cache, refresh_cache=refresh_cache
    )
    _output(result)


@cli.group("operation")
def operation_group():
    """Inspect or resume secret-free operation receipts."""


@operation_group.command("show")
@click.argument("receipt_or_id")
def operation_show(receipt_or_id):
    """Show one operation receipt by ID or file path."""
    _output(operation_mod.show_receipt(receipt_or_id))


@operation_group.command("list")
@click.option("--state", type=click.Choice(["unknown", "submitted", "complete", "failed"]), default=None)
def operation_list(state):
    """List operation receipts, optionally filtered by state."""
    _output(operation_mod.list_receipts(state=state))


@operation_group.command("resume")
@click.argument("receipt_or_id")
@click.option("--timeout", default=1800, type=click.FloatRange(min=0))
@click.option("--poll-interval", default=5, type=click.FloatRange(min=0))
@click.pass_context
def operation_resume(ctx, receipt_or_id, timeout, poll_interval):
    """Resume status polling or a known interrupted TUS session; never replay a POST."""
    _output(operation_mod.resume_operation(
        _get_client(ctx), receipt_or_id, timeout=timeout, poll_interval=poll_interval
    ))


@cli.group("config")
def config_group():
    """Manage Galaxy server connection settings."""


@config_group.command("set-url")
@click.argument("url")
def config_set_url(url):
    """Set the Galaxy server URL."""
    result = config_mod.set_url(url)
    _output(result, lambda d: click.echo(f"Galaxy URL set to: {d['url']}"))


@config_group.command("set-key")
@click.argument("api_key")
def config_set_key(api_key):
    """Set the Galaxy API key."""
    result = config_mod.set_key(api_key)
    _output(result, lambda d: click.echo("API key saved."))


@config_group.command("show")
def config_show():
    """Show current configuration (effective url/key + active profile)."""
    result = config_mod.show_config()
    def _human(d):
        click.echo(f"URL: {d['url']} [{d['url_source']}]")
        click.echo(f"API Key: {d['api_key']} [{d['api_key_source']}]")
        if d.get("active_profile"):
            click.echo(f"Active profile: {d['active_profile']}")
        if d.get("profiles"):
            click.echo(f"Profiles: {', '.join(d['profiles'])}")
    _output(result, _human)


# ── Profile Commands ─────────────────────────────────────────────────────

@cli.group("profile")
def profile_group():
    """Manage multiple Galaxy server profiles (multi-instance support).

    \b
    Profiles let you store credentials for several Galaxy servers
    (e.g., usegalaxy.org, usegalaxy.eu, a local dev server) and switch
    between them without re-exporting env vars.

    \b
    Resolution order (first match wins, per field):
      1. --url / --api-key flags
      2. GALAXY_URL / GALAXY_API_KEY env vars
      3. GALAXY_API_KEY_FILE (API key only)
      4. --profile NAME (selected for this command)
      5. Active profile (set by `profile use`)
      6. Legacy top-level config

    \b
    Credentials persist in ~/.galaxy-cli/config.json (mode 0600). No
    re-auth needed across shell sessions or agent invocations.
    """


@profile_group.command("add")
@click.argument("name")
@click.option("--url", required=True, help="Galaxy server URL")
@click.option("--api-key", required=True, help="Galaxy API key")
@click.option("--use", is_flag=True, help="Also mark this profile as active")
def profile_add(name, url, api_key, use):
    """Create or update a named profile.

    \b
    Examples:
      galaxy-cli profile add main --url https://usegalaxy.org --api-key $KEY --use
      galaxy-cli profile add eu   --url https://usegalaxy.eu  --api-key $KEY
    """
    result = config_mod.add_profile(name, url, api_key, use=use)
    _output(result, lambda d: click.echo(
        f"Saved profile '{d['name']}' ({d['url']}){'  [active]' if d['active'] else ''}"
    ))


@profile_group.command("list")
def profile_list():
    """List all saved profiles."""
    result = config_mod.list_profiles()
    def _human(data):
        if not data:
            click.echo("No profiles saved. Use: galaxy-cli profile add <name> --url ... --api-key ...")
            return
        for p in data:
            marker = " *" if p["active"] else "  "
            click.echo(f" {marker} {p['name']:<15} {p['url']}  key={p['api_key']}")
    _output(result, _human)


@profile_group.command("show")
@click.argument("name", required=False)
def profile_show(name):
    """Show a profile (defaults to the active one)."""
    result = config_mod.show_profile(name)
    def _human(d):
        if d.get("error"):
            click.echo(d["message"], err=True)
            return
        click.echo(f"Profile: {d['name']}{'  [active]' if d['active'] else ''}")
        click.echo(f"  URL: {d['url']}")
        click.echo(f"  API Key: {d['api_key']}")
    _output(result, _human)
    if isinstance(result, dict) and result.get("error"):
        raise click.exceptions.Exit(EXIT_USER_ERROR)


@profile_group.command("use")
@click.argument("name")
def profile_use(name):
    """Set the active profile."""
    result = config_mod.use_profile(name)
    if result.get("error"):
        _output(result, lambda d: click.echo(d["message"], err=True))
        raise click.exceptions.Exit(EXIT_USER_ERROR)
    _output(result, lambda d: click.echo(f"Active profile: {d['active_profile']}"))


@profile_group.command("remove")
@click.argument("name")
def profile_remove(name):
    """Delete a profile."""
    result = config_mod.remove_profile(name)
    if result.get("error"):
        _output(result, lambda d: click.echo(d["message"], err=True))
        raise click.exceptions.Exit(EXIT_USER_ERROR)
    _output(result, lambda d: click.echo(f"Removed profile: {d['name']}"))


@config_group.command("test")
@click.pass_context
def config_test(ctx):
    """Test connection to Galaxy server."""
    client = _get_client(ctx)
    result = config_mod.test_connection(client)
    _output(result, lambda d: click.echo(
        f"Connected to Galaxy {d['galaxy_version']}\n"
        f"User: {d['user']}"
    ))


# ── History Commands ─────────────────────────────────────────────────────

@cli.group("history")
def history_group():
    """Manage Galaxy histories."""


@history_group.command("list")
@click.option("--deleted", is_flag=True, help="Include deleted histories")
@click.option("--limit", default=50, help="Max results")
@click.pass_context
def history_list(ctx, deleted, limit):
    """List histories."""
    client = _get_client(ctx)
    results = history_mod.list_histories(client, deleted=deleted, limit=limit)
    def _human(data):
        if not data:
            click.echo("No histories found.")
            return
        for h in data:
            marker = " [D]" if h["deleted"] else ""
            click.echo(f"  {h['id']}  {h['name']}{marker}  ({h['state']})")
    _output(results, _human)


@history_group.command("create")
@click.argument("name", default="Unnamed history")
@click.pass_context
def history_create(ctx, name):
    """Create a new history and set it as the current working history.

    \b
    Examples:
      galaxy-cli history create "benchmark-T01"
      galaxy-cli history create "RNA-seq analysis"

    \b
    The returned JSON includes the history ID:
      {"id": "abc123...", "name": "benchmark-T01", ...}
    Use the id value with --history-id on subsequent commands.
    """
    client = _get_client(ctx)
    result = history_mod.create_history(client, name=name)
    session_mod.set_current_history(result["id"], result["name"])
    _output(result, lambda d: click.echo(f"Created history: {d['name']} ({d['id']})"))


@history_group.command("copy")
@click.argument("history_id")
@click.argument("name", required=False)
@click.option("--all-datasets", is_flag=True, help="Also copy deleted datasets and deleted collections")
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for copied datasets and collections to become ready. Default: --wait.",
)
@click.option(
    "--timeout",
    default=1800,
    type=click.FloatRange(min=0),
    help="Maximum readiness wait in seconds (default: 1800).",
)
@click.option(
    "--poll-interval",
    default=5,
    type=click.FloatRange(min=0),
    help="Seconds between copied-content checks (default: 5).",
)
@click.pass_context
def history_copy(ctx, history_id, name, all_datasets, wait, timeout, poll_interval):
    """Copy an existing history and set the copy as the current working history.

    \b
    Examples:
      galaxy-cli history copy abc123
      galaxy-cli history copy abc123 "working copy"
      galaxy-cli history copy abc123 "working copy" --all-datasets
    """
    client = _get_client(ctx)
    if wait:
        _progress(f"Waiting for copied history contents from {history_id}...")
    receipt_payload = {
        "source_history_id": history_id, "name": name,
        "all_datasets": all_datasets,
    }
    try:
        result = history_mod.copy_history(
            client,
            history_id,
            name=name,
            all_datasets=all_datasets,
            wait=wait,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    except GalaxyBackendError as exc:
        _operation_receipt("history_copy", receipt_payload, error=exc)
        raise
    _operation_receipt("history_copy", receipt_payload, result=result)
    session_mod.set_current_history(result["id"], result["name"])
    _output(
        result,
        lambda d: click.echo(
            f"Copied history {d['copied_from_history_id']} to {d['name']} ({d['id']})"
        ),
    )


def _history_content_options(function):
    options = [
        click.option("--name", default=None, help="Case-insensitive name substring."),
        click.option("--exact-name", default=None, help="Exact content name."),
        click.option("--hid", default=None, type=int),
        click.option("--type", "content_type", type=click.Choice(["dataset", "collection", "hda", "hdca"])),
        click.option("--state", default=None),
        click.option("--extension", default=None),
        click.option("--limit", default=50, type=click.IntRange(min=1)),
    ]
    for option in reversed(options):
        function = option(function)
    return function


@history_group.command("contents")
@click.argument("history_id")
@_history_content_options
@click.pass_context
def history_contents(ctx, history_id, name, exact_name, hid, content_type, state, extension, limit):
    """List compact history contents with stable filters."""
    result = history_mod.history_contents(
        _get_client(ctx), history_id, name=name, exact_name=exact_name, hid=hid,
        content_type=content_type, state=state, extension=extension, limit=limit,
    )
    _output(result)


@history_group.command("resolve")
@click.argument("history_id")
@_history_content_options
@click.pass_context
def history_resolve(ctx, history_id, name, exact_name, hid, content_type, state, extension, limit):
    """Resolve filters to exactly one dataset or collection."""
    result = history_mod.resolve_history_content(
        _get_client(ctx), history_id, name=name, exact_name=exact_name, hid=hid,
        content_type=content_type, state=state, extension=extension, limit=limit,
    )
    _output(result)


@history_group.command("show")
@click.argument("history_id")
@click.option("--contents", is_flag=True, help="Include history contents")
@click.pass_context
def history_show(ctx, history_id, contents):
    """Show history details."""
    client = _get_client(ctx)
    result = history_mod.show_history(client, history_id, contents=contents)
    def _human(d):
        click.echo(f"History: {d['name']} ({d['id']})")
        click.echo(f"  State: {d['state']}")
        click.echo(f"  Size: {d['size']}")
        click.echo(f"  Created: {d['create_time']}")
        if d.get("contents"):
            click.echo(f"  Contents ({len(d['contents'])} items):")
            for item in d["contents"]:
                click.echo(f"    {item['id']}  {item['name']}  [{item['extension']}]  ({item['state']})")
    _output(result, _human)


@history_group.command("delete")
@click.argument("history_id")
@click.option("--purge", is_flag=True, help="Permanently purge")
@click.pass_context
def history_delete(ctx, history_id, purge):
    """Delete a history."""
    client = _get_client(ctx)
    result = history_mod.delete_history(client, history_id, purge=purge)
    _output(result, lambda d: click.echo(f"Deleted history: {d['id']}"))


@history_group.command("use")
@click.argument("history_id")
@click.pass_context
def history_use(ctx, history_id):
    """Set the current working history."""
    client = _get_client(ctx)
    info = history_mod.show_history(client, history_id)
    result = session_mod.set_current_history(history_id, info["name"])
    _output(result, lambda d: click.echo(f"Now using history: {d['current_history_name']} ({d['current_history_id']})"))


@history_group.command("update")
@click.argument("history_id")
@click.option("--name", default=None, help="Rename the history")
@click.option("--annotation", default=None, help="Set the history annotation")
@click.option("--tag", "tags", multiple=True, help="Set a history tag; repeat for multiple tags")
@click.option("--published", type=click.BOOL, default=None, help="Set published true/false")
@click.option("--importable", type=click.BOOL, default=None, help="Set importable true/false")
@click.pass_context
def history_update(ctx, history_id, name, annotation, tags, published, importable):
    """Update history metadata and sharing flags.

    \b
    Examples:
      galaxy-cli history update HISTORY_ID --name "paper run"
      galaxy-cli history update HISTORY_ID --published true --importable true
    """
    if (
        name is None
        and annotation is None
        and not tags
        and published is None
        and importable is None
    ):
        raise click.UsageError(
            "Provide at least one field to update, such as --name or --published true."
        )
    client = _get_client(ctx)
    result = history_mod.update_history(
        client,
        history_id,
        name=name,
        annotation=annotation,
        tags=list(tags) if tags else None,
        published=published,
        importable=importable,
    )
    _output(
        result,
        lambda d: click.echo(f"Updated history {d['id']}: {', '.join(d['updated'])}"),
    )


@history_group.command("export")
@click.argument("history_id")
@click.pass_context
def history_export(ctx, history_id):
    """Start exporting a history archive."""
    client = _get_client(ctx)
    result = history_mod.export_history(client, history_id)
    _output(result, lambda d: click.echo(f"Export started for history {d['id']}"))


# ── Dataset Commands ─────────────────────────────────────────────────────

@cli.group("dataset")
def dataset_group():
    """Manage datasets in histories."""


@dataset_group.command("list")
@click.option("--history-id", default=None, help="History ID (uses current if not set)")
@click.option("--limit", default=50)
@click.pass_context
def dataset_list(ctx, history_id, limit):
    """List datasets in a history."""
    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)
    results = dataset_mod.list_datasets(client, hid, limit=limit)
    def _human(data):
        if not data:
            click.echo("No datasets found.")
            return
        for ds in data:
            click.echo(f"  {ds['id']}  {ds['name']}  [{ds['extension']}]  ({ds['state']})")
    _output(results, _human)


@dataset_group.command("upload")
@click.argument("file_path")
@click.option("--history-id", default=None, help="Target history ID")
@click.option("--file-type", default="auto", help="Galaxy file type")
@click.option(
    "--upload-backend", type=click.Choice(["auto", "tus", "legacy"]),
    default="auto", show_default=True,
)
@click.option("--dbkey", default="?", help="Genome build")
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for the upload job to reach a terminal state. Default: --wait.",
)
@click.option(
    "--timeout",
    default=1800,
    help="Max wait time in seconds; also used for the upload POST unless --upload-timeout is set (default: 1800)",
)
@click.option(
    "--upload-timeout",
    type=click.FloatRange(min=0.001),
    default=None,
    envvar="GALAXY_CLI_UPLOAD_TIMEOUT",
    help="Max seconds for the HTTP upload request. Defaults to --timeout.",
)
@click.option("--poll-interval", default=30, help="Seconds between status checks (default: 30)")
@click.pass_context
def dataset_upload(
    ctx,
    file_path,
    history_id,
    file_type,
    upload_backend,
    dbkey,
    wait,
    timeout,
    upload_timeout,
    poll_interval,
):
    """Upload a local file to a Galaxy history.

    \b
    Examples:
      galaxy-cli dataset upload data.tabular --history-id HID
      galaxy-cli dataset upload reads.fastq --history-id HID --file-type fastqsanger

    \b
    The returned JSON includes the dataset ID:
      {"id": "xyz789...", "name": "data.tabular", "state": "ok", ...}
    Use the id value as a tool input:  -i input=hda:DATASET_ID
    """
    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)
    receipt_payload = {
        "history_id": hid, "file_name": os.path.basename(file_path),
        "local_path": os.path.abspath(file_path),
        "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else None,
        "file_type": file_type,
        "dbkey": dbkey,
    }
    try:
        result = dataset_mod.upload_dataset(
            client,
            hid,
            file_path,
            file_type=file_type,
            dbkey=dbkey,
            wait=wait,
            timeout=timeout,
            upload_timeout=upload_timeout if upload_timeout is not None else timeout,
            poll_interval=poll_interval,
            upload_backend=upload_backend,
        )
    except GalaxyBackendError as exc:
        _operation_receipt("upload", receipt_payload, error=exc)
        raise
    _operation_receipt("upload", receipt_payload, result=result)
    if result.get("id"):
        session_mod.track_dataset(result["id"])
    _output(result, lambda d: click.echo(f"Uploaded: {d.get('name', file_path)} ({d.get('id', 'pending')})"))


@dataset_group.command("show")
@click.argument("dataset_id")
@click.option("--history-id", default=None)
@click.pass_context
def dataset_show(ctx, dataset_id, history_id):
    """Show dataset details."""
    client = _get_client(ctx)
    result = dataset_mod.show_dataset(client, dataset_id, history_id=history_id)
    def _human(d):
        click.echo(f"Dataset: {d['name']} ({d['id']})")
        click.echo(f"  Type: {d['extension']}")
        click.echo(f"  State: {d['state']}")
        click.echo(f"  Size: {d['file_size']}")
        click.echo(f"  Genome: {d['genome_build']}")
        if d.get("misc_blurb"):
            click.echo(f"  Info: {d['misc_blurb']}")
    _output(result, _human)


@dataset_group.command("download")
@click.argument("dataset_id")
@click.argument("output_path")
@click.option("--history-id", default=None, help="Accepted for consistency; ignored.")
@click.pass_context
def dataset_download(ctx, dataset_id, output_path, history_id):
    """Download a dataset to a local file."""
    client = _get_client(ctx)
    result = dataset_mod.download_dataset(client, dataset_id, output_path, history_id=history_id)
    _output(result, lambda d: click.echo(f"Downloaded to: {d['output']} ({d['size']:,} bytes)"))


@dataset_group.command("peek")
@click.argument("dataset_id")
@click.option("--lines", default=10, help="Number of lines to preview")
@click.option("--history-id", default=None, help="Accepted for consistency; ignored.")
@click.option(
    "--max-chars-per-line",
    default=500,
    type=click.IntRange(min=0),
    help="Maximum characters per preview line; use 0 for no limit (default: 500)",
)
@click.option(
    "--max-fields",
    default=20,
    type=click.IntRange(min=0),
    help="Maximum fields to return for delimited rows; use 0 for no limit (default: 20)",
)
@click.option(
    "--delimiter",
    default=None,
    help="Delimiter for field-aware previews: tab, comma, space, or a single character.",
)
@click.pass_context
def dataset_peek(ctx, dataset_id, lines, history_id, max_chars_per_line, max_fields, delimiter):
    """Preview the first few lines of a dataset."""
    client = _get_client(ctx)
    result = dataset_mod.peek_dataset(
        client,
        dataset_id,
        lines=lines,
        history_id=history_id,
        max_chars_per_line=max_chars_per_line,
        max_fields=max_fields,
        delimiter=delimiter,
    )
    def _human(d):
        for line in d.get("lines", []):
            click.echo(line)
    _output(result, _human)


@dataset_group.command("delete")
@click.argument("dataset_id")
@click.option("--history-id", default=None)
@click.option("--purge", is_flag=True)
@click.pass_context
def dataset_delete(ctx, dataset_id, history_id, purge):
    """Delete a dataset from a history."""
    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)
    result = dataset_mod.delete_dataset(client, dataset_id, hid, purge=purge)
    _output(result, lambda d: click.echo(f"Deleted dataset: {d['id']}"))


# ── Collection Commands ──────────────────────────────────────────────────

@cli.group("collection")
def collection_group():
    """Manage dataset collections."""


@collection_group.command("create")
@click.argument("name")
@click.option("--history-id", default=None, help="Target history ID")
@click.option("--collection-type", "ctype", default="list",
              type=click.Choice(["list", "paired", "list:paired"]),
              help="Collection type (default: list)")
@click.option("--forward", default=None, help="Forward dataset ID for paired collections")
@click.option("--reverse", default=None, help="Reverse dataset ID for paired collections")
@click.option("--element", "-e", "elements", multiple=True,
              help="List element: DATASET_ID or name=DATASET_ID")
@click.option("--pair", "-p", "pairs", multiple=True,
              help="Paired element: pair_name:forward_id:reverse_id")
@click.pass_context
def collection_create(ctx, name, history_id, ctype, forward, reverse, elements, pairs):
    """Create a dataset collection from uploaded datasets.

    \b
    Upload datasets first, then group them into a collection.
    Use collection IDs as workflow/tool inputs with the hdca: prefix.

    \b
    Examples:
      galaxy-cli collection create "samples" --history-id HID -e DSID1 -e DSID2
      galaxy-cli collection create "pair" --history-id HID --collection-type paired --forward FWD --reverse REV
      galaxy-cli collection create "pair" --history-id HID --collection-type paired -e forward=FWD -e reverse=REV
      galaxy-cli collection create "samples" --history-id HID -e s1=DSID1 -e s2=DSID2
      galaxy-cli collection create "pairs" --history-id HID --collection-type list:paired -p "sA:FWD:REV"

    \b
    The returned collection ID can be used as a tool/workflow input:
      -i input=hdca:COLLECTION_ID
    """
    try:
        if ctype == "paired" and forward and reverse and elements:
            raise click.UsageError(
                "Use either --forward/--reverse or -e for paired collections, not both"
            )
        if ctype == "paired" and forward and reverse:
            element_ids = [
                {"name": "forward", "id": forward, "src": "hda"},
                {"name": "reverse", "id": reverse, "src": "hda"},
            ]
        elif ctype == "paired" and (forward or reverse):
            raise click.UsageError("Paired collections require both --forward and --reverse")
        elif ctype != "paired" and (forward or reverse):
            raise click.UsageError("--forward/--reverse only apply to paired collections")
        elif ctype == "list:paired":
            if not pairs:
                raise click.UsageError(
                    "list:paired collections require --pair/-p arguments.\n"
                    "Format: -p 'pair_name:forward_id:reverse_id'"
                )
            element_ids = collection_mod.build_paired_elements(list(pairs))
        elif ctype == "paired":
            if not elements:
                raise click.UsageError(
                    "paired collections require exactly two --element/-e arguments.\n"
                    "Format: -e forward=DATASET_ID -e reverse=DATASET_ID"
                )
            element_ids = collection_mod.build_pair_collection_elements(list(elements))
        else:
            if not elements:
                raise click.UsageError(
                    "list collections require --element/-e arguments.\n"
                    "Format: -e DATASET_ID  or  -e name=DATASET_ID"
                )
            element_ids = collection_mod.build_list_elements(list(elements))
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)

    result = collection_mod.create_collection(
        client, hid, name, collection_type=ctype,
        element_identifiers=element_ids,
        include_elements=_json_mode_enabled(),
    )
    _output(result, lambda d: click.echo(
        f"Created collection: {d['name']} ({d['id']}) "
        f"[{d['collection_type']}, {d['element_count']} elements]"
    ))


@collection_group.command("list")
@click.option("--history-id", default=None, help="History ID (uses current if not set)")
@click.option("--limit", default=50)
@click.pass_context
def collection_list(ctx, history_id, limit):
    """List dataset collections in a history."""
    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)
    results = collection_mod.list_collections(client, hid, limit=limit)
    def _human(data):
        if not data:
            click.echo("No collections found.")
            return
        for c in data:
            click.echo(
                f"  {c['id']}  {c['name']}  [{c['collection_type']}]  "
                f"{c['element_count']} elements  ({c['state']})"
            )
    _output(results, _human)


@collection_group.command("show")
@click.argument("collection_id")
@click.option("--flatten", is_flag=True, help="Recursively return leaf datasets with stable paths.")
@click.option("--limit", default=100, type=click.IntRange(min=1))
@click.option("--max-depth", default=20, type=click.IntRange(min=1), hidden=True)
@click.pass_context
def collection_show(ctx, collection_id, flatten, limit, max_depth):
    """Show collection details including element structure."""
    client = _get_client(ctx)
    result = collection_mod.show_collection(
        client, collection_id, flatten=flatten, limit=limit, max_depth=max_depth
    )
    def _human(d):
        if flatten:
            for elem in d.get("elements", []):
                click.echo(
                    f"  {elem['element_path']}: {elem['src']}:{elem['id']} "
                    f"[{elem.get('extension', '')}] ({elem.get('state', '')})"
                )
            if d.get("truncated"):
                click.echo("  ... truncated")
            return
        click.echo(f"Collection: {d['name']} ({d['id']})")
        click.echo(f"  Type: {d['collection_type']}")
        click.echo(f"  Elements: {d['element_count']}")
        click.echo(f"  State: {d['populated_state']}")
        for elem in d.get("elements", []):
            if elem["element_type"] == "hda":
                click.echo(
                    f"    [{elem['element_index']}] {elem['element_identifier']}: "
                    f"{elem['name']} [{elem.get('extension', '?')}] ({elem.get('state', '?')})"
                )
            elif elem["element_type"] == "dataset_collection":
                click.echo(
                    f"    [{elem['element_index']}] {elem['element_identifier']} "
                    f"({elem.get('collection_type', 'paired')})"
                )
                for sub in elem.get("elements", []):
                    click.echo(
                        f"      {sub['element_identifier']}: "
                        f"{sub.get('name', '')} [{sub.get('extension', '?')}]"
                    )
    _output(result, _human)


@collection_group.command("resolve")
@click.argument("collection_id")
@click.option("--element", "element_path", required=True, help="Slash-separated nested element path.")
@click.option("--max-depth", default=20, type=click.IntRange(min=1), hidden=True)
@click.pass_context
def collection_resolve(ctx, collection_id, element_path, max_depth):
    """Resolve one nested collection element path to a dataset reference."""
    result = collection_mod.resolve_collection_element(
        _get_client(ctx), collection_id, element_path, max_depth=max_depth
    )
    _output(result)


# ── User-Defined Tool Commands ───────────────────────────────────────────

@cli.group("udt")
def udt_group():
    """Create, inspect, run, and deactivate Galaxy user-defined tools."""


@udt_group.command("list")
@click.option(
    "--include-inactive",
    is_flag=True,
    help="Include inactive user-defined tools.",
)
@click.pass_context
def udt_list(ctx, include_inactive):
    """List user-defined tools. Active tools are shown by default."""
    result = udt_mod.list_udts(_get_client(ctx), include_inactive=include_inactive)
    _output(result)


@udt_group.command("show")
@click.argument("uuid")
@click.pass_context
def udt_show(ctx, uuid):
    """Show one user-defined tool and its representation."""
    _output(udt_mod.show_udt(_get_client(ctx), uuid))


@udt_group.command("validate")
@click.option(
    "--representation-json", required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option("--history-id", default=None, help="Existing history used as build context.")
@click.pass_context
def udt_validate(ctx, representation_json, history_id):
    """Run Galaxy build/runtime preflight without creating a UDT."""
    representation = udt_mod.load_json_object(
        representation_json, "--representation-json"
    )
    _output(udt_mod.validate_udt(_get_client(ctx), representation, history_id=history_id))


@udt_group.command("create")
@click.option(
    "--representation-json",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON file containing the GalaxyUserTool representation.",
)
@click.pass_context
def udt_create(ctx, representation_json):
    """Validate and create one user-defined tool."""
    representation = udt_mod.load_json_object(
        representation_json, "--representation-json"
    )
    result = udt_mod.create_udt(_get_client(ctx), representation)
    _output(result)


@udt_group.command("delete")
@click.argument("uuid")
@click.pass_context
def udt_delete(ctx, uuid):
    """Deactivate one user-defined tool."""
    _output(udt_mod.delete_udt(_get_client(ctx), uuid))


@udt_group.command("run")
@click.argument("uuid")
@click.option("--history-id", required=True, help="Target history ID")
@click.option(
    "--inputs-json",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON file containing native Galaxy input references.",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for all UDT jobs to finish. Default: --wait.",
)
@click.option("--timeout", default=1800, help="Max job wait time in seconds")
@click.option(
    "--poll-interval", default=180, help="Seconds between job status checks"
)
@click.pass_context
def udt_run(ctx, uuid, history_id, inputs_json, wait, timeout, poll_interval):
    """Run an active UDT by UUID through Galaxy's tool execution endpoint."""
    inputs = udt_mod.load_json_object(inputs_json, "--inputs-json")
    client = _get_client(ctx)
    receipt_payload = {"uuid": uuid, "history_id": history_id, "inputs": inputs}
    try:
        result = udt_mod.run_udt(
            client,
            uuid,
            history_id,
            inputs,
            wait=wait,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    except GalaxyBackendError as exc:
        _operation_receipt("udt", receipt_payload, error=exc)
        raise
    _operation_receipt("udt", receipt_payload, result=result)
    for job in result.get("jobs", []):
        if job.get("id"):
            session_mod.track_job(job["id"])
    _output(result)


@udt_group.command("create-run")
@click.option(
    "--representation-json",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON file containing the GalaxyUserTool representation.",
)
@click.option("--history-id", required=True, help="Target history ID")
@click.option(
    "--inputs-json",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON file containing native Galaxy input references.",
)
@click.option("--timeout", default=1800, help="Max job wait time in seconds")
@click.option(
    "--poll-interval", default=180, help="Seconds between job status checks"
)
@click.option(
    "--evidence-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Write redacted full request, response, job, and output JSON files.",
)
@click.pass_context
def udt_create_run(
    ctx,
    representation_json,
    history_id,
    inputs_json,
    timeout,
    poll_interval,
    evidence_dir,
):
    """Create one UDT, run its returned UUID, wait, and return output IDs."""
    representation = udt_mod.load_json_object(
        representation_json, "--representation-json"
    )
    inputs = udt_mod.load_json_object(inputs_json, "--inputs-json")
    evidence = {} if evidence_dir else None
    client = _get_client(ctx)
    try:
        receipt_payload = {
            "representation": representation, "history_id": history_id,
            "inputs": inputs,
        }
        try:
            result = udt_mod.create_run_udt(
                client,
                representation,
                history_id,
                inputs,
                timeout=timeout,
                poll_interval=poll_interval,
                evidence=evidence,
            )
        except GalaxyBackendError as exc:
            _operation_receipt("udt", receipt_payload, error=exc)
            raise
        _operation_receipt("udt", receipt_payload, result=result)
    finally:
        if evidence:
            udt_mod.write_evidence(
                evidence_dir, evidence, secrets=(getattr(client, "api_key", None),)
            )
    for job in result.get("jobs", []):
        if job.get("id"):
            session_mod.track_job(job["id"])
    _output(result)


# ── Tool Commands ────────────────────────────────────────────────────────

@cli.group("tool")
def tool_group():
    """Manage and run Galaxy tools."""


@tool_group.command("list")
@click.option("--query", "-q", default=None, help="Search query")
@click.option("--limit", default=50)
@click.pass_context
def tool_list(ctx, query, limit):
    """List available tools."""
    client = _get_client(ctx)
    results = tool_mod.list_tools(client, query=query, limit=limit)
    def _human(data):
        if not data:
            click.echo("No tools found.")
            return
        for t in data:
            click.echo(f"  {t['id']}  {t['name']} v{t['version']}")
            if t['description']:
                click.echo(f"    {t['description']}")
    _output(results, _human)


@tool_group.command("search")
@click.argument("query")
@click.option("--limit", default=25, type=click.IntRange(min=1), help="Maximum matches to return (default: 25)")
@click.option("--resolve", is_flag=True, help="Resolve string-only hits with extra tool detail requests.")
@click.option("--exact", is_flag=True, help="Match the exact tool ID or name.")
@click.option("--input-extension", default=None, help="Require a compatible input extension.")
@click.option("--output-extension", default=None, help="Require a declared output extension.")
@click.option("--version", default=None, help="Require an exact installed tool version.")
@click.option("--all-versions", is_flag=True, help="Return installed versions without choosing one.")
@click.option(
    "--cache/--no-cache",
    "use_cache",
    default=True,
    help="Use the local versioned tool metadata cache. Default: --cache.",
)
@click.option("--refresh-cache", is_flag=True, help="Refresh the local full tool list cache before searching.")
@click.pass_context
def tool_search(
    ctx, query, limit, resolve, exact, input_extension, output_extension,
    version, all_versions, use_cache, refresh_cache,
):
    """Search for tools by name or description."""
    client = _get_client(ctx)
    search_options = {
        "limit": limit,
        "resolve": resolve,
        "use_cache": use_cache,
        "refresh_cache": refresh_cache,
    }
    if exact:
        search_options["exact"] = True
    if input_extension:
        search_options["input_extension"] = input_extension
    if output_extension:
        search_options["output_extension"] = output_extension
    if version:
        search_options["version"] = version
    if all_versions:
        search_options["all_versions"] = True
    results = tool_mod.search_tools(client, query, **search_options)
    def _human(data):
        if not data:
            click.echo(f"No tools matching '{query}'.")
            return
        click.echo(f"Found {len(data)} tool(s):")
        for t in data:
            click.echo(f"  {t['id']}  {t['name']} v{t['version']}")
    _output(results, _human)


@tool_group.command("show")
@click.argument("tool_id")
@click.option(
    "--full",
    is_flag=True,
    help="Include verbose fields (help text, EDAM ontology, requirements). "
    "Off by default to keep output compact.",
)
@click.option(
    "--refresh-cache",
    is_flag=True,
    help="Refresh the compact input-template cache before showing the tool.",
)
@click.option(
    "--cache/--no-cache",
    "use_cache",
    default=True,
    help="Use the versioned local compact input-template cache. Default: --cache.",
)
@click.pass_context
def tool_show(ctx, tool_id, full, refresh_cache, use_cache):
    """Show tool inputs and outputs so you know how to call tool run.

    \b
    By default, returns a compact record: id, name, version, description,
    inputs (with names, types, options, defaults), and outputs.
    Pass --full to include help text, EDAM ontology, and requirements.

    \b
    Examples:
      galaxy-cli tool show Cut1
      galaxy-cli tool show datamash_ops
      galaxy-cli tool show Cut1 --full

    \b
    Key fields in each input entry:
      name      — the parameter name to pass to tool run via -i name=value
      type      — data, select, boolean, integer, float, text, repeat, ...
      options   — for select types, the allowed {value, label} pairs
      default   — the default value if the parameter is omitted
      optional  — whether the parameter can be skipped
    """
    client = _get_client(ctx)
    result = tool_mod.show_tool(
        client,
        tool_id,
        full=full,
        use_cache=use_cache,
        refresh_cache=refresh_cache,
    )
    def _fmt_input(inp, indent=4):
        """Format a single input parameter for human display."""
        prefix = " " * indent
        ptype = inp.get("type", "")
        opt = " (optional)" if inp.get("optional") else ""
        line = f"{prefix}{inp['name']} [{ptype}]{opt}: {inp.get('label', '')}"
        click.echo(line)

        if ptype == "data":
            exts = inp.get("extensions", [])
            if exts:
                click.echo(f"{prefix}  accepts: {', '.join(exts)}")
        elif ptype == "select":
            opts = inp.get("options", [])
            default = inp.get("default")
            if opts:
                choices = [f"{o['value']!r} ({o['label']})" for o in opts[:10]]
                click.echo(f"{prefix}  choices: {', '.join(choices)}")
                if len(opts) > 10:
                    click.echo(f"{prefix}  ... and {len(opts) - 10} more")
            if default:
                click.echo(f"{prefix}  default: {default!r}")
        elif ptype == "boolean":
            click.echo(f"{prefix}  default: {inp.get('default', False)}")
        elif ptype in ("integer", "float"):
            parts = [f"default: {inp.get('default')}"]
            if inp.get("min") is not None:
                parts.append(f"min: {inp['min']}")
            if inp.get("max") is not None:
                parts.append(f"max: {inp['max']}")
            click.echo(f"{prefix}  {', '.join(parts)}")
        elif ptype == "text":
            default = inp.get("default")
            if default:
                click.echo(f"{prefix}  default: {default!r}")
        elif ptype == "conditional":
            tp = inp.get("test_param", {})
            if tp:
                click.echo(f"{prefix}  switch on: {tp.get('name', '?')}")
                _fmt_input(tp, indent + 4)
            for case in inp.get("cases", []):
                click.echo(f"{prefix}  when {tp.get('name', '?')}={case['value']!r}:")
                for ci in case.get("inputs", []):
                    _fmt_input(ci, indent + 6)
        elif ptype == "repeat":
            click.echo(f"{prefix}  (repeatable)")
            for ri in inp.get("inputs", []):
                _fmt_input(ri, indent + 4)
        elif ptype == "section":
            for si in inp.get("inputs", []):
                _fmt_input(si, indent + 4)

    def _human(d):
        click.echo(f"Tool: {d['name']} ({d['id']}) v{d['version']}")
        click.echo(f"  {d['description']}")
        if d["inputs"]:
            click.echo("  Inputs:")
            for inp in d["inputs"]:
                _fmt_input(inp)
        if d["outputs"]:
            click.echo("  Outputs:")
            for out in d["outputs"]:
                click.echo(f"    {out['name']} [{out['format']}]: {out['label']}")
    _output(result, _human)


@tool_group.command("template")
@click.argument("tool_id")
@click.option("--refresh-cache", is_flag=True)
@click.option("--cache/--no-cache", "use_cache", default=True)
@click.pass_context
def tool_template(ctx, tool_id, refresh_cache, use_cache):
    """Return a machine-fillable nested JSON input skeleton."""
    result = tool_mod.tool_template(
        _get_client(ctx), tool_id, use_cache=use_cache, refresh_cache=refresh_cache
    )
    _output(result)


@tool_group.command("examples")
@click.argument("tool_id")
@click.option("--limit", default=2, type=click.IntRange(min=1))
@click.option("--max-chars", default=12000, type=click.IntRange(min=1))
@click.pass_context
def tool_examples(ctx, tool_id, limit, max_chars):
    """Return bounded Galaxy-provided tool test examples."""
    _output(tool_mod.tool_examples(_get_client(ctx), tool_id, limit, max_chars))


@tool_group.command("validate")
@click.argument("tool_id")
@click.option("--history-id", required=True)
@click.option(
    "--inputs-json", required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.pass_context
def tool_validate(ctx, tool_id, history_id, inputs_json):
    """Run local and server-side validation without submitting a job."""
    inputs = udt_mod.load_json_object(inputs_json, "--inputs-json")
    result = tool_mod.validate_tool_on_server(
        _get_client(ctx), tool_id, history_id, inputs
    )
    _output(result)


@tool_group.command("run")
@click.argument("tool_id")
@click.option("--history-id", default=None)
@click.option("--input", "-i", "inputs", multiple=True, help="Tool input as key=value")
@click.option(
    "--inputs-json",
    "inputs_json",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to JSON file with tool inputs. Keys are input names; values may "
    "be nested dicts/lists for repeats and conditionals. -i flags override.",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for job completion and refresh output metadata. Default: --wait.",
)
@click.option("--timeout", default=1800, help="Max wait time in seconds (default: 1800)")
@click.option("--poll-interval", default=180, help="Seconds between status checks (default: 180)")
@click.option(
    "--execution-backend",
    type=click.Choice(["auto", "strict", "legacy"]),
    default="auto",
    show_default=True,
    help="Tool execution API. Auto prefers strict and only safely falls back when unsupported.",
)
@click.option(
    "--peek-output",
    default=None,
    metavar="OUTPUT_NAME",
    help="Return a bounded preview for this dataset output after a successful blocking run.",
)
@click.option(
    "--peek-lines",
    default=10,
    type=click.IntRange(min=1),
    show_default=True,
    help="Maximum lines to preview for --peek-output.",
)
@click.option(
    "--dry-run-payload",
    is_flag=True,
    help="Print the exact Galaxy POST body and do not submit the tool.",
)
@click.option(
    "--save-payload",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write the exact Galaxy POST body to PATH before submitting.",
)
@click.pass_context
def tool_run(
    ctx,
    tool_id,
    history_id,
    inputs,
    inputs_json,
    wait,
    timeout,
    poll_interval,
    execution_backend,
    peek_output,
    peek_lines,
    dry_run_payload,
    save_payload,
):
    """Run a tool with given inputs.

    \b
    Inputs can be provided two ways:
      -i key=value           (one flag per parameter, simple values)
      --inputs-json file.json (entire input dict, supports nesting)

    Both can be combined; -i flags override matching keys from --inputs-json.

    \b
    Parameter encoding rules (Galaxy native format):
      • Datasets:     -i input=hda:DATASET_ID  (or just the dataset id)
      • Collections:  -i input=hdca:COLLECTION_ID
      • Nested dataset/collection JSON may use {"src":"hda","id":"DATASET_ID"}
        or {"src":"hdca","id":"COLLECTION_ID"}
      • Booleans:     -i some_flag=true        (use true / false, not yes/no)
      • Repeat blocks use pipe syntax with a 0-based index per item:
            -i operations_0|op_name=mean
            -i operations_0|op_column=2
            -i operations_1|op_name=sum
            -i operations_1|op_column=3
      • Conditionals are flattened the same way:
            -i cond|selector=advanced
            -i cond|threshold=0.5

    \b
    Equivalent --inputs-json file:
      {
        "input": "hda:abc123",
        "operations": [
          {"op_name": "mean", "op_column": "2"},
          {"op_name": "sum",  "op_column": "3"}
        ],
        "cond": {"selector": "advanced", "threshold": "0.5"}
      }

    \b
    MultiQC FastQC inputs in the current IUC wrapper:
      {
        "results": [
          {
            "software_cond": {
              "software": "fastqc",
              "output": [
                {
                  "type": "data",
                  "input": [
                    {"src": "hda", "id": "FASTQC_RAW_DATA_1"},
                    {"src": "hda", "id": "FASTQC_RAW_DATA_2"}
                  ]
                }
              ]
            }
          }
        ]
      }

    By default this waits for the job and, in JSON mode, refreshes output
    state/type/size so agents do not need follow-up job/dataset show calls.
    Use --no-wait only when you intentionally want asynchronous submission.

    Use --dry-run-payload or --save-payload PATH to inspect the exact POST body
    after dataset and collection references have been normalized.

    Use `tool show <tool_id>` only when task files do not provide enough input
    names/options to build the submission JSON.
    """
    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)
    input_dict = {}
    if inputs_json:
        try:
            with open(inputs_json, "r") as fh:
                loaded = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise click.UsageError(f"Failed to read --inputs-json file: {exc}")
        if not isinstance(loaded, dict):
            raise click.UsageError(
                "--inputs-json file must contain a JSON object at the top level."
            )
        input_dict.update(loaded)
    for inp in inputs:
        if "=" not in inp:
            raise click.UsageError(f"Invalid input format: {inp}. Use key=value")
        k, v = inp.split("=", 1)
        input_dict[k] = v
    if peek_output and not wait:
        raise click.UsageError("--peek-output requires the default --wait behavior.")

    plan = None
    if dry_run_payload or save_payload:
        plan = tool_mod.build_tool_execution_plan(
            client,
            tool_id,
            hid,
            inputs=input_dict,
            execution_backend=execution_backend,
        )
        if save_payload:
            _write_json_file(save_payload, plan["post_body"])
        if dry_run_payload:
            public_plan = tool_mod.execution_plan_for_output(plan)
            _output(
                public_plan,
                lambda d: click.echo(json.dumps(d, indent=2, default=str)),
            )
            return
    if wait:
        _progress(f"Waiting for all jobs from tool {tool_id}...")
    receipt_payload = plan["post_body"] if plan else {
        "tool_id": tool_id, "history_id": hid, "inputs": input_dict,
        "execution_backend": execution_backend,
    }
    try:
        result = tool_mod.run_tool(
            client,
            tool_id,
            hid,
            inputs=input_dict,
            execution_backend=execution_backend,
            wait=wait,
            timeout=timeout,
            poll_interval=poll_interval,
            plan=plan,
        )
    except GalaxyBackendError as exc:
        _operation_receipt("tool", receipt_payload, error=exc)
        raise
    _operation_receipt("tool", receipt_payload, result=result)
    if save_payload:
        result["saved_payload"] = save_payload
        result["saved_payload_backend"] = plan["execution_backend"]
    for job in result.get("jobs", []):
        if job.get("id"):
            session_mod.track_job(job["id"])
    if peek_output:
        _attach_output_peek(client, result, peek_output, peek_lines)
    _output(result, lambda d: click.echo(
        f"Tool {d['tool_id']} submitted. "
        f"Jobs: {[j['id'] for j in d['jobs']]}. "
        f"Outputs: {[o['name'] for o in d['outputs']]}"
    ))


# ── Job Commands ─────────────────────────────────────────────────────────

@cli.group("job")
def job_group():
    """Manage Galaxy jobs."""


@job_group.command("list")
@click.option("--history-id", default=None)
@click.option("--state", default=None, help="Filter by state (ok, running, queued, error)")
@click.option("--tool-id", default=None)
@click.option("--limit", default=50)
@click.pass_context
def job_list(ctx, history_id, state, tool_id, limit):
    """List jobs."""
    client = _get_client(ctx)
    results = job_mod.list_jobs(client, history_id=history_id, state=state,
                                tool_id=tool_id, limit=limit)
    def _human(data):
        if not data:
            click.echo("No jobs found.")
            return
        for j in data:
            click.echo(f"  {j['id']}  {j['tool_id']}  ({j['state']})  {j['update_time']}")
    _output(results, _human)


@job_group.command("show")
@click.argument("job_id")
@click.option("--full", is_flag=True, help="Include resolved I/O, params, command line, stdout, and stderr")
@click.option("--logs", is_flag=True, help="Include command line, stdout, and stderr")
@click.pass_context
def job_show(ctx, job_id, full, logs):
    """Show compact job details.

    Default output is token-cheap for agents. Use --full for provenance checks
    after submission or --logs when only command line/stdout/stderr are needed.
    """
    client = _get_client(ctx)
    result = job_mod.show_job(client, job_id, full=full, logs=logs)
    def _human(d):
        click.echo(f"Job: {d['id']}")
        click.echo(f"  Tool: {d['tool_id']}")
        click.echo(f"  State: {d['state']}")
        click.echo(f"  Exit code: {d['exit_code']}")
        click.echo(f"  Created: {d['create_time']}")
        if d.get("command_line"):
            click.echo(f"  Command: {d['command_line'][:500]}")
        if d.get("stdout"):
            click.echo(f"  Stdout: {d['stdout'][:500]}")
        if d.get("stderr"):
            click.echo(f"  Stderr: {d['stderr'][:500]}")
    _output(result, _human)


@job_group.command("logs")
@click.argument("job_id")
@click.option("--tail", default=100, type=click.IntRange(min=1))
@click.option("--grep", "pattern", default=None)
@click.option("--context", default=2, type=click.IntRange(min=0))
@click.option("--max-chars", default=None, type=click.IntRange(min=1))
@click.option("--full", is_flag=True, help="Explicitly request complete logs; optionally bound with --max-chars.")
@click.pass_context
def job_logs(ctx, job_id, tail, pattern, context, max_chars, full):
    """Show bounded stdout/stderr with optional matching context."""
    effective_max_chars = max_chars if max_chars is not None else (None if full else 12000)
    result = job_mod.job_logs(
        _get_client(ctx), job_id, tail=tail, pattern=pattern,
        context=context, max_chars=effective_max_chars, full=full,
    )
    _output(result)


@job_group.command("diagnose")
@click.argument("job_id")
@click.option("--max-chars", default=12000, type=click.IntRange(min=1))
@click.pass_context
def job_diagnose(ctx, job_id, max_chars):
    """Summarize a failed job and bounded error log context."""
    _output(job_mod.diagnose_job(_get_client(ctx), job_id, max_chars=max_chars))


@job_group.command("cancel")
@click.argument("job_id")
@click.pass_context
def job_cancel(ctx, job_id):
    """Cancel a running job."""
    client = _get_client(ctx)
    result = job_mod.cancel_job(client, job_id)
    _output(result, lambda d: click.echo(f"Cancelled job: {d['id']}"))


@job_group.command("wait")
@click.argument("job_id")
@click.option("--timeout", default=600, help="Max wait time in seconds")
@click.option("--poll-interval", default=5, help="Seconds between status checks")
@click.pass_context
def job_wait(ctx, job_id, timeout, poll_interval):
    """Wait for a job to complete."""
    client = _get_client(ctx)
    _progress(f"Waiting for job {job_id}...")
    result = job_mod.wait_for_job(client, job_id, max_wait=timeout, poll_interval=poll_interval)
    _output(result, lambda d: click.echo(
        f"Job {d['id']}: {d['state']} (waited {d['waited_seconds']}s)"
    ))


# ── Workflow Commands ────────────────────────────────────────────────────

@cli.group("workflow")
def workflow_group():
    """Manage Galaxy workflows."""


@workflow_group.command("list")
@click.option("--published", is_flag=True, help="Include published workflows")
@click.pass_context
def workflow_list(ctx, published):
    """List workflows."""
    client = _get_client(ctx)
    results = workflow_mod.list_workflows(client, published=published)
    def _human(data):
        if not data:
            click.echo("No workflows found.")
            return
        for w in data:
            pub = " [published]" if w["published"] else ""
            click.echo(f"  {w['id']}  {w['name']}{pub}  ({w['step_count']} steps)")
    _output(results, _human)


@workflow_group.command("show")
@click.argument("workflow_id")
@click.pass_context
def workflow_show(ctx, workflow_id):
    """Show workflow details."""
    client = _get_client(ctx)
    result = workflow_mod.show_workflow(client, workflow_id)
    def _human(d):
        click.echo(f"Workflow: {d['name']} ({d['id']})")
        click.echo(f"  Owner: {d['owner']}")
        click.echo(f"  Steps: {d['step_count']}")
        click.echo(f"  Version: {d['version']}")
        if d.get("annotation"):
            click.echo(f"  {d['annotation']}")
        if d["inputs"]:
            click.echo("  Inputs:")
            for k, v in d["inputs"].items():
                itype = v.get("input_type", "")
                label = v.get("label", "")
                line = f"    [{k}] {label}"
                if itype == "collection":
                    ctype = v.get("collection_type", "list")
                    line += f"  (collection: {ctype})"
                elif itype == "parameter":
                    ptype = v.get("parameter_type", "text")
                    line += f"  (parameter: {ptype})"
                    default = v.get("default")
                    if default is not None:
                        line += f", default={default!r}"
                elif itype == "dataset":
                    line += "  (dataset)"
                if v.get("optional"):
                    line += " [optional]"
                click.echo(line)
                ann = v.get("annotation", "")
                if ann:
                    click.echo(f"      {ann}")
        if d["steps"]:
            click.echo("  Steps:")
            for sid, step in d["steps"].items():
                tool = step["tool_id"] or step["type"]
                click.echo(f"    [{sid}] {step['label'] or tool}")
    _output(result, _human)


@workflow_group.command("template")
@click.argument("workflow_id")
@click.pass_context
def workflow_template(ctx, workflow_id):
    """Return a machine-fillable input skeleton and parameter guide."""
    _output(workflow_mod.workflow_template(_get_client(ctx), workflow_id))


@workflow_group.command("import")
@click.argument("workflow_path")
@click.pass_context
def workflow_import(ctx, workflow_path):
    """Import a workflow from a JSON file."""
    client = _get_client(ctx)
    result = workflow_mod.import_workflow(client, workflow_path=workflow_path)
    _output(result, lambda d: click.echo(f"Imported workflow: {d['name']} ({d['id']})"))


@workflow_group.command("export")
@click.argument("workflow_id")
@click.option("-o", "--output", "output_path", default=None, help="Output file path")
@click.pass_context
def workflow_export(ctx, workflow_id, output_path):
    """Export a workflow to JSON."""
    client = _get_client(ctx)
    result = workflow_mod.export_workflow(client, workflow_id, output_path=output_path)
    if output_path:
        _output(result, lambda d: click.echo(f"Exported to: {d['output']}"))
    else:
        _output(result)


@workflow_group.command("run")
@click.argument("workflow_id")
@click.option("--history-id", default=None)
@click.option("--new-history", default=None, help="Create new history with this name")
@click.option("--input", "-i", "inputs", multiple=True, help="Step input as step_index=dataset_id")
@click.option("--wait/--no-wait", default=True, help="Wait for invocation and every job. Default: --wait.")
@click.option("--timeout", default=1800, help="Max wait time in seconds (default: 1800)")
@click.option("--poll-interval", default=10, help="Seconds between status checks (default: 10)")
@click.option(
    "--dry-run-payload",
    is_flag=True,
    help="Print the exact Galaxy invocation POST body and do not submit the workflow.",
)
@click.option(
    "--save-payload",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write the exact Galaxy invocation POST body to PATH before submitting.",
)
@click.pass_context
def workflow_run(
    ctx,
    workflow_id,
    history_id,
    new_history,
    inputs,
    wait,
    timeout,
    poll_interval,
    dry_run_payload,
    save_payload,
):
    """Run a workflow with dataset/collection inputs mapped to steps.

    \b
    Each -i flag maps a workflow step index to a dataset or collection.
    Use `workflow show` first to see the step indices and input types.

    \b
    Examples:
      galaxy-cli workflow run WF_ID --history-id HID -i 0=DSID --wait
      galaxy-cli workflow run WF_ID --history-id HID -i 0=DSID -i 1=hdca:COLL_ID --wait
      galaxy-cli workflow run WF_ID --new-history "results" -i 0=DSID --wait

    \b
    Input values:
      Dataset:    -i 0=DATASET_ID       (hda: prefix optional)
      Collection: -i 0=hdca:COLL_ID     (hdca: prefix required)

    \b
    --wait blocks until the invocation finishes (default timeout: 1800s).
    With --no-wait, returns immediately with the invocation ID.

    Use --dry-run-payload or --save-payload PATH to validate inputs and inspect
    the exact invocation POST body before submission.
    """
    client = _get_client(ctx)
    hid = history_id
    if not hid and not new_history:
        hid = _require_history(ctx)
    input_dict = {}
    for inp in inputs:
        if "=" not in inp:
            raise click.UsageError(f"Invalid input format: {inp}. Use step_index=dataset_id")
        k, v = inp.split("=", 1)
        input_dict[k] = v
    payload = None
    if dry_run_payload or save_payload:
        payload = workflow_mod.build_workflow_payload(
            client,
            workflow_id,
            history_id=hid,
            inputs=input_dict if input_dict else None,
            new_history_name=new_history,
        )
        if save_payload:
            _write_json_file(save_payload, payload)
        if dry_run_payload:
            _output(payload, lambda d: click.echo(json.dumps(d, indent=2, default=str)))
            return
    receipt_payload = payload or {
        "workflow_id": workflow_id, "history_id": hid,
        "inputs": input_dict, "new_history_name": new_history,
    }
    try:
        result = workflow_mod.run_workflow(
            client, workflow_id, history_id=hid,
            inputs=input_dict if input_dict else None,
            new_history_name=new_history,
            payload=payload,
        )
    except GalaxyBackendError as exc:
        _operation_receipt("workflow", receipt_payload, error=exc)
        raise
    if save_payload:
        result["saved_payload"] = save_payload
    if wait and result.get("id"):
        _progress(f"Waiting for invocation {result['id']}...")
        result = workflow_mod.wait_for_workflow_run(
            client, result, timeout=timeout, poll_interval=poll_interval,
        )
    _operation_receipt("workflow", receipt_payload, result=result)
    _output(result, lambda d: click.echo(
        f"Invocation {d['id']} started (state: {d['state']})"
    ))


@workflow_group.command("delete")
@click.argument("workflow_id")
@click.pass_context
def workflow_delete(ctx, workflow_id):
    """Delete a workflow."""
    client = _get_client(ctx)
    result = workflow_mod.delete_workflow(client, workflow_id)
    _output(result, lambda d: click.echo(f"Deleted workflow: {d['id']}"))


# ── Invocation Commands ──────────────────────────────────────────────────

@cli.group("invocation")
def invocation_group():
    """Manage workflow invocations."""


@invocation_group.command("list")
@click.option("--workflow-id", default=None)
@click.option("--history-id", default=None)
@click.option("--limit", default=50)
@click.pass_context
def invocation_list(ctx, workflow_id, history_id, limit):
    """List workflow invocations."""
    client = _get_client(ctx)
    results = invocation_mod.list_invocations(client, workflow_id=workflow_id,
                                               history_id=history_id, limit=limit)
    def _human(data):
        if not data:
            click.echo("No invocations found.")
            return
        for inv in data:
            click.echo(f"  {inv['id']}  wf:{inv['workflow_id']}  ({inv['state']})  {inv['update_time']}")
    _output(results, _human)


@invocation_group.command("show")
@click.argument("invocation_id")
@click.pass_context
def invocation_show(ctx, invocation_id):
    """Show invocation details."""
    client = _get_client(ctx)
    result = invocation_mod.show_invocation(client, invocation_id)
    def _human(d):
        click.echo(f"Invocation: {d['id']}")
        click.echo(f"  Workflow: {d['workflow_id']}")
        click.echo(f"  State: {d['state']}")
        click.echo(f"  Created: {d['create_time']}")
        if d["steps"]:
            click.echo(f"  Steps ({len(d['steps'])}):")
            for step in d["steps"]:
                click.echo(f"    [{step['order_index']}] {step['state']}  job:{step['job_id']}")
    _output(result, _human)


@invocation_group.command("cancel")
@click.argument("invocation_id")
@click.pass_context
def invocation_cancel(ctx, invocation_id):
    """Cancel a running invocation."""
    client = _get_client(ctx)
    result = invocation_mod.cancel_invocation(client, invocation_id)
    _output(result, lambda d: click.echo(f"Cancelled invocation: {d['id']}"))


@invocation_group.command("wait")
@click.argument("invocation_id")
@click.option("--timeout", default=1800, help="Max wait time in seconds (default: 1800)")
@click.option("--poll-interval", default=10, help="Seconds between status checks (default: 10)")
@click.pass_context
def invocation_wait(ctx, invocation_id, timeout, poll_interval):
    """Wait for an invocation to complete."""
    client = _get_client(ctx)
    _progress(f"Waiting for invocation {invocation_id}...")
    result = invocation_mod.wait_for_invocation(
        client, invocation_id, max_wait=timeout, poll_interval=poll_interval,
    )
    _output(result, lambda d: click.echo(
        f"Invocation {d['id']}: {d['state']} (waited {d['waited_seconds']}s)"
    ))


# ── Library Commands ─────────────────────────────────────────────────────

@cli.group("library")
def library_group():
    """Manage shared data libraries."""


@library_group.command("list")
@click.option("--deleted", is_flag=True)
@click.pass_context
def library_list(ctx, deleted):
    """List data libraries."""
    client = _get_client(ctx)
    results = library_mod.list_libraries(client, deleted=deleted)
    def _human(data):
        if not data:
            click.echo("No libraries found.")
            return
        for lib in data:
            click.echo(f"  {lib['id']}  {lib['name']}")
    _output(results, _human)


@library_group.command("create")
@click.argument("name")
@click.option("--description", default="", help="Library description")
@click.pass_context
def library_create(ctx, name, description):
    """Create a new data library."""
    client = _get_client(ctx)
    result = library_mod.create_library(client, name, description=description)
    _output(result, lambda d: click.echo(f"Created library: {d['name']} ({d['id']})"))


@library_group.command("show")
@click.argument("library_id")
@click.pass_context
def library_show(ctx, library_id):
    """Show library details."""
    client = _get_client(ctx)
    result = library_mod.show_library(client, library_id)
    _output(result, lambda d: click.echo(
        f"Library: {d['name']} ({d['id']})\n  {d['description']}"
    ))


@library_group.command("contents")
@click.argument("library_id")
@click.pass_context
def library_contents(ctx, library_id):
    """List contents of a library."""
    client = _get_client(ctx)
    results = library_mod.list_library_contents(client, library_id)
    def _human(data):
        for item in data:
            click.echo(f"  {item['id']}  [{item['type']}]  {item['name']}")
    _output(results, _human)


@library_group.command("delete")
@click.argument("library_id")
@click.pass_context
def library_delete(ctx, library_id):
    """Delete a library."""
    client = _get_client(ctx)
    result = library_mod.delete_library(client, library_id)
    _output(result, lambda d: click.echo(f"Deleted library: {d['id']}"))


# ── User Commands ────────────────────────────────────────────────────────

@cli.group("user")
def user_group():
    """User information and API key management."""


@user_group.command("whoami")
@click.option("--full", is_flag=True, help="Include full identity fields such as email.")
@click.option("--show-email", is_flag=True, help="Include the account email in output.")
@click.pass_context
def user_whoami(ctx, full, show_email):
    """Show current user info."""
    client = _get_client(ctx)
    result = client.whoami()
    include_email = full or show_email
    username = result.get("username", "")
    email = result.get("email", "")
    if not include_email and "@" in username:
        username = _mask_email(username)
    info = {
        "id": result.get("id", ""),
        "username": username,
        "is_admin": result.get("is_admin", False),
        "total_disk_usage": result.get("total_disk_usage", 0),
        "nice_total_disk_usage": result.get("nice_total_disk_usage", ""),
    }
    if include_email:
        info["email"] = email
    elif email:
        info["email_redacted"] = True

    def _human(d):
        lines = [f"User: {d['username']}"]
        if include_email:
            lines.append(f"Email: {d.get('email', '')}")
        lines.extend([
            f"Admin: {d['is_admin']}",
            f"Disk usage: {d['nice_total_disk_usage']}",
        ])
        click.echo("\n".join(lines))

    _output(info, _human)


# ── Skill Commands ───────────────────────────────────────────────────────

@cli.group("skill")
def skill_group():
    """Install or inspect the bundled AI agent skill."""


@skill_group.command("path")
def skill_path():
    """Show the packaged SKILL.md path."""
    result = skill_mod.skill_info()
    _output(result, lambda d: click.echo(d["path"]))


@skill_group.command("show")
def skill_show():
    """Print the bundled SKILL.md content."""
    result = {
        **skill_mod.skill_info(),
        "content": skill_mod.read_skill(),
    }
    _output(result, lambda d: click.echo(d["content"], nl=False))


@skill_group.command("install")
@click.option(
    "--agent",
    type=click.Choice(skill_mod.SUPPORTED_AGENTS),
    default="codex",
    show_default=True,
    help="Agent skill directory convention to use.",
)
@click.option(
    "--target-dir",
    type=click.Path(file_okay=False, dir_okay=True),
    default=None,
    help="Override the skills directory. Installs into TARGET_DIR/galaxy-cli/SKILL.md.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing different skill file.")
def skill_install(agent, target_dir, force):
    """Install the bundled skill for Codex or Claude Code."""
    result = skill_mod.install_skill(agent=agent, target_dir=target_dir, force=force)
    _output(result, lambda d: click.echo(f"{d['status']}: {d['destination']}"))


# ── Session Commands ─────────────────────────────────────────────────────

@cli.group("session")
def session_group():
    """Manage local CLI session state."""


@session_group.command("show")
def session_show():
    """Show current session state."""
    result = session_mod.show_session()
    _output(result)


@session_group.command("clear")
def session_clear():
    """Clear session state."""
    result = session_mod.clear_session()
    _output(result, lambda d: click.echo("Session cleared."))


# ── REPL ─────────────────────────────────────────────────────────────────

@cli.command("repl", hidden=True)
@click.pass_context
def repl(ctx):
    """Enter interactive REPL mode."""
    from galaxy_cli.utils.repl_skin import ReplSkin

    skin = ReplSkin("galaxy", version=__version__)
    skin.print_banner()

    pt_session = skin.create_prompt_session()

    commands_help = {
        "config":     "Manage server connection (set-url, set-key, show, test)",
        "profile":    "Manage multiple Galaxy profiles (add, list, show, use, remove)",
        "history":    "Manage histories (list, create, show, delete, use, export)",
        "dataset":    "Manage datasets (list, upload, show, download, peek, delete)",
        "collection": "Manage dataset collections (create, list, show)",
        "udt":        "Manage user-defined tools (list, show, create, delete, run)",
        "tool":       "Manage/run tools (list, search, show, run)",
        "job":        "Manage jobs (list, show, cancel, wait)",
        "workflow":   "Manage workflows (list, show, import, export, run, delete)",
        "invocation": "Manage invocations (list, show, cancel, wait)",
        "library":    "Manage libraries (list, create, show, contents, delete)",
        "user":       "User info (whoami)",
        "skill":      "Install/inspect bundled AI agent skill",
        "session":    "Local session state (show, clear)",
        "help":       "Show this help",
        "quit":       "Exit the REPL",
    }

    while True:
        try:
            sess = session_mod.load_session()
            hist_name = sess.get("current_history_name", "")
            line = skin.get_input(pt_session, project_name=hist_name or "")
        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break

        if not line:
            continue

        cmd = line.strip().lower()
        if cmd in ("quit", "exit", "q"):
            skin.print_goodbye()
            break
        if cmd in ("help", "?"):
            skin.help(commands_help)
            continue

        try:
            args = shlex.split(line)
        except ValueError as exc:
            skin.error(f"Parse error: {exc}")
            continue

        args = _normalize_repl_args(args, ctx.obj.get("json_mode", False))

        try:
            cli.main(args=args, standalone_mode=False, obj=dict(ctx.obj))
        except SystemExit:
            pass
        except click.UsageError as exc:
            skin.error(_redact_cli_text(exc, ctx.obj))
        except GalaxyBackendError as exc:
            skin.error(_redact_cli_text(exc, ctx.obj))
        except Exception as exc:
            skin.error(f"Error: {_redact_cli_text(exc, ctx.obj)}")


# ── Entry point ──────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    root_obj = {"json_mode": _resolve_json_mode(_json_mode_from_argv(sys.argv[1:]))}
    try:
        cli.main(args=sys.argv[1:], prog_name="galaxy-cli", obj=root_obj, standalone_mode=False)
    except Exit as exc:
        sys.exit(exc.exit_code)
    except click.UsageError as exc:
        if root_obj["json_mode"]:
            click.echo(_compact_json({
                "error": True,
                "category": "usage_error",
                "message": _redact_cli_text(exc, root_obj),
            }))
        else:
            click.echo(f"Error: {_redact_cli_text(exc, root_obj)}", err=True)
        sys.exit(EXIT_USER_ERROR)
    except click.ClickException as exc:
        if root_obj["json_mode"]:
            click.echo(_compact_json({
                "error": True,
                "category": "click_error",
                "message": _redact_cli_text(exc.format_message(), root_obj),
            }))
        else:
            click.echo(
                f"Error: {_redact_cli_text(exc.format_message(), root_obj)}",
                err=True,
            )
        sys.exit(getattr(exc, "exit_code", EXIT_USER_ERROR))
    except GalaxyBackendError as exc:
        if root_obj["json_mode"]:
            payload = _redact_cli_value(exc.to_dict(), root_obj)
            click.echo(_compact_json(payload))
        else:
            msg = _redact_cli_text(exc, root_obj)
            if exc.suggestion:
                msg += f"\n  Suggestion: {_redact_cli_text(exc.suggestion, root_obj)}"
            click.echo(f"Error: {msg}", err=True)
        sys.exit(exc.exit_code)


if __name__ == "__main__":
    main()
