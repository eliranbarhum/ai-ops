"""Alert channels and rules CRUD proxy + test endpoint."""
import asyncio
import json
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request

from shared import CONFIG_STORE_URL, _proxy

router = APIRouter()
logger = logging.getLogger("api-gateway.alerts")

_REDIS_URL = os.getenv("REDIS_URL", "")


@router.get("/api/v1/alert-channels")
async def list_alert_channels():
    return await _proxy("GET", f"{CONFIG_STORE_URL}/alert-channels")


@router.post("/api/v1/alert-channels")
async def create_alert_channel(request: Request):
    body = await request.json()
    return await _proxy("POST", f"{CONFIG_STORE_URL}/alert-channels", body=body)


@router.delete("/api/v1/alert-channels/{channel_id}")
async def delete_alert_channel(channel_id: str):
    return await _proxy("DELETE", f"{CONFIG_STORE_URL}/alert-channels/{channel_id}")


@router.post("/api/v1/alert-channels/{channel_id}/test")
async def test_alert_channel(channel_id: str):
    channels_data = await _proxy("GET", f"{CONFIG_STORE_URL}/alert-channels")
    channel = next((c for c in channels_data.get("channels", []) if c["id"] == channel_id), None)
    if not channel:
        raise HTTPException(404, detail="Channel not found")
    cfg = channel.get("config", {})
    url = cfg.get("webhook_url") or cfg.get("url", "")
    if not url:
        raise HTTPException(400, detail="Channel has no webhook_url configured")
    payload = {
        "text": "MCO Test Alert — your alert channel is working!",
        "source": "mco-platform",
        "test": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
        return {"ok": True, "status_code": r.status_code}
    except Exception as e:
        raise HTTPException(502, detail=f"Webhook delivery failed: {e}")


@router.get("/api/v1/alert-rules")
async def list_alert_rules():
    return await _proxy("GET", f"{CONFIG_STORE_URL}/alert-rules")


@router.post("/api/v1/alert-rules")
async def create_alert_rule(request: Request):
    body = await request.json()
    return await _proxy("POST", f"{CONFIG_STORE_URL}/alert-rules", body=body)


@router.patch("/api/v1/alert-rules/{rule_id}")
async def update_alert_rule(rule_id: str, request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.patch(f"{CONFIG_STORE_URL}/alert-rules/{rule_id}", json=body)
        r.raise_for_status()
        return r.json()


@router.delete("/api/v1/alert-rules/{rule_id}")
async def delete_alert_rule(rule_id: str):
    return await _proxy("DELETE", f"{CONFIG_STORE_URL}/alert-rules/{rule_id}")


@router.post("/api/v1/alerts/publish")
async def publish_event(request: Request):
    """Publish a test event to mco:events on Redis (for rule testing)."""
    body = await request.json()
    redis_url = _REDIS_URL
    if not redis_url:
        raise HTTPException(503, detail="Redis not configured")
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.publish("mco:events", json.dumps(body))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, detail=f"Redis publish failed: {e}")
