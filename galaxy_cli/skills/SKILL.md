---
name: "galaxy-cli"
description: "Operate Galaxy through the galaxy-cli command line interface with low-token, progressive command lookup."
---

# galaxy-cli

Use this skill when the task requires Galaxy operations through `galaxy-cli`.
Keep discovery progressive: start with `galaxy-cli <group> <command> --help`
for the one command you need.

## Choose the Operation

- Use `tool run` for regular Galaxy tools.
- Use `udt create-run` when creating and immediately running a new UDT; use
  `udt run` for an existing UDT.
- Let tool, UDT, upload, and history-copy commands wait by default. Use
  `--no-wait` only when asynchronous submission is explicitly required.
- Leave `tool run` on `--execution-backend auto` unless compatibility
  diagnosis requires an explicit backend.
- Use `tool show` only when the exact tool ID and input contract are not
  already available. Refresh its cache when freshness matters.

## Trust and Retry Rules

- Trust a successful blocking result. It already contains final job states and
  compact dataset and collection output metadata.
- Do not repeat `job show`, `dataset show`, `collection show`, or history
  contents calls merely to verify a successful blocking result.
- Use job logs only to diagnose a reported failure.
- Never retry a mutating command when `submission_state` is `unknown` or
  `retry_safe` is false. Resolve the existing request or job IDs first.
- A non-zero timeout or job failure is final for that CLI invocation; do not
  reinterpret it as a successful submission.

## Safety and Scope

- Use `galaxy-cli` alone for Galaxy operations. Do not mix in BioBlend, raw
  HTTP, MCP, or Galaxy source inspection for duplicate execution or routine
  verification.
- Do not inspect, print, or pass API keys in command arguments. Let the CLI
  read `GALAXY_API_KEY` or `GALAXY_API_KEY_FILE` from the environment.
- Pass `--history-id` explicitly for concurrent or agent-driven work.
- Prefer `--inputs-json` for nested, repeated, conditional, multiple-data, or
  collection inputs.
- Do not download outputs by default. Use returned Galaxy IDs for downstream
  commands; request a bounded preview only when its output name is known.
- Compact JSON is the default. Keep progress on stderr and consume stdout as
  one machine-readable result.
