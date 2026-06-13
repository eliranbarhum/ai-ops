import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from shared import DISCOVERY_ENGINE_URL, _proxy

router = APIRouter()


@router.get("/api/v1/discovery/summary")
async def discovery_summary():
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/summary", timeout=15.0)


@router.get("/api/v1/discovery/networks")
async def discovery_networks():
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/networks", timeout=20.0)


@router.post("/api/v1/discovery/networks")
async def discovery_add_network(request: Request):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/networks", body=await request.json())


@router.delete("/api/v1/discovery/networks/{cidr:path}")
async def discovery_remove_network(cidr: str):
    return await _proxy("DELETE", f"{DISCOVERY_ENGINE_URL}/networks/{cidr}")


@router.get("/api/v1/discovery/scans")
async def discovery_list_scans():
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/scans")


@router.post("/api/v1/discovery/scans")
async def discovery_start_scan(request: Request):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/scans", body=await request.json(), timeout=15.0)


@router.post("/api/v1/discovery/scans/{scan_id}/stop")
async def discovery_stop_scan(scan_id: str):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/scans/{scan_id}/stop")


@router.delete("/api/v1/discovery/scans/{scan_id}")
async def discovery_delete_scan(scan_id: str):
    return await _proxy("DELETE", f"{DISCOVERY_ENGINE_URL}/scans/{scan_id}")


@router.get("/api/v1/discovery/scans/{scan_id}/events")
async def discovery_scan_events(scan_id: str):
    async def _stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{DISCOVERY_ENGINE_URL}/scans/{scan_id}/events"
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n"
                    else:
                        yield "\n"

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/discovery/scans/{scan_id}/diff")
async def discovery_scan_diff(scan_id: str):
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/scans/{scan_id}/diff", timeout=15.0)


@router.get("/api/v1/discovery/scans/{scan_id}/export")
async def discovery_scan_export(scan_id: str):
    async def _stream():
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("GET", f"{DISCOVERY_ENGINE_URL}/scans/{scan_id}/export") as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        _stream(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=discovery-{scan_id}.csv"},
    )


@router.get("/api/v1/discovery/scans/{scan_id}/hosts")
async def discovery_hosts(scan_id: str, risk: str = "", device_class: str = ""):
    params: dict = {}
    if risk:
        params["risk"] = risk
    if device_class:
        params["device_class"] = device_class
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{DISCOVERY_ENGINE_URL}/scans/{scan_id}/hosts", params=params
        )
        resp.raise_for_status()
        return resp.json()


@router.get("/api/v1/discovery/hosts/{ip}")
async def discovery_host_detail(ip: str, scan_id: str = ""):
    params: dict = {}
    if scan_id:
        params["scan_id"] = scan_id
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{DISCOVERY_ENGINE_URL}/hosts/{ip}", params=params)
        resp.raise_for_status()
        return resp.json()


@router.post("/api/v1/discovery/hosts/{ip}/credentials")
async def discovery_save_credentials(ip: str, request: Request):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/hosts/{ip}/credentials", body=await request.json())


@router.get("/api/v1/discovery/hosts/{ip}/credentials")
async def discovery_list_credentials(ip: str):
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/hosts/{ip}/credentials")


@router.delete("/api/v1/discovery/hosts/{ip}/credentials/{cred_type}")
async def discovery_delete_credentials(ip: str, cred_type: str):
    return await _proxy("DELETE", f"{DISCOVERY_ENGINE_URL}/hosts/{ip}/credentials/{cred_type}")


@router.post("/api/v1/discovery/hosts/{ip}/deep-scan")
async def discovery_trigger_deep_scan(ip: str, cred_type: str = "ssh"):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/hosts/{ip}/deep-scan",
                        params={"cred_type": cred_type}, timeout=15.0)


@router.get("/api/v1/discovery/hosts/{ip}/deep-scan")
async def discovery_get_deep_scan(ip: str, cred_type: str = "ssh"):
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/hosts/{ip}/deep-scan",
                        params={"cred_type": cred_type})


# ---------------------------------------------------------------------------
# Vulnerability scan endpoints
# ---------------------------------------------------------------------------

@router.get("/api/v1/discovery/vuln-scans/scopes")
async def vuln_scan_scopes():
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/vuln-scans/scopes")


@router.post("/api/v1/discovery/vuln-scans/estimate")
async def vuln_scan_estimate(request: Request):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/vuln-scans/estimate", body=await request.json())


@router.get("/api/v1/discovery/vuln-scans")
async def vuln_scan_list():
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/vuln-scans")


@router.post("/api/v1/discovery/vuln-scans")
async def vuln_scan_start(request: Request):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/vuln-scans", body=await request.json(), timeout=15.0)


@router.post("/api/v1/discovery/vuln-scans/{vscan_id}/stop")
async def vuln_scan_stop(vscan_id: str):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/vuln-scans/{vscan_id}/stop")


@router.delete("/api/v1/discovery/vuln-scans/{vscan_id}")
async def vuln_scan_delete(vscan_id: str):
    return await _proxy("DELETE", f"{DISCOVERY_ENGINE_URL}/vuln-scans/{vscan_id}")


@router.get("/api/v1/discovery/vuln-scans/{vscan_id}/events")
async def vuln_scan_events(vscan_id: str):
    async def _stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{DISCOVERY_ENGINE_URL}/vuln-scans/{vscan_id}/events"
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n"
                    else:
                        yield "\n"

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/discovery/vuln-scans/{vscan_id}/findings")
async def vuln_scan_findings(vscan_id: str, severity: str = "", host: str = ""):
    params: dict = {}
    if severity:
        params["severity"] = severity
    if host:
        params["host"] = host
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{DISCOVERY_ENGINE_URL}/vuln-scans/{vscan_id}/findings", params=params
        )
        resp.raise_for_status()
        return resp.json()


# ─── Schedule CRUD ────────────────────────────────────────────────────────────

@router.get("/api/v1/discovery/vuln-scans/schedules")
async def vuln_scan_schedules_list():
    return await _proxy("GET", f"{DISCOVERY_ENGINE_URL}/vuln-scans/schedules")


@router.post("/api/v1/discovery/vuln-scans/schedules")
async def vuln_scan_schedule_create(request: Request):
    return await _proxy("POST", f"{DISCOVERY_ENGINE_URL}/vuln-scans/schedules", body=await request.json())


@router.patch("/api/v1/discovery/vuln-scans/schedules/{sid}")
async def vuln_scan_schedule_update(sid: str, request: Request):
    return await _proxy("PATCH", f"{DISCOVERY_ENGINE_URL}/vuln-scans/schedules/{sid}", body=await request.json())


@router.delete("/api/v1/discovery/vuln-scans/schedules/{sid}")
async def vuln_scan_schedule_delete(sid: str):
    return await _proxy("DELETE", f"{DISCOVERY_ENGINE_URL}/vuln-scans/schedules/{sid}")
