# galaxy-cli

CLI harness for the [Galaxy](https://galaxyproject.org/) bioinformatics platform.
It wraps Galaxy's REST API to provide command-line and REPL access to histories,
datasets, tools, workflows, jobs, and libraries.

This project was initially generated with `cli-anything` and then refined into
the standalone `galaxy-cli` package.

## Prerequisites

- Python 3.10+
- A running Galaxy server
- A Galaxy API key from your target instance

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
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key

galaxy-cli config test
```

## Usage

```bash
galaxy-cli history list
galaxy-cli history create "My Analysis"
galaxy-cli tool search "bowtie"
galaxy-cli --json workflow list
```

Run the REPL:

```bash
galaxy-cli
```

## Tests

```bash
python3 -m pytest cli_anything/galaxy/tests/ -v
```
