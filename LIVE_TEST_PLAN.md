# UseGalaxy Live Validation Plan

This document defines a reusable release-gate plan for validating
`galaxy-cli` against [usegalaxy.org](https://usegalaxy.org/). It separates
deterministic local tests, the automated live compatibility suite, manual CLI
black-box checks, optional capability fixtures, cleanup, and release evidence.

The plan must be run against the exact release candidate. A passing base suite
does not imply that skipped tool, workflow, collection, or UDT capabilities
were tested.

## Validation layers

| Layer | Purpose | Release meaning |
| --- | --- | --- |
| Local suite | Validate deterministic behavior with mocked Galaxy responses | Required for every change |
| Base live suite | Validate authentication, read-only capabilities, history lifecycle, and tiny uploads | Minimum live release gate |
| Extended live suite | Validate regular tools, multi-job/collection execution, UDTs, workflows, receipts, collection preview, and failure diagnostics | Required when the changed area is in scope and fixtures exist |
| CLI black-box checks | Validate installed command parsing, JSON/envelope output, stderr progress, and cleanup through the public executable | Required before a release |
| Package smoke | Validate the built wheel in an isolated environment | Required before publishing |

The automated live tests import `galaxy_cli.core` functions. They exercise real
Galaxy API behavior, but they do not by themselves prove that the installed
`galaxy-cli` command, Click parsing, stdout, stderr, or output-file behavior is
correct. Run the CLI black-box phase separately.

## Safety rules

1. Use a dedicated test account with no production histories or private
   scientific data.
2. Run only one live validation at a time for an account. The automated base
   suite currently uses fixed temporary history names.
3. Set `GALAXY_CLI_LIVE=1` explicitly. Credentials alone must never activate
   live tests.
4. Never print the API key, run `env`, enable shell tracing, or store a key in
   a test artifact.
5. Give manual histories a unique run prefix and delete only exact IDs created
   by the current run.
6. Record every created history, UDT, receipt, and local artifact in a cleanup
   ledger as soon as it is created.
7. Do not resubmit a mutating request when its submission state is unknown.
   Reconcile its operation receipt or known IDs first.
8. Cleanup runs in `finally` or an EXIT trap, but an explicit cleanup audit is
   still required before release.
9. Missing optional fixtures are reported as `not validated`, never as passed.
10. Stop the release if a selected live test fails, cleanup is incomplete, or
    the exact release commit differs from the tested commit.

## Required tools and environment

Prepare the development environment:

```bash
uv sync --group dev
source .venv/bin/activate
```

Load the server URL and key without placing the key in shell history:

```bash
export GALAXY_URL=https://usegalaxy.org
read -r -s GALAXY_API_KEY
export GALAXY_API_KEY
export GALAXY_CLI_LIVE=1
export GALAXY_CLI_LIVE_TARGET=usegalaxy
```

Confirm only that required values are present:

```bash
test -n "$GALAXY_URL"
test -n "$GALAXY_API_KEY"
test "$GALAXY_CLI_LIVE" = "1"
test "$GALAXY_CLI_LIVE_TARGET" = "usegalaxy"
```

Do not echo these variables. Store local evidence under the ignored `build/`
directory:

```bash
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
export RUN_DIR="build/live-test/$RUN_ID"
mkdir -p "$RUN_DIR"
```

## Fixture contract

The base suite needs only `GALAXY_URL` and `GALAXY_API_KEY`. Extended tests
use the following optional variables:

| Capability | Required variables | Fixture requirements |
| --- | --- | --- |
| Strict regular tool | `GALAXY_CLI_LIVE_TOOL_ID`, `GALAXY_CLI_LIVE_TOOL_INPUTS` | Stable installed tool; JSON object with disposable or dedicated test inputs |
| Multi-job or collection tool | `GALAXY_CLI_LIVE_MULTI_TOOL_ID`, `GALAXY_CLI_LIVE_MULTI_TOOL_INPUTS` | Must create multiple jobs or an HDCA deterministically |
| UDT lifecycle | `GALAXY_CLI_LIVE_UDT_REPRESENTATION`, `GALAXY_CLI_LIVE_UDT_INPUTS` | Small reviewed representation file and JSON input object |
| Workflow | `GALAXY_CLI_LIVE_WORKFLOW_ID`, `GALAXY_CLI_LIVE_WORKFLOW_INPUTS` | Stable imported workflow and disposable test inputs |
| Collection resolution | `GALAXY_CLI_LIVE_COLLECTION_ID`, `GALAXY_CLI_LIVE_COLLECTION_ELEMENT` | Dedicated collection and exact stable element path |
| Failure diagnostics | `GALAXY_CLI_LIVE_FAILED_JOB_ID` | Dedicated terminal failed job containing no sensitive logs |

Fixture values must not be invented during a release run. Review them before
use, confirm that referenced data belongs to the test account, and record only
presence booleans in the run report. JSON input variables must decode to
objects. The UDT representation path should be relative to the test workspace.

If a changed feature requires an extended capability and no safe fixture is
available, the release is blocked or the unvalidated risk must be explicitly
accepted by the release owner.

## Phase 1: freeze and local checks

Record the candidate identity without recording credentials:

```bash
git rev-parse HEAD
git status --short
galaxy-cli --version
```

Run the deterministic release checks:

```bash
ruff check .
pytest -q
python -m build
git diff --check
```

Acceptance criteria:

- Ruff passes.
- All mocked and unit tests pass.
- Only explicitly gated live tests skip.
- Wheel and sdist build successfully.
- The working tree contains only reviewed release changes.

## Phase 2: read-only live preflight

Run authentication and capability probes first:

```bash
galaxy-cli --agent --output-file "$RUN_DIR/config-test.json" \
  config test > "$RUN_DIR/config-test-summary.json"

galaxy-cli --agent --output-file "$RUN_DIR/capabilities.json" \
  server capabilities --no-cache --refresh-cache \
  > "$RUN_DIR/capabilities-summary.json"
```

Verify:

- both commands exit zero;
- stdout contains a valid bounded envelope summary;
- output files contain complete redacted envelopes;
- no progress or diagnostic text is written to stdout;
- the capability result reports a read-only probe mode.

Stop before mutations if authentication or read-only probes fail.

## Phase 3: automated base live suite

Run the usegalaxy-marked suite only:

```bash
GALAXY_CLI_LIVE=1 GALAXY_CLI_LIVE_TARGET=usegalaxy \
  pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -q \
  --junitxml="$RUN_DIR/usegalaxy-live.xml"
```

The base capabilities are:

1. Server version, authentication, and read-only capability detection.
2. Temporary history creation.
3. Blocking history copy readiness.
4. Copy deletion and purge.
5. Tiny upload with `--upload-backend auto` behavior.
6. Tiny upload with the legacy backend.
7. Base history deletion and purge.

The fixtures use `finally` cleanup. A result such as `4 passed, 7 skipped`
means that the four base test functions passed and seven optional capabilities
were not validated. It is not a full-capability pass.

## Phase 4: extended live capabilities

First record fixture presence without printing values. Then run each available
capability independently so its failure boundary is clear:

```bash
pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -k regular_tool_strict_nested_execution -q

pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -k multi_job_or_collection_execution -q

pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -k udt_validate_create_run_deactivate -q

pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -k workflow_run_returns_final_outputs -q

pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -k operation_receipt_resume -q

pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -k collection_resolve_and_preview -q

pytest galaxy_cli/tests/test_live_compatibility.py \
  -m 'live and usegalaxy' -k failure_diagnostics_are_bounded -q
```

For each selected test, require zero exit status, authoritative terminal
results, bounded output, and successful cleanup. Do not continue to another
mutating capability after an unknown submission state until it is reconciled.

## Phase 5: CLI black-box lifecycle

Use unique names because this phase runs through the installed executable:

```bash
PREFIX="galaxy-cli-live-$RUN_ID"
BASE_HID=""
COPY_HID=""

cleanup_history() {
  test -z "$1" || galaxy-cli history delete "$1" --purge >/dev/null
}

cleanup() {
  cleanup_history "$COPY_HID" || true
  cleanup_history "$BASE_HID" || true
}

trap cleanup EXIT
```

Create a history and extract its ID from the complete envelope:

```bash
galaxy-cli --agent --output-file "$RUN_DIR/history-create.json" \
  history create "$PREFIX-base" > "$RUN_DIR/history-create-summary.json"

BASE_HID=$(python -c \
  'import json, sys; print(json.load(open(sys.argv[1]))["data"]["id"])' \
  "$RUN_DIR/history-create.json")
```

Create a tiny non-scientific input and exercise both upload paths:

```bash
printf 'one\ntwo\n' > "$RUN_DIR/tiny.txt"

galaxy-cli --agent --output-file "$RUN_DIR/upload-auto.json" \
  dataset upload "$RUN_DIR/tiny.txt" --history "$BASE_HID" \
  --upload-backend auto --timeout 180 --poll-interval 2 \
  > "$RUN_DIR/upload-auto-summary.json"

galaxy-cli --agent --output-file "$RUN_DIR/upload-legacy.json" \
  dataset upload "$RUN_DIR/tiny.txt" --history "$BASE_HID" \
  --upload-backend legacy --timeout 180 --poll-interval 2 \
  > "$RUN_DIR/upload-legacy-summary.json"
```

Extract the auto-uploaded dataset ID and verify bounded content retrieval:

```bash
AUTO_DID=$(python -c \
  'import json, sys; print(json.load(open(sys.argv[1]))["data"]["id"])' \
  "$RUN_DIR/upload-auto.json")

galaxy-cli --agent --output-file "$RUN_DIR/dataset-preview.json" \
  dataset preview "$AUTO_DID" --history "$BASE_HID" --head 2 \
  > "$RUN_DIR/dataset-preview-summary.json"
```

On usegalaxy.org, use the small installed `cat1` tool for a real strict tool
submission. Inspect its current template before constructing inputs; do not
assume that a tool schema is unchanged merely because a previous release run
passed:

```bash
galaxy-cli --agent --output-file "$RUN_DIR/cat1-inputs.json" \
  tool inputs cat1 --no-cache > "$RUN_DIR/cat1-inputs-summary.json"

INPUTS="{\"input1\":{\"src\":\"hda\",\"id\":\"$AUTO_DID\"}}"

galaxy-cli --agent --output-file "$RUN_DIR/tool-validate.json" \
  tool validate cat1 --history "$BASE_HID" --inputs "$INPUTS" \
  > "$RUN_DIR/tool-validate-summary.json"

galaxy-cli --agent --output-file "$RUN_DIR/tool-run.json" \
  tool run cat1 --history "$BASE_HID" --execution-backend strict \
  --timeout 300 --poll-interval 2 --inputs "$INPUTS" \
  > "$RUN_DIR/tool-run-summary.json"
```

Require `valid: true`, no validation errors, `execution_backend: strict`, a
terminal successful state, and non-empty jobs and outputs. In envelope v1 the
`operation_receipt` value is a receipt ID string, not a nested object. Read it
and verify the stored receipt through the public command:

```bash
RECEIPT_ID=$(python -c \
  'import json, sys; print(json.load(open(sys.argv[1]))["data"]["operation_receipt"])' \
  "$RUN_DIR/tool-run.json")

galaxy-cli --agent --output-file "$RUN_DIR/operation-show.json" \
  operation show "$RECEIPT_ID" > "$RUN_DIR/operation-show-summary.json"
```

Copy the history and verify readiness:

```bash
galaxy-cli --agent --output-file "$RUN_DIR/history-copy.json" \
  history copy "$BASE_HID" --name "$PREFIX-copy" \
  --timeout 180 --poll-interval 2 \
  > "$RUN_DIR/history-copy-summary.json"

COPY_HID=$(python -c \
  'import json, sys; print(json.load(open(sys.argv[1]))["data"]["id"])' \
  "$RUN_DIR/history-copy.json")
```

Verify every command:

- exit code is zero;
- stdout stays below the agent budget and parses as envelope v1;
- stderr contains progress only;
- complete output files are redacted;
- upload results contain one authoritative `ok` output;
- copy result reports ready contents;
- no API key occurs in any artifact.

Also exercise output-file handling on two nonzero paths: a local usage error
and a real Galaxy backend error produced by requesting a nonexistent dataset.
For each, require a nonzero exit status, a complete parseable redacted output
file, a parseable bounded stdout envelope, and no credential in stdout,
stderr, the output file, or its echoed path. Do not hard-code one numeric exit
code across error classes; record and compare the command's actual contract.

Run explicit cleanup before leaving the phase, then clear IDs so the EXIT trap
does not repeat deletion:

```bash
cleanup_history "$COPY_HID"
COPY_HID=""
cleanup_history "$BASE_HID"
BASE_HID=""
```

## Phase 6: cleanup audit

Search only for the exact run prefix or the automated suite's exact fixed
names. Never bulk-delete histories based on a partial generic name.

The cleanup report must record:

- active matching histories before cleanup;
- histories purged during the audit;
- remaining active matches after cleanup;
- UDTs deactivated by the run;
- unresolved receipts or unknown submissions;
- cleanup errors.

Scan artifacts for the current key without printing it:

```bash
python - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["RUN_DIR"])
secret = os.environ["GALAXY_API_KEY"].encode()
matches = [
    path
    for path in root.rglob("*")
    if path.is_file() and secret in path.read_bytes()
]
if matches:
    raise SystemExit("credential found in live-test artifacts")
print("artifact secret scan passed")
PY
```

Acceptance requires zero active temporary histories, zero temporary UDTs, and
no unresolved unknown mutation. Keep the local `build/live-test/` artifacts
until the release decision is made, then remove them according to the project
retention policy.

## Phase 7: package and release gate

Build and smoke-test the exact candidate wheel in an isolated environment.
Confirm that `galaxy-cli --version`, structured help, aliases, and one
non-mutating command work from the installed wheel rather than the source tree.

Release only when all applicable gates are satisfied:

| Gate | Required result |
| --- | --- |
| Local Ruff/tests/build/diff | Pass |
| Base usegalaxy suite | Pass with cleanup |
| Scope-required extended capabilities | Pass, or explicit release-owner risk acceptance |
| CLI black-box lifecycle | Pass with cleanup |
| Secret scan | No key in files, stdout, stderr, or evidence |
| CI for the release commit | Python 3.9–3.13 pass |
| Commit identity | Tested commit equals tag target |

After publishing, wait for the release workflow to finish testing, version
validation, distribution checks, artifact attachment, and package publication.
Install the published version without a local package cache and verify its
reported version.

## Run report template

Copy this section into the private release handoff. Do not include credentials
or raw environment dumps.

```markdown
### UseGalaxy live validation

- Run ID:
- UTC start/end:
- Commit and candidate version:
- Target: usegalaxy
- Base live result: passed / failed
- Extended capabilities selected:
- Extended capabilities skipped and reason:
- CLI black-box result:
- Created resource counts:
- Cleanup result and remaining resources:
- Local suite result:
- CI URL and result:
- Package smoke result:
- Release decision:
- Residual risks:
```

## Abort conditions

Abort without release when any of these occurs:

- authentication or capability preflight fails;
- a selected test fails or times out;
- a mutation has unknown submission state and cannot be reconciled;
- cleanup leaves an active temporary resource;
- stdout exceeds its contract or contains progress text;
- any artifact contains a credential;
- the tested commit changes before tagging;
- CI, distribution validation, or package publication fails.

An aborted run should preserve redacted evidence, clean known resources, and
record the exact failure boundary before any retry.

## Recorded manual black-box run: 2026-07-12 UTC

This run used a no-cache PyPI installation of `galaxy-cli==1.6.0` in an
isolated virtual environment and isolated home directory. It ran from outside
the source tree against usegalaxy.org with Python 3.14.4. The tested tag target
was commit `ac9d647f3a2db8200155201847ce7ffd6fa36f52` (`v1.6.0`).

The following commands were executed individually through the installed
`galaxy-cli` executable, not through pytest:

- authenticated `config test` and uncached `server capabilities`;
- history creation;
- tiny blocking uploads with `auto` and `legacy` backends;
- a two-line dataset preview with the expected `one` and `two` contents;
- uncached `tool inputs cat1` and server-side `tool validate cat1`;
- a blocking `tool run cat1` with `--execution-backend strict`;
- `operation show` using the returned receipt ID;
- a second history lifecycle containing an upload and blocking history copy;
- usage-error and live backend-error output-file paths;
- explicit purge by exact history ID, active-history audits, artifact JSON
  parsing, stdout byte checks, and API-key scanning.

All Galaxy and CLI checks passed. The validation result was `valid: true` with
no errors. The strict tool result contained a terminal successful state,
non-empty jobs, non-empty outputs, and a receipt. The preview returned exactly
two untruncated rows. Both error paths created complete redacted JSON files and
bounded JSON stdout while returning nonzero. The largest captured agent stdout
was 691 UTF-8 bytes, below the 128 KiB budget. No artifact contained the API
key.

Three temporary histories were created across two cleanup-isolated
lifecycles. Every delete response confirmed `purged: true`, and the final
active-history queries found zero names with either unique run prefix. There
were no unresolved submissions or receipts.

During the first orchestration pass, a local assertion incorrectly treated
`operation_receipt` as an object. The strict Galaxy job had already completed
successfully and returned an authoritative result. The EXIT trap purged its
history, the active-history audit returned zero, and the receipt was then
successfully checked using its actual string form. This was a validation-script
mistake, not a CLI or Galaxy failure; the reusable procedure above records the
correct string contract.

This manual run did not validate UDT creation, workflow execution,
multi-job/collection execution, collection element preview, a pre-existing
failed-job diagnostic fixture, or large-file TUS interruption and resume.
Those remain fixture-gated extended capabilities and must not be reported as
passed.
