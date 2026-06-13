"""
Async-safe TTL cache for tool results.
Each tool registers a TTL; calls within that window return cached data.
"""
import asyncio
import time
import logging
from typing import Callable, Any

logger = logging.getLogger("tool_cache")

# TTL seconds per tool name — tune based on data volatility
TOOL_TTL: dict[str, int] = {
    "get_vcenter_inventory":  300,  # inventory stable for 5 min
    "get_cluster_capacity":    60,  # capacity changes slowly
    "get_esxi_metrics":        30,  # metrics change quickly
    "get_vrops_metrics":       30,
    "query_logs":              60,
    "check_vcf_compatibility": 600, # compat data is static
    "check_broadcom_interop":  600,
    "get_network_metrics":     60,
    "get_env_manifest":        300,
    "get_sddc_health":         60,
    "get_discovery_assets":    120,
    "get_datastore_capacity":  120,  # capacity moves slowly
}

_cache: dict[str, tuple[float, Any]] = {}  # key → (expires_at, value)
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


async def cached_call(tool_name: str, fn: Callable) -> Any:
    ttl = TOOL_TTL.get(tool_name, 60)
    now = time.monotonic()

    entry = _cache.get(tool_name)
    if entry and now < entry[0]:
        logger.debug(f"Cache HIT: {tool_name} (expires in {entry[0]-now:.0f}s)")
        return entry[1]

    async with _get_lock(tool_name):
        # Re-check after acquiring lock
        entry = _cache.get(tool_name)
        if entry and now < entry[0]:
            return entry[1]

        logger.debug(f"Cache MISS: {tool_name} — fetching fresh data")
        result = await fn()
        _cache[tool_name] = (now + ttl, result)
        return result


def invalidate(tool_name: str | None = None):
    """Invalidate one tool or all tools."""
    if tool_name:
        _cache.pop(tool_name, None)
    else:
        _cache.clear()
