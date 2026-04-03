# Galaxy CLI Harness — Software-Specific SOP

## Software Overview

Galaxy is a web-based scientific analysis platform primarily used in bioinformatics
and computational biology. It provides a GUI for running analysis tools, managing
datasets, building workflows, and sharing results.

**Key difference from desktop GUI apps:** Galaxy is a client-server application.
The "real software" is a running Galaxy server instance. The CLI wraps Galaxy's
REST API to provide command-line access to all operations.

## Backend Engine

- **Server:** Python FastAPI/Gunicorn web application
- **Database:** PostgreSQL (production) / SQLite (development)
- **Job Runners:** Local, Slurm, Kubernetes, Pulsar, HTCondor, PBS, AWS Batch, GCP Batch
- **REST API:** 74+ endpoint modules at `/api/*`
- **Authentication:** API keys (header `x-api-key`, env `GALAXY_API_KEY`)

## GUI-to-API Mapping

| GUI Action | API Endpoint | CLI Command |
|-----------|-------------|-------------|
| Create history | POST /api/histories | `history create` |
| Upload file | POST /api/tools (upload1) | `dataset upload` |
| Run tool | POST /api/tools/{id} | `tool run` |
| View dataset | GET /api/datasets/{id} | `dataset show` |
| Download dataset | GET /api/datasets/{id}/display | `dataset download` |
| Create workflow | POST /api/workflows | `workflow import` |
| Run workflow | POST /api/workflows/{id}/invocations | `workflow run` |
| View job status | GET /api/jobs/{id} | `job show` |
| Manage libraries | GET/POST /api/libraries | `library list/create` |

## Data Model

- **History:** Container for analysis work. Holds datasets and metadata.
- **Dataset (HDA):** A data file within a history. Has datatype, metadata, state.
- **Job:** Execution of a tool. Has state (new, queued, running, ok, error).
- **Workflow:** Directed acyclic graph of tool steps.
- **Invocation:** An execution of a workflow.
- **Library:** Shared data collections with folder hierarchy.
- **Tool:** Analysis program with defined inputs/outputs/parameters.

## Existing CLI Tools

- **BioBlend:** Official Python API client (pip installable)
- **Planemo:** Tool development and testing CLI
- **Ephemeris:** Galaxy server setup and tool installation
- **/scripts/api/*.py:** Example API scripts in Galaxy source

## CLI Architecture

### Command Groups

1. **config** — Server connection settings (URL, API key)
2. **history** — Create, list, show, delete, export, import histories
3. **dataset** — Upload, download, show, delete, peek at datasets
4. **tool** — List, search, show, run tools
5. **job** — List, show, cancel, rerun jobs
6. **workflow** — Import, export, list, show, run, delete workflows
7. **invocation** — List, show, cancel, step status for workflow runs
8. **library** — Create, list, show, manage shared data libraries
9. **user** — Show current user info, API key management
10. **session** — Local session state management

### State Model

- **Connection state:** Galaxy URL + API key (persisted in config file or env vars)
- **Session state:** Current history, last job, preferences (JSON session file)
- **No project files:** Unlike desktop apps, Galaxy manages all data server-side

### Authentication

```
# Environment variable (preferred)
export GALAXY_URL=https://usegalaxy.org
export GALAXY_API_KEY=your-api-key

# CLI flags
galaxy-cli --url https://usegalaxy.org --api-key your-key

# Config file (~/.galaxy-cli/config.json)
galaxy-cli config set-url https://usegalaxy.org
galaxy-cli config set-key your-api-key
```

## Backend Integration

The CLI connects to a running Galaxy server via HTTP REST API using the `requests`
library. The Galaxy server is a **hard dependency** — the CLI is useless without
a reachable Galaxy instance.

The `galaxy_backend.py` module handles:
- Server URL and API key management
- HTTP request execution with proper headers
- Error handling with clear messages
- Response parsing (JSON)
- File upload/download streaming
