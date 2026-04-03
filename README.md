# cli-galaxy

CLI harness for the [Galaxy](https://galaxyproject.org/) bioinformatics platform.
It wraps Galaxy's REST API to provide command-line and REPL access to histories,
datasets, tools, workflows, jobs, and libraries.

This project was initially generated with `cli-anything` and then refined into
the standalone `cli-galaxy` package.

## Prerequisites

- Python 3.10+
- A running Galaxy server
- A Galaxy API key from your target instance

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
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key

cli-galaxy config test
```

## Usage

```bash
cli-galaxy history list
cli-galaxy history create "My Analysis"
cli-galaxy tool search "bowtie"
cli-galaxy --json workflow list
```

Run the REPL:

```bash
cli-galaxy
```

## Tests

```bash
python3 -m pytest cli_anything/galaxy/tests/ -v
```
