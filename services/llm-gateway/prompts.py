def build_system_prompt(target: str = "vcf_readiness", vcf_doc_context: str = "") -> str:
    goals = {
        "vcf_readiness": (
            "Interpret and explain a pre-computed VCF readiness assessment to infrastructure teams. "
            "Translate technical scores and risk factors into clear, actionable language for operators and management."
        ),
        "capacity": (
            "Interpret and explain a pre-computed infrastructure capacity assessment. "
            "Focus on CPU, memory, and storage headroom — what is available, what is trending toward saturation, "
            "and what action is needed. Do NOT discuss VCF upgrades, HCL certification, or maintenance windows."
        ),
        "anomaly_detection": (
            "Interpret and explain pre-computed infrastructure health and anomaly data. "
            "Focus on what is broken or degrading RIGHT NOW — not upgrades, not future plans. "
            "Surface active incidents, their probable causes, and immediate remediation steps."
        ),
        "network": (
            "Interpret and explain a pre-computed network security assessment. "
            "Focus on port exposure, dangerous protocols, host risk scores, and concrete remediation actions. "
            "Do NOT discuss VCF upgrades or compute capacity."
        ),
    }
    goal = goals.get(target, goals["vcf_readiness"])

    base = f"""\
GOAL: {goal}

You are the MCO (Multi-Cloud Operations) AI analyst for VMware Cloud Foundation environments.

CRITICAL CONSTRAINTS:
- NEVER recalculate scores or invent risk factors — all scores come from the deterministic scoring engine
- NEVER query VMware systems or external APIs — all data is provided in the prompt
- NEVER contradict the scoring engine's verdict — you explain it, not override it
- ALWAYS reference specific version numbers, host names, and domain names from the provided data
- ALWAYS structure the response with exactly the section headers requested"""

    if vcf_doc_context:
        return (
            base
            + "\n\nWhen VCF documentation context is provided, treat it as authoritative for upgrade procedures, "
            "component versions, and PowerCLI cmdlets — prefer it over general knowledge.\n\n"
            + vcf_doc_context
        )
    return base


# ── Per-scan-type prompt builders ────────────────────────────────────────────

def build_vcf_readiness_prompt(
    score: int,
    status: str,
    risk_factors: list[dict],
    recommendations: list[str],
    query: str,
    raw_data: dict,
) -> str:
    # ── Interop & HCL ────────────────────────────────────────────────────────
    interop = raw_data.get("check_broadcom_interop", {})
    components = interop.get("components", {})
    hcl_results = interop.get("hcl_results", [])
    interop_gaps = interop.get("interop_gaps", [])
    deprecation_warnings = interop.get("deprecation_warnings", [])
    target_ver = interop.get("target_version", "9.1")

    comp_lines = "\n".join(
        f"  {k.replace('_',' ').title()}: {v or 'unknown'}"
        for k, v in components.items() if isinstance(v, str)
    ) or "  Component versions not available"

    hcl_lines = "\n".join(
        f"  {r.get('platform_name','?')}: {'CERTIFIED' if r.get('certified') is True else 'NOT CERTIFIED' if r.get('certified') is False else 'UNCONFIRMED'} for ESXi {r.get('esxi_version','?')}"
        for r in hcl_results
    ) or "  No hardware data available"

    gap_lines = "\n".join(f"  BLOCKER: {g}" for g in interop_gaps) if interop_gaps else "  None detected"

    # ── SDDC Manager data ────────────────────────────────────────────────────
    sddc = raw_data.get("get_sddc_health", {})
    sddc_version = sddc.get("sddc_version", "unknown")
    domains = sddc.get("domains", [])
    sddc_hosts = sddc.get("hosts", [])
    upgrade_blockers = sddc.get("upgrade_blockers", [])
    upgrade_warnings = sddc.get("upgrade_warnings", [])
    domain_health = sddc.get("domain_health", "unknown")

    domain_lines = "\n".join(
        f"  {d.get('name','?')} [{d.get('type','?')}]: status={d.get('status','?')}, upgrade_state={d.get('upgrade_state','?')}"
        for d in domains
    ) or "  SDDC Manager not configured or unreachable"

    host_lines = "\n".join(
        f"  {h.get('fqdn','?')}: ESXi {h.get('esxi_version','?')}, {h.get('hardware_vendor','')} {h.get('hardware_model','')}"
        for h in sddc_hosts[:8]
    ) or "  No host data from SDDC Manager"

    blocker_lines = "\n".join(f"  BLOCKER: {b}" for b in upgrade_blockers) if upgrade_blockers else "  None"
    warning_lines = "\n".join(f"  {w}" for w in upgrade_warnings[:5]) if upgrade_warnings else "  None"

    # ── Risk factors ─────────────────────────────────────────────────────────
    risk_by_severity = {"critical": [], "warning": [], "info": []}
    for r in risk_factors:
        risk_by_severity.setdefault(r.get("severity", "info"), []).append(r.get("message", ""))
    risk_text = ""
    for sev in ("critical", "warning", "info"):
        items = risk_by_severity[sev]
        if items:
            risk_text += f"\n  {sev.upper()}:\n" + "\n".join(f"    - {m}" for m in items)

    rec_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(recommendations)) or "  No immediate action required."

    dep_lines = "\n".join(f"  {d}" for d in deprecation_warnings[:8]) if deprecation_warnings else "  None for this target version"

    # ── Network Discovery ────────────────────────────────────────────────────
    discovery = raw_data.get("get_discovery_assets", {})
    # ── Capacity snapshot — per-host usage + datastores ──────────────────────
    capacity_lines = ""
    esxi_entities = raw_data.get("get_esxi_metrics", {}).get("normalized", {}).get("entities", [])
    for e in esxi_entities[:8]:
        if e.get("entity_type") == "esxi_host" and e.get("cpu_usage") is not None:
            capacity_lines += f"  {e.get('name','?')}: CPU {e.get('cpu_usage')}% | RAM {e.get('ram_usage','?')}%\n"
    ds_entities = raw_data.get("get_datastore_capacity", {}).get("normalized", {}).get("entities", [])
    for e in ds_entities[:6]:
        flag = " [CRIT]" if e.get("health_state") == "red" else " [WARN]" if e.get("health_state") == "yellow" else ""
        capacity_lines += f"  Datastore {e.get('name','?')} ({e.get('ds_type','?')}): {e.get('used_pct','?')}% used, {e.get('free_gb',0):,.0f} GB free{flag}\n"
    capacity_lines = capacity_lines or "  No capacity telemetry available\n"

    discovery_lines = ""
    if discovery.get("scanned"):
        total_hosts = discovery.get("total_hosts", 0)
        risk_bk = discovery.get("risk_breakdown", {})
        dangerous = discovery.get("dangerous_port_findings", [])
        discovery_lines = (
            f"  Hosts discovered: {total_hosts} "
            f"(critical: {risk_bk.get('critical',0)}, high: {risk_bk.get('high',0)}, "
            f"medium: {risk_bk.get('medium',0)}, low: {risk_bk.get('low',0)})\n"
        )
        if dangerous:
            discovery_lines += "  Dangerous protocol exposure:\n"
            for f in dangerous[:6]:
                dns = f.get('dns_names', [])
                host = dns[0] if dns else f.get('ip', '?')
                discovery_lines += f"    - {host}: {f.get('service','?')} port {f.get('port','?')} OPEN [{f.get('severity','').upper()}]\n"
    else:
        discovery_lines = "  No network scan data available (run a discovery scan to enrich this analysis)"

    verdict = {
        "READY": f"PROCEED — infrastructure meets VCF {target_ver} requirements",
        "WARNING": "PROCEED WITH CAUTION — resolve identified issues before upgrade window",
        "NOT_READY": "DO NOT PROCEED — critical blockers must be resolved first",
    }.get(status, "UNKNOWN — insufficient data to make a determination")

    return f"""The deterministic MCO Scoring Engine has completed a VCF {target_ver} readiness analysis.

═══ SCORING SUMMARY ═══
Overall Score: {score}/100  |  Status: {status}  |  Verdict: {verdict}

═══ COMPONENT VERSIONS (from vCenter/vROps/SDDC Manager) ═══
{comp_lines}
  SDDC Manager: {sddc_version}

═══ SDDC MANAGER DOMAINS ═══
Domain health: {domain_health.upper()}
{domain_lines}

Upgrade lifecycle readiness:
{blocker_lines}
Pending upgrades:
{warning_lines}

═══ REGISTERED ESXi HOSTS (from SDDC Manager) ═══
{host_lines}

═══ CAPACITY SNAPSHOT ═══
{capacity_lines}
═══ BROADCOM INTEROPERABILITY MATRIX (target: VCF {target_ver}) ═══
Hard blockers:
{gap_lines}
Hardware HCL status:
{hcl_lines}
Deprecated hardware/drivers (plan replacements before upgrade):
{dep_lines}

═══ NETWORK DISCOVERY (from live nmap scans) ═══
{discovery_lines}
═══ RISK FACTORS (from scoring engine) ═══
{risk_text or "  None identified"}

═══ RECOMMENDATIONS (from scoring engine) ═══
{rec_text}

USER QUERY: {query}

─────────────────────────────────────────────────────────────
Please provide a structured response with EXACTLY these sections, using the headers below.
Use specific version numbers, host names, and domain names from the data above — do not be generic.
Keep each section concise. Total response under 600 words.

**Executive Summary**
2-3 sentences. State the verdict, the score, and the single most important finding. Include the go/no-go recommendation.

**Critical Findings**
Bullet list of the most important issues. Reference actual component versions and host names from the data.
If no critical issues, say "No critical blockers detected — proceed to validation checklist."

**Upgrade Path**
State current SDDC Manager/VCF version, target version, and whether the path is direct or staged. List any blockers from the SDDC lifecycle engine.
For VCF 9.1 upgrades, note the required order: VCF Operations first (PAK file), then vCenter+NSX, then ESX hosts, then NSX Edge/finalize.

**Hardware & HCL**
Table-style summary of server platforms and their HCL certification status for the target ESXi version.
If no data: "Hardware data unavailable — validate manually at compatibilitymatrix.broadcom.com"

**Action Items by Team**
- **IT Manager / CTO**: 1-2 items (business risk, timeline)
- **CISO / Security**: 1-2 items (patch compliance, exposure)
- **DevOps / Platform**: 1-2 items (specific technical steps)
- **Networking**: 1 item (NSX/DVS concerns if any)
- **Finance**: 1 item (maintenance window cost implication if relevant)
"""


def build_capacity_prompt(
    score: int,
    status: str,
    risk_factors: list[dict],
    recommendations: list[str],
    query: str,
    raw_data: dict,
) -> str:
    cluster_cap = raw_data.get("get_cluster_capacity", {})
    vrops_data = raw_data.get("get_vrops_metrics", {})
    esxi_data = raw_data.get("get_esxi_metrics", {})
    ds_data = raw_data.get("get_datastore_capacity", {})
    inventory = raw_data.get("get_vcenter_inventory", {})

    # Merge vCenter and vROps cluster entities — prefer vROps values (non-zero)
    cap_entities = cluster_cap.get("normalized", {}).get("entities", [])
    vrops_entities = vrops_data.get("normalized", {}).get("entities", [])
    clusters: dict = {}
    for e in cap_entities + vrops_entities:
        name = e.get("name", "?")
        existing = clusters.get(name)
        if not existing or (e.get("cpu_usage", 0) > 0 or e.get("ram_usage", 0) > 0):
            clusters[name] = e

    cluster_lines = ""
    for name, e in clusters.items():
        cpu = e.get("cpu_usage", 0) or 0.0
        ram = e.get("ram_usage", 0) or 0.0
        lat = e.get("storage_latency_ms", 0) or 0.0
        health = e.get("health_state", "unknown")
        cpu_flag = " [CRIT]" if cpu >= 90 else (" [WARN]" if cpu >= 75 else "")
        ram_flag = " [CRIT]" if ram >= 92 else (" [WARN]" if ram >= 80 else "")
        lat_flag = " [CRIT]" if lat >= 20 else (" [WARN]" if lat >= 10 else "")
        cluster_lines += f"  {name}: CPU {cpu:.1f}%{cpu_flag} | RAM {ram:.1f}%{ram_flag} | Latency {lat:.2f}ms{lat_flag} | health={health}\n"

    if not cluster_lines:
        cluster_lines = "  No cluster data available from vCenter or vROps"

    # Data quality note for zero-metric clusters
    raw_clusters = cluster_cap.get("raw", {}).get("clusters", [])
    zero_clusters = [c["name"] for c in raw_clusters if c.get("host_count", 1) == 0]
    data_quality = ""
    if zero_clusters:
        data_quality = f"\n  NOTE: {', '.join(zero_clusters)} returned zero metrics from vCenter (host_count=0) — using vROps values above."

    # ESXi host breakdown
    esxi_entities = esxi_data.get("normalized", {}).get("entities", [])
    host_lines = ""
    for e in esxi_entities[:8]:
        if e.get("entity_type") == "esxi_host":
            cpu = e.get("cpu_usage", "?")
            ram = e.get("ram_usage", "?")
            hw = f" | {e.get('cpu_cores')}c/{e.get('memory_gb')}GB" if e.get("cpu_cores") else ""
            host_lines += f"  {e.get('name','?')}: CPU {cpu}% | RAM {ram}%{hw} | health={e.get('health_state','?')}\n"
    host_lines = host_lines or "  No per-host metrics available"

    # Datastore capacity
    ds_entities = ds_data.get("normalized", {}).get("entities", [])
    ds_lines = ""
    for e in ds_entities[:8]:
        used = e.get("used_pct", "?")
        flag = " [CRIT]" if e.get("health_state") == "red" else " [WARN]" if e.get("health_state") == "yellow" else ""
        ds_lines += f"  {e.get('name','?')} ({e.get('ds_type','?')}): {used}% used of {e.get('capacity_gb',0):,.0f} GB, {e.get('free_gb',0):,.0f} GB free{flag}\n"
    ds_lines = ds_lines or "  No datastore data available"

    # VM count for growth-headroom estimation
    vm_count = 0
    for e in inventory.get("normalized", {}).get("entities", []):
        if e.get("entity_type") == "summary" and e.get("vm_count"):
            vm_count = e["vm_count"]
            break

    # Evidence thresholds from scoring engine
    evidence = cluster_cap.get("evidence", [])
    evidence_lines = "\n".join(
        f"  [{ev.get('source','')}] {ev.get('metric','')}: {ev.get('value','?')}  (threshold: {ev.get('threshold','')})"
        for ev in evidence[:6]
    ) or "  No raw evidence"

    risk_by_severity: dict = {"critical": [], "warning": [], "info": []}
    for r in risk_factors:
        risk_by_severity.setdefault(r.get("severity", "info"), []).append(r.get("message", ""))
    risk_text = ""
    for sev in ("critical", "warning", "info"):
        items = risk_by_severity[sev]
        if items:
            risk_text += f"\n  {sev.upper()}:\n" + "\n".join(f"    - {m}" for m in items)

    rec_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(recommendations)) or "  Capacity within normal parameters."

    status_verdict = {
        "READY": "HEALTHY — capacity headroom is sufficient",
        "WARNING": "WATCH — capacity tightening, plan ahead",
        "NOT_READY": "ACTION REQUIRED — capacity constraints detected",
    }.get(status, "UNKNOWN — insufficient data")

    return f"""The MCO Scoring Engine has completed a capacity assessment.

═══ CAPACITY SCORE ═══
Score: {score}/100  |  Status: {status}  |  Verdict: {status_verdict}

═══ CLUSTER METRICS ═══
{cluster_lines}{data_quality}

═══ HOST METRICS ═══
{host_lines}

═══ DATASTORE CAPACITY ═══
{ds_lines}

═══ WORKLOAD ═══
  VMs in inventory: {vm_count or "unknown"}

═══ SCORING EVIDENCE ═══
{evidence_lines}

═══ RISK FACTORS ═══
{risk_text or "  None identified"}

═══ RECOMMENDATIONS ═══
{rec_text}

USER QUERY: {query}

─────────────────────────────────────────────────────────────
Respond with EXACTLY these sections. Use actual cluster names, percentages, and host names from the data.
Do NOT discuss VCF upgrades, HCL certification, or maintenance windows — this is a capacity scan.
If a metric is zero or missing, say so explicitly — do not guess. Total response under 400 words.

**Capacity Summary**
2-3 sentences. State the overall capacity health, the single most constrained resource
(compute, memory, or storage — compare their utilization), and whether action is needed now or soon.

**Cluster & Host Breakdown**
Per-host CPU%/RAM% if available, otherwise cluster level. Call out the busiest and the idlest host
if the spread is large. If data is missing or zero, state that clearly.

**Storage**
Per datastore: used%, free space, and which one will fill first at current usage.

**Growth Headroom**
Using current VM count and the most constrained resource, estimate roughly how much more workload
fits (e.g. "memory at 26% across 4 hosts supports roughly 2-3× current VM count"). One honest
sentence — state it is an estimate based on averages.

**Recommended Actions**
Numbered list. Be specific — "add compute nodes to vcf01-prg-cl01" not "consider adding capacity."
If no action needed: "No immediate capacity actions required — monitor trending metrics."
"""


def build_anomaly_prompt(
    score: int,
    status: str,
    risk_factors: list[dict],
    recommendations: list[str],
    query: str,
    raw_data: dict,
) -> str:
    logs_data = raw_data.get("query_logs", {})
    vrops_data = raw_data.get("get_vrops_metrics", {})
    esxi_data = raw_data.get("get_esxi_metrics", {})

    # Deduplicate log events
    log_entities = logs_data.get("normalized", {}).get("entities", [])
    seen: set = set()
    unique_events: list = []
    for e in log_entities:
        key = (e.get("name", "") + (e.get("text", "") or "")[:60])
        if key not in seen:
            seen.add(key)
            unique_events.append(e)

    critical_events = [e for e in unique_events if e.get("health_state") == "red"]
    warning_events = [e for e in unique_events if e.get("health_state") == "yellow"]
    dup_count = len(log_entities) - len(unique_events)
    dup_note = f" ({dup_count} duplicates removed)" if dup_count > 0 else ""

    def _event_line(e: dict) -> str:
        ts = (e.get("timestamp", "") or "")[:16]
        host = e.get("hostname", "")
        text = (e.get("text") or e.get("name", "?"))[:120]
        return f"  [{ts}] {host}: {text}"

    critical_lines = "\n".join(_event_line(e) for e in critical_events[:5]) or "  None"
    warning_lines = "\n".join(_event_line(e) for e in warning_events[:5]) or "  None"

    # vROps metrics — flag anomalies
    vrops_entities = vrops_data.get("normalized", {}).get("entities", [])
    vrops_lines = ""
    for e in vrops_entities[:6]:
        cpu = e.get("cpu_usage", 0) or 0.0
        ram = e.get("ram_usage", 0) or 0.0
        lat = e.get("storage_latency_ms", 0) or 0.0
        health = e.get("health_state", "?")
        flag = ""
        if cpu >= 75 or ram >= 80 or lat >= 10:
            flag = "  ← ANOMALY"
        elif health in ("red", "yellow"):
            flag = f"  ← {health.upper()}"
        vrops_lines += f"  {e.get('name','?')}: CPU {cpu:.1f}% | RAM {ram:.1f}% | Latency {lat:.2f}ms | health={health}{flag}\n"
    vrops_lines = vrops_lines or "  No vROps metrics available"

    # ESXi host health — only unhealthy hosts
    esxi_entities = esxi_data.get("normalized", {}).get("entities", [])
    unhealthy_hosts = [e for e in esxi_entities
                       if e.get("entity_type") == "esxi_host" and e.get("health_state") in ("red", "yellow")]
    host_lines = "\n".join(
        f"  {e.get('name','?')}: health={e.get('health_state','?')}"
        for e in unhealthy_hosts
    ) or "  All monitored hosts healthy"

    risk_by_severity: dict = {"critical": [], "warning": [], "info": []}
    for r in risk_factors:
        risk_by_severity.setdefault(r.get("severity", "info"), []).append(r.get("message", ""))
    risk_text = ""
    for sev in ("critical", "warning"):
        items = risk_by_severity[sev]
        if items:
            risk_text += f"\n  {sev.upper()}:\n" + "\n".join(f"    - {m}" for m in items)

    rec_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(recommendations)) or "  No active incidents detected."

    status_verdict = {
        "READY": "HEALTHY — no active anomalies detected",
        "WARNING": "DEGRADED — anomalies detected, investigate soon",
        "NOT_READY": "INCIDENT — active issues require immediate attention",
    }.get(status, "UNKNOWN — insufficient data")

    return f"""The MCO Scoring Engine has completed an anomaly detection scan.

═══ HEALTH SCORE ═══
Score: {score}/100  |  Status: {status}  |  Verdict: {status_verdict}

═══ CRITICAL LOG EVENTS ({len(critical_events)}{dup_note}) ═══
{critical_lines}

═══ WARNING LOG EVENTS ({len(warning_events)}) ═══
{warning_lines}

═══ CURRENT METRICS (anomalies are marked with "← ANOMALY"; unmarked values are NORMAL) ═══
{vrops_lines}
═══ ESXi HOST HEALTH ═══
{host_lines}

═══ RISK FACTORS ═══
{risk_text or "  None identified"}

═══ RECOMMENDATIONS ═══
{rec_text}

USER QUERY: {query}

─────────────────────────────────────────────────────────────
Respond with EXACTLY these sections. Focus on what is broken or degrading RIGHT NOW.
Do NOT discuss VCF upgrades, HCL, or maintenance windows — this is incident triage.
CRITICAL: only items marked "← ANOMALY" or listed under RISK FACTORS are anomalies.
A CPU/RAM/latency value WITHOUT a marker is normal — never present a normal value as a spike,
threshold breach, or anomaly. If there are no marked anomalies and no risk factors, say the
environment is healthy.
If log events are duplicates of the same failure, note the pattern and count. Total response under 350 words.

**Incident Summary**
1-2 sentences. Is there an active incident? If yes: what and how severe? If no: current health status.

**Active Issues**
Bullet list. For each: what component, what the event/metric says, timestamp if available.
If the same event repeats, say "X occurrences of: <event>" rather than listing each.

**Root Cause Analysis**
For each active issue: most probable cause from the data. Be specific — "SDDC Manager NSX upgrade task failing repeatedly" not "there may be an issue."

**Immediate Actions**
Numbered list in priority order. What the on-call engineer should do RIGHT NOW.
"""


def build_network_prompt(
    score: int,
    status: str,
    risk_factors: list[dict],
    recommendations: list[str],
    query: str,
    raw_data: dict,
) -> str:
    discovery = raw_data.get("get_discovery_assets", {})
    network_metrics = raw_data.get("get_network_metrics", {})
    vcenter = raw_data.get("get_vcenter_inventory", {})

    if discovery.get("scanned"):
        total_hosts = discovery.get("total_hosts", 0)
        risk_bk = discovery.get("risk_breakdown", {})
        dangerous = discovery.get("dangerous_port_findings", [])

        discovery_summary = (
            f"  Hosts scanned: {total_hosts}\n"
            f"  Risk distribution: critical={risk_bk.get('critical',0)}, "
            f"high={risk_bk.get('high',0)}, medium={risk_bk.get('medium',0)}, "
            f"low={risk_bk.get('low',0)}"
        )
        # Group findings by port — one line per exposure type, not per host
        by_port: dict = {}
        for f in dangerous:
            by_port.setdefault((f.get("port"), f.get("service", "?")), []).append(f)
        findings_lines = ""
        for (port, svc), items in sorted(by_port.items(), key=lambda kv: (kv[0][0] or 0)):
            hosts = []
            for f in items[:5]:
                dns = f.get("dns_names", [])
                hosts.append(dns[0] if dns else f.get("ip", "?"))
            more = f" +{len(items)-5} more" if len(items) > 5 else ""
            note = ""
            if port == 514:
                note = "  ← NOTE: on VMware appliances port 514 is almost always the SYSLOG listener, not rsh — treat as verify-and-confirm, not as confirmed rsh exposure"
            findings_lines += f"  Port {port} ({svc}) open on {len(items)} host(s): {', '.join(hosts)}{more}{note}\n"
        findings_lines = findings_lines or "  No dangerous port findings detected"
    else:
        discovery_summary = "  No network discovery data available — enable nmap scanning to populate this section"
        findings_lines = "  No data"

    # Network metrics (NSX/DVS health)
    net_entities = (network_metrics or {}).get("normalized", {}).get("entities", [])
    net_lines = "\n".join(
        f"  {e.get('name','?')}: health={e.get('health_state','?')} type={e.get('entity_type','?')}"
        for e in net_entities[:8]
    ) or "  Network metrics not available in this deployment"

    # vCenter distributed switches
    vcenter_raw = (vcenter or {}).get("raw", {})
    dvs_list = vcenter_raw.get("distributed_switches", [])
    dvs_lines = "\n".join(
        f"  {d.get('name','?')}: version={d.get('version','?')} ports={d.get('num_ports','?')}"
        for d in dvs_list[:5]
    ) or "  No distributed switch data available"

    risk_by_severity: dict = {"critical": [], "warning": [], "info": []}
    for r in risk_factors:
        risk_by_severity.setdefault(r.get("severity", "info"), []).append(r.get("message", ""))
    risk_text = ""
    for sev in ("critical", "warning", "info"):
        items = risk_by_severity[sev]
        if items:
            risk_text += f"\n  {sev.upper()}:\n" + "\n".join(f"    - {m}" for m in items)

    rec_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(recommendations)) or "  No high-priority network security findings."

    status_verdict = {
        "READY": "SECURE — no high-risk network exposure detected",
        "WARNING": "EXPOSURE DETECTED — remediation recommended before maintenance window",
        "NOT_READY": "CRITICAL EXPOSURE — immediate action required",
    }.get(status, "UNKNOWN — insufficient data")

    return f"""The MCO Scoring Engine has completed a network security assessment.

═══ SECURITY SCORE ═══
Score: {score}/100  |  Status: {status}  |  Verdict: {status_verdict}

═══ NETWORK DISCOVERY SUMMARY ═══
{discovery_summary}

═══ DANGEROUS PORT FINDINGS ═══
{findings_lines}
═══ NETWORK INFRASTRUCTURE (NSX/DVS) ═══
{net_lines}

═══ DISTRIBUTED SWITCHES ═══
{dvs_lines}

═══ RISK FACTORS ═══
{risk_text or "  None identified"}

═══ RECOMMENDATIONS ═══
{rec_text}

USER QUERY: {query}

─────────────────────────────────────────────────────────────
Respond with EXACTLY these sections. Focus on network security posture.
Do NOT discuss compute capacity or VCF upgrade paths. Total response under 350 words.
The RISK FACTORS section is the authoritative classification — when a raw service name
(e.g. "rsh" on port 514) conflicts with a risk factor saying "likely syslog", trust the risk factor.
NEVER list the same port/host combination twice. Group findings: one bullet per exposure type
with the list of affected hosts — not one bullet per host.

**Security Posture**
1-2 sentences. Overall security health: hosts scanned, exposure types found, the single most
urgent item.

**Exposure Summary**
One bullet per exposure type (port/service): affected host count + names, why it matters,
confidence (confirmed dangerous vs needs verification).

**Infrastructure Health**
Status of NSX/DVS components from available data. If no data, state that clearly in one sentence.

**Remediation Actions**
Numbered list by priority. Confirmed legacy protocols (telnet/FTP) first: "Disable immediately."
Verification items (port 514 syslog-vs-rsh) next: how to verify in one command.
Exposure restrictions (RDP/VNC/NFS) last: "Restrict to management VLAN via NSX firewall rule."
"""


# ── Dispatcher ───────────────────────────────────────────────────────────────

def build_prompt(
    target: str,
    score: int,
    status: str,
    risk_factors: list[dict],
    recommendations: list[str],
    query: str,
    raw_data: dict,
) -> str:
    if target == "capacity":
        return build_capacity_prompt(score, status, risk_factors, recommendations, query, raw_data)
    if target == "anomaly_detection":
        return build_anomaly_prompt(score, status, risk_factors, recommendations, query, raw_data)
    if target == "network":
        return build_network_prompt(score, status, risk_factors, recommendations, query, raw_data)
    return build_vcf_readiness_prompt(score, status, risk_factors, recommendations, query, raw_data)
