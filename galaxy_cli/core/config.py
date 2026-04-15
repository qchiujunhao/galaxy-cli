"""Configuration management for Galaxy CLI."""

import os

from galaxy_cli.utils.galaxy_backend import GalaxyClient


def set_url(url):
    """Save the Galaxy server URL."""
    GalaxyClient.save_config("url", url.rstrip("/"))
    return {"url": url.rstrip("/"), "status": "saved"}


def set_key(api_key):
    """Save the Galaxy API key."""
    GalaxyClient.save_config("api_key", api_key)
    return {"status": "saved"}


def show_config():
    """Show effective configuration, including environment overrides."""
    cfg = GalaxyClient.load_config()
    url = os.environ.get("GALAXY_URL") or cfg.get("url")
    key = os.environ.get("GALAXY_API_KEY") or cfg.get("api_key")
    result = {
        "url": url or "(not set)",
        "url_source": "env" if os.environ.get("GALAXY_URL") else ("config" if cfg.get("url") else "unset"),
        "api_key": "(not set)",
        "api_key_source": "env" if os.environ.get("GALAXY_API_KEY") else ("config" if cfg.get("api_key") else "unset"),
    }
    if key:
        result["api_key"] = f"***...{key[-4:]}" if len(key) >= 4 else "***"
    return result


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
