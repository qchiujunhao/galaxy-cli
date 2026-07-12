"""Small, secret-free cache for read-only Galaxy metadata."""

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from galaxy_cli.utils.galaxy_backend import DEFAULT_CONFIG_DIR


DEFAULT_CACHE_TTL = 24 * 60 * 60
_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COUNTER_NAMES = ("hit", "miss", "expired", "corrupt")
_COUNTERS = {name: 0 for name in _COUNTER_NAMES}
_COUNTER_LOCK = threading.Lock()


def cache_root():
    configured = os.environ.get("GALAXY_CLI_CACHE_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_CONFIG_DIR / "cache"


def cache_ttl():
    try:
        ttl = float(os.environ.get("GALAXY_CLI_CACHE_TTL", DEFAULT_CACHE_TTL))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL
    if not math.isfinite(ttl):
        return DEFAULT_CACHE_TTL
    return max(0.0, ttl)


def server_identity(url):
    """Return a cache-safe server identity without URL credentials or queries."""
    try:
        parsed = urlsplit(str(url))
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return "invalid-server"
        hostname = parsed.hostname.lower()
        host = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            host += f":{parsed.port}"
        path = parsed.path or "/"
        return urlunsplit((parsed.scheme.lower(), host, path, "", ""))
    except (TypeError, ValueError):
        return "invalid-server"


def _namespace_dir(namespace):
    if not isinstance(namespace, str) or not _NAMESPACE_PATTERN.fullmatch(namespace):
        raise ValueError("Cache namespace must contain only letters, numbers, '.', '_' or '-'.")
    return cache_root() / namespace


def _identity(key):
    return json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)


def _digest(key):
    return hashlib.sha256(_identity(key).encode("utf-8")).hexdigest()


def _path(namespace, key):
    return _namespace_dir(namespace) / f"{_digest(key)}.json"


def _count(outcome):
    with _COUNTER_LOCK:
        _COUNTERS[outcome] += 1


def _normalized_secrets(secrets):
    return tuple(
        secret for secret in secrets if isinstance(secret, str) and secret
    )


def cache_key(value, secrets=()):
    """Redact known secrets before a key is hashed or persisted."""
    return _redact_known(value, _normalized_secrets(secrets))


def invocation_counters(reset=False):
    """Return process-local cache lookup counters without persistent writes."""
    with _COUNTER_LOCK:
        result = dict(_COUNTERS)
        if reset:
            for name in _COUNTER_NAMES:
                _COUNTERS[name] = 0
    return result


def _discard(path):
    try:
        path.unlink()
    except OSError:
        pass


def read(namespace, key, ttl=None, secrets=()):
    """Return a fresh cached value; silently discard invalid cache files."""
    key = cache_key(key, secrets)
    path = _path(namespace, key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _count("miss")
        return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        _count("corrupt")
        _discard(path)
        return None
    try:
        created_at = float(payload["created_at"])
        age = time.time() - created_at
        if payload.get("key") != key or not math.isfinite(created_at) or age < 0:
            raise ValueError("mismatched cache entry")
        if age > (cache_ttl() if ttl is None else float(ttl)):
            _count("expired")
            _discard(path)
            return None
        value = payload["value"]
    except (KeyError, TypeError, ValueError):
        _count("corrupt")
        _discard(path)
        return None
    _count("hit")
    return value


def _redact_known(value, secrets):
    if isinstance(value, dict):
        return {
            _redact_known(key, secrets): _redact_known(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_known(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [_redact_known(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def write(namespace, key, value, secrets=()):
    """Atomically write a read-only metadata cache entry."""
    secrets = _normalized_secrets(secrets)
    key = cache_key(key, secrets)
    path = _path(namespace, key)
    temporary = None
    try:
        root = cache_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        payload = {
            "key": key,
            "created_at": time.time(),
            "value": _redact_known(value, secrets),
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=".cache-", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, separators=(",", ":"), default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(str(temporary), str(path))
        return path
    except (OSError, TypeError, ValueError, RecursionError):
        return None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _server_identity_hash(key):
    candidate = key[0] if isinstance(key, (list, tuple)) and key else None
    if not isinstance(candidate, str):
        return None
    try:
        identity = server_identity(candidate)
        if identity == "invalid-server":
            return None
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _inspect_entry(path, ttl, now):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "key" not in payload or "value" not in payload:
            raise ValueError("missing cache fields")
        created_at = float(payload["created_at"])
        age = now - created_at
        if (
            not math.isfinite(created_at)
            or age < 0
            or path.name != f"{_digest(payload['key'])}.json"
        ):
            raise ValueError("invalid cache metadata")
        status = "stale" if age > ttl else "fresh"
        return status, _server_identity_hash(payload["key"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "corrupt", None


def stats(namespace=None):
    """Return cache metadata only; never return cached keys or values."""
    root = cache_root()
    ttl = cache_ttl()
    now = time.time()
    if namespace is not None:
        directories = [(namespace, _namespace_dir(namespace))]
    else:
        try:
            directories = [
                (path.name, path)
                for path in sorted(root.iterdir(), key=lambda item: item.name)
                if path.is_dir() and not path.is_symlink()
            ]
        except OSError:
            directories = []

    namespaces = {}
    all_hashes = set()
    totals = {"entry_count": 0, "bytes": 0, "fresh": 0, "stale": 0, "corrupt": 0}
    for name, directory in directories:
        summary = {
            "entry_count": 0,
            "bytes": 0,
            "fresh": 0,
            "stale": 0,
            "corrupt": 0,
            "server_identity_hashes": [],
        }
        try:
            entries = [
                path for path in directory.iterdir()
                if path.is_file() and not path.is_symlink() and path.suffix == ".json"
            ]
        except OSError:
            entries = []
        hashes = set()
        for path in entries:
            summary["entry_count"] += 1
            try:
                summary["bytes"] += path.stat().st_size
            except OSError:
                pass
            status, server_hash = _inspect_entry(path, ttl, now)
            summary[status] += 1
            if server_hash:
                hashes.add(server_hash)
        summary["server_identity_hashes"] = sorted(hashes)
        namespaces[name] = summary
        all_hashes.update(hashes)
        for field in ("entry_count", "bytes", "fresh", "stale", "corrupt"):
            totals[field] += summary[field]

    return {
        **totals,
        "ttl_seconds": ttl,
        "server_identity_hashes": sorted(all_hashes),
        "invocation_counters": invocation_counters(),
        "namespaces": namespaces,
    }


def clear(namespace=None):
    """Remove cache entries from one namespace or from the whole cache."""
    root = cache_root()
    if namespace is not None:
        directories = [_namespace_dir(namespace)]
    else:
        try:
            directories = [
                path for path in root.iterdir()
                if path.is_dir() and not path.is_symlink()
            ]
        except OSError:
            directories = []

    removed = 0
    removed_bytes = 0
    removed_temporary_files = 0
    for directory in directories:
        try:
            entries = [
                path for path in directory.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and (path.suffix == ".json" or path.name.startswith(".cache-"))
            ]
        except OSError:
            continue
        for path in entries:
            try:
                size = path.stat().st_size
                path.unlink()
                if path.name.startswith(".cache-"):
                    removed_temporary_files += 1
                else:
                    removed += 1
                removed_bytes += size
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
    return {
        "namespace": namespace or "all",
        "removed_entries": removed,
        "removed_bytes": removed_bytes,
        "removed_temporary_files": removed_temporary_files,
    }


def server_version(client, refresh=False):
    """Return a cached Galaxy server version without storing credentials."""
    key = [server_identity(client.url)]
    secrets = (getattr(client, "api_key", ""),)
    if not refresh:
        cached = read("server-version", key, secrets=secrets)
        if cached is not None:
            return cached
    version = client.get_version()
    write("server-version", key, version, secrets=secrets)
    return version
