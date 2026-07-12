# galaxy-cli

`galaxy-cli` is a compact, machine-readable command-line client for a
running [Galaxy](https://galaxyproject.org/) server. It manages histories,
datasets, collections, regular tools, user-defined tools (UDTs), workflows,
jobs, and safe recovery of interrupted operations.

The runtime supports Python 3.9–3.13. Server-backed commands require a
reachable Galaxy server and API key.

## Install

```bash
uv tool install galaxy-cli
galaxy-cli --version
```

Alternatively:

```bash
python3 -m pip install galaxy-cli
```

## Configure access

```bash
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY_FILE=secrets/galaxy-api-key
galaxy-cli config test
```

`GALAXY_API_KEY` is also supported. Do not put a key in a command, input
file, receipt, cache entry, or log. Configuration output masks credentials and
the CLI redacts configured keys from stdout, stderr, output files, and errors.

## Canonical happy path

```bash
galaxy-cli history copy SEED_HISTORY_ID --name analysis
galaxy-cli history find HISTORY_ID --exact-name input.tsv
galaxy-cli tool find "tool name"
galaxy-cli tool inputs TOOL_ID
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs @inputs.json
galaxy-cli dataset preview OUTPUT_ID --lines 5
```

The natural-language names above are aliases. Machine-readable command
identity is always normalized to the canonical command:

| Alias | Canonical command |
| --- | --- |
| `tool find` | `tool search` |
| `tool inputs`, `tool schema` | `tool template` |
| `dataset preview`, `dataset head` | `dataset peek` |
| `history ls` | `history list` |
| `history find` | `history resolve` |
| `collection get` | `collection resolve` |
| `job debug` | `job diagnose` |

All v1.5 canonical names remain valid.

## Short structured help

Ask for only the contract needed for one command:

```bash
galaxy-cli help tool.run --json
galaxy-cli help dataset.upload --json
galaxy-cli help operation.resume --json
```

This bounded response contains the canonical command, shortest usage,
required values, mechanical defaults, and safety rules. Use `tool template`
only when the exact tool input contract is not already known.

## Unified JSON inputs

The recommended form is `--inputs @file`:

```bash
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs @inputs.json
galaxy-cli tool validate TOOL_ID --history HISTORY_ID --inputs @inputs.json
galaxy-cli udt run UUID --history HISTORY_ID --inputs @inputs.json
```

Inline JSON and stdin are also accepted:

```bash
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs '{"input":{"src":"hda","id":"DATASET_ID"}}'
printf '%s\n' '{"input":{"src":"hda","id":"DATASET_ID"}}' |
  galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs -
```

The existing `--inputs-json FILE` flag remains supported. Do not combine it
with `--inputs`. Inputs must decode to a JSON object; errors never echo the
inline payload. Existing `-i key=value` flags still work and override loaded
JSON keys for `tool run`.

`--history` is an alias for `--history-id`.

## Blocking results and safe recovery

Tool, UDT, workflow, and upload commands wait by default. One global deadline
covers request expansion, every spawned job, and final output metadata.
Successful results include final job states and compact dataset/collection
metadata, so routine follow-up status calls are unnecessary.

Polling is adaptive by default:

```text
5s, 10s, 20s, 30s, 30s, ...
```

An explicit `--poll-interval N` selects the compatible fixed interval.

Mutating operations create secret-free receipts. If a request is interrupted:

```bash
galaxy-cli operation resume RECEIPT_ID
```

Resume discovers known requests, jobs, and outputs, waits against one deadline,
refreshes final metadata, and never replays an ordinary submission POST. If
`submission_state` is `unknown` or `retry_safe` is false, do not resubmit.
TUS recovery serializes its one permitted fetch submission with a durable
per-receipt lock and rejects a changed local file by size and SHA-256 identity.
For a very large source, interruption recovery may require a full file read to
finish or revalidate SHA-256; progress is written only to stderr and Ctrl-C
cancels the scan.
An external receipt path cannot authorize TUS mutation. Use the returned
`next_commands` in envelope/agent mode.

## Bounded previews

Head previews use Galaxy's bounded raw-data provider:

```bash
galaxy-cli dataset preview DATASET_ID --head 10
galaxy-cli dataset preview DATASET_ID --head 10 --fields 1,3,5 --delimiter tab
```

`--tail` and `--grep` may scan a temporary local copy only when Galaxy
reports that the dataset is within the hard 5 MiB preview threshold:

```bash
galaxy-cli dataset preview DATASET_ID --tail 10
galaxy-cli dataset preview DATASET_ID --grep 'error|failed' --context 2
```

Oversized or size-unknown datasets are refused with a suggestion to use an
explicit `dataset download`. Temporary scan files live in the private CLI
cache area (or `GALAXY_CLI_PREVIEW_TMPDIR`), are scanned with bounded streaming
selectors, and are removed in a `finally` path.

Collections require an exact element path:

```bash
galaxy-cli collection preview COLLECTION_ID --element sample1/report --lines 5
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs @inputs.json \
  --peek-output results --peek-element sample1/report --peek-lines 5
```

Resolution retains cycle, depth, ambiguity, and result-count guards. The CLI
never expands an entire collection or guesses an element.

## Stable opt-in output

Default 1.x output remains compact single-line JSON without a new envelope.
Opt in when a stable top-level contract is useful:

```bash
galaxy-cli --envelope tool run TOOL_ID --history HISTORY_ID --inputs @inputs.json
export GALAXY_CLI_OUTPUT=envelope-v1
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs @inputs.json
```

Envelope v1 has these stable fields:

```json
{
  "schema_version": "1.0",
  "command": "tool.run",
  "success": true,
  "data": {},
  "warnings": [],
  "next_commands": {}
}
```

`--agent` enables envelope v1, compact JSON, blocking defaults, bounded
lists/strings, a 128 KiB serialized-stdout budget, a 1000-node traversal
budget, stderr-only progress, actionable errors, and safe mechanical next
commands. Budget exhaustion is reported in `warnings`. Agent mode never
selects a Galaxy tool, changes scientific parameters, rewrites inputs, chooses
an output, or executes a next command.

`--output-file PATH` atomically writes the complete redacted success or error
JSON result to the file. Stdout contains only a bounded summary; known API keys
are redacted from both the file and the echoed path.

The packaged schema is `galaxy_cli/schemas/envelope-v1.json`.

## Cache observability

Stable read-only server/tool metadata is cached automatically:

```bash
galaxy-cli cache stats
galaxy-cli cache clear --namespace tool-schema
galaxy-cli cache warm --server --tools
```

`stats` reports namespaces, counts, bytes, fresh/stale/corrupt entries,
process-local hit counters, TTL, and hashed server identities. It never returns
cached keys or schema bodies. Stored server identities exclude URL credentials,
queries, and fragments. Histories, jobs, datasets, operation outputs,
UDT results, and scientific data are never warmed.

Set `GALAXY_CLI_CACHE_DIR` in the launcher to isolate concurrent processes,
tasks, or users. Agents normally should not clear or warm caches themselves.

## Diagnostics

Errors preserve non-zero exit codes and add mechanical fields such as JSON
path, expected type, bounded allowed values, correction, and
`did_you_mean`. Suggestions are never executed.

```bash
galaxy-cli job diagnose JOB_ID
galaxy-cli job logs JOB_ID --tail 100 --grep error --context 2
```

Full execution, safety, cache, and recovery semantics are documented in
[GALAXY.md](GALAXY.md). Development and live-test instructions are in
[galaxy_cli/tests/TEST.md](galaxy_cli/tests/TEST.md).

## Development

```bash
uv sync --group dev
.venv/bin/python -m pytest galaxy_cli/tests -q
.venv/bin/ruff check galaxy_cli
.venv/bin/python -m build
```

Live compatibility tests are explicitly opt-in and use separate
`usegalaxy`/local markers. They never run merely because credentials happen
to exist.
