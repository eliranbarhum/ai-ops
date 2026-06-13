"""Maintenance window CRUD and current-window status check."""
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request

from shared import CONFIG_STORE_URL, _proxy

router = APIRouter()
logger = logging.getLogger("api-gateway.maintenance")


def _window_active(window: dict) -> bool:
    if not window.get("enabled"):
        return False
    now = datetime.now(timezone.utc)
    # day_of_week: 0=Mon … 6=Sun (Python weekday())
    if now.weekday() != int(window.get("day_of_week", -1)):
        return False
    start_h = int(window.get("start_hour", 0))
    start_m = int(window.get("start_minute", 0))
    dur_m = int(window.get("duration_minutes", 60))
    window_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    from datetime import timedelta
    window_end = window_start + timedelta(minutes=dur_m)
    return window_start <= now < window_end


async def is_in_maintenance_window() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{CONFIG_STORE_URL}/maintenance-windows")
            if r.status_code != 200:
                return True  # fail open: if we can't check, allow
            windows = r.json().get("windows", [])
    except Exception:
        return True  # fail open
    if not windows:
        return True  # no windows defined → always allow
    enabled = [w for w in windows if w.get("enabled")]
    if not enabled:
        return True  # all disabled → always allow
    return any(_window_active(w) for w in enabled)


@router.get("/api/v1/maintenance-windows")
async def list_maintenance_windows():
    return await _proxy("GET", f"{CONFIG_STORE_URL}/maintenance-windows")


@router.post("/api/v1/maintenance-windows")
async def create_maintenance_window(request: Request):
    body = await request.json()
    return await _proxy("POST", f"{CONFIG_STORE_URL}/maintenance-windows", body=body)


@router.patch("/api/v1/maintenance-windows/{window_id}")
async def update_maintenance_window(window_id: str, request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.patch(f"{CONFIG_STORE_URL}/maintenance-windows/{window_id}", json=body)
        r.raise_for_status()
        return r.json()


@router.delete("/api/v1/maintenance-windows/{window_id}")
async def delete_maintenance_window(window_id: str):
    return await _proxy("DELETE", f"{CONFIG_STORE_URL}/maintenance-windows/{window_id}")


@router.get("/api/v1/maintenance-windows/status")
async def maintenance_status():
    """Return whether we are currently inside any active maintenance window."""
    active = await is_in_maintenance_window()
    windows_data = await _proxy("GET", f"{CONFIG_STORE_URL}/maintenance-windows")
    windows = windows_data.get("windows", [])
    active_window = next((w for w in windows if _window_active(w)), None)
    return {
        "in_window": active,
        "active_window": active_window,
        "window_count": len([w for w in windows if w.get("enabled")]),
    }
