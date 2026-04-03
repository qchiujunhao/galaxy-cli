"""galaxy-cli: CLI harness for Galaxy bioinformatics platform.

Provides stateful CLI and REPL access to a running Galaxy server via its REST API.
"""

import json
import sys
import shlex

import click

from cli_anything.galaxy import __version__
from cli_anything.galaxy.utils.galaxy_backend import GalaxyClient, GalaxyBackendError
from cli_anything.galaxy.core import (
    config as config_mod,
    history as history_mod,
    dataset as dataset_mod,
    tool as tool_mod,
    job as job_mod,
    workflow as workflow_mod,
    invocation as invocation_mod,
    library as library_mod,
    session as session_mod,
)

# ── Helpers ──────────────────────────────────────────────────────────────

_JSON_MODE = False


def _output(data, human_func=None):
    """Output data as JSON or human-readable."""
    if _JSON_MODE:
        click.echo(json.dumps(data, indent=2, default=str))
    elif human_func:
        human_func(data)
    else:
        click.echo(json.dumps(data, indent=2, default=str))


def _get_client(ctx):
    """Get or create a GalaxyClient from context."""
    if ctx.obj and ctx.obj.get("client"):
        return ctx.obj["client"]
    url = ctx.obj.get("url") if ctx.obj else None
    api_key = ctx.obj.get("api_key") if ctx.obj else None
    return GalaxyClient(url=url, api_key=api_key)


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


# ── Main CLI Group ───────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.option("--url", envvar="GALAXY_URL", help="Galaxy server URL")
@click.option("--api-key", envvar="GALAXY_API_KEY", help="Galaxy API key")
@click.option("--history-id", default=None, help="Override current history ID")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON for machine parsing")
@click.version_option(__version__, prog_name="galaxy-cli")
@click.pass_context
def cli(ctx, url, api_key, history_id, json_mode):
    """CLI harness for Galaxy bioinformatics platform.

    Connect to a running Galaxy server and manage histories, datasets,
    tools, workflows, and jobs from the command line.
    """
    global _JSON_MODE
    _JSON_MODE = json_mode
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key
    ctx.obj["history_id"] = history_id
    # Lazily create client — only when a subcommand needs it
    ctx.obj["client"] = None
    if url or api_key:
        try:
            ctx.obj["client"] = GalaxyClient(url=url, api_key=api_key)
        except GalaxyBackendError:
            pass  # Will fail when command actually needs the client

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ── Config Commands ──────────────────────────────────────────────────────

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
    """Show current configuration."""
    result = config_mod.show_config()
    _output(result, lambda d: click.echo(f"URL: {d['url']}\nAPI Key: {d['api_key']}"))


@config_group.command("test")
@click.pass_context
def config_test(ctx):
    """Test connection to Galaxy server."""
    client = _get_client(ctx)
    result = config_mod.test_connection(client)
    _output(result, lambda d: click.echo(
        f"Connected to Galaxy {d['galaxy_version']}\n"
        f"User: {d['user']} ({d['email']})"
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
    """Create a new history."""
    client = _get_client(ctx)
    result = history_mod.create_history(client, name=name)
    session_mod.set_current_history(result["id"], result["name"])
    _output(result, lambda d: click.echo(f"Created history: {d['name']} ({d['id']})"))


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
@click.option("--dbkey", default="?", help="Genome build")
@click.pass_context
def dataset_upload(ctx, file_path, history_id, file_type, dbkey):
    """Upload a local file to a Galaxy history."""
    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)
    result = dataset_mod.upload_dataset(client, hid, file_path, file_type=file_type, dbkey=dbkey)
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
@click.pass_context
def dataset_download(ctx, dataset_id, output_path):
    """Download a dataset to a local file."""
    client = _get_client(ctx)
    result = dataset_mod.download_dataset(client, dataset_id, output_path)
    _output(result, lambda d: click.echo(f"Downloaded to: {d['output']} ({d['size']:,} bytes)"))


@dataset_group.command("peek")
@click.argument("dataset_id")
@click.option("--lines", default=10, help="Number of lines to preview")
@click.pass_context
def dataset_peek(ctx, dataset_id, lines):
    """Preview the first few lines of a dataset."""
    client = _get_client(ctx)
    result = dataset_mod.peek_dataset(client, dataset_id, lines=lines)
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
    results = tool_mod.list_tools(client, query=query)[:limit]
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
@click.pass_context
def tool_search(ctx, query):
    """Search for tools by name or description."""
    client = _get_client(ctx)
    results = tool_mod.search_tools(client, query)
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
@click.pass_context
def tool_show(ctx, tool_id):
    """Show detailed tool information including inputs/outputs."""
    client = _get_client(ctx)
    result = tool_mod.show_tool(client, tool_id)
    def _human(d):
        click.echo(f"Tool: {d['name']} ({d['id']}) v{d['version']}")
        click.echo(f"  {d['description']}")
        if d["inputs"]:
            click.echo("  Inputs:")
            for inp in d["inputs"]:
                opt = " (optional)" if inp["optional"] else ""
                click.echo(f"    {inp['name']} [{inp['type']}]{opt}: {inp['label']}")
        if d["outputs"]:
            click.echo("  Outputs:")
            for out in d["outputs"]:
                click.echo(f"    {out['name']} [{out['format']}]: {out['label']}")
    _output(result, _human)


@tool_group.command("run")
@click.argument("tool_id")
@click.option("--history-id", default=None)
@click.option("--input", "-i", "inputs", multiple=True, help="Tool input as key=value")
@click.option("--wait", is_flag=True, help="Wait for job to complete")
@click.pass_context
def tool_run(ctx, tool_id, history_id, inputs, wait):
    """Run a tool with given inputs."""
    client = _get_client(ctx)
    hid = history_id or _require_history(ctx)
    input_dict = {}
    for inp in inputs:
        if "=" not in inp:
            raise click.UsageError(f"Invalid input format: {inp}. Use key=value")
        k, v = inp.split("=", 1)
        input_dict[k] = v
    result = tool_mod.run_tool(client, tool_id, hid, inputs=input_dict)
    if result.get("jobs"):
        job_id = result["jobs"][0]["id"]
        session_mod.track_job(job_id)
        if wait:
            click.echo(f"Waiting for job {job_id}...")
            wait_result = job_mod.wait_for_job(client, job_id)
            result["wait_result"] = wait_result
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
@click.option("--full", is_flag=True, help="Show full details including I/O")
@click.pass_context
def job_show(ctx, job_id, full):
    """Show job details."""
    client = _get_client(ctx)
    result = job_mod.show_job(client, job_id, full=full)
    def _human(d):
        click.echo(f"Job: {d['id']}")
        click.echo(f"  Tool: {d['tool_id']}")
        click.echo(f"  State: {d['state']}")
        click.echo(f"  Exit code: {d['exit_code']}")
        click.echo(f"  Created: {d['create_time']}")
        if d.get("stdout"):
            click.echo(f"  Stdout: {d['stdout'][:500]}")
        if d.get("stderr"):
            click.echo(f"  Stderr: {d['stderr'][:500]}")
    _output(result, _human)


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
@click.pass_context
def job_wait(ctx, job_id, timeout):
    """Wait for a job to complete."""
    client = _get_client(ctx)
    click.echo(f"Waiting for job {job_id}...")
    result = job_mod.wait_for_job(client, job_id, max_wait=timeout)
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
        if d["inputs"]:
            click.echo("  Inputs:")
            for k, v in d["inputs"].items():
                click.echo(f"    [{k}] {v['label']}")
        if d["steps"]:
            click.echo("  Steps:")
            for sid, step in d["steps"].items():
                tool = step["tool_id"] or step["type"]
                click.echo(f"    [{sid}] {step['label'] or tool}")
    _output(result, _human)


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
@click.option("--wait", is_flag=True, help="Wait for invocation to complete")
@click.pass_context
def workflow_run(ctx, workflow_id, history_id, new_history, inputs, wait):
    """Run a workflow."""
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
    result = workflow_mod.run_workflow(
        client, workflow_id, history_id=hid,
        inputs=input_dict if input_dict else None,
        new_history_name=new_history,
    )
    if wait and result.get("id"):
        click.echo(f"Waiting for invocation {result['id']}...")
        wait_result = invocation_mod.wait_for_invocation(client, result["id"])
        result["wait_result"] = wait_result
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
@click.option("--timeout", default=1800, help="Max wait time in seconds")
@click.pass_context
def invocation_wait(ctx, invocation_id, timeout):
    """Wait for an invocation to complete."""
    client = _get_client(ctx)
    click.echo(f"Waiting for invocation {invocation_id}...")
    result = invocation_mod.wait_for_invocation(client, invocation_id, max_wait=timeout)
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
@click.pass_context
def user_whoami(ctx):
    """Show current user info."""
    client = _get_client(ctx)
    result = client.whoami()
    info = {
        "id": result.get("id", ""),
        "username": result.get("username", ""),
        "email": result.get("email", ""),
        "is_admin": result.get("is_admin", False),
        "total_disk_usage": result.get("total_disk_usage", 0),
        "nice_total_disk_usage": result.get("nice_total_disk_usage", ""),
    }
    _output(info, lambda d: click.echo(
        f"User: {d['username']}\n"
        f"Email: {d['email']}\n"
        f"Admin: {d['is_admin']}\n"
        f"Disk usage: {d['nice_total_disk_usage']}"
    ))


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
    from cli_anything.galaxy.utils.repl_skin import ReplSkin

    skin = ReplSkin("galaxy", version=__version__)
    skin.print_banner()

    pt_session = skin.create_prompt_session()

    commands_help = {
        "config":     "Manage server connection (set-url, set-key, show, test)",
        "history":    "Manage histories (list, create, show, delete, use, export)",
        "dataset":    "Manage datasets (list, upload, show, download, peek, delete)",
        "tool":       "Manage/run tools (list, search, show, run)",
        "job":        "Manage jobs (list, show, cancel, wait)",
        "workflow":   "Manage workflows (list, show, import, export, run, delete)",
        "invocation": "Manage invocations (list, show, cancel, wait)",
        "library":    "Manage libraries (list, create, show, contents, delete)",
        "user":       "User info (whoami)",
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

        # Inject --json if parent context has it
        if _JSON_MODE and "--json" not in args:
            args = ["--json"] + args

        try:
            cli.main(args=args, standalone_mode=False, **{"obj": ctx.obj})
        except SystemExit:
            pass
        except click.UsageError as exc:
            skin.error(str(exc))
        except GalaxyBackendError as exc:
            skin.error(str(exc))
        except Exception as exc:
            skin.error(f"Error: {exc}")


# ── Entry point ──────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    try:
        cli(obj={})
    except GalaxyBackendError as exc:
        if _JSON_MODE:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
