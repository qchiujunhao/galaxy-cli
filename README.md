# galaxy-cli — Galaxy bioinformatics command-line client

`galaxy-cli` is a Python CLI and REPL for automating the
[Galaxy](https://galaxyproject.org/) bioinformatics platform from the shell. It
wraps the Galaxy REST API for histories, datasets, collections, tools,
user-defined tools, workflows, jobs, and libraries.

This project was initially generated with `cli-anything` and then refined into
the standalone `galaxy-cli` package.

```bash
pip install galaxy-cli
```

## What can galaxy-cli do?

`galaxy-cli` is a Galaxy bioinformatics CLI for users who want a Galaxy
command-line client that works naturally in shell scripts and reproducible
Galaxy analyses. You can use it to:

- run Galaxy workflows from the command line and submit individual Galaxy tools
- upload and download datasets
- create and manage histories and collections
- inspect jobs, logs, tools, and workflow metadata
- automate Galaxy workflows for reproducible Galaxy bioinformatics analyses

## Prerequisites

- Python 3.9+
- A running Galaxy server
- A Galaxy API key from your target instance

## Installation

Install from PyPI with `uv` or `pip`:

```bash
uv tool install galaxy-cli
```

```bash
pip install galaxy-cli
```

For local development from this repository:

```bash
python3 -m pip install .
```

Verify installation:

```bash
which galaxy-cli
galaxy-cli --version
```

## AI Agent Skill

The package includes a `galaxy-cli` skill for Codex and Claude Code so agents
can use the CLI without rediscovering command syntax.

```bash
# Install for Codex: ~/.codex/skills/galaxy-cli/SKILL.md
galaxy-cli skill install --agent codex

# Install for Claude Code: ~/.claude/skills/galaxy-cli/SKILL.md
galaxy-cli skill install --agent claude
```

Use `galaxy-cli skill path` to find the packaged skill, or
`galaxy-cli skill install --target-dir /path/to/skills` for another agent or
project-level skills directory.

## Configuration

Set your Galaxy server URL and API key:

```bash
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key

galaxy-cli config test
```

For secret-file environments, set `GALAXY_API_KEY_FILE` instead. Explicit
`--api-key` and `GALAXY_API_KEY` values take precedence over the file; the CLI
reads the file without exposing its contents:

```bash
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY_FILE=.secrets/galaxy-api-key
galaxy-cli config test
```

Session state is stored in `~/.galaxy-cli/session.json`. It is intended for a
single active writer; for parallel automation or multiple concurrent agents,
pass `--history-id` explicitly instead of relying on shared session state.

## Usage

Documentation site: https://qchiujunhao.github.io/galaxy-cli/

```bash
galaxy-cli history list
galaxy-cli history create "My Analysis"
galaxy-cli tool search "bowtie" --limit 5
galaxy-cli workflow list
galaxy-cli workflow list | jq .
galaxy-cli --human config show
```

`galaxy-cli` defaults to compact JSON output. Use `--human` for
human-readable terminal output.

Create and run a fresh Galaxy user-defined tool in one blocking command:

```bash
galaxy-cli udt create-run \
  --representation-json udt.json \
  --history-id HIST_ID \
  --inputs-json inputs.json
```

The representation file contains the inner `GalaxyUserTool` object; the CLI
constructs the API envelope, resolves the returned UUID, submits through the
portable tool execution endpoint, waits for every spawned job, and returns
compact job and output metadata. Add `--evidence-dir evidence` to save redacted
full request and response evidence without expanding stdout.

Tool and workflow submissions validate obvious input mistakes before posting to
Galaxy, including unknown input names, missing required data inputs, invalid
dataset-vs-collection source prefixes, and simple select/boolean/numeric value
errors. Use `--dry-run-payload` to validate and print the exact Galaxy POST
body without submitting:

```bash
galaxy-cli tool run TOOL_ID --history-id HIST_ID -i input=DATASET_ID --dry-run-payload
galaxy-cli workflow run WF_ID --history-id HIST_ID -i 0=DATASET_ID --dry-run-payload
```

For large dataset uploads, use `--upload-timeout` to control the HTTP POST
separately from the upload job wait. `GALAXY_CLI_REQUEST_TIMEOUT` controls
regular API request reads, and `GALAXY_CLI_UPLOAD_TIMEOUT` controls uploads.

Run the REPL:

```bash
galaxy-cli
```

## Tests

```bash
python3 -m pytest galaxy_cli/tests/ -v
```

## Releases

Releases are published from GitHub Releases through PyPI Trusted Publishing.
See [RELEASE.md](RELEASE.md) for the one-time PyPI setup and release checklist.
