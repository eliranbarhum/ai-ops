"""
Deterministic VCF Readiness Scoring Engine.
NO LLM. NO randomness. Same inputs → same outputs.

Score breakdown for vcf_readiness (100 points total):
  20 pts — Compute: CPU headroom (per-host, worst governs; imbalance detection)
  20 pts — Compute: RAM headroom (per-host consumed memory)
  15 pts — Storage: datastore capacity (vSAN-aware) + I/O latency when present
  15 pts — Platform: SDDC task failures (deduped patterns, repeat-failure rules)
  10 pts — Hosts: connection health + mixed ESXi build detection
  10 pts — Compatibility: VCF version & HCL gaps
   5 pts — Resilience: HA / DRS / N+1 host count
   5 pts — Network Security: dangerous ports from discovery (syslog-aware)

Design rules learned from the 2026-06 ground-truth audit:
  - A missing metric is scored as MISSING (data_missing), never as a healthy 0.
  - Per-host beats per-cluster: cluster averages hid a 50% spread across hosts.
  - Findings are aggregated (one risk per pattern), not repeated per entity.
"""

from __future__ import annotations
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

def _thresholds() -> dict:
    return {
        "CPU_WARN":        float(os.getenv("SCORE_CPU_WARN",     "75")),
        "CPU_CRIT":        float(os.getenv("SCORE_CPU_CRIT",     "90")),
        "RAM_WARN":        float(os.getenv("SCORE_RAM_WARN",     "80")),
        "RAM_CRIT":        float(os.getenv("SCORE_RAM_CRIT",     "92")),
        "LATENCY_WARN_MS": float(os.getenv("SCORE_LATENCY_WARN", "10")),
        "LATENCY_CRIT_MS": float(os.getenv("SCORE_LATENCY_CRIT", "20")),
    }


# Module-level names kept for backward compat; re-read via _thresholds() inside scorer functions
CPU_WARN        = 75.0
CPU_CRIT        = 90.0
RAM_WARN        = 80.0
RAM_CRIT        = 92.0
LATENCY_WARN_MS = 10.0
LATENCY_CRIT_MS = 20.0


@dataclass
class SubScoreResult:
    score: int
    risks: list = field(default_factory=list)
    recs:  list = field(default_factory=list)
    entities: list = field(default_factory=list)  # contributing entities for trace UI
    detail: dict | None = None                     # extra data (compatibility only)
    data_missing: bool = False                     # True when collector returned no data


@dataclass
class _Subscorer:
    name:      str
    label:     str
    icon:      str
    max_score: int
    _fn:       Callable[[dict], SubScoreResult]

    def score(self, data: dict) -> SubScoreResult:
        return self._fn(data)


def _sub(name, label, score, max_score, risks, icon="", entities=None, detail=None, data_missing=False) -> dict:
    pct = round((score / max_score) * 100) if max_score else 0
    status = "unknown" if data_missing else ("ok" if pct >= 80 else "warning" if pct >= 50 else "critical")
    d: dict = {
        "name": name, "label": label, "score": score, "max": max_score,
        "pct": pct, "status": status, "icon": icon,
        "critical_count": sum(1 for r in risks if r.get("severity") == "critical"),
        "warning_count":  sum(1 for r in risks if r.get("severity") == "warning"),
        "entities": entities or [],
        "data_missing": data_missing,
    }
    if detail is not None:
        d["detail"] = detail
    return d


def _no_data(label: str, component: str, max_score: int) -> SubScoreResult:
    """Returned when a collector provided no data for a dimension.
    Scores at half the maximum — conservative penalty that degrades the overall
    score to WARNING on total outage, without tanking it to NOT_READY on a single miss.
    """
    return SubScoreResult(
        score=max_score // 2,
        risks=[{"severity": "warning",
                "message": f"{label} data unavailable — collector returned no usable data. Scoring conservatively.",
                "component": component}],
        recs=[f"Verify the {component} collector is reachable and returning data before trusting this score."],
        data_missing=True,
    )


def _extract_entities(tools_data: dict, *tool_names: str) -> list[dict]:
    entities = []
    for name in tool_names:
        tool_result = tools_data.get(name, {})
        normalized = tool_result.get("normalized", {})
        entities.extend(normalized.get("entities", []))
    return entities


def _host_or_cluster_values(data: dict, metric: str) -> tuple[list[tuple[str, float]], str]:
    """
    Preferred source: per-host values (esxi_host entities) — a cluster average of
    36% can hide one host at 90%. Fallback: cluster-level values from vROps.
    Returns ([(name, value)...], granularity) where granularity is "host"|"cluster".
    """
    host_entities = [
        e for e in _extract_entities(data, "get_esxi_metrics")
        if e.get("entity_type") == "esxi_host" and e.get(metric) is not None
    ]
    if host_entities:
        return [(e.get("name", "?"), e[metric]) for e in host_entities], "host"

    cluster_entities = [
        e for e in _extract_entities(data, "get_vrops_metrics", "get_cluster_capacity")
        if e.get(metric) is not None
    ]
    return [(e.get("name", "?"), e[metric]) for e in cluster_entities], "cluster"


def _drs_enabled(data: dict) -> bool:
    for e in _extract_entities(data, "get_vcenter_inventory", "get_cluster_capacity"):
        if e.get("entity_type") == "cluster" and e.get("drs_enabled"):
            return True
    return False


# ── Individual scorers ──────────────────────────────────────────────────────────

def _score_usage(data: dict, metric: str, label: str, warn: float, crit: float,
                 max_score: int, rec_crit: str, rec_warn: str) -> SubScoreResult:
    values, granularity = _host_or_cluster_values(data, metric)
    if not values:
        return _no_data(label, "compute", max_score)

    max_name, max_val = max(values, key=lambda nv: nv[1])
    entities = [
        {"name": n, "value": round(v, 1), "unit": "%", "threshold": crit if v >= crit else warn,
         "status": "critical" if v >= crit else "warning" if v >= warn else "ok"}
        for n, v in values
    ]
    risks, recs = [], []
    if max_val >= crit:
        score = round(max_score * 0.2)
        risks.append({"severity": "critical", "component": "compute",
                      "message": f"{label} critical on {max_name}: {max_val:.1f}% (threshold {crit}%)"})
        recs.append(rec_crit)
    elif max_val >= warn:
        score = round(max_score * 0.6)
        risks.append({"severity": "warning", "component": "compute",
                      "message": f"{label} elevated on {max_name}: {max_val:.1f}% (threshold {warn}%)"})
        recs.append(rec_warn.format(name=max_name, val=max_val))
    else:
        score = max_score

    # Load imbalance: meaningful only with per-host data and several hosts.
    # A 40-point spread with DRS on means DRS is not doing its job (rules,
    # pinning, or a host effectively out of rotation).
    if granularity == "host" and len(values) >= 3:
        min_name, min_val = min(values, key=lambda nv: nv[1])
        spread = max_val - min_val
        if spread >= 40 and max_val >= 30:
            drs = _drs_enabled(data)
            score = max(score - round(max_score * 0.2), round(max_score * 0.2))
            risks.append({
                "severity": "warning", "component": "compute",
                "message": (
                    f"{label} imbalance: {max_name} at {max_val:.1f}% vs {min_name} at {min_val:.1f}% "
                    f"({spread:.0f}-point spread{', DRS is enabled but not balancing' if drs else ', DRS is disabled'})"
                ),
            })
            recs.append(
                f"Investigate {label.lower()} imbalance — check DRS rules/automation level and whether {min_name} is excluded from placement"
                if drs else
                f"Enable DRS to balance {label.lower()} across hosts ({spread:.0f}-point spread today)"
            )

    return SubScoreResult(score=score, risks=risks, recs=recs, entities=entities)


def _score_cpu(data: dict) -> SubScoreResult:
    t = _thresholds()
    return _score_usage(
        data, "cpu_usage", "CPU usage", t["CPU_WARN"], t["CPU_CRIT"], 20,
        rec_crit="Immediately reduce CPU load or add compute capacity before VCF upgrade",
        rec_warn="Monitor CPU headroom — {name} peaks at {val:.1f}% and may spike during VCF upgrade maintenance windows",
    )


def _score_ram(data: dict) -> SubScoreResult:
    t = _thresholds()
    return _score_usage(
        data, "ram_usage", "Memory usage", t["RAM_WARN"], t["RAM_CRIT"], 20,
        rec_crit="Add memory capacity — VCF upgrade requires headroom for management VMs during migration",
        rec_warn="Memory headroom is thin on {name} at {val:.1f}% — consider vMotion of non-critical VMs before upgrade",
    )


def _score_storage(data: dict) -> SubScoreResult:
    """
    Capacity first (always available from vCenter), latency second (only when
    vROps actually provides it). The old scorer trusted a latency metric that
    never existed and gave storage a perfect score forever.
    """
    t = _thresholds()
    LAT_WARN = t["LATENCY_WARN_MS"]; LAT_CRIT = t["LATENCY_CRIT_MS"]
    max_score = 15

    datastores = [
        e for e in _extract_entities(data, "get_datastore_capacity")
        if e.get("entity_type") == "datastore"
    ]
    lat_values = [
        (e.get("name", "?"), e["storage_latency_ms"])
        for e in _extract_entities(data, "get_esxi_metrics", "get_vrops_metrics")
        if e.get("storage_latency_ms") is not None
    ]

    if not datastores and not lat_values:
        return _no_data("Storage", "storage", max_score)

    score = max_score
    risks, recs, entities = [], [], []

    # ── Capacity (up to -10) ────────────────────────────────────────────────
    for ds in sorted(datastores, key=lambda d: -(d.get("used_pct") or 0)):
        used = ds.get("used_pct", 0)
        warn = ds.get("warn_pct", 75)
        crit = ds.get("crit_pct", 90)
        is_vsan = "VSAN" in (ds.get("ds_type") or "")
        status = "critical" if used >= crit else "warning" if used >= warn else "ok"
        entities.append({"name": ds.get("name", "?"), "value": round(used, 1), "unit": "% used",
                         "threshold": crit if used >= crit else warn, "status": status})
        if status == "critical":
            score -= 10
            risks.append({"severity": "critical", "component": "storage",
                          "message": f"Datastore {ds['name']} at {used:.1f}% used ({ds.get('ds_type','')}) — "
                                     f"{'vSAN rebuild headroom exhausted' if is_vsan else 'capacity nearly exhausted'}"})
            recs.append(f"Free up or expand {ds['name']} before any upgrade — "
                        f"{'vSAN needs slack to re-protect data during host reboots' if is_vsan else 'snapshots and staging need free space'}")
        elif status == "warning":
            score -= 4
            risks.append({"severity": "warning", "component": "storage",
                          "message": f"Datastore {ds['name']} at {used:.1f}% used ({ds.get('ds_type','')}, warn at {warn}%)"})
            recs.append(f"Plan capacity for {ds['name']} — {used:.1f}% used and upgrades temporarily increase usage")

    # ── Latency (up to -5, only when telemetry exists) ──────────────────────
    if lat_values:
        lat_name, max_latency = max(lat_values, key=lambda nv: nv[1])
        entities.extend(
            {"name": n, "value": round(v, 2), "unit": "ms",
             "threshold": LAT_CRIT if v >= LAT_CRIT else LAT_WARN,
             "status": "critical" if v >= LAT_CRIT else "warning" if v >= LAT_WARN else "ok"}
            for n, v in lat_values
        )
        if max_latency >= LAT_CRIT:
            score -= 5
            risks.append({"severity": "critical", "component": "storage",
                          "message": f"Storage latency critical on {lat_name}: {max_latency:.1f}ms (threshold {LAT_CRIT}ms)"})
            recs.append("Investigate storage latency — VCF upgrade may be aborted if storage performance degrades further")
        elif max_latency >= LAT_WARN:
            score -= 2
            risks.append({"severity": "warning", "component": "storage",
                          "message": f"Storage latency elevated on {lat_name}: {max_latency:.1f}ms (threshold {LAT_WARN}ms)"})
            recs.append("Investigate datastore latency before initiating VCF upgrade")

    return SubScoreResult(score=max(0, score), risks=risks, recs=recs, entities=entities)


# Failed-task patterns that block upgrade logistics specifically
_LCM_PATTERNS = ("BUNDLE_DOWNLOAD", "BUNDLE_UPLOAD", "DEPOT")


def _score_logs(data: dict) -> SubScoreResult:
    """
    Deduplicate failures by pattern and score on distinct problems, not raw
    event count. 25 copies of the same failed task is one problem, repeated —
    and a repeated failure is *more* suspicious than a one-off, not less.
    """
    raw = [e for e in _extract_entities(data, "query_logs") if e.get("entity_type") == "log_event"]
    max_score = 15
    if not raw:
        return SubScoreResult(score=max_score)

    pattern_counts: Counter = Counter(e.get("name", "event") for e in raw)
    severity_by_pattern: dict[str, str] = {}
    for e in raw:
        name = e.get("name", "event")
        sev = {"red": "critical", "yellow": "warning"}.get(e.get("health_state"), "info")
        # Keep the worst severity seen for the pattern
        order = {"critical": 0, "warning": 1, "info": 2}
        if order.get(sev, 2) < order.get(severity_by_pattern.get(name, "info"), 2):
            severity_by_pattern[name] = sev
        severity_by_pattern.setdefault(name, sev)

    score = max_score
    risks, recs, entities = [], [], []

    for pattern, count in pattern_counts.most_common():
        sev = severity_by_pattern.get(pattern, "info")
        is_lcm = any(k in pattern.upper() for k in _LCM_PATTERNS)

        # Escalation: repeated failures of the same task are a systemic problem
        if sev == "info" and count >= 3:
            sev = "warning"

        if sev == "critical":
            score -= 8
        elif sev == "warning":
            score -= 4 if count >= 3 else 2

        label = f"{pattern} — {count}× in window" if count > 1 else pattern
        entities.append({"name": label, "value": sev, "unit": "pattern",
                         "status": "critical" if sev == "critical" else "warning" if sev == "warning" else "ok"})

        if sev in ("critical", "warning"):
            if is_lcm:
                risks.append({"severity": sev, "component": "platform",
                              "message": f"LCM depot failing: {pattern} failed {count}× — upgrade bundles cannot be downloaded/staged"})
                recs.append("Fix SDDC Manager depot connectivity (proxy/DNS/credentials) — without bundle downloads no upgrade can be staged")
            else:
                risks.append({"severity": sev, "component": "platform",
                              "message": f"{pattern} failed {count}× in the lookback window"})
                recs.append(f"Investigate root cause of repeated '{pattern}' failures before upgrade")

    return SubScoreResult(score=max(0, score), risks=risks, recs=recs, entities=entities)


def _score_hosts(data: dict) -> SubScoreResult:
    raw = _extract_entities(data, "get_esxi_metrics", "get_vcenter_inventory")
    host_entities = [e for e in raw if e.get("entity_type") == "esxi_host"]
    # Dedup by name — esxi_metrics and inventory can both report the same host
    seen: dict[str, dict] = {}
    for e in host_entities:
        seen.setdefault(e.get("name", "?"), e)
    host_entities = list(seen.values())
    if not host_entities:
        return _no_data("Host health", "hosts", 10)

    red    = [e for e in host_entities if e.get("health_state") == "red"]
    yellow = [e for e in host_entities if e.get("health_state") == "yellow"]
    ok     = [e for e in host_entities if e.get("health_state") not in ("red", "yellow")]

    entities = (
        [{"name": e.get("name", "?"), "value": "disconnected", "unit": "state", "status": "critical"} for e in red] +
        [{"name": e.get("name", "?"), "value": "degraded",     "unit": "state", "status": "warning"}  for e in yellow] +
        [{"name": e.get("name", "?"), "value": "healthy",      "unit": "state", "status": "ok"}       for e in ok]
    )
    risks, recs = [], []
    if red:
        score = 0
        names = ", ".join(h.get("name", "?") for h in red[:3])
        risks.append({"severity": "critical", "message": f"Disconnected/unhealthy ESXi hosts: {names}", "component": "compute"})
        recs.append("Restore disconnected ESXi hosts to connected state before VCF upgrade")
    elif yellow:
        score = 5
        names = ", ".join(h.get("name", "?") for h in yellow[:3])
        risks.append({"severity": "warning", "message": f"ESXi hosts in degraded state: {names}", "component": "compute"})
        recs.append("Investigate degraded ESXi host states before upgrade")
    else:
        score = 10

    # Mixed ESXi builds complicate upgrades and indicate drift
    versions = {e.get("esxi_version") for e in host_entities if e.get("esxi_version")}
    if len(versions) > 1:
        score = max(score - 3, 0)
        risks.append({"severity": "warning", "component": "compute",
                      "message": f"Mixed ESXi builds across hosts: {', '.join(sorted(versions))}"})
        recs.append("Remediate hosts to a single ESXi build before upgrading the fleet")

    return SubScoreResult(score=score, risks=risks, recs=recs, entities=entities)


def _score_resilience(data: dict) -> SubScoreResult:
    """HA / DRS / N+1 — config signals that decide whether an upgrade can roll
    through the cluster without downtime."""
    clusters = [
        e for e in _extract_entities(data, "get_vcenter_inventory", "get_cluster_capacity")
        if e.get("entity_type") == "cluster"
    ]
    # Dedup by name, prefer entries that actually carry the flags
    by_name: dict[str, dict] = {}
    for c in clusters:
        cur = by_name.get(c.get("name", "?"))
        if cur is None or (not cur.get("ha_enabled") and c.get("ha_enabled") is not None):
            by_name[c.get("name", "?")] = c
    clusters = list(by_name.values())
    if not clusters:
        return _no_data("Cluster resilience", "resilience", 5)

    host_count = len({
        e.get("name") for e in _extract_entities(data, "get_esxi_metrics", "get_vcenter_inventory")
        if e.get("entity_type") == "esxi_host"
    })

    score = 5
    risks, recs, entities = [], [], []
    for c in clusters:
        name = c.get("name", "?")
        ha = bool(c.get("ha_enabled"))
        drs = bool(c.get("drs_enabled"))
        entities.append({"name": f"{name} HA",  "value": "on" if ha else "off",  "unit": "config", "status": "ok" if ha else "critical"})
        entities.append({"name": f"{name} DRS", "value": "on" if drs else "off", "unit": "config", "status": "ok" if drs else "warning"})
        if not ha:
            score -= 3
            risks.append({"severity": "critical", "component": "resilience",
                          "message": f"vSphere HA is disabled on cluster {name} — host failure during upgrade means VM downtime"})
            recs.append(f"Enable vSphere HA on {name} before entering an upgrade maintenance window")
        if not drs:
            score -= 1
            risks.append({"severity": "warning", "component": "resilience",
                          "message": f"DRS is disabled on cluster {name} — rolling host remediation requires manual vMotion"})
            recs.append(f"Enable DRS (at least partially automated) on {name} so maintenance mode can evacuate hosts automatically")

    if 0 < host_count < 3:
        score -= 1
        risks.append({"severity": "warning", "component": "resilience",
                      "message": f"Only {host_count} ESXi host(s) — no N+1 capacity for rolling upgrades"})
        recs.append("A minimum of 3 hosts (4 for vSAN) is recommended to tolerate one host in maintenance")

    return SubScoreResult(score=max(0, score), risks=risks, recs=recs, entities=entities)


def _score_compatibility(data: dict) -> SubScoreResult:
    compat_data = data.get("check_vcf_compatibility", {})
    # Copy to avoid mutating the caller's tools_data dict on the extend() call below
    gaps = list(compat_data.get("compatibility_gaps", []))
    if not gaps:
        for e in compat_data.get("normalized", {}).get("entities", []):
            gaps.extend(e.get("compatibility_gaps", []))

    interop_data        = data.get("check_broadcom_interop", {})
    interop_gaps        = interop_data.get("interop_gaps", [])
    interop_warnings    = interop_data.get("interop_warnings", [])
    deprecation_warnings= interop_data.get("deprecation_warnings", [])
    hcl_results         = interop_data.get("hcl_results", [])
    components          = interop_data.get("components", {})
    target_version      = interop_data.get("target_version", "9.1")
    upgrade_workflow    = interop_data.get("upgrade_workflow", {})

    sddc_data    = data.get("get_sddc_health", {})
    sddc_gaps    = sddc_data.get("upgrade_blockers", [])
    sddc_warnings= sddc_data.get("upgrade_warnings", [])

    all_critical = gaps + interop_gaps + sddc_gaps
    all_warnings = interop_warnings + sddc_warnings + deprecation_warnings

    detail = {
        "components": components, "target_version": target_version,
        "hcl_results": hcl_results, "version_gaps": gaps,
        "interop_gaps": interop_gaps, "sddc_gaps": sddc_gaps,
        "sddc_warnings": sddc_warnings, "deprecation_warnings": deprecation_warnings,
        "hcl_warnings": [w for w in interop_warnings
                         if not w.startswith("[Deprecation") and not w.startswith("[Upgrade Workflow")
                         and not w.startswith("[New Requirement") and not w.startswith("[Consolidation")],
        "upgrade_workflow": upgrade_workflow,
    }

    entities = (
        [{"name": g, "value": "blocker", "unit": "gap", "status": "critical"} for g in all_critical[:10]] +
        [{"name": w, "value": "warning", "unit": "gap", "status": "warning"}  for w in all_warnings[:5]]
    )

    if not all_critical and not all_warnings:
        return SubScoreResult(score=10, entities=entities, detail=detail)

    score = 0 if len(all_critical) >= 3 else 5 if len(all_critical) >= 1 else 8
    risks, recs = [], []
    for gap in all_critical:
        risks.append({"severity": "critical", "message": gap, "component": "platform"})
    for warning in (interop_warnings + sddc_warnings)[:4]:
        if not warning.startswith("[Deprecation"):
            risks.append({"severity": "warning", "message": warning, "component": "platform"})
    for dep in deprecation_warnings[:3]:
        risks.append({"severity": "info", "message": dep, "component": "platform"})

    if all_critical:
        recs.append("Address all version compatibility and interoperability gaps before VCF upgrade")
    if interop_warnings:
        recs.append("Verify hardware HCL certification at compatibilitymatrix.broadcom.com before upgrade")
    if sddc_warnings:
        recs.append("Resolve SDDC Manager domain warnings before initiating VCF upgrade lifecycle")
    if deprecation_warnings:
        recs.append("Review deprecated hardware and drivers listed in VCF 9.1 release notes — plan replacements before upgrade")
    if upgrade_workflow:
        required_wf = upgrade_workflow.get("required_workflow", "")
        if required_wf:
            recs.append(f"Required upgrade workflow: {required_wf} (see KB440630)")
        for action in upgrade_workflow.get("consolidation_actions", []):
            comp = action.get("component", "")
            if comp == "vcf_license_server":
                recs.append("Deploy the new centralized VCF License Server — required component in VCF 9.1")
            elif comp == "vmware_identity_broker":
                recs.append("VMware Identity Broker will be consolidated into VCF Management Services — verify network placement to determine if downtime is required")

    return SubScoreResult(score=score, risks=risks, recs=recs, entities=entities, detail=detail)


# Unambiguous legacy plaintext protocols — always critical
_CRITICAL_PORTS = {23: "Telnet", 21: "FTP", 512: "rexec", 513: "rlogin"}
# Ports whose IANA name looks scary but are usually something else on VMware
# infrastructure: 514/tcp is syslog ingestion on vCenter/Avi/log appliances,
# not rsh. Flag for verification, don't scream.
_VERIFY_PORTS = {514: ("syslog (TCP) — nmap labels it 'shell/rsh' but on VMware appliances this is almost always the syslog listener", "rsh")}
_HIGH_PORTS    = {3389: "RDP", 5900: "VNC", 5901: "VNC", 2049: "NFS", 111: "rpcbind"}


def _score_network_security(data: dict, max_score: int = 5) -> SubScoreResult:
    """
    Penalties are defined on a 25-point native scale and scaled to max_score:
    inside vcf_readiness this dimension is worth 5 pts; as the standalone
    `network` target it runs at 25 pts so a single finding does not slam the
    normalized score to 0.
    """
    discovery = data.get("get_discovery_assets", {})
    if not discovery.get("scanned"):
        # No penalty without a scan, but never present "unscanned" as "secure"
        return SubScoreResult(
            score=max_score,
            risks=[{"severity": "info", "component": "network_security",
                    "message": "No network discovery scan has run — port exposure is unverified"}],
            recs=["Run a discovery scan (Discovery page) to verify management-network port exposure"],
            data_missing=True,
        )

    findings = discovery.get("dangerous_port_findings", [])
    risks, recs, entities = [], [], []

    # Aggregate by port — 8 hosts with the same port open is ONE finding with
    # 8 affected hosts, not 8 separate critical alerts.
    by_port: dict[int, list[dict]] = {}
    for f in findings:
        by_port.setdefault(int(f.get("port", 0)), []).append(f)

    penalty = 0.0   # in native 25-pt units
    for port, items in sorted(by_port.items()):
        hosts = []
        for f in items:
            dns = f.get("dns_names", [])
            hosts.append(dns[0] if dns else f.get("ip", "?"))
        host_list = ", ".join(hosts[:5])
        if len(hosts) > 5:
            host_list += f" +{len(hosts) - 5} more"

        if port in _CRITICAL_PORTS:
            svc = _CRITICAL_PORTS[port]
            penalty += 8 + min(len(hosts) - 1, 4)   # more hosts = worse
            risks.append({"severity": "critical", "component": "network_security",
                          "message": f"{svc} (port {port}) open on {len(hosts)} host(s): {host_list} — legacy plaintext protocol"})
            recs.append(f"Disable {svc} on: {host_list} — use SSH/HTTPS alternatives")
            for h in hosts:
                entities.append({"name": h, "value": f"port {port} ({svc})", "unit": "port", "status": "critical"})
        elif port in _VERIFY_PORTS:
            explanation, scary_name = _VERIFY_PORTS[port]
            penalty += 2
            risks.append({"severity": "warning", "component": "network_security",
                          "message": f"Port {port} open on {len(hosts)} host(s): {host_list} — likely {explanation}"})
            recs.append(f"Verify port {port} on these hosts is syslog and not {scary_name}: {host_list}")
            for h in hosts:
                entities.append({"name": h, "value": f"port {port} (verify: syslog vs {scary_name})", "unit": "port", "status": "warning"})
        elif port in _HIGH_PORTS:
            svc = _HIGH_PORTS[port]
            penalty += 3
            risks.append({"severity": "warning", "component": "network_security",
                          "message": f"{svc} (port {port}) exposed on {len(hosts)} host(s): {host_list}"})
            recs.append(f"Restrict {svc} access to the management VLAN only: {host_list}")
            for h in hosts:
                entities.append({"name": h, "value": f"port {port} ({svc})", "unit": "port", "status": "warning"})

    risk_breakdown  = discovery.get("risk_breakdown", {})
    critical_hosts  = risk_breakdown.get("critical", 0)
    if critical_hosts > 0:
        penalty += 2
        risks.append({"severity": "warning", "component": "network_security",
                      "message": f"{critical_hosts} host(s) with critical discovery risk score on the management network"})

    score = max(0, round(max_score - penalty * (max_score / 25.0)))
    return SubScoreResult(score=score, risks=risks, recs=recs, entities=entities)


# ── Rollback risk sub-module ─────────────────────────────────────────────────────

def compute_rollback_risk(tools_data: dict) -> dict:
    """
    Rollback risk score (0–100): higher = riskier to upgrade or roll back.
    Signals: ESXi host count, powered-on VM count, vSAN resync, upgrade blockers.
    """
    host_entities = [
        e for e in _extract_entities(tools_data, "get_esxi_metrics", "get_vcenter_inventory")
        if e.get("entity_type") == "esxi_host"
    ]
    host_count = len({e.get("name") for e in host_entities})

    # vm_count comes from the inventory summary entity — there are no per-VM
    # entities in the pipeline (the old per-VM count was always 0).
    vm_count = 0
    for e in _extract_entities(tools_data, "get_vcenter_inventory"):
        if e.get("entity_type") == "summary" and e.get("vm_count"):
            vm_count = int(e["vm_count"])
            break

    vrops_entities = _extract_entities(tools_data, "get_vrops_metrics")
    vsan_resync = any((e.get("vsan_resync_pct") or 0) > 0 for e in vrops_entities)

    compat_data = tools_data.get("check_vcf_compatibility", {})
    sddc_data = tools_data.get("get_sddc_health", {})
    blockers = (
        len(compat_data.get("compatibility_gaps", []))
        + len(sddc_data.get("upgrade_blockers", []))
    )

    score = 0
    factors: list[str] = []

    if host_count:
        score += min(host_count * 3, 25)
        factors.append(f"{host_count} ESXi host{'s' if host_count != 1 else ''} require reboots")

    if vm_count:
        score += min(round(vm_count / 10), 20)
        factors.append(f"{vm_count} VM{'s' if vm_count != 1 else ''} in inventory may need vMotion during host remediation")

    if vsan_resync:
        score += 25
        factors.append("vSAN resync in progress")

    if blockers:
        score += min(blockers * 10, 30)
        factors.append(f"{blockers} upgrade blocker{'s' if blockers != 1 else ''}")

    score = min(score, 100)
    level = "high" if score >= 70 else "medium" if score >= 40 else "low"

    return {
        "score": score,
        "level": level,
        "reasons": factors,
        "host_count": host_count,
        "vm_count": vm_count,
        "vsan_resync": vsan_resync,
        "blocker_count": blockers,
    }


# ── Subscorer registry ──────────────────────────────────────────────────────────

SUBSCORERS: list[_Subscorer] = [
    _Subscorer("cpu",              "CPU Headroom",       "cpu",     20, _score_cpu),
    _Subscorer("ram",              "Memory Headroom",    "memory",  20, _score_ram),
    _Subscorer("storage",          "Storage",            "storage", 15, _score_storage),
    _Subscorer("platform",         "Platform Health",    "platform",15, _score_logs),
    _Subscorer("hosts",            "Host Health",        "hosts",   10, _score_hosts),
    _Subscorer("compatibility",    "HCL & Compatibility","hcl",     10, _score_compatibility),
    _Subscorer("resilience",       "Resilience (HA/DRS)","shield",   5, _score_resilience),
    _Subscorer("network_security", "Network Security",   "shield",   5, _score_network_security),
]

# Which sub-scorers run per scan type — only scorers whose data is actually collected
SUBSCORERS_BY_TARGET: dict[str, list[str]] = {
    "vcf_readiness":    ["cpu", "ram", "storage", "platform", "hosts", "compatibility", "resilience", "network_security"],
    "capacity":         ["cpu", "ram", "storage", "hosts"],
    "anomaly_detection":["cpu", "ram", "platform", "hosts"],
    "network":          ["network_security"],
}

_SUBSCORER_INDEX: dict[str, _Subscorer] = {s.name: s for s in SUBSCORERS}

# Standalone network target runs the same scorer on its native 25-pt scale so a
# single confirmed finding reads as WARNING, not an instant 0/100.
_NETWORK_FULL = _Subscorer(
    "network_security", "Network Security", "shield", 25,
    lambda d: _score_network_security(d, max_score=25),
)


# ── Main entry point ────────────────────────────────────────────────────────────

def compute_score(tools_data: dict, target: str) -> dict:
    enabled = SUBSCORERS_BY_TARGET.get(target, SUBSCORERS_BY_TARGET["vcf_readiness"])
    if target == "network":
        active = [_NETWORK_FULL]
    else:
        active = [_SUBSCORER_INDEX[name] for name in enabled if name in _SUBSCORER_INDEX]
    max_total = sum(s.max_score for s in active)

    risk_factors:    list[dict] = []
    recommendations: list[str] = []
    sub_scores:      list[dict] = []
    raw_score  = max_total
    signals_ok = 0
    signals_missing = 0

    for subscorer in active:
        result = subscorer.score(tools_data)
        raw_score -= (subscorer.max_score - result.score)
        risk_factors.extend(result.risks)
        recommendations.extend(result.recs)
        sub_scores.append(_sub(
            subscorer.name, subscorer.label, result.score, subscorer.max_score,
            result.risks, subscorer.icon, result.entities, result.detail,
            data_missing=result.data_missing,
        ))
        if result.data_missing:
            signals_missing += 1
        else:
            signals_ok += 1

    # Normalize to 0–100 regardless of how many sub-scorers ran
    normalized = round((raw_score / max_total) * 100) if max_total else 0
    normalized = max(0, min(100, normalized))
    status = "READY" if normalized >= 80 else "WARNING" if normalized >= 50 else "NOT_READY"

    # Deduplicate risk_factors by (severity, message) — different clusters can
    # trip the same rule and emit duplicate cards
    seen_rf: set[tuple] = set()
    unique_risks: list[dict] = []
    for rf in risk_factors:
        key = (rf.get("severity"), rf.get("message"))
        if key not in seen_rf:
            seen_rf.add(key)
            unique_risks.append(rf)

    signals_total = signals_ok + signals_missing

    return {
        "readiness_score": normalized,
        "status":          status,
        "risk_factors":    unique_risks,
        "recommendations": list(dict.fromkeys(recommendations)),
        "sub_scores":      sub_scores,
        "signals_scored":  signals_ok,
        "signals_total":   signals_total,
        "confidence_note": (
            f"Scored on {signals_ok}/{signals_total} signals — {signals_missing} collector(s) returned no data"
            if signals_missing else None
        ),
        "rollback_risk": compute_rollback_risk(tools_data),
    }
