import os
import logging
import httpx
from normalizers import normalize_vrops_metrics

logger = logging.getLogger("tool.vrops_metrics")

VROPS_COLLECTOR_URL = os.getenv("VROPS_COLLECTOR_URL", "http://collector-vrops:8004")


async def get_vrops_metrics() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{VROPS_COLLECTOR_URL}/collect/metrics")
        resp.raise_for_status()
        raw = resp.json()

    normalized = normalize_vrops_metrics(raw)

    evidence = []
    for entity in normalized.get("entities", []):
        for metric_name in ["cpu_usage", "ram_usage", "storage_latency_ms"]:
            val = entity.get(metric_name)
            if val is not None:
                evidence.append({
                    "source": "VCF_OPERATIONS",
                    "metric": metric_name,
                    "value": str(val),
                    "threshold": None,
                })

    return {"normalized": normalized, "evidence": evidence, "raw": raw}
