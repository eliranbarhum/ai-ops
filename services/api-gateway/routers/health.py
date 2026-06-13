import asyncio
import datetime as _dt
import os
import httpx
from fastapi import APIRouter, Request
from shared import (
    ORCHESTRATOR_URL, CONFIG_STORE_URL, LLM_GATEWAY_URL,
    VCENTER_COLLECTOR, DISCOVERY_ENGINE_URL,
)

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "healthy", "service": "api-gateway"}


@router.get("/api/v1/health")
async def health_v1():
    # Unauthenticated external probe URL: both the gateway auth guard
    # (_AUTH_SKIP_PREFIXES) and oauth2-proxy (--skip-auth-route) exempt this
    # path, so it must exist for monitoring through the portal.
    return {"status": "healthy", "service": "api-gateway"}


@router.get("/api/v1/me")
async def me(request: Request):
    """Return the authenticated user identity from oauth2-proxy headers."""
    email = request.headers.get("x-forwarded-email", "")
    # Prefer email (always human-readable); fall back to preferred-username only
    # if it isn't a protobuf-encoded Dex subject (those start with "CiQ")
    preferred = request.headers.get("x-forwarded-preferred-username", "")
    username = email or (preferred if preferred and not preferred.startswith("CiQ") else "") or "unknown"
    return {"username": username, "email": email}


@router.get("/")
async def root():
    return {"service": "MCO API Gateway", "version": "1.0.0", "docs": "/docs"}


@router.get("/api/v1/health/services")
async def health_services():
    _SDDC_COLLECTOR  = os.getenv("SDDC_COLLECTOR_URL",  "http://collector-sddc:8011")
    _VROPS_COLLECTOR = os.getenv("VROPS_COLLECTOR_URL",  "http://collector-vrops:8004")
    _LOGS_COLLECTOR  = os.getenv("LOGS_COLLECTOR_URL",   "http://collector-logs:8005")
    _TOOLS_URL       = os.getenv("TOOL_SERVICE_URL",     "http://tools:8002")

    checks = {
        "vcenter":      f"{VCENTER_COLLECTOR}/health",
        "sddc":         f"{_SDDC_COLLECTOR}/health",
        "vrops":        f"{_VROPS_COLLECTOR}/health",
        "logs":         f"{_LOGS_COLLECTOR}/health",
        "tools":        f"{_TOOLS_URL}/health",
        "orchestrator": f"{ORCHESTRATOR_URL}/health",
        "llm_gateway":  f"{LLM_GATEWAY_URL}/health",
        "discovery":    f"{DISCOVERY_ENGINE_URL}/health",
    }

    results: dict[str, str] = {}

    async def _check(name: str, url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(url)
                results[name] = "ok" if r.status_code < 400 else "error"
        except Exception:
            results[name] = "unreachable"

    await asyncio.gather(*[_check(n, u) for n, u in checks.items()])

    llm_provider = "unknown"
    llm_model = ""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            cfg_r = await c.get(f"{CONFIG_STORE_URL}/config/raw")
            cfg = cfg_r.json()
            llm_provider = cfg.get("llm_provider", "anthropic")
            llm_model = (
                cfg.get("vllm_model") if llm_provider == "ollama"
                else cfg.get(f"{llm_provider}_model", "")
            ) or ""
    except Exception:
        pass

    all_ok = all(v == "ok" for v in results.values())
    any_unreachable = any(v == "unreachable" for v in results.values())
    overall = "ok" if all_ok else ("degraded" if not any_unreachable else "unreachable")

    return {
        "overall": overall,
        "services": results,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
