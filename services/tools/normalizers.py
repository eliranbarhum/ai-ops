"""
Deterministic normalization functions — no LLM, no randomness.
All functions return {"entities": [...]} with the canonical schema.

Canonical entity schema:
{
  "entity_type": str,
  "name": str,
  "cpu_usage": float | None,
  "ram_usage": float | None,
  "storage_latency_ms": float | None,
  "health_state": "green" | "yellow" | "red" | "unknown",
  "timestamp": str  # ISO-8601
}
"""

from datetime import datetime, timezone


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _health_from_connection(state: str, power: str) -> str:
    if state == "CONNECTED" and power == "POWERED_ON":
        return "green"
    if state == "DISCONNECTED":
        return "red"
    return "yellow"


def normalize_vcenter_inventory(data: dict) -> dict:
    entities = []
    ts = data.get("timestamp", _ts())

    for dc in data.get("datacenters", []):
        entities.append({
            "entity_type": "datacenter",
            "name": dc.get("name", "unknown"),
            "cpu_usage": None,
            "ram_usage": None,
            "storage_latency_ms": None,
            "health_state": "green",
            "timestamp": ts,
        })

    for cluster in data.get("clusters", []):
        entities.append({
            "entity_type": "cluster",
            "name": cluster.get("name", "unknown"),
            "cpu_usage": None,
            "ram_usage": None,
            "storage_latency_ms": None,
            "health_state": "green",
            "timestamp": ts,
            "ha_enabled": cluster.get("ha_enabled", False),
            "drs_enabled": cluster.get("drs_enabled", False),
            "host_count": cluster.get("host_count", 0),
        })

    # Hosts from inventory carry connection state — the host-health fallback
    # when the dedicated esxi_metrics tool fails.
    for host in data.get("hosts", []):
        entities.append({
            "entity_type": "esxi_host",
            "name": host.get("name", "unknown"),
            "cpu_usage": None,
            "ram_usage": None,
            "storage_latency_ms": None,
            "health_state": _health_from_connection(
                host.get("connection_state", "UNKNOWN"),
                host.get("power_state", "UNKNOWN"),
            ),
            "timestamp": ts,
        })

    entities.append({
        "entity_type": "summary",
        "name": "vcenter_inventory",
        "cpu_usage": None,
        "ram_usage": None,
        "storage_latency_ms": None,
        "health_state": "green",
        "timestamp": ts,
        "vm_count": data.get("vm_count", 0),
    })

    return {"entities": entities, "source": "VCENTER"}


def normalize_cluster_capacity(data: dict) -> dict:
    entities = []
    ts = data.get("timestamp", _ts())

    for cluster in data.get("clusters", []):
        # vCenter REST gives no cluster usage metrics in VCF 9.x — keep them None
        # so the scorer relies on vROps instead of treating fake 0% as healthy.
        entities.append({
            "entity_type": "cluster",
            "name": cluster.get("name", "unknown"),
            "cpu_usage": cluster.get("cpu_usage_pct"),
            "ram_usage": cluster.get("ram_usage_pct"),
            "storage_latency_ms": cluster.get("storage_latency_ms"),
            "health_state": _health_from_thresholds(
                cluster.get("cpu_usage_pct"),
                cluster.get("ram_usage_pct"),
            ),
            "timestamp": ts,
            "ha_enabled": cluster.get("ha_enabled", False),
            "drs_enabled": cluster.get("drs_enabled", False),
            "host_count": cluster.get("host_count", 0),
        })

    return {"entities": entities, "source": "VCENTER"}


def normalize_esxi_metrics(data: dict) -> dict:
    entities = []
    ts = data.get("timestamp", _ts())

    for host in data.get("hosts", []):
        entities.append({
            "entity_type": "esxi_host",
            "name": host.get("name", "unknown"),
            "cpu_usage": host.get("cpu_usage_pct"),
            "ram_usage": host.get("ram_usage_pct"),
            "storage_latency_ms": host.get("storage_latency_ms"),
            "health_state": _health_from_connection(
                host.get("connection_state", "UNKNOWN"),
                host.get("power_state", "UNKNOWN"),
            ),
            "timestamp": ts,
            "cpu_cores": host.get("cpu_cores", 0),
            "memory_gb": host.get("memory_gb", 0),
            "memory_size_mb": host.get("memory_size_mb", 0),
            "esxi_version": host.get("esxi_version", ""),
            "cpu_model": host.get("cpu_model", ""),
        })

    return {"entities": entities, "source": "ESXI"}


def normalize_vrops_metrics(data: dict) -> dict:
    entities = []
    ts = data.get("timestamp", _ts())

    for resource in data.get("resources", []):
        cpu = resource.get("cpu_usage")
        ram = resource.get("ram_usage")
        latency = resource.get("storage_latency_ms")
        entities.append({
            "entity_type": "cluster",
            "name": resource.get("name", "unknown"),
            "cpu_usage": cpu,
            "ram_usage": ram,
            "ram_active_pct": resource.get("ram_active_pct"),
            "storage_latency_ms": latency,
            "health_state": _health_from_thresholds(cpu, ram, latency),
            "timestamp": ts,
            "metrics_used": resource.get("_metrics_used"),
        })

    return {"entities": entities, "source": "VCF_OPERATIONS"}


def normalize_logs(data: dict) -> dict:
    entities = []
    ts = data.get("timestamp", _ts())

    for anomaly in data.get("anomalies", []):
        severity = anomaly.get("severity", "info")
        entities.append({
            "entity_type": "log_event",
            "name": anomaly.get("pattern", "unknown"),
            "cpu_usage": None,
            "ram_usage": None,
            "storage_latency_ms": None,
            "health_state": "red" if severity == "critical" else "yellow" if severity == "warning" else "green",
            "timestamp": ts,
            "severity": severity,
            "hostname": anomaly.get("hostname", "unknown"),
            "text": anomaly.get("text", ""),
        })

    return {"entities": entities, "source": "SDDC_MANAGER_TASKS", "total": data.get("total", 0)}


def normalize_network_metrics(data: dict) -> dict:
    entities = []
    ts = data.get("timestamp", _ts())

    for resource in data.get("network_resources", []):
        loss = resource.get("packet_loss_pct", 0)
        entities.append({
            "entity_type": "network_segment",
            "name": resource.get("name", "unknown"),
            "cpu_usage": None,
            "ram_usage": None,
            "storage_latency_ms": resource.get("latency_ms"),
            "health_state": "red" if loss > 1.0 else "yellow" if loss > 0.1 else "green",
            "timestamp": ts,
            "packet_loss_pct": resource.get("packet_loss_pct", 0),
            "throughput_mbps": resource.get("throughput_mbps", 0),
        })

    return {"entities": entities, "source": "VCF_OPERATIONS_FOR_NETWORKS"}


def _health_from_thresholds(cpu: float | None, ram: float | None, latency: float | None = 0) -> str:
    cpu = cpu or 0
    ram = ram or 0
    latency = latency or 0
    if cpu > 90 or ram > 92 or latency > 20:
        return "red"
    if cpu > 75 or ram > 80 or latency > 10:
        return "yellow"
    return "green"
