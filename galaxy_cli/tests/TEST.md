# Galaxy CLI Test Strategy

The default suite is deterministic and uses mocked Galaxy responses. Live
tests are opt-in and run only when a reachable server and credentials are
already available in the environment.

## Required Coverage

| Area | Required behavior |
| --- | --- |
| Shared job wait | One job, multiple jobs, one global deadline, mixed success and failure, structured timeout with `EXIT_TIMEOUT` |
| Regular tool execution | Strict and legacy backends, all spawned jobs, authoritative final outputs, single-job compatibility fields |
| Safe fallback | Auto fallback only for an unsupported initial strict endpoint; no fallback after 400, 422, timeout, transport failure, 5xx, or uncertain submission |
| Nested inputs | Repeat, conditional, multiple dataset, and collection references remain nested for strict execution and retain legacy flattening when requested |
| Tool requests | Request ID capture, state polling, detail retrieval, spawned jobs, and implicit collection outputs |
| UDT execution | Existing UDT commands, multiple jobs, failures, timeout exit code, collection outputs, and debug-only evidence compatibility |
| History copy | Immediate readiness, delayed datasets and collections, content failure, one-deadline timeout, and `--no-wait` compatibility |
| Output normalization | Dataset and collection names, IDs, sources, states, types, sizes, and element counts |
| Bounded preview | Only the named dataset output is previewed; line count is bounded; collections return an unsupported result without expansion |
| Tool template cache | URL, server version, exact tool ID, and tool version isolation; refresh, bypass, and corrupt-cache recovery |
| Structured validation | JSON path, expected type, allowed values, and a short correction example without returning a full schema |
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
python3 -m pytest galaxy_cli/tests -q
```

For focused development:

```bash
python3 -m pytest galaxy_cli/tests/test_core.py -q
python3 -m pytest galaxy_cli/tests/test_cli.py -q
python3 -m pytest galaxy_cli/tests/test_udt.py -q
```

Do not record a test count or timing here. Report the actual command, pass/fail
summary, and skipped live tests in the release or change handoff after the full
suite finishes.

## Opt-In Live Tests

Live tests remain skipped unless `GALAXY_URL` and either `GALAXY_API_KEY` or
`GALAXY_API_KEY_FILE` are already set. Run them only with credentials intended
for testing:

```bash
python3 -m pytest galaxy_cli/tests/test_full_e2e.py -q
```

UDT lifecycle coverage also requires explicit opt-in:

```bash
GALAXY_CLI_TEST_UDT=1 python3 -m pytest galaxy_cli/tests/test_full_e2e.py -q
```

Never write credentials into a fixture, command transcript, failure snapshot,
or documentation example.
