# Galaxy CLI Harness — Software-Specific SOP

## Purpose and boundary

`galaxy-cli` operates an existing Galaxy server through its REST API. It
does not run Galaxy tools locally and does not choose a tool, workflow,
container, statistical method, parameter value, or scientific conclusion.

Do not resubmit a mutating request when its submission state is unknown. Use
its operation receipt to resume an interrupted operation, and treat a
successful blocking result as authoritative unless explicit diagnostics are
required.

## Galaxy objects

- A **history** is a server-side workspace.
- An **HDA** is one history dataset. Native input references use
  `{"src":"hda","id":"..."}`.
- An **HDCA** is a dataset collection. Native references use
  `{"src":"hdca","id":"..."}`.
- A **job** is one tool execution. Mapping a collection can create several
  jobs from one request.
- A **tool request** tracks strict `/api/jobs` expansion into jobs and
  implicit collections.
- A **workflow invocation** tracks scheduled workflow steps and jobs.
- A **UDT** is a user-defined Galaxy tool addressed by UUID.

## Progressive command discovery

Use bounded structured help instead of loading the full README:

```bash
galaxy-cli help tool.run --json
```

The help metadata is centralized in `galaxy_cli/core/agent_help.py` and
contains a schema version, canonical command, shortest usage, required values,
mechanical defaults, examples where needed, aliases, and safety rules. It does
not initialize a Galaxy client.

`help COMMAND --json` is the single structured-help interface. Keeping it on
one command avoids duplicating a `--help-agent` option across every Click
command and keeps all contracts in the same registry.

Aliases resolve to the same Click command and callback as their canonical
names. Group help lists them for discovery, and machine-readable identity is
canonicalized.

## Input contract

Regular tool and UDT inputs accept one common JSON-object loader:

```bash
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs @inputs.json
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs '{"input":{"src":"hda","id":"ID"}}'
galaxy-cli tool run TOOL_ID --history HISTORY_ID --inputs -
```

`--inputs-json FILE` remains compatible. Supplying both JSON flags is an
error. Files, stdin, and inline values must decode to an object; arrays,
strings, empty input, and malformed JSON fail before client creation. Errors
report the source type and a mechanical correction without echoing the input.

The loader does not persist input text. Operation receipts store only a
payload hash and safe identifiers.

## Blocking execution contract

Blocking tool, UDT, workflow, upload, and resume paths use one absolute
monotonic deadline. They do not restart the timeout per request or job.
Success requires every known job to reach `ok`.

The shared adaptive polling sequence is:

```text
attempt 0: 5 seconds
attempt 1: 10 seconds
attempt 2: 20 seconds
attempt 3+: 30 seconds
```

Each sleep is clipped to the remaining global deadline. An explicit
`--poll-interval N` is a fixed compatibility override. Retry/backoff hints
used by retryable GET requests are bounded to 0–60 seconds and cannot extend
the operation deadline.

A successful blocking result includes:

- `success: true`, `state: ok`, and the execution backend;
- history, exact tool/workflow identity, and tool version where applicable;
- every known job ID, final state, and exit code;
- every dataset output name, ID, source, state, extension, and file size;
- every collection output name, ID, source, state, collection type, and
  element count.

That response is the normal verification boundary. Additional status calls
are only for explicit diagnostics or information absent from the result.

## Strict tool execution and fallback

`tool run --execution-backend auto` prefers strict nested execution:

1. Validate and normalize the input object.
2. POST once to `/api/jobs`.
3. Poll the returned tool request within the global deadline.
4. Read request detail and discover all jobs and implicit collections.
5. Wait for all jobs.
6. Read job outputs and refresh final dataset/collection metadata.

Auto mode falls back to legacy `/api/tools` only when the initial strict
endpoint clearly returns HTTP 404 or 405. It does not fall back after:

- HTTP 400 or 422 validation failure;
- authentication failure;
- timeout or connection loss;
- HTTP 5xx;
- malformed response or unknown submission state;
- any response after the strict request was accepted.

Unknown submission state always means `retry_safe: false`.

## Actionable errors

Default 1.x error keys (`error`, `category`, `message`) are retained.
Additive fields can include:

- `success: false`;
- `error_kind`;
- `path` and `expected`;
- `received_type`;
- bounded `allowed_values` plus `truncated: true`;
- a mechanical `correction`;
- up to three `did_you_mean` values;
- `submission_state` and `retry_safe`;
- known request, job, output, history, and tool IDs.

Suggestions correct syntax only. They never select scientific parameters,
choose a tool, rewrite a payload, or execute the guessed command. Missing
parameters include the shortest Click usage. Errors retain a non-zero exit
code.

## Safe next commands

Envelope and agent modes construct `next_commands` only from IDs already in
the result:

- one dataset output → bounded `dataset preview` and an `hda:ID` reference;
- one collection output → `collection show` and an `hdca:ID` reference;
- one failed job → `job diagnose`;
- one receipt → `operation resume`.

Every shell argument is quoted. Multiple ambiguous objects produce no object
command. No next command repeats a mutating submission. Unknown state or
`retry_safe: false` adds `do_not_resubmit: true`.

## Operation receipt and resume state machine

Mutation results use one stable reference shape:

```json
{"operation_receipt":"RECEIPT_ID"}
```

`operation_receipt` is always a string ID, including error details. It is not
the receipt record. Pass the string directly to `operation show` or
`operation resume`; those commands return the full receipt object in their
result data.

Receipts are private, atomic JSON records containing:

- receipt ID, operation type, payload hash, and timestamps;
- submission and receipt state;
- history/tool/workflow/UDT/backend identity;
- known request, job, and output IDs plus safe output references;
- resumable TUS metadata only when required, including the local file size and
  SHA-256 identity needed to reject a changed source file.

They do not contain the original request body or API key.

Receipt states have these meanings:

| Receipt state | Meaning | Resume behavior |
| --- | --- | --- |
| `unknown` | Submission cannot be determined | Do not resubmit; use known IDs if any |
| `submitted` | Work may still be running or observation failed | Discover and poll known records |
| `failed` | A terminal Galaxy failure is known | Preserve final failure context |
| `complete` | An authoritative result is persisted | Return the saved result without resubmission |

`success: true, state: submitted` from `--no-wait` is not complete.
Timeout, transport failure, and status-query failure do not get mislabeled as
a terminal job failure.

Normal resume performs only read-side operations:

1. Load the receipt and create one deadline.
2. Query every known tool request or workflow invocation.
3. Merge newly discovered jobs and outputs into the receipt.
4. Wait for every known job.
5. Read final job details.
6. Refresh dataset and collection metadata.
7. Atomically persist a redacted authoritative result.

If there is no request, job, or safe upload session, resume returns:

```json
{
  "resumable": false,
  "reason": "no_known_request_job_or_upload_session",
  "recommended_action": "do_not_resubmit"
}
```

### TUS recovery latch

An interrupted TUS byte transfer is the only recovery path that may need a
mutating call. Before the final fetch POST, the receipt atomically records
`fetch_submission_state: attempting`. A successful response changes it to
`submitted`. A lost response or process interruption leaves
`attempting/unknown`; future resume calls refuse to replay fetch. This may
require human reconciliation, but it cannot create a duplicate submission.
Each TUS receipt has a cross-process lock, and its write-ahead state is flushed
before fetch. A transport failure before fetch begins remains safely
resumable; a failure after the write-ahead transition does not.
TUS mutation is allowed only for receipts already in the private operation
directory. A receipt supplied from another path may be imported for read-only
reconciliation, but it cannot authorize a local-file upload. Resume also
rechecks the source fingerprint and keeps the same open file through transfer.
For very large files, finishing the interrupted fingerprint and validating it
before resume can each require a complete SHA-256 read. Progress is emitted on
stderr only, and Ctrl-C cancels the scan.

## Bounded output selection

Dataset head previews use Galaxy's bounded raw-data provider. Lines, fields,
source-line characters, per-line characters, total characters, context, grep
pattern length, and field indexes all have hard limits.

`tail` and `grep` require scanning. The client first obtains dataset size:

- unknown size → refuse;
- more than 5 MiB → refuse;
- within the limit → download into the private preview directory, process,
  and remove the temporary file in `finally`.

Tail and grep scan the bounded copy line by line. Tail retains only its bounded
deque, and grep retains only bounded context windows; newline-heavy files do
not expand into an in-memory list of every line.

The result records downloaded bytes, the configured temporary area, cleanup
status, selected line numbers, and truncation metadata. It never streams an
unbounded dataset to stdout.

Collection preview requires one exact element path. Recursive resolution
retains cycle, depth, ambiguity, result-count, API-request, and traversed-node
guards. Partially embedded nested collections are refreshed from their ID. A
truncated resolution is an error rather than a false “not found.”

## JSON output compatibility

The default remains the compact v1.5-compatible payload. Envelope v1 is
explicitly enabled by `--envelope`, `--agent`, or
`GALAXY_CLI_OUTPUT=envelope-v1`:

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

The top-level schema is stable; command-specific content remains under
`data`. The JSON Schema is packaged at
`galaxy_cli/schemas/envelope-v1.json`.

`--agent` combines only mechanical defaults: compact JSON, envelope v1,
blocking command defaults, bounded output, stderr progress, actionable errors,
and safe next commands. A 128 KiB serialized-stdout budget and 1000-node
traversal budget apply globally; exhaustion is explicit in `warnings`. Agent
mode cannot change tool choice, inputs, history, scientific behavior, or output
selection.

`--output-file PATH` atomically saves the complete redacted success or error
JSON result before stdout limiting. With `--agent` or `--envelope`, the saved
result uses envelope v1; plain `--output-file` retains the compatible 1.x JSON
shape. Stdout contains only a bounded summary with status, path, byte count,
command, and available unique IDs. Known API keys are redacted from the file
content and echoed path.

## Cache safety and isolation

Only stable read-only metadata is cached: server versions/capabilities,
installed tool lists, compact tool schemas and aliases, and datatype mappings.
Histories, jobs, datasets, UDT results, operation results, logs, and scientific
data are not warmed.

Cache files use private directory/file modes and atomic replacement. Server
identities discard URL user information, query strings, and fragments before
they are hashed or stored; known credentials are also redacted from cache keys
and values.
`cache stats` reads metadata without returning cached keys or values. It
reports per-namespace bytes and fresh/stale/corrupt counts, TTL, process-local
hit/miss/expired/corrupt counters, and hashed server identities.

Concurrent processes can share the atomic cache, but separate tasks or users
should receive distinct `GALAXY_CLI_CACHE_DIR` values from their launcher.
Routine agent workflows do not need cache management.

## Authentication and redaction

Credential resolution is:

1. explicit CLI option;
2. `GALAXY_API_KEY`;
3. `GALAXY_API_KEY_FILE`;
4. selected or active profile;
5. legacy configuration.

The key is sent only in the `x-api-key` header. Known secrets are recursively
redacted before JSON, human output, output files, errors, receipt results, and
next commands. Cache-management output never includes cached contents.

## Live compatibility

Mocked tests are the default. Live tests require both
`GALAXY_CLI_LIVE=1` and an explicit
`GALAXY_CLI_LIVE_TARGET=usegalaxy|local`. The suite separately marks public
and local targets and uses optional environment fixtures for regular tools,
multi-job tools, UDTs, workflows, collections, and failed jobs.

Live resources are small and cleanup runs in `finally`. Missing optional
capability fixtures skip only that capability. Once live mode is explicitly
enabled, connectivity or authentication failures are real failures rather
than broadly caught skips.
