---
name: "galaxy-cli"
description: "Operate Galaxy through the galaxy-cli command line interface with low-token, progressive command lookup."
---

# galaxy-cli

Use this skill when the task requires Galaxy operations through `galaxy-cli`.
Keep token use low: read this file once, then use `galaxy-cli <command> --help`
only for the specific command you are about to run.

## Token-Cheap Defaults

`galaxy-cli` is agent-first. The default path is:

1. Submit each tool with `galaxy-cli tool run ... --inputs-json FILE`.
2. Let `tool run` wait. Do not add `--no-wait` unless the task explicitly asks
   for asynchronous submission.
3. Use the returned `outputs` array for output IDs, state, datatype, and size.
4. Do not call `job show`, `dataset show`, `collection show`, `dataset list`,
   or `collection list` for routine verification.
5. Do not call `job show --logs` unless debugging a failed job.
6. Do not call `tool show` when task files already provide exact tool IDs and
   parameter JSON.

## Rules

- Use only `galaxy-cli` for Galaxy actions in this condition.
- Do not use BioBlend, raw HTTP clients, MCP tools, or Galaxy source code.
- Do not inspect or print API keys. Use `GALAXY_URL` and `GALAXY_API_KEY` from
  the environment.
- Compact JSON output is the default. Use `--human` only when a task needs
  human-readable terminal output.
- Pass `--history-id` explicitly on every history-scoped command. Do not rely
  on shared session state when multiple agents or concurrent runs may touch the
  same machine.
- Prefer `--inputs-json FILE` for tool runs with conditionals, repeats, or more
  than two parameters.
- Store large command output in files and extract only needed fields with `jq`.
- If Galaxy returns `429`, `502`, `503`, `504`, or a server-busy response, sleep
  180 seconds before retrying.
- If the task already provides exact tool IDs and parameter JSON, submit the
  tool directly. Do not call `tool show` just to re-discover supplied params.
- Do not download datasets or reports to local files unless the task explicitly
  asks for a local artifact. Reuse Galaxy dataset ids and collection ids
  directly in downstream tool runs.
- For `workflow run`, explicit source prefixes must be `hda:`, `hdca:`, or
  `ldda:`. Treat any other prefix as invalid input and fix it before submit.
- `workflow run --wait` should be trusted only when the invocation reaches
  Galaxy's `scheduled` state and all discovered jobs are terminal; this avoids
  reporting success while later steps are still being scheduled.

## Minimal Command Recipes

Create a fresh history:

```bash
HID=$(galaxy-cli history create "task run" | jq -r .id)
echo "$HID" > history_id.txt
```

Copy a prepared source history into a fresh working history:

```bash
HID=$(galaxy-cli history copy "$SOURCE_HISTORY_ID" "task run copy" | jq -r .id)
echo "$HID" > history_id.txt
```

Upload local datasets:

```bash
FWD=$(galaxy-cli dataset upload inputs/reads_1.fastq.gz --history-id "$HID" --file-type fastqsanger.gz | jq -r .id)
REV=$(galaxy-cli dataset upload inputs/reads_2.fastq.gz --history-id "$HID" --file-type fastqsanger.gz | jq -r .id)
```

Create collections:

```bash
PAIR=$(galaxy-cli collection create "pair" --history-id "$HID" --collection-type paired --forward "$FWD" --reverse "$REV" | jq -r .id)
PAIR_ALT=$(galaxy-cli collection create "pair" --history-id "$HID" --collection-type paired -e forward="$FWD" -e reverse="$REV" | jq -r .id)
LIST_PAIR=$(galaxy-cli collection create "reads" --history-id "$HID" --collection-type list:paired -p "pair:$FWD:$REV" | jq -r .id)
LIST=$(galaxy-cli collection create "reports" --history-id "$HID" --collection-type list -e pair="$DATASET_ID" | jq -r .id)
```

`collection create` includes resolved element IDs in JSON mode. Save its output if the
next tool needs a nested collection element; do not call `collection show` unless
the create output is insufficient.

Run a tool:

```bash
cat > tool_inputs.json <<EOF
{
  "input": "hda:$DATASET_ID"
}
EOF
galaxy-cli tool run "$TOOL_ID" --history-id "$HID" --inputs-json tool_inputs.json > tool_result.json
JOB=$(jq -r '.jobs[0].id' tool_result.json)
```

Check job and output states:

```bash
jq '{job:.jobs[0], wait_result, outputs}' tool_result.json
```

`tool run` waits by default. In JSON mode, the `outputs` array includes final
dataset or dataset-collection state/type/size metadata after wait. Do not call
`job show --full`, `dataset show`, or `collection show` for those outputs
unless a needed field is missing.

Download outputs only when the task explicitly asks for local artifacts:

```bash
galaxy-cli dataset download "$DATASET_ID" results/output.dat
```

## Input Encoding

- Dataset: `hda:DATASET_ID`
- Dataset collection: `hdca:COLLECTION_ID`
- Library dataset: `ldda:DATASET_ID`
- Boolean: `true` or `false`
- Conditional or repeat params: prefer nested JSON in `--inputs-json`.
- Flattened conditional paths use pipes when needed, for example
  `single_paired|paired_input`.

## What To Read Next

- For command syntax, run `galaxy-cli <group> --help` or
  `galaxy-cli <group> <command> --help`.
- For tool parameters, use the task's `workflow/step_specs.json`,
  `workflow/required_step_params.json`, and `workflow/step_execution_hints.json`.
- Only run `galaxy-cli tool show TOOL_ID` when those task files do not
  provide enough input names/options to build the submission JSON.
- Do not read package source code. The command help and task files are enough.
