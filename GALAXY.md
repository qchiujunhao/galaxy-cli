# Galaxy CLI Harness — Software-Specific SOP

## Purpose

Galaxy is a client-server bioinformatics platform. `galaxy-cli` operates a
running Galaxy instance through its REST API; it does not run Galaxy tools
locally. A reachable server and an API key are therefore required for every
server-backed command.

The CLI is intentionally small: Python 3.9+, Click, and requests. It does not
depend on BioBlend, Node, or MCP. Mixing another Galaxy client into one
execution flow is discouraged because duplicate POSTs can create duplicate
jobs.

## Data Model

- **History**: a server-side workspace containing datasets and collections.
- **Dataset (HDA)**: one history dataset with a state, datatype, and file size.
- **Dataset collection (HDCA)**: a typed collection such as a list or pair.
- **Job**: one tool execution. Collection mapping can spawn several jobs from
  one request.
- **Tool request**: the strict execution record that tracks job creation and
  implicit collections.
- **Workflow invocation**: a scheduled workflow execution with steps and jobs.
- **User-defined tool (UDT)**: a user-owned Galaxy tool representation addressed
  by UUID.

## API Mapping

| Operation | Primary API flow | CLI command |
| --- | --- | --- |
| Create or copy a history | `POST /api/histories` | `history create`, `history copy` |
| Inspect copy readiness | `GET /api/histories/{id}/contents` | internal to blocking `history copy` |
| Upload a dataset | legacy upload form through `POST /api/tools` | `dataset upload` |
| Run a regular tool strictly | `POST /api/jobs`, then `/api/tool_requests/{id}` | `tool run` |
| Run a regular tool on an older server | `POST /api/tools` | `tool run --execution-backend legacy` |
| Inspect a dataset | `GET /api/datasets/{id}` or history contents | `dataset show` |
| Inspect a collection | history collection contents endpoint | `collection show` |
| Inspect a job | `GET /api/jobs/{id}` | `job show` |
| Manage UDTs | `/api/unprivileged_tools` | `udt list`, `show`, `create`, `delete` |
| Run a UDT | portable tool execution endpoint | `udt run`, `udt create-run` |
| Invoke a workflow | workflow invocation endpoint | `workflow run` |

## Execution Contract

Blocking commands wait for all known jobs with one global deadline. They report
success only when every job is `ok`. Failure states such as `error`, `deleted`,
`paused`, and `failed_metadata`, or a non-terminal job at the deadline, produce
a non-zero structured error.

A successful blocking regular-tool result contains:

- `success: true` and `state: ok`
- the strict or legacy execution backend
- history ID, exact tool ID, and tool version
- every job ID, final state, and exit code
- compact metadata for every dataset and collection output

That result is the normal verification boundary. Additional job, dataset,
collection, or history queries are for failure diagnosis or explicitly missing
information, not routine confirmation.

## Strict Execution and Safe Fallback

`--execution-backend auto` is the default for `tool run`. Auto mode first uses
strict nested execution through `/api/jobs`, polls the returned tool request,
retrieves its detail, waits for all spawned jobs, and includes implicit
collections in the final output list.

Auto may use legacy `/api/tools` only when the initial strict endpoint clearly
responds with HTTP 404 or 405. The following conditions must not cause fallback
or a second POST:

- HTTP 400 or 422 input errors
- timeout or connection loss
- HTTP 5xx server errors
- a malformed response or unknown submission state
- any failure after a strict tool request was accepted

When submission is uncertain, the error reports `submission_state: unknown`
and `retry_safe: false`. A caller must resolve known request or job IDs instead
of blindly retrying.

Strict mode preserves nested repeat, conditional, multiple-data, and collection
inputs. Native dataset references use `src: hda`; collection references use
`src: hdca`. Legacy mode retains the existing pipe-key flattening behavior.

## History Copy Readiness

Galaxy can return a new history before copied contents are usable. Blocking
`history copy` polls the copied datasets and collections with one deadline. It
waits through transient states, fails on any content failure, and returns only
a compact contents map. `--no-wait` retains the immediate 1.4.1 behavior.

Dataset and collection state fields differ across Galaxy versions. The client
normalizes dataset `state` and collection `populated_state` while preserving the
content ID, HID, source, name, type, and element count needed by callers.

## Authentication and Output Safety

Authentication resolution is:

1. Explicit command option
2. `GALAXY_API_KEY`
3. `GALAXY_API_KEY_FILE`
4. Selected or active profile
5. Legacy configuration fields

The key is sent in the `x-api-key` header. Configuration output masks it, and
known secrets are removed from structured errors. Compact JSON is written as
one value on stdout; progress belongs on stderr.

## Agent Operating Rules

- Prefer command-specific `--help` over static syntax assumptions.
- Use `tool run` for regular tools and UDT commands for user-defined tools.
- Trust a successful blocking result.
- Do not combine `galaxy-cli` with BioBlend, raw HTTP, MCP, or source inspection
  for duplicate submission or routine verification.
- Do not retry an unknown submission state.
- Do not download outputs unless a local artifact is explicitly required.
- Request a bounded preview only for a named dataset output; do not expand
  collection outputs automatically.
