import os
import logging
import httpx
from normalizers import normalize_logs

logger = logging.getLogger("tool.query_logs")

LOGS_COLLECTOR_URL = os.getenv("LOGS_COLLECTOR_URL", "http://collector-logs:8005")


async def query_logs() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{LOGS_COLLECTOR_URL}/collect/anomalies")
        resp.raise_for_status()
        raw = resp.json()

    normalized = normalize_logs(raw)

    evidence = []
    for entity in normalized.get("entities", []):
        if entity.get("entity_type") == "log_event":
            evidence.append({
                "source": "SDDC_MANAGER_TASKS",
                "metric": "anomaly",
                "value": entity.get("name", "unknown event"),
                "threshold": None,
            })

    return {"normalized": normalized, "evidence": evidence, "raw": raw}
