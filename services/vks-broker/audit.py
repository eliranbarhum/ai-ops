"""
Emit audit events to the mco:events Redis bus (mirrors the existing audit infrastructure).
Falls back to structured logging if Redis is unavailable.
"""
import asyncio
import json
import logging
import os
import time

logger = logging.getLogger("vks-broker.audit")

REDIS_URL = os.getenv("REDIS_URL", "")
_redis = None
_redis_lock = asyncio.Lock()


async def _get_redis():
    global _redis
    async with _redis_lock:
        if _redis is not None:
            return _redis
        if not REDIS_URL:
            return None
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
            await client.ping()
            _redis = client
        except Exception as e:
            logger.warning("Redis unavailable for audit: %s", e)
            return None
    return _redis


_recent: list[dict] = []
_RECENT_MAX = 200


def get_recent(limit: int = 50) -> list[dict]:
    """Return the most recent in-memory audit events."""
    return list(reversed(_recent[-limit:]))


async def emit(
    user: str,
    verb: str,
    cluster: str,
    namespace: str,
    kind: str,
    name: str,
    params: dict | None = None,
    status: str = "initiated",
):
    event = {
        "source": "vks-broker",
        "timestamp": time.time(),
        "user": user,
        "verb": verb,
        "cluster": cluster,
        "namespace": namespace,
        "kind": kind,
        "name": name,
        "params": params or {},
        "status": status,
    }
    _recent.append(event)
    if len(_recent) > _RECENT_MAX:
        del _recent[: len(_recent) - _RECENT_MAX]
    logger.info("audit: %s %s %s/%s/%s %s by %s", verb, cluster, namespace, kind, name, status, user)
    try:
        r = await _get_redis()
        if r:
            await r.xadd("mco:events", {"data": json.dumps(event)}, maxlen=10000)
    except Exception as e:
        logger.debug("Audit Redis publish failed: %s", e)
