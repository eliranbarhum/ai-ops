"""Compliance snapshot export — bundles audit log, vuln findings, AD data, fleet scoring into tar.gz."""
import asyncio
import hashlib
import io
import json
import logging
import tarfile
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from shared import CONFIG_STORE_URL, SCORING_ENGINE_URL

router = APIRouter()
logger = logging.getLogger("api-gateway.compliance")

_PG_URL = __import__("os").getenv("POSTGRES_URL", "")

DISCOVERY_ENGINE_URL = __import__("os").getenv("DISCOVERY_ENGINE_URL", "http://discovery-engine:8010")


async def _empty_result() -> dict:
    return {}


async def _safe_get(client: httpx.AsyncClient, url: str, **kwargs) -> dict | list:
    try:
        r = await client.get(url, timeout=10.0, **kwargs)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning("compliance: GET %s failed: %s", url, e)
    return {}


async def _fetch_audit_log(period_days: int) -> list:
    if not _PG_URL:
        return []
    try:
        import asyncpg
        conn = await asyncpg.connect(_PG_URL, command_timeout=10)
        rows = await conn.fetch(
            "SELECT ts, user_id, source_ip, action, resource, status_code "
            "FROM audit_log WHERE ts > NOW() - ($1 || ' days')::interval "
            "ORDER BY ts DESC LIMIT 10000",
            str(period_days),
        )
        await conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("compliance: audit_log fetch failed: %s", e)
        return []


@router.get("/api/v1/compliance/export")
async def compliance_export(
    request: Request,
    period: str = Query("90d", description="Audit lookback: 30d, 90d, 180d, 365d"),
):
    period_map = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}
    period_days = period_map.get(period, 90)

    timestamp = datetime.now(timezone.utc).isoformat()
    generated_at = timestamp.replace(":", "-").replace(".", "-")

    # Forward authenticated user identity so self-calls pass the auth guard
    user = (
        request.headers.get("x-forwarded-preferred-username")
        or request.headers.get("x-forwarded-user")
        or request.headers.get("x-forwarded-email")
        or ""
    )
    auth_headers = {"x-forwarded-user": user} if user else {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        cfg_r = await client.get(f"{CONFIG_STORE_URL}/config/raw", timeout=5.0)
        cfg = cfg_r.json() if cfg_r.status_code == 200 else {}
        ad_host = cfg.get("ad_host", "")

        scoring_data, ad_overview, vuln_scans, discovery_summary, audit_rows = await asyncio.gather(
            _safe_get(client, f"{SCORING_ENGINE_URL}/history?limit=10"),
            _safe_get(client, "http://api-gateway:8000/api/v1/ad/overview", headers=auth_headers) if ad_host else _empty_result(),
            _safe_get(client, f"{DISCOVERY_ENGINE_URL}/vuln-scans"),
            _safe_get(client, "http://api-gateway:8000/api/v1/fleet", headers=auth_headers),
            _fetch_audit_log(period_days),
        )

    # Build manifest
    files: dict[str, bytes] = {}

    files["scoring_history.json"] = json.dumps(scoring_data, indent=2, default=str).encode()
    files["ad_overview.json"] = json.dumps(ad_overview, indent=2, default=str).encode()
    files["vuln_scans.json"] = json.dumps(vuln_scans, indent=2, default=str).encode()
    files["fleet_snapshot.json"] = json.dumps(discovery_summary, indent=2, default=str).encode()
    files["audit_log.json"] = json.dumps(audit_rows, indent=2, default=str).encode()

    manifest = {
        "generated_at": timestamp,
        "period": period,
        "period_days": period_days,
        "files": {},
    }
    for name, content in files.items():
        manifest["files"][name] = {
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    manifest_bytes = json.dumps(manifest, indent=2).encode()
    manifest["files"]["manifest.json"] = {
        "size_bytes": len(manifest_bytes),
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }

    # Pack into tar.gz in memory
    buf = io.BytesIO()
    prefix = f"mco-compliance-{generated_at[:10]}"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=f"{prefix}/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        manifest_final = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(name=f"{prefix}/manifest.json")
        info.size = len(manifest_final)
        tar.addfile(info, io.BytesIO(manifest_final))
    buf.seek(0)

    filename = f"mco-compliance-{generated_at[:10]}.tar.gz"
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
