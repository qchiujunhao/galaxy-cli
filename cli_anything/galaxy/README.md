# galaxy-cli

CLI harness for the [Galaxy](https://galaxyproject.org/) bioinformatics platform.
Wraps Galaxy's REST API to provide full command-line and REPL access to histories,
datasets, tools, workflows, jobs, and libraries.

This package was initially generated with `cli-anything` and then refined into
the standalone `galaxy-cli` package.

## Prerequisites

- **Python 3.10+**
- **A running Galaxy server** — this CLI connects to Galaxy via its REST API.
  - Public: https://usegalaxy.org, https://usegalaxy.eu, https://usegalaxy.org.au
  - Local: Follow [Galaxy installation docs](https://docs.galaxyproject.org/)
- **Galaxy API key** — obtain from your Galaxy instance at `<url>/user/api_key`

## Installation

Install from PyPI with `uv` or `pip`:

```bash
uv tool install galaxy-cli
```

```bash
python3 -m pip install galaxy-cli
```

For local development from this repository:

```bash
cd agent-harness
python3 -m pip install .
```

Verify installation:

```bash
which galaxy-cli
galaxy-cli --version
```

## Configuration

Set your Galaxy server URL and API key:

```bash
# Environment variables (preferred)
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key

# Or use the config commands
galaxy-cli config set-url https://usegalaxy.org
galaxy-cli config set-key your-api-key

# Test connection
galaxy-cli config test
```

## Usage

### One-shot commands

```bash
# List histories
galaxy-cli history list

# Create a history
galaxy-cli history create "My Analysis"

# Upload a file
galaxy-cli dataset upload data.fastq --history-id abc123

# Search for tools
galaxy-cli tool search "bowtie"

# Run a tool
galaxy-cli tool run toolshed.g2.bx.psu.edu/repos/.../bowtie2 \
  --history-id abc123 -i input=dataset-id

# Check job status
galaxy-cli job show job-id

# Run a workflow
galaxy-cli workflow run workflow-id -i 0=dataset-id
```

### JSON output (for agents)

```bash
galaxy-cli --json history list
galaxy-cli --json tool show bowtie2
```

### Interactive REPL

```bash
galaxy-cli
# Enters interactive mode
# Type commands without the "galaxy-cli" prefix
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
