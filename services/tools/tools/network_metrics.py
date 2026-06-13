import os
import logging
import httpx
from normalizers import normalize_network_metrics

logger = logging.getLogger("tool.network_metrics")

VROPS_COLLECTOR_URL = os.getenv("VROPS_COLLECTOR_URL", "http://collector-vrops:8004")


async def get_network_metrics() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{VROPS_COLLECTOR_URL}/collect/network")
        resp.raise_for_status()
        raw = resp.json()

    normalized = normalize_network_metrics(raw)

    evidence = []
    for entity in normalized.get("entities", []):
        for metric in ["packet_loss_pct", "throughput_mbps", "latency_ms"]:
            val = entity.get(metric)
            if val is not None:
                evidence.append({
                    "source": "VCF_OPERATIONS_FOR_NETWORKS",
                    "metric": metric,
                    "value": str(val),
                    "threshold": None,
                })

    return {"normalized": normalized, "evidence": evidence, "raw": raw}
