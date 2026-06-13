import os
import logging
import httpx
from normalizers import normalize_vcenter_inventory

logger = logging.getLogger("tool.vcenter_inventory")

VCENTER_COLLECTOR_URL = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")


async def get_vcenter_inventory() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{VCENTER_COLLECTOR_URL}/collect/inventory")
        resp.raise_for_status()
        raw = resp.json()

    normalized = normalize_vcenter_inventory(raw)

    evidence = []
    for entity in normalized.get("entities", []):
        evidence.append({
            "source": "VCENTER",
            "metric": "inventory",
            "value": f"{entity.get('name')} ({entity.get('entity_type')})",
            "threshold": None,
        })

    return {"normalized": normalized, "evidence": evidence, "raw": raw}
