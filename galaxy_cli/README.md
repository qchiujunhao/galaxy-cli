# galaxy-cli

`galaxy-cli` is a Python 3.9+ command-line client for the
[Galaxy](https://galaxyproject.org/) bioinformatics platform. It exposes
histories, datasets, collections, tools, user-defined tools, workflows, jobs,
and libraries through compact JSON commands suitable for people and agents.

## Install and Configure

```bash
uv tool install galaxy-cli
```

or:

```bash
python3 -m pip install galaxy-cli
```

Provide a Galaxy URL and API key:

```bash
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key
galaxy-cli config test
```

Secret-file environments can set `GALAXY_API_KEY_FILE` instead. The CLI reads
the file without printing its contents. Explicit keys and `GALAXY_API_KEY`
take precedence over the file.

Pass `--history-id` explicitly for concurrent or agent-driven work. Shared
session state is intended for one active writer.

## Authoritative Blocking Commands

Regular tools, UDTs, and uploads wait by default. History copies also wait by
default in 1.5.0. One global deadline applies to all jobs or copied contents;
the timeout is not restarted for each item.

A successful blocking tool result reports `success: true`, final state,
execution backend, history and tool identity, every job state and exit code,
and compact final metadata for all dataset and collection outputs. Callers do
not need routine follow-up `job show`, `dataset show`, `collection show`, or
history-contents calls.

Any job failure or timeout exits non-zero. Structured failures include
`submission_state` and `retry_safe`. Never automatically retry a mutation when
submission is unknown or retry safety is false.

Use `--no-wait` only for an intentionally asynchronous, non-authoritative
submission result.

## Strict and Legacy Tool Execution

`tool run` supports `--execution-backend auto|strict|legacy`. The default
`auto` mode prefers strict nested execution through `/api/jobs`, follows the
tool request, waits for all spawned jobs, and includes implicit collections in
the final outputs.

Auto mode falls back to legacy `/api/tools` only when the initial strict
endpoint explicitly returns HTTP 404 or 405. It never falls back after 400 or
422 input errors, timeouts, connection failures, 5xx errors, unknown submission
states, or failures after strict submission. Legacy mode retains pipe-key
flattening for older servers.

Use nested native references in JSON input files:

```json
{
  "input": {"src": "hda", "id": "DATASET_ID"},
  "reads": {"src": "hdca", "id": "COLLECTION_ID"}
}
```

Inspect the backend and exact request body without submitting:

```bash
galaxy-cli tool run TOOL_ID \
  --history-id HISTORY_ID \
  --inputs-json inputs.json \
  --dry-run-payload
```

## History Copy

`history copy` waits for datasets and collections to become ready and returns a
compact contents map:

```bash
galaxy-cli history copy SOURCE_HISTORY_ID "working copy" \
  --timeout 1800 \
  --poll-interval 10
```

A copied-content failure or timeout exits non-zero. `--no-wait` preserves the
immediate-return behavior from 1.4.1.

## Bounded Preview and Tool Cache

Request a bounded preview for one named dataset output:

```bash
galaxy-cli tool run TOOL_ID \
  --history-id HISTORY_ID \
  --inputs-json inputs.json \
  --peek-output report \
  --peek-lines 5
```

The CLI does not preview or download outputs by default. Collection outputs are
not expanded automatically.

Compact `tool show` templates are cached by Galaxy URL, server version, exact
tool ID, and tool version:

```bash
galaxy-cli tool show TOOL_ID --refresh-cache
galaxy-cli tool show TOOL_ID --no-cache
```

Validation errors return the failing JSON path, expected type or allowed
values, and a short correction example without emitting the full schema.

## User-Defined Tools

The existing `udt list`, `show`, `create`, `delete`, `run`, and `create-run`
commands remain available. Create and run a new UDT with:

```bash
galaxy-cli udt create-run \
  --representation-json udt.json \
  --history-id HISTORY_ID \
  --inputs-json udt-inputs.json
```

UDT execution shares the all-job wait and non-zero timeout behavior. A
successful blocking result can be trusted without routine verification calls.

`--evidence-dir` is retained only as an explicit debug and 1.4.1 compatibility
option. Normal executions do not need evidence files.

## Output and Help

Compact single-line JSON is the default, and progress goes to stderr. Use
`--human` for interactive output. Prefer command-specific help over a static
tutorial:

```bash
galaxy-cli tool run --help
galaxy-cli history copy --help
galaxy-cli udt create-run --help
```

## Agent Skill

Install the bundled decision-rule skill:

```bash
galaxy-cli skill install --agent codex
galaxy-cli skill install --agent claude
```

Use `galaxy-cli skill path` to locate the packaged source, or pass a relative
custom destination such as `--target-dir project-skills`.

## Tests

```bash
python3 -m pytest galaxy_cli/tests -q
```

Live integration tests remain opt-in and use credentials already present in
the environment.
