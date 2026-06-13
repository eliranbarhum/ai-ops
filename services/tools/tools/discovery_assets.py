import os
import logging
import httpx

logger = logging.getLogger("tool.discovery_assets")

DISCOVERY_ENGINE_URL = os.getenv("DISCOVERY_ENGINE_URL", "http://discovery-engine:8010")


async def get_discovery_assets() -> dict:
    """
    Returns aggregated network discovery findings.
    Feeds into: network_security sub-score, LLM analysis context.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{DISCOVERY_ENGINE_URL}/summary")
            resp.raise_for_status()
            summary = resp.json()
    except Exception as e:
        logger.warning(f"Discovery engine unavailable: {e}")
        return {"scanned": False, "error": str(e)}

    evidence = []

    dangerous = summary.get("dangerous_port_findings", [])
    for finding in dangerous:
        evidence.append({
            "source": "DISCOVERY",
            "metric": "dangerous_port",
            "value": f"{finding['ip']} has {finding['service']} (port {finding['port']}) open",
            "threshold": "No dangerous protocols on management hosts",
        })

    risk = summary.get("risk_breakdown", {})
    critical_count = risk.get("critical", 0)
    high_count = risk.get("high", 0)
    if critical_count or high_count:
        evidence.append({
            "source": "DISCOVERY",
            "metric": "risk_exposure",
            "value": f"{critical_count} critical-risk hosts, {high_count} high-risk hosts discovered",
            "threshold": "0 critical-risk hosts",
        })

    return {**summary, "evidence": evidence}
