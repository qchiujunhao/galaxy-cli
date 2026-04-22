"""Configuration and profile management for Galaxy CLI."""

import os

from galaxy_cli.utils.galaxy_backend import GalaxyClient


def _mask(key):
    if not key:
        return "(not set)"
    return f"***...{key[-4:]}" if len(key) >= 4 else "***"


def set_url(url):
    """Save the Galaxy server URL (legacy top-level config)."""
    GalaxyClient.save_config("url", url.rstrip("/"))
    return {"url": url.rstrip("/"), "status": "saved"}


def set_key(api_key):
    """Save the Galaxy API key (legacy top-level config)."""
    GalaxyClient.save_config("api_key", api_key)
    return {"status": "saved"}


def show_config():
    """Show effective configuration, including environment and profile overrides."""
    cfg = GalaxyClient.load_config()
    active = cfg.get("active_profile")
    env_url = os.environ.get("GALAXY_URL")
    env_key = os.environ.get("GALAXY_API_KEY")

    url = env_url or GalaxyClient._load_config_value("url")
    key = env_key or GalaxyClient._load_config_value("api_key")

    if env_url:
        url_source = "env"
    elif active and cfg.get("profiles", {}).get(active, {}).get("url"):
        url_source = f"profile:{active}"
    elif cfg.get("url"):
        url_source = "config"
    else:
        url_source = "unset"

    if env_key:
        key_source = "env"
    elif active and cfg.get("profiles", {}).get(active, {}).get("api_key"):
        key_source = f"profile:{active}"
    elif cfg.get("api_key"):
        key_source = "config"
    else:
        key_source = "unset"

    return {
        "url": url or "(not set)",
        "url_source": url_source,
        "api_key": _mask(key),
        "api_key_source": key_source,
        "active_profile": active,
        "profiles": sorted((cfg.get("profiles") or {}).keys()),
    }


def test_connection(client):
    """Test the connection to Galaxy server."""
    version = client.get_version()
    user = client.whoami()
    return {
        "status": "connected",
        "galaxy_version": version.get("version_major", "unknown"),
        "extra": version.get("version_minor", ""),
        "user": user.get("username", "unknown"),
        "email": user.get("email", "unknown"),
        "user_id": user.get("id", "unknown"),
    }


# ── Profile management ──────────────────────────────────────────────────

def add_profile(name, url, api_key, use=False):
    """Create or update a named profile."""
    saved = GalaxyClient.save_profile(name, url, api_key, use=use)
    cfg = GalaxyClient.load_config()
    return {
        "name": name,
        "url": saved["url"],
        "api_key": _mask(saved["api_key"]),
        "active": cfg.get("active_profile") == name,
        "status": "saved",
    }


def list_profiles():
    """Return all profiles with the active one flagged."""
    cfg = GalaxyClient.load_config()
    active = cfg.get("active_profile")
    profiles = cfg.get("profiles") or {}
    return [
        {
            "name": name,
            "url": p.get("url", ""),
            "api_key": _mask(p.get("api_key")),
            "active": name == active,
        }
        for name, p in sorted(profiles.items())
    ]


def show_profile(name=None):
    """Show a specific profile, or the active profile if name omitted."""
    cfg = GalaxyClient.load_config()
    target = name or cfg.get("active_profile")
    if not target:
        return {"error": True, "message": "No active profile. Use: galaxy-cli profile use <name>"}
    profiles = cfg.get("profiles") or {}
    if target not in profiles:
        return {"error": True, "message": f"Profile not found: {target}"}
    p = profiles[target]
    return {
        "name": target,
        "url": p.get("url", ""),
        "api_key": _mask(p.get("api_key")),
        "active": cfg.get("active_profile") == target,
    }


def use_profile(name):
    """Set the active profile."""
    try:
        GalaxyClient.use_profile(name)
    except KeyError:
        return {"error": True, "message": f"Profile not found: {name}"}
    return {"active_profile": name, "status": "saved"}


def remove_profile(name):
    """Delete a profile by name."""
    try:
        GalaxyClient.remove_profile(name)
    except KeyError:
        return {"error": True, "message": f"Profile not found: {name}"}
    return {"name": name, "status": "removed"}
