import os
import asyncio
import logging
import httpx
from datetime import datetime, timezone

logger = logging.getLogger("tool.sddc_health")

SDDC_COLLECTOR_URL = os.getenv("SDDC_COLLECTOR_URL", "http://collector-sddc:8011")


async def get_sddc_health() -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        async def _get(path: str) -> dict:
            try:
                r = await client.get(f"{SDDC_COLLECTOR_URL}{path}")
                return r.json() if r.status_code == 200 else {}
            except Exception as e:
                logger.warning(f"SDDC collector {path} failed: {e}")
                return {}

        system, domains_raw, hosts_raw, clusters_raw, upgradables_raw = await asyncio.gather(
            _get("/collect/system"),
            _get("/collect/domains"),
            _get("/collect/hosts"),
            _get("/collect/clusters"),
            _get("/collect/upgradables"),
        )

    sddc_version = system.get("version", "unknown")
    domain_list = domains_raw.get("domains", [])
    host_list = hosts_raw.get("hosts", [])
    cluster_list = clusters_raw.get("clusters", [])
    upgradable_list = upgradables_raw.get("upgradables", [])

    degraded_domains = [
        d for d in domain_list
        if d.get("status", "").upper() not in ("ACTIVE", "")
    ]
    domain_health = (
        "unknown" if not domain_list else
        "degraded" if degraded_domains else "ok"
    )

    upgrade_blockers: list[str] = []
    upgrade_warnings: list[str] = []
    for item in upgradable_list:
        comp = item.get("component_type", "UNKNOWN")
        domain_name = item.get("domain_name", "")
        from_v = item.get("current_version", "")
        to_v = item.get("target_version", "")
        if not item.get("upgradable", True):
            for reason in item.get("blocking_reasons", []):
                upgrade_blockers.append(f"{comp} in domain '{domain_name}': {reason}")
        elif from_v and to_v and from_v != to_v:
            upgrade_warnings.append(f"{comp}: {from_v} → {to_v} upgrade available in '{domain_name}'")

    degraded_clusters = [c["name"] for c in cluster_list if c.get("status", "").upper() not in ("ACTIVE", "")]

    evidence = [
        {"source": "SDDC_MANAGER", "metric": "sddc_version", "value": sddc_version, "threshold": "9.0"},
        {"source": "SDDC_MANAGER", "metric": "domain_count", "value": str(len(domain_list)), "threshold": None},
        {"source": "SDDC_MANAGER", "metric": "host_count", "value": str(len(host_list)), "threshold": None},
        {"source": "SDDC_MANAGER", "metric": "upgrade_blockers", "value": str(len(upgrade_blockers)), "threshold": "0"},
    ]
    for h in host_list[:10]:
        evidence.append({
            "source": "SDDC_MANAGER",
            "metric": f"esxi_version:{h.get('fqdn', '?')}",
            "value": h.get("esxi_version", "unknown"),
            "threshold": "8.0.3",
        })

    return {
        "sddc_version": sddc_version,
        "domains": domain_list,
        "hosts": host_list,
        "clusters": cluster_list,
        "upgradables": upgradable_list,
        "domain_health": domain_health,
        "degraded_domains": [d["name"] for d in degraded_domains],
        "degraded_clusters": degraded_clusters,
        "upgrade_blockers": upgrade_blockers,
        "upgrade_warnings": upgrade_warnings,
        "evidence": evidence,
        "normalized": {
            "entities": [
                {
                    "entity_type": "sddc_domain",
                    "name": d.get("name", ""),
                    "health_state": "red" if d.get("status", "").upper() not in ("ACTIVE", "") else "green",
                    "upgrade_state": d.get("upgrade_state", ""),
                }
                for d in domain_list
            ]
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
