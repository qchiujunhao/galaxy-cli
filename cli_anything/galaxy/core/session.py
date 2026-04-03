"""Session management — local state persistence for CLI sessions.

Tracks current history, last job, preferences across CLI invocations.
"""

import fcntl
import json
from pathlib import Path

DEFAULT_SESSION_DIR = Path.home() / ".galaxy-cli"
DEFAULT_SESSION_FILE = DEFAULT_SESSION_DIR / "session.json"


def _locked_save_json(path, data):
    """Save JSON with exclusive file locking to prevent corruption."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}")
    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_session(session_path=None):
    """Load the current session state."""
    path = Path(session_path) if session_path else DEFAULT_SESSION_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "current_history_id": None,
        "current_history_name": None,
        "last_job_id": None,
        "last_dataset_id": None,
        "preferences": {},
    }


def save_session(session_data, session_path=None):
    """Save the current session state."""
    path = Path(session_path) if session_path else DEFAULT_SESSION_FILE
    _locked_save_json(path, session_data)
    return {"status": "saved", "path": str(path)}


def set_current_history(history_id, history_name=None, session_path=None):
    """Set the current working history."""
    session = load_session(session_path)
    session["current_history_id"] = history_id
    session["current_history_name"] = history_name
    save_session(session, session_path)
    return {"current_history_id": history_id, "current_history_name": history_name}


def get_current_history(session_path=None):
    """Get the current working history ID."""
    session = load_session(session_path)
    return {
        "current_history_id": session.get("current_history_id"),
        "current_history_name": session.get("current_history_name"),
    }


def track_job(job_id, session_path=None):
    """Track the last job ID in session."""
    session = load_session(session_path)
    session["last_job_id"] = job_id
    save_session(session, session_path)


def track_dataset(dataset_id, session_path=None):
    """Track the last dataset ID in session."""
    session = load_session(session_path)
    session["last_dataset_id"] = dataset_id
    save_session(session, session_path)


def show_session(session_path=None):
    """Show current session state."""
    return load_session(session_path)


def clear_session(session_path=None):
    """Clear session state."""
    path = Path(session_path) if session_path else DEFAULT_SESSION_FILE
    empty = {
        "current_history_id": None,
        "current_history_name": None,
        "last_job_id": None,
        "last_dataset_id": None,
        "preferences": {},
    }
    save_session(empty, session_path)
    return {"status": "cleared"}
