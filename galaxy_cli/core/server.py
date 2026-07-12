"""Read-only Galaxy server capability discovery."""

from galaxy_cli.core import metadata_cache
from galaxy_cli.utils.galaxy_backend import GalaxyBackendError


def _probe_get(client, path, params=None):
    try:
        client.get(path, params=params)
        return True
    except GalaxyBackendError as exc:
        if exc.status_code in {404, 405}:
            return False
        if exc.status_code in {401, 403}:
            return None
        return None


def server_capabilities(client, use_cache=True, refresh_cache=False):
    """Inspect only read-only endpoints; never probe by submitting a write."""
    version = metadata_cache.server_version(client, refresh=refresh_cache)
    identity = metadata_cache.server_identity(client.url)
    secrets = (getattr(client, "api_key", ""),)
    key = [identity, version]
    if use_cache and not refresh_cache:
        cached = metadata_cache.read(
            "server-capabilities", key, secrets=secrets
        )
        if isinstance(cached, dict):
            return cached

    configuration = {}
    try:
        raw = client.get("configuration")
        if isinstance(raw, dict):
            configuration = raw
    except GalaxyBackendError:
        pass
    tus_setting = next(
        (
            configuration[name]
            for name in ("enable_tus", "tus_upload_store", "tus_upload_path")
            if name in configuration
        ),
        None,
    )
    endpoints = {
        "tool_requests": _probe_get(client, "tool_requests", {"limit": 1}),
        "udt": _probe_get(client, "unprivileged_tools"),
        "histories": _probe_get(client, "histories", {"limit": 1}),
        "workflows": _probe_get(client, "workflows", {"limit": 1}),
        "jobs": _probe_get(client, "jobs", {"limit": 1}),
        "datatypes": _probe_get(client, "datatypes", {"extension_only": True}),
    }
    version_major = version.get("version_major", "") if isinstance(version, dict) else str(version)
    if tus_setting is None:
        try:
            parts = tuple(int(part) for part in version_major.split(".")[:2])
            tus_supported = parts >= (22, 1)
        except (TypeError, ValueError):
            tus_supported = None
    else:
        tus_supported = bool(tus_setting)
    try:
        version_parts = tuple(int(part) for part in version_major.split(".")[:2])
    except (TypeError, ValueError):
        version_parts = ()
    strict_supported = bool(endpoints["jobs"] and version_parts >= (24, 0))
    result = {
        "galaxy_url": identity,
        "server_version": version,
        "capabilities": {
            "strict_tool_requests": strict_supported,
            "udt_endpoints": endpoints["udt"],
            "history_copy": endpoints["histories"],
            "tus_upload": tus_supported,
            "workflow_invocation": endpoints["workflows"],
            "server_side_tool_validation": endpoints["jobs"],
        },
        "endpoints": endpoints,
        "probe_mode": "read_only",
    }
    if use_cache:
        metadata_cache.write(
            "server-capabilities", key, result, secrets=secrets
        )
    return result


def datatype_mapping(client, use_cache=True, refresh_cache=False):
    version = metadata_cache.server_version(client, refresh=refresh_cache)
    secrets = (getattr(client, "api_key", ""),)
    key = [metadata_cache.server_identity(client.url), version]
    if use_cache and not refresh_cache:
        cached = metadata_cache.read(
            "datatype-mapping", key, secrets=secrets
        )
        if cached is not None:
            return cached
    value = client.get("datatypes", params={"extension_only": False})
    if use_cache:
        metadata_cache.write(
            "datatype-mapping",
            key,
            value,
            secrets=secrets,
        )
    return value
