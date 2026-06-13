import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("collector-logs")

app = FastAPI(title="MCO Logs Collector", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")
_sddc_token: Optional[str] = None
_sddc_last_host: Optional[str] = None


async def _get_cfg() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "sddc_host": os.getenv("SDDC_HOST", ""),
            "sddc_user": os.getenv("SDDC_USER", "administrator@vsphere.local"),
            "sddc_password": os.getenv("SDDC_PASSWORD", ""),
            "sddc_verify_ssl": os.getenv("SDDC_VERIFY_SSL", "false").lower() == "true",
        }


def _make_client(verify: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=verify, timeout=30.0)


async def _get_token(cfg: dict) -> str:
    global _sddc_token, _sddc_last_host
    host = cfg["sddc_host"]
    if _sddc_token and _sddc_last_host == host:
        return _sddc_token
    async with _make_client(cfg.get("sddc_verify_ssl", False)) as client:
        resp = await client.post(
            f"https://{host}/v1/tokens",
            json={"username": cfg["sddc_user"], "password": cfg["sddc_password"]},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        _sddc_token = resp.json()["accessToken"]
        _sddc_last_host = host
    return _sddc_token


async def _sddc_get(cfg: dict, path: str) -> dict:
    global _sddc_token
    host = cfg["sddc_host"]
    verify = cfg.get("sddc_verify_ssl", False)
    token = await _get_token(cfg)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with _make_client(verify) as client:
        resp = await client.get(f"https://{host}{path}", headers=headers)
        if resp.status_code == 401:
            _sddc_token = None
            token = await _get_token(cfg)
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.get(f"https://{host}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json()


def _classify_task_severity(task_type: str, errors: list) -> str:
    task_type = (task_type or "").upper()
    critical_types = {"SDDC_MANAGER_CERTIFICATE", "HOST_DECOMMISSION", "CLUSTER_DESTROY",
                      "DOMAIN_DESTROY", "VSAN_STRETCHED_CLUSTER"}
    warning_types = {"HOST_COMMISSION", "CLUSTER_EXPAND", "DOMAIN_DEPLOY",
                     "UPGRADE", "CERTIFICATE_RENEWAL"}
    if task_type in critical_types or any("critical" in e.get("errorCode", "").lower() for e in errors):
        return "critical"
    if task_type in warning_types:
        return "warning"
    return "info"


@app.get("/collect/anomalies")
async def collect_anomalies(limit: int = 200, since_hours: int = 168):
    """
    limit      — max anomalies to return (default 200, safety cap 500)
    since_hours — look-back window in hours (default 168 = 7 days)
    """
    limit = min(limit, 500)
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured — open Settings to configure")
    try:
        page_size = min(limit, 200)
        data = await _sddc_get(cfg, f"/v1/tasks?status=FAILED&pageSize={page_size}")
        tasks = data.get("elements", [])

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=since_hours)

        anomalies = []
        for task in tasks:
            created_str = task.get("creationTimestamp", "")
            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except Exception:
                created = now

            if created < cutoff:
                continue

            errors = task.get("errors") or []
            task_type = task.get("type", "UNKNOWN")
            error_msg = "; ".join(e.get("message", "") for e in errors if e.get("message")) or task.get("name", "Unknown error")

            anomalies.append({
                "pattern": f"SDDC task failed: {task_type}",
                "text": error_msg[:300],
                "timestamp": int(created.timestamp() * 1000),
                "hostname": "sddc-manager",
                "severity": _classify_task_severity(task_type, errors),
                "task_id": task.get("id", ""),
                "task_name": task.get("name", ""),
            })

        return {
            "anomalies": anomalies[:limit],
            "total": len(anomalies),
            "window_hours": since_hours,
            "timestamp": now.isoformat(),
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    cfg = await _get_cfg()
    return {"status": "healthy", "service": "collector-logs", "sddc_configured": bool(cfg.get("sddc_host"))}
