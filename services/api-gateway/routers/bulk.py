from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from shared import _get_cfg
import bulk_executor
from routers.maintenance import is_in_maintenance_window

router = APIRouter()

_MAX_CSV_BYTES = 10 * 1024 * 1024  # 10 MB


async def _read_limited(file: UploadFile) -> bytes:
    content = await file.read(_MAX_CSV_BYTES + 1)
    if len(content) > _MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV file exceeds 10 MB limit")
    return content


@router.post("/api/v1/bulk/parse-csv/vms")
async def bulk_parse_vms(file: UploadFile = File(...)):
    content = await _read_limited(file)
    rows, errors = bulk_executor.parse_csv_vms(content)
    return {"rows": rows, "errors": errors, "total": len(rows),
            "valid": sum(1 for r in rows if r.get("_status") == "valid")}


@router.post("/api/v1/bulk/parse-csv/users")
async def bulk_parse_users(file: UploadFile = File(...)):
    content = await _read_limited(file)
    rows, errors = bulk_executor.parse_csv_ad_users(content)
    return {"rows": rows, "errors": errors, "total": len(rows),
            "valid": sum(1 for r in rows if r.get("_status") == "valid")}


@router.post("/api/v1/bulk/execute/vms")
async def bulk_execute_vms(request: Request):
    if not await is_in_maintenance_window():
        raise HTTPException(423, detail="Bulk VM provisioning is blocked outside a maintenance window. Configure one in Settings → Maintenance.")
    body = await request.json()
    rows = body.get("rows", [])
    if not rows:
        raise HTTPException(status_code=400, detail="No rows provided")
    cfg = await _get_cfg()
    results = await bulk_executor.execute_vm_batch(rows, cfg)
    return {"results": results}


@router.post("/api/v1/bulk/execute/users")
async def bulk_execute_users(request: Request):
    if not await is_in_maintenance_window():
        raise HTTPException(423, detail="Bulk user provisioning is blocked outside a maintenance window. Configure one in Settings → Maintenance.")
    body = await request.json()
    rows = body.get("rows", [])
    if not rows:
        raise HTTPException(status_code=400, detail="No rows provided")
    cfg = await _get_cfg()
    if not cfg.get("ad_host"):
        raise HTTPException(status_code=503, detail="Active Directory not configured in Settings")
    results = await bulk_executor.execute_ad_batch(rows, cfg)
    return {"results": results}
