import os
import asyncio
import logging
import httpx
from normalizers import normalize_esxi_metrics

logger = logging.getLogger("tool.esxi_metrics")

VCENTER_COLLECTOR_URL = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")
VROPS_COLLECTOR_URL   = os.getenv("VROPS_COLLECTOR_URL",   "http://collector-vrops:8004")


async def get_esxi_metrics() -> dict:
    """
    Per-host metrics from two sources merged by FQDN:
      - vCenter /collect/hosts       → connection/power state (authoritative for health)
      - vROps  /collect/host-details → real cpu/ram usage, ESXi version, hardware
    vCenter alone has no per-host metrics in VCF 9.x, so the vROps merge is what
    makes CPU/RAM/latency scoring per-host instead of cluster-wide.
    """
    async with httpx.AsyncClient(timeout=45.0) as client:
        async def _get(url: str) -> dict:
            try:
                r = await client.get(url)
                return r.json() if r.status_code == 200 else {}
            except Exception as e:
                logger.warning("collector fetch failed %s: %s", url, e)
                return {}

        vcenter_raw, vrops_raw = await asyncio.gather(
            _get(f"{VCENTER_COLLECTOR_URL}/collect/hosts"),
            _get(f"{VROPS_COLLECTOR_URL}/collect/host-details"),
        )

    # Index vROps host details by FQDN for the merge
    vrops_by_name = {h.get("name", ""): h for h in vrops_raw.get("hosts", [])}

    merged_hosts = []
    for host in vcenter_raw.get("hosts", []):
        name = host.get("name", "")
        v = vrops_by_name.get(name, {})
        merged_hosts.append({
            **host,
            "cpu_usage_pct":      v.get("cpu_usage"),
            "ram_usage_pct":      v.get("ram_usage"),
            "storage_latency_ms": v.get("storage_latency_ms"),
            "esxi_version":       v.get("esxi_version") or host.get("esxi_version", ""),
            "cpu_model":          v.get("cpu_model") or host.get("cpu_model", ""),
            "cpu_cores":          v.get("cpu_cores") or host.get("cpu_cores", 0),
            "memory_gb":          v.get("memory_gb") or host.get("memory_gb", 0),
        })
    # vROps may know hosts vCenter didn't list (other vCenters) — skip those;
    # health state must come from the vCenter this platform manages.

    raw = {"hosts": merged_hosts, "timestamp": vcenter_raw.get("timestamp", "")}
    normalized = normalize_esxi_metrics(raw)

    evidence = []
    for entity in normalized.get("entities", []):
        health = entity.get("health_state", "unknown")
        evidence.append({
            "source": "ESXI",
            "metric": "host_health",
            "value": f"{entity.get('name')}: {health}",
            "threshold": "green",
        })
        cpu = entity.get("cpu_usage")
        ram = entity.get("ram_usage")
        if cpu is not None:
            evidence.append({
                "source": "VCF_OPERATIONS",
                "metric": f"host_cpu_usage:{entity.get('name')}",
                "value": f"{cpu}%",
                "threshold": "75% warn / 90% crit",
            })
        if ram is not None:
            evidence.append({
                "source": "VCF_OPERATIONS",
                "metric": f"host_ram_usage:{entity.get('name')}",
                "value": f"{ram}%",
                "threshold": "80% warn / 92% crit",
            })
        lat = entity.get("storage_latency_ms")
        if lat is not None and lat > 0:
            evidence.append({
                "source": "VCF_OPERATIONS",
                "metric": "storage_latency_ms",
                "value": f"{lat} ms",
                "threshold": "< 10 ms",
            })

    return {"normalized": normalized, "evidence": evidence, "raw": raw}
