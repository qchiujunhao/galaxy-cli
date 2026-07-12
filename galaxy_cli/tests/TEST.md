# Galaxy CLI Test Strategy

The default suite is deterministic and uses mocked Galaxy responses. Live
tests are opt-in and run only when a reachable server and credentials are
already available in the environment.

## Required Coverage

| Area | Required behavior |
| --- | --- |
| Shared job wait | Adaptive `5/10/20/30` policy, fixed override, one/multiple jobs, one absolute deadline, mixed success/failure, fake-clock timeout with `EXIT_TIMEOUT` |
| Regular tool execution | Strict and legacy backends, all spawned jobs, authoritative final outputs, single-job compatibility fields |
| Safe fallback | Auto fallback only for an unsupported initial strict endpoint; no fallback after 400, 422, timeout, transport failure, 5xx, or uncertain submission |
| Nested inputs | Repeat, conditional, multiple dataset, and collection references remain nested for strict execution and retain legacy flattening when requested |
| Tool requests | Request ID capture, state polling, detail retrieval, spawned jobs, and implicit collection outputs |
| UDT execution | Existing UDT commands, multiple jobs, failures, timeout exit code, collection outputs, and debug-only evidence compatibility |
| History copy | Immediate readiness, delayed datasets and collections, content failure, one-deadline timeout, and `--no-wait` compatibility |
| Output normalization | Dataset and collection names, IDs, sources, states, types, sizes, and element counts |
| Bounded preview | Head/tail/grep/context/fields hard bounds, streaming small-file scans and cleanup; collections require one exact element path |
| Metadata cache | URL/version/tool isolation, refresh/bypass, hit counters, expiration, corruption, atomic concurrent writes, secret-free stats, clear, and warm |
| Discovery | History filters, tool templates/examples/preflight, read-only capabilities, and search I/O/version filters |
| Diagnostics | Bounded tail/grep context, truncation metadata, and explicit full-log recovery |
| Collections | Recursive list/pair flattening, stable paths, limits, cycles, and depth protection |
| Operations | Secret-free atomic receipts, authoritative tool/UDT/workflow/upload resume, request/output discovery, unknown handling, TUS fetch latch/source identity/external trust, and no ordinary POST replay |
| Workflows and uploads | Workflow templates and all-job waits; TUS selection, safe fallback, interruption, and legacy compatibility |
| Structured validation | JSON path, expected type, allowed values, and a short correction example without returning a full schema |
| Agent UX | Unified `@file`/inline/stdin inputs, history/command aliases, canonical identity, bounded structured help, and typo suggestions that never execute |
| Output envelope | Opt-in stable top level, default 1.5-compatible payload, shell-safe next commands, and mechanical agent-mode defaults |
| Output discipline | Compact one-line JSON on stdout and progress only on stderr |
| Secret safety | API keys and supplied secrets never appear in output, exceptions, cache files, fixtures, or recorded snapshots |
| Compatibility | Existing commands remain available, nested references still work, legacy execution remains selectable, and single-job `wait_result` remains additive |

## Trace-Derived Fixtures

The suite keeps anonymized fixtures for these response shapes:

- A regular tool with nested repeat and conditional inputs, including multiple
  datasets and a collection reference.
- A collection-mapped tool that creates multiple jobs and implicit collection
  outputs.
- A UDT create-run operation that creates multiple jobs or a collection
  output.

Fixtures must contain only the minimum fields needed by a test. They must not
contain API keys, user identities, local paths, benchmark answers, or
scientific-method recipes.

## Run the Mocked Suite

```bash
uv sync --group dev
.venv/bin/python -m pytest galaxy_cli/tests -q
```

For focused development:

```bash
.venv/bin/python -m pytest galaxy_cli/tests/test_core.py -q
.venv/bin/python -m pytest galaxy_cli/tests/test_cli.py -q
.venv/bin/python -m pytest galaxy_cli/tests/test_udt.py -q
```

Do not record a test count or timing here. Report the actual command, pass/fail
summary, and skipped live tests in the release or change handoff after the full
suite finishes.

## Opt-In Live Tests

Live tests remain skipped unless the run is explicitly enabled. Credentials
alone never activate them. Select exactly one target:

```bash
export GALAXY_CLI_LIVE=1
export GALAXY_CLI_LIVE_TARGET=usegalaxy
python3 -m pytest galaxy_cli/tests/test_live_compatibility.py -m 'live and usegalaxy' -q
```

For a local/private server:

```bash
export GALAXY_CLI_LIVE=1
export GALAXY_CLI_LIVE_TARGET=local
python3 -m pytest galaxy_cli/tests/test_live_compatibility.py -m 'live and local_galaxy' -q
```

The base live suite covers version, authentication, capabilities, history
copy/readiness/delete, and tiny auto/legacy uploads. Set only the fixtures for
the additional capabilities available on the selected server:

- `GALAXY_CLI_LIVE_TOOL_ID` and `GALAXY_CLI_LIVE_TOOL_INPUTS`
- `GALAXY_CLI_LIVE_MULTI_TOOL_ID` and `GALAXY_CLI_LIVE_MULTI_TOOL_INPUTS`
- `GALAXY_CLI_LIVE_UDT_REPRESENTATION` and `GALAXY_CLI_LIVE_UDT_INPUTS`
- `GALAXY_CLI_LIVE_WORKFLOW_ID` and `GALAXY_CLI_LIVE_WORKFLOW_INPUTS`
- `GALAXY_CLI_LIVE_COLLECTION_ID` and `GALAXY_CLI_LIVE_COLLECTION_ELEMENT`
- `GALAXY_CLI_LIVE_FAILED_JOB_ID`

Input variables contain JSON objects; the UDT representation variable names a
small test file. Missing optional fixtures skip only their capability. After
explicit opt-in, connection, authentication, assertion, and cleanup failures
remain visible rather than being swallowed by a broad exception handler.

Never write credentials into a fixture, command transcript, failure snapshot,
or documentation example.
