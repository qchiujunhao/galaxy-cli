import json
import shlex
from pathlib import Path

from galaxy_cli.core.output_contract import envelope_v1, safe_next_commands


def test_dataset_next_command_is_shell_quoted_and_round_trips():
    output_id = "dataset id;echo nope"
    commands = safe_next_commands(
        {"success": True, "outputs": [{"id": output_id, "src": "hda"}]},
        "tool.run",
    )
    assert shlex.split(commands["preview_output"]) == [
        "galaxy-cli", "dataset", "preview", output_id, "--lines", "5"
    ]
    assert commands["use_output_as_input"] == f"hda:{output_id}"


def test_unknown_submission_never_suggests_resubmission():
    commands = safe_next_commands({
        "success": False,
        "submission_state": "unknown",
        "retry_safe": False,
        "job_ids": ["job-1"],
        "operation_receipt": "receipt-1",
    })
    assert commands["do_not_resubmit"] is True
    assert "diagnose" in commands
    assert "resume" in commands
    assert all("tool run" not in str(value) for value in commands.values())


def test_rejected_before_submission_does_not_suggest_resume():
    commands = safe_next_commands({
        "success": False,
        "submission_state": "not_submitted",
        "retry_safe": True,
        "operation_receipt": "receipt-1",
    })
    assert "resume" not in commands


def test_ambiguous_outputs_do_not_generate_object_commands():
    commands = safe_next_commands({
        "success": True,
        "outputs": [{"id": "a", "src": "hda"}, {"id": "b", "src": "hda"}],
    })
    assert commands == {}


def test_collection_next_command_does_not_guess_element_path():
    commands = safe_next_commands({
        "success": True,
        "outputs": [{"id": "collection-1", "src": "hdca"}],
    })
    assert shlex.split(commands["show_output_collection"]) == [
        "galaxy-cli", "collection", "show", "collection-1"
    ]
    assert commands["use_output_as_input"] == "hdca:collection-1"


def test_transient_output_only_suggests_receipt_resume():
    commands = safe_next_commands({
        "success": True,
        "state": "submitted",
        "outputs": [{"id": "dataset-1", "src": "hda", "state": "new"}],
        "operation_receipt": "receipt-1",
    }, "tool.run")
    assert commands == {
        "resume": "galaxy-cli operation resume receipt-1",
    }


def test_non_resumable_result_never_suggests_resume_again():
    commands = safe_next_commands({
        "id": "receipt-1",
        "state": "submitted",
        "resumable": False,
        "retry_safe": False,
        "recommended_action": "do_not_resubmit",
    }, "operation.resume")
    assert commands == {"do_not_resubmit": True}


def test_job_guidance_requires_one_unique_id_across_all_error_fields():
    ambiguous = safe_next_commands({
        "success": False,
        "jobs": [{"id": "job-1"}],
        "job_ids": ["job-1", "job-2"],
    })
    assert "diagnose" not in ambiguous

    unique = safe_next_commands({
        "success": False,
        "jobs": [{"id": "job-1"}],
        "job_ids": ["job-1"],
    })
    assert unique["diagnose"] == "galaxy-cli job diagnose job-1"


def test_envelope_schema_has_stable_top_level_fields():
    wrapped = envelope_v1("tool.run", {"success": True, "outputs": []})
    assert list(wrapped) == [
        "schema_version", "command", "success", "data", "warnings", "next_commands"
    ]
    assert wrapped["schema_version"] == "1.0"
    assert wrapped["command"] == "tool.run"
    assert wrapped["success"] is True

    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "envelope-v1.json").read_text()
    )
    assert schema["required"] == list(wrapped)
    assert schema["properties"]["schema_version"]["const"] == "1.0"
