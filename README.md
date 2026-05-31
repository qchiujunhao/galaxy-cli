# galaxy-cli — Galaxy bioinformatics command-line client

`galaxy-cli` is a Python CLI and REPL for automating the
[Galaxy](https://galaxyproject.org/) bioinformatics platform from the shell. It
wraps the Galaxy REST API for histories, datasets, collections, tools,
workflows, jobs, and libraries.

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
