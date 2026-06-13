import os
import asyncio
import hashlib
import json
import logging
import time
from typing import AsyncIterator
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger("orchestrator.pipeline")

REDIS_URL = os.getenv("REDIS_URL", "")
_redis = None


async def _get_redis():
    global _redis
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
        logger.warning("Redis unavailable for pipeline dedup: %s", e)
        return None
    return _redis

TOOL_SERVICE_URL  = os.getenv("TOOL_SERVICE_URL",   "http://tools:8002")
SCORING_ENGINE_URL= os.getenv("SCORING_ENGINE_URL", "http://scoring-engine:8007")
LLM_GATEWAY_URL   = os.getenv("LLM_GATEWAY_URL",   "http://llm-gateway:8008")

INTENT_TOOL_MAP = {
    "vcf_readiness": [
        "get_vcenter_inventory",
        "get_cluster_capacity",
        "get_esxi_metrics",
        "get_vrops_metrics",
        "get_datastore_capacity",  # vSAN/VMFS capacity headroom
        "query_logs",
        "check_vcf_compatibility",
        "check_broadcom_interop",
        "get_sddc_health",
        "get_discovery_assets",
    ],
    "capacity": [
        "get_vcenter_inventory",
        "get_cluster_capacity",
        "get_esxi_metrics",        # per-host CPU/RAM merged from vROps host-details
        "get_vrops_metrics",
        "get_datastore_capacity",
    ],
    "anomaly_detection": [
        "query_logs",
        "get_vrops_metrics",
        "get_esxi_metrics",
        "get_vcenter_inventory",   # host-health fallback entities
    ],
    "network": [
        "get_network_metrics",
        "get_vcenter_inventory",
        "get_discovery_assets",    # port-scan findings for network security scoring
    ],
}

# ── Idempotency: prevent duplicate pipeline runs ───────────────────────────────
_active_pipelines: set[str] = set()
_DEDUP_WINDOW_S = 10  # seconds to hold the lock after completion


def _pipeline_key(target: str, query: str) -> str:
    return hashlib.sha256(f"{target}:{query}".encode()).hexdigest()[:16]


# ── Retry policy for transient upstream errors ─────────────────────────────────
_RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)

_tool_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)

_score_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=6),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)


@_tool_retry
async def _call_tool_once(client: httpx.AsyncClient, tool_name: str) -> dict:
    resp = await client.post(
        f"{TOOL_SERVICE_URL}/tools/{tool_name}",
        json={},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()


async def call_tool(client: httpx.AsyncClient, tool_name: str) -> dict:
    try:
        data = await _call_tool_once(client, tool_name)
        return {"tool": tool_name, "status": "success", "data": data}
    except Exception as e:
        logger.warning(f"Tool {tool_name} failed after retries: {e} — continuing with partial data")
        return {"tool": tool_name, "status": "error", "data": None, "error": str(e)}


@_score_retry
async def call_scoring_engine(client: httpx.AsyncClient, aggregated_data: dict) -> dict:
    resp = await client.post(
        f"{SCORING_ENGINE_URL}/score",
        json=aggregated_data,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


async def call_llm_gateway(
    client: httpx.AsyncClient, scoring_result: dict, raw_data: dict, query: str, target: str = "vcf_readiness"
) -> dict:
    resp = await client.post(
        f"{LLM_GATEWAY_URL}/explain",
        json={"scoring_result": scoring_result, "raw_data": raw_data, "query": query, "target": target},
        timeout=630.0,
    )
    resp.raise_for_status()
    return resp.json()


async def run_pipeline(target: str, query: str) -> dict:
    tools = INTENT_TOOL_MAP.get(target, INTENT_TOOL_MAP["vcf_readiness"])
    logger.info(f"Executing pipeline for target={target} with {len(tools)} tools")

    async with httpx.AsyncClient() as client:
        tool_results = await asyncio.gather(*[call_tool(client, t) for t in tools])

        aggregated = {}
        evidence = []
        for result in tool_results:
            if result["status"] == "success" and result["data"]:
                aggregated[result["tool"]] = result["data"]
                if "evidence" in result["data"]:
                    evidence.extend(result["data"]["evidence"])

        logger.info(f"Aggregated data from {len(aggregated)}/{len(tools)} tools")

        try:
            scoring_result = await call_scoring_engine(client, {
                "tools_data": aggregated,
                "target": target,
            })
        except Exception as e:
            logger.error(f"Scoring engine failed: {e}")
            scoring_result = {
                "readiness_score": 0,
                "status": "NOT_READY",
                "risk_factors": [{"severity": "critical", "message": f"Scoring engine unavailable: {e}", "component": "scoring-engine"}],
                "recommendations": ["Investigate scoring engine connectivity"],
            }

        try:
            llm_result = await call_llm_gateway(client, scoring_result, aggregated, query, target)
            explanation = llm_result.get("explanation", "No explanation available.")
        except Exception as e:
            logger.error(f"LLM gateway failed: {e}")
            explanation = f"Analysis complete. Score: {scoring_result.get('readiness_score', 'N/A')}. LLM explanation unavailable."

        return {
            "readiness_score": scoring_result.get("readiness_score", 0),
            "status":          scoring_result.get("status", "UNKNOWN"),
            "risk_factors":    scoring_result.get("risk_factors", []),
            "recommendations": scoring_result.get("recommendations", []),
            "sub_scores":      scoring_result.get("sub_scores", []),
            "evidence":        evidence,
            "explanation":     explanation,
            "raw_metrics":     aggregated,
        }


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def run_pipeline_stream(target: str, query: str) -> AsyncIterator[str]:
    """
    Streaming variant of run_pipeline.
    SSE events: progress | scored | token | done | error
    Heartbeat comments (:) keep the connection alive through long LLM calls.
    Idempotency: returns 409-equivalent error event if same pipeline already running.
    """
    key = _pipeline_key(target, query)

    # Try Redis SETNX for cross-replica dedup; fall back to in-memory
    r = await _get_redis()
    if r is not None:
        acquired = await r.set(f"pipeline:{key}", "1", nx=True, ex=300)
        if not acquired:
            yield _sse({"type": "error", "message": "Duplicate pipeline already running — please wait."})
            return
    else:
        if key in _active_pipelines:
            yield _sse({"type": "error", "message": "Duplicate pipeline already running — please wait."})
            return
        _active_pipelines.add(key)
    try:
        yield _sse({"type": "progress", "step": "collecting",
                    "detail": f"Running {len(INTENT_TOOL_MAP.get(target, INTENT_TOOL_MAP['vcf_readiness']))} data collectors in parallel…"})

        tools = INTENT_TOOL_MAP.get(target, INTENT_TOOL_MAP["vcf_readiness"])
        async with httpx.AsyncClient() as client:
            tool_results = await asyncio.gather(*[call_tool(client, t) for t in tools])

            aggregated: dict = {}
            evidence: list = []
            for r in tool_results:
                if r["status"] == "success" and r["data"]:
                    aggregated[r["tool"]] = r["data"]
                    if "evidence" in r["data"]:
                        evidence.extend(r["data"]["evidence"])

            yield _sse({"type": "progress", "step": "scoring",
                        "detail": f"Scoring {len(aggregated)}/{len(tools)} data sources…"})

            try:
                scoring_result = await call_scoring_engine(client, {"tools_data": aggregated, "target": target})
            except Exception as e:
                logger.error(f"Scoring engine failed: {e}")
                scoring_result = {
                    "readiness_score": 0, "status": "NOT_READY",
                    "risk_factors": [{"severity": "critical", "message": f"Scoring engine unavailable: {e}", "component": "scoring-engine"}],
                    "recommendations": ["Investigate scoring engine connectivity"],
                }

            yield _sse({"type": "scored", "data": {
                "readiness_score": scoring_result.get("readiness_score", 0),
                "status":          scoring_result.get("status", "UNKNOWN"),
                "risk_factors":    scoring_result.get("risk_factors", []),
                "recommendations": scoring_result.get("recommendations", []),
                "sub_scores":      scoring_result.get("sub_scores", []),
                "evidence":        evidence,
                "raw_metrics":     aggregated,
            }})

            yield _sse({"type": "progress", "step": "reasoning", "detail": "AI generating explanation…"})

            # Stream LLM tokens — send heartbeat every 15s to prevent LB timeout
            try:
                async with client.stream(
                    "POST", f"{LLM_GATEWAY_URL}/explain/stream",
                    json={"scoring_result": scoring_result, "raw_data": aggregated, "query": query, "target": target},
                    timeout=630.0,
                ) as resp:
                    resp.raise_for_status()
                    last_heartbeat = time.monotonic()
                    async for line in resp.aiter_lines():
                        # Heartbeat comment keeps load-balancers alive on long LLM calls
                        now = time.monotonic()
                        if now - last_heartbeat >= 15:
                            yield ": heartbeat\n\n"
                            last_heartbeat = now

                        if not line.startswith("data: "):
                            continue
                        try:
                            obj = json.loads(line[6:])
                        except Exception:
                            continue
                        t = obj.get("type")
                        if t == "token":
                            yield _sse({"type": "token", "text": obj["text"]})
                        elif t == "error":
                            yield _sse({"type": "token", "text": f"\n\n⚠ LLM error: {obj.get('message', 'unknown')}"})
                            break
                        elif t == "done":
                            break
            except Exception as e:
                logger.error(f"LLM stream failed: {e}")
                yield _sse({"type": "token", "text": f"Analysis complete (score: {scoring_result.get('readiness_score', 'N/A')}). LLM explanation unavailable: {e}"})

            yield _sse({"type": "done"})

    finally:
        if r is None:
            async def _release():
                await asyncio.sleep(_DEDUP_WINDOW_S)
                _active_pipelines.discard(key)
            asyncio.create_task(_release())
        else:
            # Explicitly release the Redis lock after a brief cooldown (300s TTL is only a stuck-scan fallback)
            async def _release_redis():
                await asyncio.sleep(_DEDUP_WINDOW_S)
                try:
                    await r.delete(f"pipeline:{key}")
                except Exception:
                    pass
            asyncio.create_task(_release_redis())
