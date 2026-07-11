# Changelog

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
