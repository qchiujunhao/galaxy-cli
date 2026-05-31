"""Install and inspect the bundled galaxy-cli agent skill."""

import os
from importlib import resources
from pathlib import Path

from galaxy_cli.utils.galaxy_backend import EXIT_USER_ERROR, GalaxyBackendError


SKILL_NAME = "galaxy-cli"
SUPPORTED_AGENTS = ("codex", "claude")


def bundled_skill_path():
    """Return the packaged SKILL.md path."""
    return resources.files("galaxy_cli") / "skills" / "SKILL.md"


def read_skill():
    """Return the bundled skill text."""
    return bundled_skill_path().read_text()


def default_skills_dir(agent):
    """Return the default user skill directory for a supported agent."""
    if agent == "codex":
        base = os.environ.get("CODEX_HOME")
        return Path(base).expanduser() / "skills" if base else Path.home() / ".codex" / "skills"
    if agent == "claude":
        base = os.environ.get("CLAUDE_HOME")
        return Path(base).expanduser() / "skills" if base else Path.home() / ".claude" / "skills"
    raise GalaxyBackendError(
        f"Unsupported skill agent: {agent}",
        category="invalid_request",
        exit_code=EXIT_USER_ERROR,
        suggestion=f"Use one of: {', '.join(SUPPORTED_AGENTS)}",
    )


def install_skill(agent="codex", target_dir=None, force=False):
    """Install the bundled skill under an agent's skill directory."""
    if agent not in SUPPORTED_AGENTS:
        raise GalaxyBackendError(
            f"Unsupported skill agent: {agent}",
            category="invalid_request",
            exit_code=EXIT_USER_ERROR,
            suggestion=f"Use one of: {', '.join(SUPPORTED_AGENTS)}",
        )

    source = bundled_skill_path()
    skills_dir = Path(target_dir).expanduser() if target_dir else default_skills_dir(agent)
    skill_dir = skills_dir / SKILL_NAME
    destination = skill_dir / "SKILL.md"
    source_text = source.read_text()
    existed = destination.exists()

    if existed:
        try:
            existing_text = destination.read_text()
        except OSError:
            existing_text = None
        if existing_text == source_text:
            return {
                "agent": agent,
                "name": SKILL_NAME,
                "source": str(source),
                "destination": str(destination),
                "status": "already_installed",
            }
        if not force:
            raise GalaxyBackendError(
                f"Skill already exists at {destination}",
                category="file_exists",
                exit_code=EXIT_USER_ERROR,
                suggestion="Use --force to overwrite it, or pass --target-dir to install elsewhere.",
            )

    skill_dir.mkdir(parents=True, exist_ok=True)
    destination.write_text(source_text)
    return {
        "agent": agent,
        "name": SKILL_NAME,
        "source": str(source),
        "destination": str(destination),
        "status": "updated" if existed else "installed",
    }


def skill_info():
    """Return metadata for the bundled skill."""
    path = bundled_skill_path()
    return {
        "name": SKILL_NAME,
        "path": str(path),
        "exists": path.is_file(),
    }
