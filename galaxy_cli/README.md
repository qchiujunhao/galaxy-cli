# galaxy-cli packaged quick reference

`galaxy-cli` is a Python 3.9–3.13 client for a running Galaxy server. It
returns compact JSON and uses one requests-based execution path for histories,
datasets, collections, tools, UDTs, workflows, jobs, and operation recovery.

## Install and configure

```bash
uv tool install galaxy-cli
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY_FILE=secrets/galaxy-api-key
galaxy-cli config test
```

## Happy path

```bash
galaxy-cli history copy SEED_HISTORY_ID --name analysis
galaxy-cli history find HISTORY_ID --exact-name input.tsv
galaxy-cli tool find "tool name"
galaxy-cli tool inputs TOOL_ID
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs @inputs.json
galaxy-cli dataset preview OUTPUT_ID --lines 5
```

`--history-id`, `tool search`, `tool template`, `dataset peek`,
`history resolve`, and the other canonical v1.5 names remain valid.

Get bounded, machine-readable help for one command:

```bash
galaxy-cli help tool.run --json
```

## Inputs

`--inputs` accepts `@PATH`, an inline JSON object, or `-` for stdin.
The existing `--inputs-json FILE` remains supported, but the two JSON flags
cannot be combined. `tool run -i key=value` remains compatible and overrides
loaded keys.

Dataset and collection references use:

```json
{"dataset":{"src":"hda","id":"DATASET_ID"},"collection":{"src":"hdca","id":"COLLECTION_ID"}}
```

## Execution and recovery

Blocking commands wait for all jobs against one global deadline and return
final job plus output metadata. Polling defaults to `5,10,20,30,30...`
seconds. Explicit `--poll-interval N` selects a fixed interval.

Trust successful blocking and `operation resume` results. Do not routinely
re-query final jobs or outputs. Never repeat a mutation when
`submission_state` is unknown or `retry_safe` is false.
Mutation results expose `operation_receipt` as a string receipt ID, not an
object. Pass that string directly to `operation show` or `operation resume`;
those commands return the full receipt object.
TUS resume accepts mutation only from a private local receipt and an unchanged
source file. Large-file recovery may require a complete SHA-256 read before
resume; progress is written to stderr and Ctrl-C cancels the scan.

```bash
galaxy-cli operation resume RECEIPT_ID
galaxy-cli job diagnose JOB_ID
```

## Bounded previews

```bash
galaxy-cli dataset preview DATASET_ID --head 10 --fields 1,3,5 --delimiter tab
galaxy-cli dataset preview DATASET_ID --grep error --context 2
galaxy-cli collection preview COLLECTION_ID --element sample/report --lines 5
```

Tail/grep scans download only datasets with a known size within the hard
preview threshold and always remove their private temporary file. Collections
require an exact path and are never expanded wholesale.

## Automation output

Default compact JSON remains 1.x compatible. Opt in to envelope v1 with
`--envelope`, `--agent`, or
`GALAXY_CLI_OUTPUT=envelope-v1`. Its stable fields are
`schema_version`, `command`, `success`, `data`, `warnings`, and
`next_commands`.

`--agent` changes only mechanical output/wait defaults. Stdout has a 128 KiB
serialized budget and a 1000-node traversal budget. `--output-file PATH`
atomically saves the complete redacted success or error result and leaves only
a bounded summary on stdout. Agent mode never chooses a tool, parameter,
history, analysis, or output.

## Cache

```bash
galaxy-cli cache stats
galaxy-cli cache clear --namespace tool-schema
galaxy-cli cache warm --server --tools
```

Only stable read-only metadata is cached. Stats never return cached keys or
schema bodies, and stored server identities omit URL credentials and queries.
Use `GALAXY_CLI_CACHE_DIR` to isolate tasks or users.

Full documentation is available at
[qchiujunhao.github.io/galaxy-cli](https://qchiujunhao.github.io/galaxy-cli/).
