# galaxy-cli — Galaxy bioinformatics command-line client

`galaxy-cli` is a Python CLI and REPL for operating the
[Galaxy](https://galaxyproject.org/) bioinformatics platform. It provides
compact, machine-readable commands for histories, datasets, collections,
regular tools, user-defined tools (UDTs), workflows, jobs, and libraries.

The client is designed for both people and automation. Blocking commands return
enough final job and output metadata for a caller to trust one invocation
without repeating status and contents queries.

## Requirements

- Python 3.9 or newer
- A reachable Galaxy server
- A Galaxy API key for that server

The runtime remains based on Click and requests and does not require BioBlend,
Node, or an MCP client.

## Install

Install from PyPI:

```bash
uv tool install galaxy-cli
```

or:

```bash
python3 -m pip install galaxy-cli
```

For local development from this repository:

```bash
python3 -m pip install .
```

Verify the command:

```bash
galaxy-cli --version
galaxy-cli --help
```

## Configure Galaxy Access

Set the server URL and provide the key directly or through a secret file:

```bash
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key
galaxy-cli config test
```

```bash
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY_FILE=secrets/galaxy-api-key
galaxy-cli config test
```

An explicit `--api-key` takes precedence over `GALAXY_API_KEY`, which takes
precedence over `GALAXY_API_KEY_FILE`. Configuration output masks credentials,
and structured errors redact known secrets.

Session state is stored in a per-user application directory. In concurrent or
agent-driven work, pass `--history-id` explicitly instead of relying on shared
session state.

## Reliable Blocking Results

`tool run`, `udt run`, `udt create-run`, and `dataset upload` wait by default.
`history copy` also waits by default in 1.5.0. All spawned jobs share one
deadline; the timeout is not restarted for each job.

A successful blocking `tool run` reports:

- `success: true` and `state: ok`
- the selected `execution_backend`
- `history_id`, `tool_id`, and `tool_version`
- every job ID, final state, and exit code
- every dataset output name, ID, source, state, extension, and file size
- every collection output name, ID, source, state, collection type, and element
  count

For a single job, the existing `wait_result` remains available and the uniform
`wait_results` list is also returned. Callers should trust this successful
result rather than immediately repeating `job show`, `dataset show`,
`collection show`, or history-contents requests.

If any job fails or remains non-terminal at the deadline, the command exits
non-zero. Timeout errors use the dedicated timeout exit code. Structured errors
include the known request, job, output, history, and tool context together with
`submission_state` and `retry_safe`.

Never automatically retry a mutating command when `submission_state` is
`unknown` or `retry_safe` is false. A timeout, lost connection, server error, or
malformed success response can occur after Galaxy accepted a submission.

Use `--no-wait` only when an immediate, non-authoritative submission result is
intentional.

## Regular Tool Execution Backends

`tool run` accepts:

```text
--execution-backend auto|strict|legacy
```

The default `auto` mode prefers strict nested execution through `/api/jobs`.
It submits native nested inputs, tracks the returned tool request, discovers
all spawned jobs and implicit collections, waits for every job, and normalizes
the final outputs.

Auto mode falls back to legacy `/api/tools` execution only when the initial
strict endpoint explicitly returns HTTP 404 or 405. It does not fall back after
400 or 422 validation errors, timeouts, connection failures, 5xx responses, an
unknown submission state, or any failure after a strict request was accepted.
This rule prevents accidental duplicate submissions.

Use `legacy` for a server known not to support strict execution. Legacy mode
retains the existing pipe-key input flattening behavior.

Inspect the selected backend and exact request without submitting:

```bash
galaxy-cli tool run TOOL_ID \
  --history-id HISTORY_ID \
  --inputs-json tool-inputs.json \
  --dry-run-payload
```

Nested dataset and collection references use Galaxy's native objects:

```json
{
  "dataset_input": {"src": "hda", "id": "DATASET_ID"},
  "collection_input": {"src": "hdca", "id": "COLLECTION_ID"}
}
```

Repeats, conditionals, and multiple-data inputs can remain nested in the input
file. Run `galaxy-cli tool run --help` for the current command options.

## History Copy Readiness

`history copy` waits for copied datasets and collections to leave transient
states such as `new`, `queued`, `running`, and `setting_metadata`. All copied
contents share one deadline. A content failure or timeout produces a non-zero
structured error.

```bash
galaxy-cli history copy SOURCE_HISTORY_ID "working copy" \
  --timeout 1800 \
  --poll-interval 10
```

The successful result contains a compact `contents` map with history content
IDs, HIDs, sources, names, states, and dataset or collection type metadata. It
does not emit the complete history response.

Use `--no-wait` to preserve the immediate-return behavior from 1.4.1.

## Reduce Follow-Up Calls

Preview one explicitly named dataset output as part of a successful tool run:

```bash
galaxy-cli tool run TOOL_ID \
  --history-id HISTORY_ID \
  --inputs-json tool-inputs.json \
  --peek-output report \
  --peek-lines 5
```

No output is previewed or downloaded by default. Collection outputs are not
expanded automatically; requesting one returns a clear unsupported result.

Compact `tool show` input templates are cached by Galaxy URL, server version,
exact tool ID, and tool version. Refresh or bypass the cache when needed:

```bash
galaxy-cli tool show TOOL_ID --refresh-cache
galaxy-cli tool show TOOL_ID --no-cache
```

Validation failures stay compact. They identify the failing JSON path,
expected type or allowed values, and a short correction example rather than
printing the complete tool schema.

## User-Defined Tools

The UDT command surface remains stable:

```text
udt list
udt show
udt create
udt delete
udt run
udt create-run
```

Create and run a new UDT in one blocking command:

```bash
galaxy-cli udt create-run \
  --representation-json udt.json \
  --history-id HISTORY_ID \
  --inputs-json udt-inputs.json
```

UDTs use the same all-job wait and output normalization behavior as regular
blocking execution. A successful result needs no routine job, dataset, or
history verification.

`--evidence-dir` remains available for explicit debugging and compatibility
with 1.4.1. It is not required for normal execution and should not be enabled by
default.

## Output Modes

Compact, single-line JSON is the default:

```bash
galaxy-cli history list | jq .
galaxy-cli tool run TOOL_ID --history-id HISTORY_ID --inputs-json inputs.json
```

Progress is written to stderr so stdout remains one JSON value. Use `--human`
when interactive prose is preferable.

## Agent Skill

The package includes a short decision-rule skill for Codex and Claude Code:

```bash
galaxy-cli skill install --agent codex
galaxy-cli skill install --agent claude
```

Use `galaxy-cli skill path` to locate the packaged source. A relative custom
destination is also supported:

```bash
galaxy-cli skill install --target-dir project-skills
```

The skill directs agents to command help, authoritative blocking results, and
safe retry rules. It intentionally does not contain a full CLI tutorial,
benchmark recipes, scientific-method selection, or answer-extraction rules.

## Tests and Releases

Run the mocked suite:

```bash
python3 -m pytest galaxy_cli/tests -q
```

Live Galaxy tests are opt-in and run only when test credentials are already
available in the environment. Releases are built from Git tags through GitHub
Actions and PyPI Trusted Publishing; see [RELEASE.md](RELEASE.md).

Documentation site: <https://qchiujunhao.github.io/galaxy-cli/>
