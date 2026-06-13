"""
Vulnerability scanner — runs Nuclei against a list of discovered hosts.
Scopes: safe / standard / full.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime

logger = logging.getLogger("vuln_scanner")

# Only allow well-formed IPs, hostnames, CIDRs, and URLs — no shell-special chars
_TARGET_RE = re.compile(r"^[\w.\-:/\[\]]+$")

# ---------------------------------------------------------------------------
# Scope profiles
# ---------------------------------------------------------------------------
SCOPE_PROFILES: dict[str, dict] = {
    "safe": {
        "label": "Safe",
        "tags": ["misconfiguration", "exposure", "technology", "ssl"],
        "severity": None,           # all severities, but non-intrusive templates only
        "extra_args": ["-timeout", "5", "-rate-limit", "50"],
        "description": (
            "Misconfigurations, exposed services, TLS issues, and technology "
            "fingerprinting. No active CVE probes — safe to run against "
            "production systems at any time."
        ),
        "est_seconds_per_host": 30,
        "risk_note": "Non-intrusive. No active exploitation attempts.",
        "resources": "~100 MB RAM · <0.1 CPU core · minimal network traffic",
    },
    "standard": {
        "label": "Standard",
        "tags": ["cves", "misconfiguration", "default-login", "exposure", "ssl"],
        "severity": ["medium", "high", "critical"],
        "extra_args": ["-timeout", "10", "-rate-limit", "100"],
        "description": (
            "CVEs for common services (HTTP, SSL, DNS, SMB), "
            "misconfiguration checks, and default credential detection. "
            "Recommended for regular vulnerability assessments."
        ),
        "est_seconds_per_host": 150,
        "risk_note": "Includes active CVE probes and login tests. Use during maintenance windows.",
        "resources": "~400 MB RAM · 0.5 CPU core · moderate network traffic",
    },
    "full": {
        "label": "Full",
        "tags": [],                 # all templates
        "severity": None,
        "extra_args": ["-timeout", "15", "-rate-limit", "100", "-bulk-size", "5", "-c", "10"],
        "description": (
            "All 7000+ Nuclei templates including all CVEs, network protocols, "
            "file exposure, OPSEC checks, and more. Comprehensive coverage "
            "but slow and potentially disruptive."
        ),
        "est_seconds_per_host": 3600,
        "risk_note": "May trigger IDS/IPS alerts. Can cause load on target services. Only use in authorised test windows.",
        "resources": "~3 GB RAM · 2 CPU cores · high network traffic · requires dedicated maintenance window",
    },
}


def estimate_duration(scope: str, host_count: int) -> dict:
    """Return human-readable time estimates for the given scope and host count."""
    profile = SCOPE_PROFILES.get(scope, SCOPE_PROFILES["standard"])
    secs = profile["est_seconds_per_host"] * host_count
    if secs < 60:
        human = f"~{secs}s"
    elif secs < 3600:
        human = f"~{secs // 60}-{secs // 60 + 2} min"
    else:
        human = f"~{secs // 3600:.1f} hr"
    return {
        "scope": scope,
        "host_count": host_count,
        "estimated_seconds": secs,
        "estimated_human": human,
        "description": profile["description"],
        "risk_note": profile["risk_note"],
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
async def run_vuln_scan(
    vuln_scan_id: str,
    targets: list[str],
    scope: str,
    db,
    publish_fn,
    redis_url: str = "",
) -> None:
    profile = SCOPE_PROFILES.get(scope, SCOPE_PROFILES["standard"])
    target_file = f"/tmp/vuln-targets-{vuln_scan_id}.txt"

    clean_targets = []
    for t in targets:
        t = t.strip()
        if not t:
            continue
        if not _TARGET_RE.match(t):
            logger.warning("vuln_scan %s: invalid target rejected: %r", vuln_scan_id, t)
            continue
        clean_targets.append(t)

    await asyncio.to_thread(
        lambda: open(target_file, "w").write("\n".join(clean_targets))
    )

    cmd = [
        "nuclei",
        "-list", target_file,
        "-j",           # JSONL to stdout (one JSON object per line)
        "-silent",
        "-no-color",
        "-ot",          # omit encoded template from output (saves memory)
        "-stats-interval", "10",
    ]

    # Scope-specific tag filter
    if profile["tags"]:
        cmd += ["-tags", ",".join(profile["tags"])]

    # Severity filter
    if profile["severity"]:
        cmd += ["-severity", ",".join(profile["severity"])]

    cmd += profile["extra_args"]

    # Build human-readable command (replace temp file with the actual targets)
    display_cmd = " ".join(
        f'"{a}"' if " " in a else a
        for a in cmd
    ).replace(target_file, f"<targets:{','.join(targets)}>")

    publish_fn({
        "type": "command",
        "vuln_scan_id": vuln_scan_id,
        "command": display_cmd,
    })
    publish_fn({
        "type": "progress",
        "vuln_scan_id": vuln_scan_id,
        "message": f"Starting nuclei ({scope} scan) against {len(targets)} target(s)…",
    })

    # Persist command to DB for later viewing
    await db.execute(
        "UPDATE vuln_scans SET command=? WHERE id=?",
        (display_cmd, vuln_scan_id),
    )
    await db.commit()

    total = 0
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Nuclei writes one JSON object per line to stdout
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            try:
                finding = json.loads(line)
            except json.JSONDecodeError:
                continue

            info = finding.get("info", {})
            severity = info.get("severity", "info").lower()
            counts[severity] = counts.get(severity, 0) + 1
            total += 1

            fid = str(uuid.uuid4())[:8]
            now = datetime.utcnow().isoformat()

            await db.execute(
                """INSERT INTO vuln_findings
                   (id, vuln_scan_id, host, template_id, template_name, severity,
                    tags, matched_at, description, reference, extracted_results, found_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fid, vuln_scan_id,
                    finding.get("host", ""),
                    finding.get("template-id", finding.get("templateID", "")),
                    info.get("name", ""),
                    severity,
                    json.dumps(info.get("tags", [])),
                    finding.get("matched-at", finding.get("url", "")),
                    info.get("description", ""),
                    json.dumps(info.get("reference", [])),
                    json.dumps(finding.get("extracted-results", [])),
                    now,
                ),
            )
            await db.commit()

            publish_fn({
                "type": "finding",
                "vuln_scan_id": vuln_scan_id,
                "template_id": finding.get("template-id", ""),
                "severity": severity,
                "host": finding.get("host", ""),
                "name": info.get("name", ""),
            })

        exit_code = await proc.wait()
        if exit_code in (0, 1, 2):   # 1/2 = no findings; still a completed scan
            status = "done"
        else:
            status = "error"
            logger.warning("nuclei exited with code %d for scan %s", exit_code, vuln_scan_id)

    except FileNotFoundError:
        logger.error("nuclei binary not found — was it installed in the image?")
        await db.execute(
            "UPDATE vuln_scans SET status='error', completed_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), vuln_scan_id),
        )
        await db.commit()
        publish_fn({"type": "error", "vuln_scan_id": vuln_scan_id, "message": "nuclei binary not found"})
        return
    except Exception as e:
        logger.exception("vuln scan error: %s", e)
        status = "error"
    finally:
        try:
            os.remove(target_file)
        except OSError:
            pass

    now = datetime.utcnow().isoformat()
    await db.execute(
        """UPDATE vuln_scans
           SET status=?, completed_at=?,
               total_findings=?, critical_count=?, high_count=?,
               medium_count=?, low_count=?
           WHERE id=?""",
        (
            status, now, total,
            counts.get("critical", 0), counts.get("high", 0),
            counts.get("medium", 0), counts.get("low", 0),
            vuln_scan_id,
        ),
    )
    await db.commit()

    publish_fn({
        "type": "done",
        "vuln_scan_id": vuln_scan_id,
        "total_findings": total,
        "counts": counts,
        "status": status,
    })

    # Publish alert event when critical or high findings are found
    critical = counts.get("critical", 0)
    high = counts.get("high", 0)
    if redis_url and status == "done" and (critical + high) > 0:
        event = {
            "type": "vuln_critical",
            "vuln_scan_id": vuln_scan_id,
            "scope": scope,
            "critical_count": critical,
            "high_count": high,
            "total_findings": total,
            "summary": f"Vuln scan ({scope}) found {critical} critical, {high} high findings across {len(targets)} target(s)",
            "severity": "critical" if critical > 0 else "high",
        }
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
            await r.publish("mco:events", json.dumps(event))
            await r.aclose()
        except Exception as e:
            logger.warning("vuln_scan: failed to publish alert event: %s", e)
