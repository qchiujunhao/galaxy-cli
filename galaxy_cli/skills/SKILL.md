---
name: "galaxy-cli"
description: "CLI harness for Galaxy bioinformatics platform — manage histories, datasets, tools, workflows, jobs, and libraries via command line"
---

# galaxy-cli

CLI harness for the Galaxy bioinformatics platform. Wraps Galaxy's REST API to
provide full command-line access to all Galaxy operations. Designed to be
efficient for LLM agents: compact single-line JSON output, structured errors,
and a `--inputs-json` flag for complex tool parameters.

## Prerequisites

- **Python 3.9+** with `pip install galaxy-cli`
- **A running Galaxy server** (e.g., https://usegalaxy.org)
- **Galaxy API key** from `<galaxy-url>/user/api_key`

## Configuration (env-based, recommended for agents)

```bash
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key
galaxy-cli --json config test
```

`GALAXY_URL` / `GALAXY_API_KEY` are picked up automatically on every command, so
agents should use environment-based authentication and pass `--history-id` per
call instead of relying on stored session state.

## Command Syntax

```
galaxy-cli [--json] [--url URL] [--api-key KEY] [--history-id ID] COMMAND [SUBCOMMAND] [ARGS]
```

Always pass `--json` for agent/script use — it emits single-line JSON on stdout
while progress goes to stderr, so `stdout` stays parseable.

## Command Groups

### config — Server Connection
| Command | Description |
|---------|-------------|
| `config show` | Show current configuration (from env + stored config) |
| `config test` | Test connection to the Galaxy server |

### history — Analysis Histories
| Command | Description |
|---------|-------------|
| `history list [--deleted]` | List histories |
| `history create NAME` | Create new history (returns id) |
| `history show ID [--contents]` | Show history details |
| `history use ID` | Set current working history (persists in session) |
| `history delete ID [--purge]` | Delete a history |
| `history export ID` | Export history archive |

### dataset — Data Files
| Command | Description |
|---------|-------------|
| `dataset list [--history-id ID]` | List datasets in history |
| `dataset upload FILE [--history-id ID] [--file-type TYPE]` | Upload file |
| `dataset show ID` | Show dataset details (state, extension, size) |
| `dataset download ID -o PATH` | Download dataset to local file |
| `dataset peek ID [--lines N]` | Preview dataset content |
| `dataset delete ID [--history-id ID]` | Delete dataset |

### tool — Analysis Tools
| Command | Description |
|---------|-------------|
| `tool list [-q QUERY]` | List available tools |
| `tool search QUERY` | Search tools by name/description |
| `tool show TOOL_ID [--full]` | Show tool inputs/outputs (compact by default) |
| `tool run TOOL_ID [--history-id ID] [-i k=v]... [--inputs-json FILE] [--wait]` | Run a tool |

### collection — Dataset Collections
| Command | Description |
|---------|-------------|
| `collection create NAME --history-id ID -e DATASET_ID` | Simple list collection |
| `collection create NAME --history-id ID -e name1=ID1 -e name2=ID2` | Named-element list |
| `collection create NAME --history-id ID --collection-type list:paired -p "name:fwd_id:rev_id"` | Paired list |
| `collection list --history-id ID` | List collections in history |
| `collection show COLLECTION_ID` | Show collection structure |

### job — Job Monitoring
| Command | Description |
|---------|-------------|
| `job list [--state STATE] [--tool-id ID]` | List jobs |
| `job show JOB_ID [--full]` | Show job details (`--full` adds stdout/stderr/I-O) |
| `job cancel JOB_ID` | Cancel a job |
| `job wait JOB_ID [--timeout SECS] [--poll-interval SECS]` | Wait for completion |

### workflow — Analysis Pipelines
| Command | Description |
|---------|-------------|
| `workflow list [--published]` | List workflows |
| `workflow show WF_ID` | Show workflow steps |
| `workflow import FILE` | Import workflow from JSON or URL |
| `workflow export WF_ID [-o FILE]` | Export workflow |
| `workflow run WF_ID -i step_index=DATASET_ID [--wait]` | Run workflow |
| `workflow delete WF_ID` | Delete workflow |

### invocation — Workflow Runs
| Command | Description |
|---------|-------------|
| `invocation list [--workflow-id ID]` | List invocations |
| `invocation show INV_ID` | Show invocation steps |
| `invocation cancel INV_ID` | Cancel invocation |
| `invocation wait INV_ID [--timeout SECS]` | Wait for completion |

### library, session, user — Misc
| Command | Description |
|---------|-------------|
| `library list / create / show / contents / delete` | Shared data libraries |
| `session show / clear` | Local CLI session state |
| `user whoami` | Current user info |

## Tool Input Encoding — READ THIS BEFORE RUNNING A TOOL

This is the single biggest pitfall agents hit. Galaxy's `tool run` parameters
use a specific encoding that the CLI passes through unchanged.

**Datasets and collections**

| Kind | Value |
|------|-------|
| HDA (dataset in history) | `-i input=hda:DATASET_ID` or just `-i input=DATASET_ID` |
| HDCA (dataset collection) | `-i input=hdca:COLLECTION_ID` |
| LDDA (library dataset) | `-i input=ldda:LIB_DATASET_ID` |

**Primitives**

| Type | Example |
|------|---------|
| Boolean | `-i some_flag=true` (use `true`/`false`, not `yes`/`no`, not `truevalue`) |
| Integer / float | `-i threshold=0.5` |
| Select | `-i op=mean` (use the `value` from `tool show`, not the `label`) |
| data_column | `-i column=2` (1-based column index, passed as a string) |
| Text | `-i pattern="^chr[0-9]+"` |

**Repeats** — use pipe syntax with a 0-based index per item:

```
-i operations_0|op_name=mean
-i operations_0|op_column=2
-i operations_1|op_name=sum
-i operations_1|op_column=3
```

**Conditionals** — same pipe-flattened form:

```
-i cond|selector=advanced
-i cond|threshold=0.5
```

**Preferred for any non-trivial tool: `--inputs-json`**

Pass the whole input dict as a JSON file. The JSON structure mirrors Galaxy's
native nested format (lists for repeats, dicts for conditionals), and the CLI
flattens it to the pipe form automatically before posting.

```bash
cat > params.json <<'EOF'
{
  "input": "hda:abc123",
  "operations": [
    {"op_name": "mean", "op_column": "2"},
    {"op_name": "sum",  "op_column": "3"}
  ],
  "cond": {"selector": "advanced", "threshold": "0.5"}
}
EOF

galaxy-cli --json tool run datamash_ops \
    --history-id "$HID" --inputs-json params.json --wait
```

`-i` flags can be combined with `--inputs-json`; `-i` values override matching
JSON keys. This is the fastest, least-error-prone way to run any tool with more
than two parameters.

## Agent Usage Examples

### Full task end-to-end

```bash
source .env

# 1. Create history, save id
HID=$(galaxy-cli --json history create "benchmark-T01" | jq -r .id)
echo "$HID" > history_id.txt

# 2. Upload input
DSID=$(galaxy-cli --json dataset upload inputs/genes.tabular \
         --history-id "$HID" | jq -r .id)

# 3. Find the tool (skip this step if the task already names it)
galaxy-cli --json tool search "cut"

# 4. Inspect inputs (skip if you already know them)
galaxy-cli --json tool show Cut1

# 5. Run with --inputs-json, --wait for completion
cat > params.json <<EOF
{"input": "hda:$DSID", "columnList": "c1,c3", "delimiter": "T"}
EOF
RESULT=$(galaxy-cli --json tool run Cut1 \
          --history-id "$HID" --inputs-json params.json --wait)

# 6. Verify output state
OUT_ID=$(echo "$RESULT" | jq -r '.outputs[0].id')
galaxy-cli --json dataset show "$OUT_ID" | jq .state
```

### Running a workflow

```bash
galaxy-cli --json workflow import workflow.ga
galaxy-cli --json workflow show "$WF_ID"
galaxy-cli --json workflow run "$WF_ID" --history-id "$HID" \
    -i 0=DATASET_ID_FOR_STEP_0 -i 1=DATASET_ID_FOR_STEP_1 --wait
```

## Agent Guidance

- **Always use `--json`.** Never parse the human-readable output — it will
  break on the next version. Single-line JSON in stdout, progress in stderr.
- **Use `jq` (or `python -c`) to extract IDs into shell variables.** Do not
  paste entire JSON blobs back into later commands.
- **Pass `--history-id` on every command.** Avoids depending on session state.
- **Use `--wait` on `tool run`** so you don't need a separate `job wait`.
- **Skip `tool show` when the task description already names the inputs.**
  Each `tool show` is ~1–3 KB of JSON the agent has to re-read every turn.
- **Use `--inputs-json` from the start** for any tool with more than two
  parameters or any tool with repeats/conditionals.
- **Never read galaxy-cli source code.** Everything you need is in this
  document plus `<command> --help`.
- **Job states:** `new`, `queued`, `running`, `ok`, `error`, `deleted`, `paused`.
- **Invocation states:** `new`, `ready`, `scheduled`, `cancelled`, `failed`.
- **JSON errors** are structured: `{"error": true, "category": ..., "message": ..., "suggestion": ...}`.
