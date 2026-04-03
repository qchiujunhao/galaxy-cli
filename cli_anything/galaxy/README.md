# cli-galaxy

CLI harness for the [Galaxy](https://galaxyproject.org/) bioinformatics platform.
Wraps Galaxy's REST API to provide full command-line and REPL access to histories,
datasets, tools, workflows, jobs, and libraries.

This package was initially generated with `cli-anything` and then refined into
the standalone `cli-galaxy` package.

## Prerequisites

- **Python 3.10+**
- **A running Galaxy server** — this CLI connects to Galaxy via its REST API.
  - Public: https://usegalaxy.org, https://usegalaxy.eu, https://usegalaxy.org.au
  - Local: Follow [Galaxy installation docs](https://docs.galaxyproject.org/)
- **Galaxy API key** — obtain from your Galaxy instance at `<url>/user/api_key`

## Installation

Install from PyPI with `uv` or `pip`:

```bash
uv tool install cli-galaxy
```

```bash
python3 -m pip install cli-galaxy
```

For local development from this repository:

```bash
cd agent-harness
python3 -m pip install .
```

Verify installation:

```bash
which cli-galaxy
cli-galaxy --version
```

## Configuration

Set your Galaxy server URL and API key:

```bash
# Environment variables (preferred)
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key

# Or use the config commands
cli-galaxy config set-url https://usegalaxy.org
cli-galaxy config set-key your-api-key

# Test connection
cli-galaxy config test
```

## Usage

### One-shot commands

```bash
# List histories
cli-galaxy history list

# Create a history
cli-galaxy history create "My Analysis"

# Upload a file
cli-galaxy dataset upload data.fastq --history-id abc123

# Search for tools
cli-galaxy tool search "bowtie"

# Run a tool
cli-galaxy tool run toolshed.g2.bx.psu.edu/repos/.../bowtie2 \
  --history-id abc123 -i input=dataset-id

# Check job status
cli-galaxy job show job-id

# Run a workflow
cli-galaxy workflow run workflow-id -i 0=dataset-id
```

### JSON output (for agents)

```bash
cli-galaxy --json history list
cli-galaxy --json tool show bowtie2
```

### Interactive REPL

```bash
cli-galaxy
# Enters interactive mode
# Type commands without the "cli-galaxy" prefix
```

## Command Groups

| Group | Description |
|-------|-------------|
| `config` | Server connection settings |
| `history` | Create, list, show, delete, export histories |
| `dataset` | Upload, download, show, peek, delete datasets |
| `tool` | List, search, show, run tools |
| `job` | List, show, cancel, wait for jobs |
| `workflow` | Import, export, list, show, run, delete workflows |
| `invocation` | List, show, cancel, wait for workflow invocations |
| `library` | Create, list, show, manage shared data libraries |
| `user` | Current user info |
| `session` | Local CLI session state |

## Running Tests

```bash
cd agent-harness
python3 -m pytest cli_anything/galaxy/tests/ -v -s
```

Force testing against installed command:

```bash
CLI_ANYTHING_FORCE_INSTALLED=1 python3 -m pytest cli_anything/galaxy/tests/ -v -s
```
