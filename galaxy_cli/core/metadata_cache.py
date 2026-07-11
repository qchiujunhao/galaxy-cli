"""Small, secret-free cache for read-only Galaxy metadata."""

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

from galaxy_cli.utils.galaxy_backend import DEFAULT_CONFIG_DIR


DEFAULT_CACHE_TTL = 24 * 60 * 60


def cache_root():
    configured = os.environ.get("GALAXY_CLI_CACHE_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_CONFIG_DIR / "cache"


def cache_ttl():
    try:
        ttl = float(os.environ.get("GALAXY_CLI_CACHE_TTL", DEFAULT_CACHE_TTL))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL
    return max(0.0, ttl)


def _path(namespace, key):
    identity = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return cache_root() / namespace / f"{digest}.json"


def _discard(path):
    try:
        path.unlink()
    except OSError:
        pass


def read(namespace, key, ttl=None):
    """Return a fresh cached value; silently discard invalid cache files."""
    path = _path(namespace, key)
    try:
        payload = json.loads(path.read_text())
        created_at = float(payload["created_at"])
        age = time.time() - created_at
        if (
            payload.get("key") != key
            or not math.isfinite(created_at)
            or age < 0
            or age > (cache_ttl() if ttl is None else ttl)
        ):
            raise ValueError("stale or mismatched cache entry")
        return payload["value"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        _discard(path)
        return None


def write(namespace, key, value):
    """Atomically write a read-only metadata cache entry."""
    path = _path(namespace, key)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        payload = {"key": key, "created_at": time.time(), "value": value}
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(path.parent), prefix=".cache-", delete=False
        ) as handle:
            json.dump(payload, handle, separators=(",", ":"), default=str)
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(str(temporary), str(path))
        return path
    except OSError:
        try:
            temporary.unlink()
        except (OSError, UnboundLocalError):
            pass
        return None


def server_version(client, refresh=False):
    """Return a cached Galaxy server version without storing credentials."""
    key = [client.url]
    if not refresh:
        cached = read("server-version", key)
        if cached is not None:
            return cached
    version = client.get_version()
    write("server-version", key, version)
    return version
