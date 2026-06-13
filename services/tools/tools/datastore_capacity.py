import os
import logging
import httpx
from datetime import datetime, timezone

logger = logging.getLogger("tool.datastore_capacity")

VCENTER_COLLECTOR_URL = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")

# vSAN needs slack for resync/rebuild — alert earlier than VMFS
VSAN_WARN_PCT, VSAN_CRIT_PCT = 65.0, 80.0
VMFS_WARN_PCT, VMFS_CRIT_PCT = 75.0, 90.0


async def get_datastore_capacity() -> dict:
    """
    Datastore capacity from vCenter — feeds the storage sub-score.
    vSAN datastores get stricter thresholds (rebuild headroom).
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{VCENTER_COLLECTOR_URL}/collect/datastores")
        resp.raise_for_status()
        raw = resp.json()

    entities = []
    evidence = []
    ts = raw.get("timestamp", datetime.now(timezone.utc).isoformat())

    for ds in raw.get("datastores", []):
        name = ds.get("name", "unknown")
        ds_type = (ds.get("type") or "").upper()
        used_pct = ds.get("used_pct")
        capacity_gb = ds.get("capacity_gb", 0)
        free_gb = ds.get("free_gb", 0)
        if used_pct is None:
            continue

        is_vsan = "VSAN" in ds_type
        warn, crit = (VSAN_WARN_PCT, VSAN_CRIT_PCT) if is_vsan else (VMFS_WARN_PCT, VMFS_CRIT_PCT)
        status = "critical" if used_pct >= crit else "warning" if used_pct >= warn else "ok"

        entities.append({
            "entity_type": "datastore",
            "name": name,
            "ds_type": ds_type,
            "used_pct": used_pct,
            "capacity_gb": capacity_gb,
            "free_gb": free_gb,
            "warn_pct": warn,
            "crit_pct": crit,
            "health_state": "red" if status == "critical" else "yellow" if status == "warning" else "green",
            "timestamp": ts,
        })
        evidence.append({
            "source": "VCENTER",
            "metric": f"datastore_used:{name}",
            "value": f"{used_pct}% of {capacity_gb:,.0f} GB ({ds_type})",
            "threshold": f"{warn}% warn / {crit}% crit",
        })

    return {
        "normalized": {"entities": entities, "source": "VCENTER"},
        "evidence": evidence,
        "raw": raw,
    }
