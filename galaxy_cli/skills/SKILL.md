---
name: "galaxy-cli"
description: "Operate Galaxy through galaxy-cli with bounded discovery, authoritative blocking results, and safe recovery."
---

# galaxy-cli

Use this skill when the task requires Galaxy operations through `galaxy-cli`.

## Canonical happy path

1. Work in an explicit history. Pass `--history HISTORY_ID` for concurrent
   or agent-driven work.
2. Resolve a known history item with `history find`.
3. Locate a regular tool with `tool find` only when its exact ID is unknown.
4. If the input contract is unknown, run `tool inputs TOOL_ID` once.
5. Put nested inputs in a JSON object and pass `--inputs @inputs.json`.
6. Run the tool with its default blocking behavior.
7. Use returned IDs directly; preview only when content inspection is needed.

For one bounded command contract, use:

```bash
galaxy-cli help tool.run --json
```

Do not load the full README when structured help answers the syntax question.

## Inputs

- Prefer `--inputs @file` for regular tools and UDTs.
- Dataset references use `{"src":"hda","id":"DATASET_ID"}`.
- Collection references use `{"src":"hdca","id":"COLLECTION_ID"}`.
- Do not combine `--inputs` with the legacy `--inputs-json` flag.
- Use `tool template` only when the input contract is not already known.
- Do not invent or choose scientific parameter values.

## Trust blocking results

- Tool, UDT, workflow, and upload commands wait by default.
- Trust a successful blocking result. It already includes every known final
  job and compact dataset/collection metadata.
- Trust a successful `operation resume` result at the same boundary.
- Do not make routine `job show`, `dataset show`, `collection show`, or
  history-contents calls merely to re-verify success.
- Use `--no-wait` only when asynchronous submission is explicitly required.
- Leave execution backend selection on `auto` unless compatibility diagnosis
  requires an explicit backend.

## Failures and recovery

- Follow returned `next_commands` when envelope or agent mode provides them.
- Otherwise use `job diagnose JOB_ID` for one failed job.
- If a receipt is present, use `operation resume RECEIPT_ID`.
- Never replay a mutating command when `submission_state` is `unknown` or
  `retry_safe` is false.
- A timeout or observation failure is not evidence that submission failed.
- Do not execute a `did_you_mean` suggestion automatically.

## Submission safety

- Do not resubmit a mutating request when its submission state is unknown.
- Use the operation receipt to resume an interrupted operation.
- Treat a successful blocking result as authoritative unless explicit
  diagnostics are required.
- Never inspect, print, or pass an API key in command arguments.
- Let the CLI obtain credentials from its configured environment or key file.

## Bounded output

- Compact JSON is the default.
- Use `--agent` when a stable envelope and safe mechanical next commands are
  useful; it does not choose tools or parameters.
- Preview datasets with a small head unless tail/grep is explicitly needed.
- Preview a collection only with one exact `--element` path.
- Never expand an entire collection to inspect it.
- Do not download outputs unless a local artifact is explicitly required.
- Treat `truncated: true` as partial output, never as a complete result.

## Cache

- Let metadata caching happen automatically.
- Do not routinely clear or warm caches.
- Use cache commands only for explicit diagnostics or runner preparation.
