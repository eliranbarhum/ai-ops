import os
import logging
import httpx
from normalizers import normalize_cluster_capacity

logger = logging.getLogger("tool.cluster_capacity")

VCENTER_COLLECTOR_URL = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")

CPU_WARN_THRESHOLD = 75
CPU_CRIT_THRESHOLD = 90
RAM_WARN_THRESHOLD = 80
RAM_CRIT_THRESHOLD = 92


async def get_cluster_capacity() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{VCENTER_COLLECTOR_URL}/collect/clusters")
        resp.raise_for_status()
        raw = resp.json()

    normalized = normalize_cluster_capacity(raw)

    evidence = []
    for entity in normalized.get("entities", []):
        name = entity.get("name", "unknown")
        cpu = entity.get("cpu_usage", 0)
        ram = entity.get("ram_usage", 0)

        evidence.append({
            "source": "VCENTER",
            "metric": "cpu_usage",
            "value": f"{cpu}%",
            "threshold": f"{CPU_WARN_THRESHOLD}% warn / {CPU_CRIT_THRESHOLD}% crit",
        })
        evidence.append({
            "source": "VCENTER",
            "metric": "ram_usage",
            "value": f"{ram}%",
            "threshold": f"{RAM_WARN_THRESHOLD}% warn / {RAM_CRIT_THRESHOLD}% crit",
        })

    return {"normalized": normalized, "evidence": evidence, "raw": raw}
