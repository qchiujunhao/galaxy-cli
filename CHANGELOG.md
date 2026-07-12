# Changelog

## 1.6.1 — 2026-07-11

- Define the existing `operation_receipt` result field as a string receipt ID
  in structured help and documentation. `operation show` and `operation
  resume` return the full receipt object. The 1.x JSON wire format is
  unchanged.

## 1.6.0 — 2026-07-11

- Add one JSON-object input loader for tool and UDT execution. The new
  `--inputs` accepts `@PATH`, inline JSON, or stdin; the existing
  `--inputs-json FILE` and `-i key=value` forms remain compatible.
- Add `--history` plus common command aliases (`tool find/inputs/schema`,
  `dataset preview/head`, `history ls/find`, `collection get`, and `job
  debug`) without duplicating callbacks or changing canonical command
  identity. Group help labels each alias with its canonical target, and
  `history ls` lists histories.
- Add bounded structured help through `galaxy-cli help COMMAND --json` and
  additive actionable error fields for missing inputs, unknown options,
  validation paths, bounded allowed values, and mechanical corrections.
- Replace fixed default job polling with a shared adaptive
  `5s, 10s, 20s, 30s, ...` policy. One absolute deadline covers request
  expansion after submission, all jobs, and output refresh. Explicit
  `--poll-interval` remains a fixed compatibility override, and GET retry
  hints are bounded to 60s.
- Make `operation resume` authoritative for tool, UDT, workflow, and upload
  receipts. Resume discovers new jobs/outputs, refreshes final dataset and
  collection metadata, persists an atomic redacted result, and never replays
  an ordinary POST. Correct asynchronous receipt classification and add a
  durable, cross-process-locked TUS fetch latch that refuses replay after an
  unknown response. Terminal request failures remain failures after all known
  jobs and outputs are reconciled. TUS resume verifies the local file size and
  SHA-256 identity, and an external receipt path cannot authorize mutation.
- Add opt-in, shell-quoted `next_commands` for unique returned IDs. Unknown or
  retry-unsafe submissions explicitly say not to resubmit.
- Add exact-path `collection preview` and collection-aware tool output peeks.
  Add bounded dataset `head`, `tail`, `grep`, `context`, and `fields`
  selectors with hard limits, small-file scan thresholds, and guaranteed
  temporary-file cleanup. Tail/grep scan with bounded streaming state rather
  than loading every line. Recursive collection resolution also bounds API
  requests and traversed nodes and refreshes partial nested metadata.
- Add `cache stats`, `cache clear`, and `cache warm --server --tools` with
  namespace counts, sizes, freshness/corruption, process-local counters, TTL,
  and hashed server identities. Cache-management output never returns keys,
  schemas, or scientific content; stored server URLs exclude credentials,
  queries, and fragments.
- Add opt-in envelope v1 through `--envelope` or
  `GALAXY_CLI_OUTPUT=envelope-v1`, plus a mechanical `--agent` mode. The
  default compact 1.x JSON payload remains unchanged. Agent output has global
  128 KiB serialized-stdout and 1000-node traversal budgets with explicit
  truncation warnings. `--output-file` atomically preserves complete redacted
  success or error results while stdout remains bounded. Package the envelope
  JSON Schema.
- Replace credential-presence live-test activation with explicit
  `GALAXY_CLI_LIVE=1` gating, separate public/local markers, configurable
  capability fixtures, and cleanup-safe coverage for the supported execution
  and recovery flows.
- Shorten the README happy path, expand the software SOP and test strategy,
  and update the bundled agent skill around structured help, `@file` inputs,
  trusted blocking/resume results, safe next commands, and bounded previews.

## 1.5.0 — 2026-07-11

- Add one shared, global-deadline job wait path for regular tools, UDTs,
  uploads, and other blocking operations. Blocking commands now require every
  spawned job to finish successfully; job failures and timeouts produce
  structured non-zero errors with the known execution context.
- Make blocking `tool run` results authoritative by returning the execution
  backend, tool version, final state and exit code for every job, and compact
  final metadata for every dataset and collection output.
- Add strict nested tool execution through `/api/jobs` and tool requests.
  `--execution-backend auto` prefers strict execution and falls back to the
  legacy `/api/tools` endpoint only when the initial strict endpoint is
  explicitly unsupported with HTTP 404 or 405. Invalid requests, transport
  failures, timeouts, server errors, and unknown submission states never
  trigger a second submission.
- Make `history copy` wait for copied datasets and collections by default,
  using one deadline and returning a compact contents map. `--no-wait`
  preserves the immediate-return behavior from 1.4.1.
- Add bounded, opt-in output previewing to `tool run` and a server/tool-version
  keyed cache for compact `tool show` input templates.
- Make `tool search` use a TTL-controlled metadata cache by default and extend
  the cache to server versions, read-only capabilities, tool panels, exact
  tool schemas, and datatype mappings. Add `GALAXY_CLI_CACHE_DIR`; corrupt or
  stale entries are discarded automatically.
- Add compact discovery commands: `history contents/resolve`, `tool
  template/examples/validate`, `server capabilities`, `job logs/diagnose`,
  recursive `collection show --flatten` and `collection resolve`, `udt
  validate`, and `workflow template`.
- Make blocking workflow runs wait for scheduling and every spawned job, then
  return final jobs and outputs with the same non-zero failure/timeout rules.
- Add secret-free operation receipts and status-only `operation
  show/list/resume`. Unknown submissions are never replayed.
- Add TUS resumable uploads with `--upload-backend auto|tus|legacy`; automatic
  fallback is limited to a clearly unsupported initial TUS endpoint.
- Add global `--output-file`, `--max-items`, and `--max-chars` output controls
  while retaining compact single-line JSON on stdout.
- Reuse the shared wait behavior for UDT execution, including non-zero timeout
  handling. Keep `--evidence-dir` as an explicit debug-only compatibility
  option rather than part of the normal agent workflow.
- Replace the bundled agent tutorial with concise decision rules: use command
  help, trust successful blocking results, avoid duplicate Galaxy clients and
  verification calls, and never blindly retry an unknown submission state.

## 1.4.1 — 2026-07-10

- Add `GALAXY_API_KEY_FILE` with explicit-key and environment-key precedence,
  masked configuration reporting, and shared error redaction.
- Add compact `udt list`, `show`, `create`, `delete`, `run`, and `create-run`
  commands using Galaxy's unprivileged-tool and tool-execution APIs.
- Add blocking UDT job/output refresh and optional redacted evidence files.
