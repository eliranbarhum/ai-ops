import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

import shared
from routers import analysis, config, fleet, workspace, ollama, kubectl, agent, bulk, guest, mcp, discovery, health, audit, ad, alerts, maintenance, compliance, upgrade, vks
from shared import _audit, _request_id_ctx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api-gateway")

_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
_REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() == "true"
# Paths exempt from auth guard: infra probes, metrics, and guest/dex public routes
_AUTH_SKIP_PREFIXES = ("/health", "/metrics", "/ping", "/api/v1/health", "/api/v1/guest", "/dex/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from alerter import run_alerter
    shared._http_client = httpx.AsyncClient(
        timeout=15.0,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    logger.info("Shared httpx pool created")
    alerter_task = asyncio.create_task(run_alerter())
    yield
    alerter_task.cancel()
    try:
        await alerter_task
    except asyncio.CancelledError:
        pass
    await shared._http_client.aclose()
    shared._http_client = None
    logger.info("Shared httpx pool closed")


app = FastAPI(title="MCO API Gateway", version="1.0.0",
              description="Enterprise AI Operations Platform", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS if _ALLOWED_ORIGINS else ["*"],
    allow_credentials=bool(_ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app)

_AUDIT_PATHS = {
    "/api/v1/analyze", "/api/v1/config", "/api/v1/kubectl/run",
    "/api/v1/workspace/execute", "/api/v1/workspace/generate",
    "/api/v1/discovery/scans", "/api/v1/agent/chat",
    "/api/v1/bulk/execute",
}

_pending_audit: set[asyncio.Task] = set()


@app.middleware("http")
async def auth_guard_middleware(request: Request, call_next):
    if _REQUIRE_AUTH:
        path = request.url.path
        if not any(path.startswith(p) for p in _AUTH_SKIP_PREFIXES):
            user = (
                request.headers.get("x-forwarded-preferred-username")
                or request.headers.get("x-forwarded-user")
                or request.headers.get("x-forwarded-email")
            )
            if not user:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required. Access via the MCO portal."},
                )
    return await call_next(request)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = _request_id_ctx.set(req_id)
    try:
        response = await call_next(request)
    finally:
        _request_id_ctx.reset(token)
    response.headers["x-request-id"] = req_id
    return response


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    should_log = (
        method in ("POST", "PUT", "PATCH", "DELETE")
        or any(path.startswith(p) for p in _AUDIT_PATHS)
    ) and not path.startswith("/api/v1/audit")

    user_id = (
        request.headers.get("x-forwarded-preferred-username")
        or request.headers.get("x-forwarded-email")
        or request.headers.get("x-auth-request-email")
        or request.headers.get("x-forwarded-user")
        or "anonymous"
    )
    source_ip = (
        request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )

    if should_log:
        logger.info("audit: %s %s %s %s", method, path, user_id, source_ip)

    response = await call_next(request)

    if should_log:
        t = asyncio.create_task(_audit(user_id, source_ip, method, path, response.status_code))
        _pending_audit.add(t)
        t.add_done_callback(_pending_audit.discard)

    return response


app.include_router(analysis.router)
app.include_router(config.router)
app.include_router(fleet.router)
app.include_router(workspace.router)
app.include_router(ollama.router)
app.include_router(kubectl.router)
app.include_router(agent.router)
app.include_router(bulk.router)
app.include_router(guest.router)
app.include_router(mcp.router)
app.include_router(discovery.router)
app.include_router(health.router)
app.include_router(audit.router)
app.include_router(ad.router)
app.include_router(alerts.router)
app.include_router(maintenance.router)
app.include_router(compliance.router)
app.include_router(upgrade.router)
app.include_router(vks.router)
