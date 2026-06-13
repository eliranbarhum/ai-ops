import asyncio
import os
import logging
import httpx
from fastapi import HTTPException

logger = logging.getLogger("api-gateway")

ORCHESTRATOR_URL     = os.getenv("ORCHESTRATOR_URL",     "http://orchestrator:8001")
CONFIG_STORE_URL     = os.getenv("CONFIG_STORE_URL",     "http://config-store:8009")
LLM_GATEWAY_URL      = os.getenv("LLM_GATEWAY_URL",      "http://llm-gateway:8008")
VCENTER_COLLECTOR    = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")
VROPS_COLLECTOR      = os.getenv("VROPS_COLLECTOR_URL",   "http://collector-vrops:8004")
REDIS_URL            = os.getenv("REDIS_URL", "")
DISCOVERY_ENGINE_URL = os.getenv("DISCOVERY_ENGINE_URL", "http://discovery-engine:8010")
SCORING_ENGINE_URL   = os.getenv("SCORING_ENGINE_URL",   "http://scoring-engine:8007")
VKS_BROKER_URL       = os.getenv("VKS_BROKER_URL",       "http://vks-broker:8012")

_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL:
        return None
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        await client.ping()
        _redis_client = client
        logger.info("Redis connected: %s", REDIS_URL)
    except Exception as e:
        logger.warning("Redis unavailable, using in-memory cache: %s", e)
        return None
    return _redis_client


import contextvars as _cv
_request_id_ctx: _cv.ContextVar[str] = _cv.ContextVar("request_id", default="")

# Shared connection pool — set by lifespan in main.py, falls back to per-call client
_http_client: httpx.AsyncClient | None = None


async def _do_request(client: httpx.AsyncClient, method: str, url: str,
                      body: dict | None, params: dict | None,
                      headers: dict, timeout: float) -> dict:
    if method.upper() == "GET":
        resp = await client.get(url, params=params, headers=headers, timeout=timeout)
    elif method.upper() == "POST":
        resp = await client.post(url, json=body or {}, params=params, headers=headers, timeout=timeout)
    elif method.upper() == "DELETE":
        resp = await client.delete(url, params=params, headers=headers, timeout=timeout)
    else:
        resp = await client.request(method, url, json=body, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


async def _proxy(method: str, url: str, *, body: dict | None = None,
                 timeout: float = 15.0, params: dict | None = None) -> dict:
    req_id = _request_id_ctx.get("")
    headers = {"x-request-id": req_id} if req_id else {}
    try:
        if _http_client is not None:
            return await _do_request(_http_client, method, url, body, params, headers, timeout)
        async with httpx.AsyncClient() as client:
            return await _do_request(client, method, url, body, params, headers, timeout)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream error")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Upstream unreachable: {e}")


async def _get_cfg() -> dict:
    try:
        client = _http_client
        if client is not None:
            resp = await client.get(f"{CONFIG_STORE_URL}/config/raw", timeout=5.0)
        else:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(f"{CONFIG_STORE_URL}/config/raw")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"_get_cfg failed: {e}")
        raise HTTPException(status_code=503, detail="Configuration store unreachable")


_PG_URL = os.getenv("POSTGRES_URL", "")
_audit_pool = None


async def _get_audit_pool():
    global _audit_pool
    if _audit_pool is not None:
        return _audit_pool
    if not _PG_URL:
        return None
    try:
        import asyncpg
        _audit_pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=3, command_timeout=5)
        logger.info("Audit DB pool connected")
    except Exception as e:
        logger.warning("Audit DB unavailable: %s", e)
        return None
    return _audit_pool


async def _audit(user_id: str, source_ip: str, method: str, path: str,
                 status_code: int, resource: str = "") -> None:
    pool = await _get_audit_pool()
    if pool is None:
        return
    action = f"{method} {path}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audit_log (user_id, source_ip, action, resource, status_code)
                   VALUES ($1, $2, $3, $4, $5)""",
                user_id, source_ip, action, resource, status_code,
            )
    except Exception as e:
        logger.debug("audit write failed: %s", e)


_K8S_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_K8S_CERT_FILE  = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_NAMESPACE  = os.getenv("POD_NAMESPACE", "mco")


def _k8s_base() -> str:
    host = os.getenv("KUBERNETES_SERVICE_HOST", "")
    port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
    return f"https://{host}:{port}" if host else ""


try:
    _K8S_TOKEN = open(_K8S_TOKEN_FILE).read().strip()
except Exception:
    _K8S_TOKEN = ""


def _k8s_auth() -> dict:
    return {"Authorization": f"Bearer {_K8S_TOKEN}"} if _K8S_TOKEN else {}


async def _k8s_get_cm(name: str) -> dict:
    base = _k8s_base()
    if not base:
        return {}
    url = f"{base}/api/v1/namespaces/{_K8S_NAMESPACE}/configmaps/{name}"
    async with httpx.AsyncClient(verify=_K8S_CERT_FILE, timeout=10.0) as client:
        r = await client.get(url, headers=_k8s_auth())
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()


async def _k8s_apply_cm(name: str, data: dict) -> None:
    base = _k8s_base()
    if not base:
        return
    body = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": _K8S_NAMESPACE},
        "data": data,
    }
    url = f"{base}/api/v1/namespaces/{_K8S_NAMESPACE}/configmaps/{name}"
    hdrs = {**_k8s_auth(), "Content-Type": "application/strategic-merge-patch+json"}
    async with httpx.AsyncClient(verify=_K8S_CERT_FILE, timeout=10.0) as client:
        r = await client.patch(url, json=body, headers=hdrs)
        if r.status_code == 404:
            r = await client.post(
                f"{base}/api/v1/namespaces/{_K8S_NAMESPACE}/configmaps",
                json=body, headers=_k8s_auth(),
            )
        r.raise_for_status()
