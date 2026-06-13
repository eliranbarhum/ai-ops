from fastapi import APIRouter, Query
from shared import _get_audit_pool

router = APIRouter()


@router.get("/api/v1/audit")
async def list_audit(
    limit: int = Query(100, ge=1, le=500),
    user_id: str = Query("", alias="user"),
    action: str = Query(""),
    offset: int = Query(0, ge=0),
    filter: str = Query("", description="Pre-canned filter: failed|after_hours|destructive|config_changes"),
):
    pool = await _get_audit_pool()
    if pool is None:
        return {"entries": [], "total": 0, "error": "audit DB unavailable"}

    clauses, params = [], []
    if user_id:
        params.append(f"%{user_id}%")
        clauses.append(f"user_id ILIKE ${len(params)}")
    if action:
        params.append(f"%{action}%")
        clauses.append(f"action ILIKE ${len(params)}")

    # Pre-canned suspicious activity filters
    if filter == "failed":
        clauses.append("status_code >= 400")
    elif filter == "after_hours":
        clauses.append("(EXTRACT(HOUR FROM ts AT TIME ZONE 'UTC') >= 20 OR EXTRACT(HOUR FROM ts AT TIME ZONE 'UTC') < 6)")
    elif filter == "destructive":
        clauses.append("action LIKE 'DELETE%'")
    elif filter == "config_changes":
        clauses.append("action LIKE '%/api/v1/config%'")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    async with pool.acquire() as conn:
        count_row = await conn.fetchrow(f"SELECT COUNT(*) AS n FROM audit_log {where}", *params)
        total = count_row["n"]
        params_q = params + [limit, offset]
        rows = await conn.fetch(
            f"""SELECT id, ts, user_id, source_ip, action, resource, status_code
                FROM audit_log {where}
                ORDER BY ts DESC
                LIMIT ${len(params)+1} OFFSET ${len(params)+2}""",
            *params_q,
        )

    entries = [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat(),
            "user_id": r["user_id"],
            "source_ip": r["source_ip"],
            "action": r["action"],
            "resource": r["resource"] or "",
            "status_code": r["status_code"],
        }
        for r in rows
    ]
    return {"entries": entries, "total": total}


@router.get("/api/v1/audit/heatmap")
async def audit_heatmap():
    """
    7×24 activity heatmap: count of audit events grouped by weekday (0=Mon…6=Sun) and hour (UTC).
    Returns a flat list of {weekday, hour, count} — UI renders as grid.
    """
    pool = await _get_audit_pool()
    if pool is None:
        # Return empty heatmap so UI renders without error
        return {
            "cells": [{"weekday": d, "hour": h, "count": 0} for d in range(7) for h in range(24)],
            "max_count": 0,
        }
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                EXTRACT(DOW FROM ts AT TIME ZONE 'UTC')::int AS dow,
                EXTRACT(HOUR FROM ts AT TIME ZONE 'UTC')::int AS hour,
                COUNT(*) AS cnt
            FROM audit_log
            WHERE ts >= NOW() - INTERVAL '90 days'
            GROUP BY dow, hour
            ORDER BY dow, hour
        """)

    # DOW: 0=Sunday in Postgres; remap to 0=Monday for display
    counts: dict[tuple[int, int], int] = {}
    for r in rows:
        pg_dow = r["dow"]  # 0=Sun, 1=Mon … 6=Sat
        mon_dow = (pg_dow - 1) % 7  # remap to 0=Mon…6=Sun
        counts[(mon_dow, r["hour"])] = r["cnt"]

    cells = [
        {"weekday": d, "hour": h, "count": counts.get((d, h), 0)}
        for d in range(7) for h in range(24)
    ]
    max_count = max((c["count"] for c in cells), default=0)
    return {"cells": cells, "max_count": max_count}
