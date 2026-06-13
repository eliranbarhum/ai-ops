import httpx
from fastapi import APIRouter, HTTPException, Request
from shared import CONFIG_STORE_URL
from dex_sync import sync_dex_config, _AD_FIELDS

router = APIRouter()

_ALLOWED_TEST_SERVICES = {
    "vcenter", "vrops", "nsx", "nsxt", "sddc", "ad", "powercli",
    "ollama", "anthropic", "openai", "gemini", "agent-ollama",
}


@router.get("/api/v1/config")
async def get_config():
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CONFIG_STORE_URL}/config")
        resp.raise_for_status()
        return resp.json()


@router.post("/api/v1/config")
async def save_config(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CONFIG_STORE_URL}/config", json=body)
        resp.raise_for_status()
        result = resp.json()

    if any(k in body for k in _AD_FIELDS):
        async with httpx.AsyncClient(timeout=10.0) as client:
            full = await client.get(f"{CONFIG_STORE_URL}/config")
            cfg = full.json() if full.status_code == 200 else body
        dex_result = await sync_dex_config(cfg)
        result["dex_sync"] = dex_result

    return result


@router.post("/api/v1/config/test/{service}")
async def test_config(service: str):
    if service not in _ALLOWED_TEST_SERVICES:
        raise HTTPException(status_code=400, detail=f"Unknown service '{service}'")
    async with httpx.AsyncClient(timeout=150.0) as client:
        resp = await client.post(f"{CONFIG_STORE_URL}/config/test/{service}")
        resp.raise_for_status()
        return resp.json()


@router.get("/api/v1/scans")
async def list_scans():
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CONFIG_STORE_URL}/scans")
        resp.raise_for_status()
        return resp.json()


@router.post("/api/v1/scans")
async def save_scan(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CONFIG_STORE_URL}/scans", json=body)
        resp.raise_for_status()
        return resp.json()


@router.delete("/api/v1/scans/{scan_id}")
async def delete_scan(scan_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{CONFIG_STORE_URL}/scans/{scan_id}")
        resp.raise_for_status()
        return resp.json()
