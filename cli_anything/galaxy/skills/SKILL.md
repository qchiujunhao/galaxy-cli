---
name: "galaxy-cli"
description: "CLI harness for Galaxy bioinformatics platform — manage histories, datasets, tools, workflows, jobs, and libraries via command line"
---

# galaxy-cli

CLI harness for the Galaxy bioinformatics platform. Wraps Galaxy's REST API to
provide full command-line access to all Galaxy operations.

## Prerequisites

- **Python 3.9+** with `pip install galaxy-cli`
- **A running Galaxy server** (e.g., https://usegalaxy.org)
- **Galaxy API key** from `<galaxy-url>/user/api_key`

## Configuration

```bash
# Environment variables (recommended for agents)
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key

# Or use config commands
galaxy-cli config set-url https://usegalaxy.org
galaxy-cli config set-key your-api-key
galaxy-cli config test
```

## Command Syntax

```
galaxy-cli [--json] [--url URL] [--api-key KEY] COMMAND [SUBCOMMAND] [ARGS]
```

Use `--json` flag for machine-readable JSON output (recommended for agents).

## Command Groups

### config — Server Connection
| Command | Description |
|---------|-------------|
| `config set-url URL` | Set Galaxy server URL |
| `config set-key KEY` | Set API key |
| `config show` | Show current configuration |
| `config test` | Test server connection |

### history — Analysis Histories
| Command | Description |
|---------|-------------|
| `history list [--deleted]` | List histories |
| `history create NAME` | Create new history |
| `history show ID [--contents]` | Show history details |
| `history use ID` | Set current working history |
| `history delete ID [--purge]` | Delete a history |
| `history export ID` | Export history archive |

### dataset — Data Files
| Command | Description |
|---------|-------------|
| `dataset list [--history-id ID]` | List datasets in history |
| `dataset upload FILE [--history-id ID] [--file-type TYPE]` | Upload file |
| `dataset show ID` | Show dataset details |
| `dataset download ID OUTPUT_PATH` | Download dataset |
| `dataset peek ID [--lines N]` | Preview dataset content |
| `dataset delete ID [--history-id ID]` | Delete dataset |

### tool — Analysis Tools
| Command | Description |
|---------|-------------|
| `tool list [-q QUERY]` | List/search tools |
| `tool search QUERY` | Search tools by name |
| `tool show TOOL_ID` | Show tool inputs/outputs |
| `tool run TOOL_ID [--history-id ID] -i key=value [--wait --timeout SECS --poll-interval SECS]` | Run a tool |

### job — Job Monitoring
| Command | Description |
|---------|-------------|
| `job list [--state STATE] [--tool-id ID]` | List jobs |
| `job show JOB_ID [--full]` | Show job details |
| `job cancel JOB_ID` | Cancel a job |
| `job wait JOB_ID [--timeout SECS] [--poll-interval SECS]` | Wait for job completion |

### workflow — Analysis Pipelines
| Command | Description |
|---------|-------------|
| `workflow list [--published]` | List workflows |
| `workflow show WF_ID` | Show workflow steps |
| `workflow import FILE` | Import workflow from JSON |
| `workflow export WF_ID [-o FILE]` | Export workflow |
| `workflow run WF_ID -i step=dataset_id [--wait --timeout SECS --poll-interval SECS]` | Run workflow |
| `workflow delete WF_ID` | Delete workflow |

### invocation — Workflow Runs
| Command | Description |
|---------|-------------|
| `invocation list [--workflow-id ID]` | List invocations |
| `invocation show INV_ID` | Show invocation steps |
| `invocation cancel INV_ID` | Cancel invocation |
| `invocation wait INV_ID [--timeout SECS] [--poll-interval SECS]` | Wait for completion |

### library — Shared Data
| Command | Description |
|---------|-------------|
| `library list` | List data libraries |
| `library create NAME` | Create library |
| `library show LIB_ID` | Show library details |
| `library contents LIB_ID` | List library contents |
| `library delete LIB_ID` | Delete library |

### session — Local State
| Command | Description |
|---------|-------------|
| `session show` | Show session state |
| `session clear` | Clear session |

### user — User Info
| Command | Description |
|---------|-------------|
| `user whoami` | Show current user |

## Agent Usage Examples

### Example 1: Upload and Analyze Data
```bash
# Create a working history
galaxy-cli --json history create "RNA-seq Analysis"
# Upload input data
galaxy-cli --json dataset upload reads.fastq --file-type fastqsanger
# Run FastQC
galaxy-cli --json tool run fastqc -i input_file=DATASET_ID --wait
# Check results
galaxy-cli --json job show JOB_ID --full
```

### Example 2: Run a Workflow
```bash
# List available workflows
galaxy-cli --json workflow list
# Show workflow inputs
galaxy-cli --json workflow show WORKFLOW_ID
# Run with inputs
galaxy-cli --json workflow run WORKFLOW_ID -i 0=DATASET_ID --wait
# Check invocation
galaxy-cli --json invocation show INVOCATION_ID
```

### Example 3: Download Results
```bash
# List datasets in history
galaxy-cli --json dataset list --history-id HISTORY_ID
# Download output
galaxy-cli --json dataset download DATASET_ID ./output.tabular
```

## Agent Guidance

- Always use `--json` for programmatic output
- In `--json` mode, progress lines are sent to `stderr` so `stdout` remains valid JSON
- Set `GALAXY_URL` and `GALAXY_API_KEY` env vars before starting
- Use `history use ID` to set a default history, avoiding `--history-id` on every command
- Use `tool run --wait --timeout ... --poll-interval ...` to block until the job completes with explicit polling control
- Use `job wait` or `invocation wait` for polling with timeout and interval control
- Job states: `new`, `queued`, `running`, `ok`, `error`, `deleted`, `paused`
- Invocation states: `new`, `ready`, `scheduled`, `cancelled`, `failed`
- JSON error output is structured with `error`, `category`, `message`, and optional `suggestion`
