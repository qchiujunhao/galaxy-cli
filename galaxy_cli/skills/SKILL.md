---
name: "galaxy-cli"
description: "Operate Galaxy through the galaxy-cli command line interface with low-token, progressive command lookup."
---

# galaxy-cli

Use this skill when the task requires Galaxy operations through `galaxy-cli`.
Keep token use low: read this file once, then use `galaxy-cli <command> --help`
only for the specific command you are about to run.

## Rules

- Use only `galaxy-cli` for Galaxy actions in this condition.
- Do not use BioBlend, raw HTTP clients, MCP tools, or Galaxy source code.
- Do not inspect or print API keys. Use `GALAXY_URL` and `GALAXY_API_KEY` from
  the environment.
- Always pass `--json` when machine-readable output is needed.
- Pass `--history-id` explicitly on every history-scoped command.
- Prefer `--inputs-json FILE` for tool runs with conditionals, repeats, or more
  than two parameters.
- Store large command output in files and extract only needed fields with `jq`.
- If Galaxy returns `429`, `502`, `503`, `504`, or a server-busy response, sleep
  180 seconds before retrying.

## Minimal Command Recipes

Create a fresh history:

```bash
HID=$(galaxy-cli --json history create "task run" | jq -r .id)
echo "$HID" > history_id.txt
```

Upload local datasets:

```bash
FWD=$(galaxy-cli --json dataset upload inputs/reads_1.fastq.gz --history-id "$HID" --file-type fastqsanger.gz | jq -r .id)
REV=$(galaxy-cli --json dataset upload inputs/reads_2.fastq.gz --history-id "$HID" --file-type fastqsanger.gz | jq -r .id)
```

Create collections:

```bash
PAIR=$(galaxy-cli --json collection create "pair" --history-id "$HID" --collection-type paired -e forward="$FWD" -e reverse="$REV" | jq -r .id)
LIST_PAIR=$(galaxy-cli --json collection create "reads" --history-id "$HID" --collection-type list:paired -p "pair:$FWD:$REV" | jq -r .id)
LIST=$(galaxy-cli --json collection create "reports" --history-id "$HID" --collection-type list -e pair="$DATASET_ID" | jq -r .id)
```

`collection create --json` includes resolved element IDs. Save its output if the
next tool needs a nested collection element; do not call `collection show` unless
the create output is insufficient.

Run a tool:

```bash
cat > tool_inputs.json <<EOF
{
  "input": "hda:$DATASET_ID"
}
EOF
galaxy-cli --json tool run "$TOOL_ID" --history-id "$HID" --inputs-json tool_inputs.json --wait > tool_result.json
JOB=$(jq -r '.jobs[0].id' tool_result.json)
```

Check job and dataset states:

```bash
galaxy-cli --json job show "$JOB" --full > job.json
jq '{id,state,tool_id,outputs:.outputs}' job.json
galaxy-cli --json dataset show "$DATASET_ID" > dataset.json
```

Download outputs only when the task asks for local artifacts:

```bash
galaxy-cli --json dataset download "$DATASET_ID" -o results/output.dat
```

## Input Encoding

- Dataset: `hda:DATASET_ID`
- Dataset collection: `hdca:COLLECTION_ID`
- Boolean: `true` or `false`
- Conditional or repeat params: prefer nested JSON in `--inputs-json`.
- Flattened conditional paths use pipes when needed, for example
  `single_paired|paired_input`.

## What To Read Next

- For command syntax, run `galaxy-cli <group> --help` or
  `galaxy-cli <group> <command> --help`.
- For tool parameters, use the task's `workflow/step_specs.json`,
  `workflow/required_step_params.json`, and `workflow/step_execution_hints.json`.
- Do not read package source code. The command help and task files are enough.
