# Galaxy CLI Test Plan & Results

## Test Inventory Plan

- `test_core.py`: ~45 unit tests planned
- `test_full_e2e.py`: ~15 E2E tests planned (against a real Galaxy server)

## Unit Test Plan (`test_core.py`)

### Config Module (`core/config.py`)
- `test_set_url` — saves URL to config file
- `test_set_key` — saves API key to config file
- `test_show_config` — reads config, masks API key
- `test_show_config_empty` — handles missing config file
- `test_test_connection` — verifies connection result structure

### History Module (`core/history.py`)
- `test_list_histories` — returns formatted list
- `test_create_history` — creates with name, returns ID
- `test_show_history` — returns full details
- `test_show_history_with_contents` — includes dataset list
- `test_delete_history` — marks as deleted
- `test_delete_history_purge` — permanently purges
- `test_update_history` — updates name/annotation
- `test_export_history` — starts export

### Dataset Module (`core/dataset.py`)
- `test_upload_dataset` — parses upload response
- `test_show_dataset` — returns dataset details
- `test_show_dataset_with_history` — uses history-scoped endpoint
- `test_download_dataset` — writes file to disk
- `test_peek_dataset` — returns preview lines
- `test_delete_dataset` — marks as deleted
- `test_list_datasets` — lists history contents

### Tool Module (`core/tool.py`)
- `test_list_tools` — returns tool list
- `test_search_tools` — filters by query
- `test_show_tool` — returns inputs/outputs
- `test_run_tool` — submits tool execution

### Job Module (`core/job.py`)
- `test_list_jobs` — returns job list
- `test_show_job` — returns job details
- `test_show_job_full` — includes I/O details
- `test_cancel_job` — cancels job
- `test_wait_for_job_ok` — returns when job completes
- `test_wait_for_job_timeout` — returns timeout state

### Workflow Module (`core/workflow.py`)
- `test_list_workflows` — returns workflow list
- `test_show_workflow` — returns steps and inputs
- `test_import_workflow_from_file` — imports from JSON file
- `test_export_workflow` — exports to JSON
- `test_run_workflow` — invokes workflow
- `test_delete_workflow` — deletes workflow

### Invocation Module (`core/invocation.py`)
- `test_list_invocations` — returns invocation list
- `test_show_invocation` — returns steps and state
- `test_cancel_invocation` — cancels invocation

### Library Module (`core/library.py`)
- `test_list_libraries` — returns library list
- `test_create_library` — creates library
- `test_show_library` — returns details
- `test_list_library_contents` — returns contents
- `test_delete_library` — deletes library

### Session Module (`core/session.py`)
- `test_load_session_new` — returns defaults when no file
- `test_save_and_load_session` — round-trip persistence
- `test_set_current_history` — saves history reference
- `test_track_job` — saves last job ID
- `test_clear_session` — resets to defaults

### Galaxy Backend (`utils/galaxy_backend.py`)
- `test_client_no_url` — raises with instructions
- `test_client_no_key` — raises with instructions
- `test_client_from_env` — reads from environment
- `test_api_url_construction` — builds correct URLs

## E2E Test Plan (`test_full_e2e.py`)

E2E tests require a running Galaxy server. They test the full pipeline:
CLI command -> HTTP request -> Galaxy API -> response parsing.

### Server Connection
- `test_server_version` — verify Galaxy server is reachable
- `test_whoami` — verify authentication works

### History Workflow
- `test_create_list_delete_history` — full lifecycle

### Dataset Workflow
- `test_upload_and_download` — upload file, download it, verify content

### Tool Workflow
- `test_list_and_search_tools` — find tools on server
- `test_show_tool_details` — inspect tool inputs

### CLI Subprocess Tests
- `test_help` — `cli-galaxy --help` returns 0
- `test_version` — `cli-galaxy --version` returns version
- `test_config_show_json` — `--json config show` returns valid JSON
- `test_history_list_json` — `--json history list` returns JSON array
- `test_tool_list_json` — `--json tool list` returns JSON array

## Realistic Workflow Scenarios

### Scenario 1: Genomics Analysis Pipeline
- **Simulates**: Upload FASTQ, run quality control, align reads, view results
- **Operations**: history create -> dataset upload -> tool run (FastQC) -> job wait -> dataset peek
- **Verified**: Job completes successfully, output datasets created

### Scenario 2: Workflow Execution
- **Simulates**: Import a workflow, provide inputs, run it, check invocation
- **Operations**: workflow import -> workflow run -> invocation wait -> dataset download
- **Verified**: Invocation completes, output files exist

### Scenario 3: Library Data Management
- **Simulates**: Organize shared data in libraries
- **Operations**: library create -> library show -> library contents -> library delete
- **Verified**: Library lifecycle works end-to-end

---

## Test Results

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0

cli_anything/galaxy/tests/test_core.py::TestConfig::test_set_url PASSED
cli_anything/galaxy/tests/test_core.py::TestConfig::test_set_key PASSED
cli_anything/galaxy/tests/test_core.py::TestConfig::test_show_config PASSED
cli_anything/galaxy/tests/test_core.py::TestConfig::test_show_config_empty PASSED
cli_anything/galaxy/tests/test_core.py::TestConfig::test_test_connection PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_list_histories PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_create_history PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_show_history PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_show_history_with_contents PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_delete_history PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_delete_history_purge PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_update_history PASSED
cli_anything/galaxy/tests/test_core.py::TestHistory::test_export_history PASSED
cli_anything/galaxy/tests/test_core.py::TestDataset::test_upload_dataset PASSED
cli_anything/galaxy/tests/test_core.py::TestDataset::test_show_dataset PASSED
cli_anything/galaxy/tests/test_core.py::TestDataset::test_show_dataset_with_history PASSED
cli_anything/galaxy/tests/test_core.py::TestDataset::test_download_dataset PASSED
cli_anything/galaxy/tests/test_core.py::TestDataset::test_peek_dataset PASSED
cli_anything/galaxy/tests/test_core.py::TestDataset::test_delete_dataset PASSED
cli_anything/galaxy/tests/test_core.py::TestDataset::test_list_datasets PASSED
cli_anything/galaxy/tests/test_core.py::TestTool::test_list_tools PASSED
cli_anything/galaxy/tests/test_core.py::TestTool::test_search_tools PASSED
cli_anything/galaxy/tests/test_core.py::TestTool::test_show_tool PASSED
cli_anything/galaxy/tests/test_core.py::TestTool::test_run_tool PASSED
cli_anything/galaxy/tests/test_core.py::TestJob::test_list_jobs PASSED
cli_anything/galaxy/tests/test_core.py::TestJob::test_show_job PASSED
cli_anything/galaxy/tests/test_core.py::TestJob::test_show_job_full PASSED
cli_anything/galaxy/tests/test_core.py::TestJob::test_cancel_job PASSED
cli_anything/galaxy/tests/test_core.py::TestJob::test_wait_for_job_ok PASSED
cli_anything/galaxy/tests/test_core.py::TestJob::test_wait_for_job_timeout PASSED
cli_anything/galaxy/tests/test_core.py::TestWorkflow::test_list_workflows PASSED
cli_anything/galaxy/tests/test_core.py::TestWorkflow::test_show_workflow PASSED
cli_anything/galaxy/tests/test_core.py::TestWorkflow::test_import_workflow_from_file PASSED
cli_anything/galaxy/tests/test_core.py::TestWorkflow::test_export_workflow PASSED
cli_anything/galaxy/tests/test_core.py::TestWorkflow::test_run_workflow PASSED
cli_anything/galaxy/tests/test_core.py::TestWorkflow::test_delete_workflow PASSED
cli_anything/galaxy/tests/test_core.py::TestInvocation::test_list_invocations PASSED
cli_anything/galaxy/tests/test_core.py::TestInvocation::test_show_invocation PASSED
cli_anything/galaxy/tests/test_core.py::TestInvocation::test_cancel_invocation PASSED
cli_anything/galaxy/tests/test_core.py::TestLibrary::test_list_libraries PASSED
cli_anything/galaxy/tests/test_core.py::TestLibrary::test_create_library PASSED
cli_anything/galaxy/tests/test_core.py::TestLibrary::test_show_library PASSED
cli_anything/galaxy/tests/test_core.py::TestLibrary::test_list_library_contents PASSED
cli_anything/galaxy/tests/test_core.py::TestLibrary::test_delete_library PASSED
cli_anything/galaxy/tests/test_core.py::TestSession::test_load_session_new PASSED
cli_anything/galaxy/tests/test_core.py::TestSession::test_save_and_load_session PASSED
cli_anything/galaxy/tests/test_core.py::TestSession::test_set_current_history PASSED
cli_anything/galaxy/tests/test_core.py::TestSession::test_track_job PASSED
cli_anything/galaxy/tests/test_core.py::TestSession::test_clear_session PASSED
cli_anything/galaxy/tests/test_core.py::TestGalaxyBackend::test_client_no_url PASSED
cli_anything/galaxy/tests/test_core.py::TestGalaxyBackend::test_client_no_key PASSED
cli_anything/galaxy/tests/test_core.py::TestGalaxyBackend::test_client_from_env PASSED
cli_anything/galaxy/tests/test_core.py::TestGalaxyBackend::test_api_url_construction PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestServerConnection::test_server_version SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestServerConnection::test_whoami SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestHistoryE2E::test_create_list_delete_history SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestDatasetE2E::test_upload_and_download SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestToolE2E::test_list_and_search_tools SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestToolE2E::test_show_tool_details SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_help PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_version PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_config_show_json PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_session_show_json PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_history_subcommand_help PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_tool_subcommand_help PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_workflow_subcommand_help PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocess::test_job_subcommand_help PASSED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocessE2E::test_history_list_json SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocessE2E::test_tool_list_json SKIPPED
cli_anything/galaxy/tests/test_full_e2e.py::TestCLISubprocessE2E::test_full_history_lifecycle SKIPPED
================== 61 passed, 9 skipped, 1.13s ===================
```

## Summary Statistics

- **Total tests:** 70
- **Passed:** 61 (100% of runnable tests)
- **Skipped:** 9 (require live Galaxy server — set GALAXY_URL and GALAXY_API_KEY)
- **Failed:** 0
- **Execution time:** 1.13s

## Coverage Notes

- **Unit tests (53):** Complete coverage of all 9 core modules + backend client
- **CLI subprocess tests (8):** Verify installed CLI command, --help, --version, --json output
- **E2E server tests (9):** Ready to run against any Galaxy instance; skipped without server
- **Gap:** E2E tests require a running Galaxy server to validate real API integration.
  Run with `GALAXY_URL=... GALAXY_API_KEY=... pytest -v -s` to execute.
