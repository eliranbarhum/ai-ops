"""
Scan orchestration — runs nmap in phases and stores results via aiosqlite.
"""
import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

logger = logging.getLogger("scanner")

# ── Scan profile definitions ──────────────────────────────────────────────────
# Each profile is (phase1_ping_args, phase2_full_args, phase2_no_os_args, batch_size)

def _profile_args(profile: str) -> tuple[list, list, list, int]:
    """Return (ping_args, full_args, no_os_args, batch_size) for a given profile."""

    ping_base = [
        "nmap", "-sn",
        "-PE", "-PP",
        "-PS22,80,443,8080,8443,3389,9443",
        "-PA80,443",
        "-oX", "-",
    ]

    if profile == "fast":
        ping = ping_base + ["--min-parallelism", "128", "--min-hostgroup", "128", "-T5"]
        full = [
            "nmap", "-sV", "-T5",
            "--top-ports", "100", "--open",
            "-oX", "-",
        ]
        no_os = full
        batch = 64

    elif profile == "stealth":
        ping = ping_base + ["-T2", "--max-parallelism", "4"]
        full = [
            "nmap", "-sV", "-O", "--osscan-guess",
            "-T2",
            "--script",
            "banner,http-title,ssl-cert",
            "--top-ports", "200",
            "--open",
            "-oX", "-",
        ]
        no_os = [
            "nmap", "-sV",
            "-T2",
            "--script", "banner,http-title,ssl-cert",
            "--top-ports", "200", "--open",
            "-oX", "-",
        ]
        batch = 8

    elif profile == "deep":
        ping = ping_base + ["--min-parallelism", "32", "--min-hostgroup", "32", "-T3"]
        full = [
            "nmap", "-sV", "-O", "--osscan-guess",
            "-T3",
            "--script",
            "banner,http-title,http-server-header,ssl-cert,"
            "smb-os-discovery,snmp-sysdescr,ssh-hostkey,"
            "rdp-enum-encryption,telnet-ntlm-info,"
            "http-auth,ftp-anon,smtp-open-relay",
            "--script-args", "snmp.community=public,snmp.community=private",
            "--top-ports", "5000",
            "--open",
            "-oX", "-",
        ]
        no_os = [
            "nmap", "-sV",
            "-T3",
            "--script",
            "banner,http-title,http-server-header,ssl-cert,"
            "smb-os-discovery,snmp-sysdescr,ssh-hostkey,"
            "rdp-enum-encryption,telnet-ntlm-info",
            "--script-args", "snmp.community=public,snmp.community=private",
            "--top-ports", "5000",
            "--open",
            "-oX", "-",
        ]
        batch = 16

    else:  # standard (default)
        ping = ping_base + ["--min-parallelism", "64", "--min-hostgroup", "64"]
        full = [
            "nmap", "-sV", "-O", "--osscan-guess",
            "-T4",
            "--script",
            "banner,http-title,http-server-header,ssl-cert,"
            "smb-os-discovery,snmp-sysdescr,ssh-hostkey,"
            "rdp-enum-encryption,telnet-ntlm-info",
            "--script-args", "snmp.community=public,snmp.community=private",
            "--top-ports", "1000",
            "--open",
            "-oX", "-",
        ]
        no_os = [
            "nmap", "-sV",
            "-T4",
            "--script",
            "banner,http-title,http-server-header,ssl-cert,"
            "smb-os-discovery,snmp-sysdescr,ssh-hostkey,"
            "rdp-enum-encryption",
            "--script-args", "snmp.community=public,snmp.community=private",
            "--top-ports", "1000",
            "--open",
            "-oX", "-",
        ]
        batch = 32

    return ping, full, no_os, batch


# Default args for backward compat
_NMAP_PING, _NMAP_FULL, _NMAP_FULL_NO_OS, _DEFAULT_BATCH = _profile_args("standard")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Device classification ──────────────────────────────────────────────────────

_DEVICE_ICONS = {
    "esxi":            "☁",
    "vcenter":         "☁",
    "nsx":             "☁",
    "vmware":          "☁",
    "windows-server":  "🪟",
    "windows-desktop": "🪟",
    "windows":         "🪟",
    "linux":           "🐧",
    "macos":           "🍎",
    "bsd":             "😈",
    "network-device":  "🌐",
    "firewall":        "🔒",
    "printer":         "🖨",
    "storage":         "💾",
    "container-node":  "📦",
    "unknown":         "❓",
}

_RISKY_PORTS  = {21, 23, 25, 53, 110, 143, 389, 445, 512, 513, 514, 1433, 1521, 3306, 5432, 5900, 5901, 6379, 8080, 9200, 27017}
_CRITICAL_PORTS = {23, 21, 512, 513, 514}  # telnet, ftp, rexec, rlogin, rsh


def classify_device(
    os_name: str,
    os_family: str,
    os_cpe: str,
    ports: list[dict],
    vendor: str,
    all_scripts: dict,
) -> tuple[str, str, int]:
    """Returns (device_class, risk_level, risk_score 0-100)."""
    combined = (os_name + " " + os_family + " " + os_cpe).lower()
    vendor_l = vendor.lower()
    snmp_descr = all_scripts.get("snmp-sysdescr", "").lower()
    smb_os = all_scripts.get("smb-os-discovery", "").lower()
    open_ports = {p["port"] for p in ports if p.get("state") == "open"}

    cls = "unknown"

    # VMware products (check before generic Linux since ESXi is Linux-based)
    if any(x in combined for x in ("esxi", "vmware esxi")):
        cls = "esxi"
    elif any(x in combined for x in ("vcenter",)):
        cls = "vcenter"
    elif "nsx" in combined:
        cls = "nsx"
    elif 902 in open_ports or "vmware" in vendor_l:
        cls = "vmware"
    # Windows
    elif "windows server" in combined or "windows server" in smb_os:
        cls = "windows-server"
    elif any(x in combined for x in ("windows 10", "windows 11", "windows xp", "windows 7", "windows 8")):
        cls = "windows-desktop"
    elif "windows" in combined or "windows" in smb_os:
        cls = "windows"
    # macOS / Darwin
    elif any(x in combined for x in ("mac os x", "macos", "darwin")):
        cls = "macos"
    # Linux distros
    elif any(x in combined for x in ("ubuntu", "debian", "centos", "rhel", "red hat", "fedora",
                                      "suse", "alpine", "arch linux", "kali", "rocky", "alma")):
        cls = "linux"
    elif "linux" in combined or "unix" in combined:
        cls = "linux"
    # BSDs
    elif any(x in combined for x in ("freebsd", "openbsd", "netbsd")):
        cls = "bsd"
    # Network devices — OS name or SNMP description
    elif any(x in combined for x in ("ios ", "junos", "aruba", "fortigate", "fortios",
                                      "panos", "eos ", "nexus", "airos", "routeros")):
        cls = "network-device"
    elif any(x in snmp_descr for x in ("cisco", "juniper", "aruba", "fortinet", "palo alto",
                                        "switch", "router", "gateway", "mikrotik", "ubiquiti")):
        cls = "network-device"
    # Firewalls
    elif any(x in combined for x in ("pfsense", "opnsense", "checkpoint", "sonicwall")):
        cls = "firewall"
    # Printers
    elif 9100 in open_ports or 631 in open_ports:
        cls = "printer"
    elif any(x in combined for x in ("jetdirect", "hp laserjet", "canon", "xerox", "ricoh")):
        cls = "printer"
    # Storage
    elif any(x in combined for x in ("netapp", "ontap", "pure storage", "isilon", "qnap", "synology")):
        cls = "storage"
    elif any(x in snmp_descr for x in ("netapp", "ontap", "pure storage", "qnap", "synology")):
        cls = "storage"
    # Container / K8s nodes
    elif {10250, 6443, 2375, 2376}.intersection(open_ports):
        cls = "container-node"

    # SNMP fallback
    if cls == "unknown" and snmp_descr:
        if "linux" in snmp_descr:
            cls = "linux"
        elif "windows" in snmp_descr:
            cls = "windows"

    # Risk score
    score = 0
    score += len(open_ports.intersection(_RISKY_PORTS)) * 10
    score += len(open_ports.intersection(_CRITICAL_PORTS)) * 30
    score += min(len(open_ports), 20) * 2  # cap port count contribution

    if 23 in open_ports:   score += 40   # telnet
    if 21 in open_ports:   score += 25   # ftp
    if 5900 in open_ports: score += 20   # vnc
    if 3389 in open_ports: score += 15   # rdp exposed
    if cls == "unknown":   score += 10

    score = min(score, 100)
    level = "critical" if score >= 75 else "high" if score >= 45 else "medium" if score >= 20 else "low"

    return cls, level, score


# ── nmap XML parser ────────────────────────────────────────────────────────────

def parse_nmap_xml(xml_str: str) -> list[dict]:
    if not xml_str.strip():
        return []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        logger.warning(f"nmap XML parse error: {e}")
        return []

    hosts = []
    for host_el in root.findall("host"):
        status = host_el.find("status")
        if status is None or status.get("state") != "up":
            continue

        ip = mac = vendor = ""
        for addr in host_el.findall("address"):
            t = addr.get("addrtype", "")
            if t == "ipv4":
                ip = addr.get("addr", "")
            elif t == "mac":
                mac = addr.get("addr", "")
                vendor = addr.get("vendor", "")

        if not ip:
            continue

        dns_names: list[str] = []
        hostnames_el = host_el.find("hostnames")
        if hostnames_el is not None:
            for hn in hostnames_el.findall("hostname"):
                n = hn.get("name", "")
                if n and n not in dns_names:
                    dns_names.append(n)

        # OS detection
        os_name = os_family = os_cpe = ""
        os_accuracy = 0
        os_el = host_el.find("os")
        if os_el is not None:
            best = -1
            for osmatch in os_el.findall("osmatch"):
                acc = int(osmatch.get("accuracy", 0))
                if acc > best:
                    best = acc
                    os_accuracy = acc
                    os_name = osmatch.get("name", "")
                    for osc in osmatch.findall("osclass"):
                        os_family = osc.get("osfamily", "")
                        cpes = osc.findall("cpe")
                        if cpes and cpes[0].text:
                            os_cpe = cpes[0].text

        # Ports
        ports: list[dict] = []
        ports_el = host_el.find("ports")
        if ports_el is not None:
            for port_el in ports_el.findall("port"):
                state_el = port_el.find("state")
                if state_el is None:
                    continue
                state = state_el.get("state", "")
                if state not in ("open", "filtered"):
                    continue
                svc = port_el.find("service")
                product = (svc.get("product", "") if svc is not None else "")
                version = (svc.get("version", "") if svc is not None else "")
                port_scripts: dict[str, str] = {}
                for sc in port_el.findall("script"):
                    port_scripts[sc.get("id", "")] = sc.get("output", "")

                ports.append({
                    "port":       int(port_el.get("portid", 0)),
                    "protocol":   port_el.get("protocol", "tcp"),
                    "state":      state,
                    "service":    (svc.get("name", "") if svc is not None else ""),
                    "version":    f"{product} {version}".strip(),
                    "extra_info": (svc.get("extrainfo", "") if svc is not None else ""),
                    "tunnel":     (svc.get("tunnel", "") if svc is not None else ""),
                    "scripts":    port_scripts,
                })

        # Host-level scripts (smb-os-discovery, snmp-sysdescr, etc.)
        host_scripts: dict[str, str] = {}
        hostscript_el = host_el.find("hostscript")
        if hostscript_el is not None:
            for sc in hostscript_el.findall("script"):
                host_scripts[sc.get("id", "")] = sc.get("output", "")

        all_scripts = {**host_scripts}
        for p in ports:
            all_scripts.update(p.get("scripts", {}))

        device_class, risk_level, risk_score = classify_device(
            os_name, os_family, os_cpe, ports, vendor, all_scripts
        )

        hosts.append({
            "ip": ip, "mac": mac, "vendor": vendor, "dns_names": dns_names,
            "os_name": os_name, "os_accuracy": os_accuracy,
            "os_family": os_family, "os_cpe": os_cpe,
            "device_class": device_class, "risk_level": risk_level, "risk_score": risk_score,
            "ports": ports, "host_scripts": host_scripts,
        })

    return hosts


# ── Scan worker ────────────────────────────────────────────────────────────────

async def _run_nmap(args: list[str], targets: list[str]) -> tuple[str, int]:
    cmd = args + targets
    logger.info(f"nmap: {' '.join(cmd[:10])} ... ({len(targets)} targets)")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode not in (0, 1):
        logger.warning(f"nmap exit {proc.returncode}: {stderr.decode()[:200]}")
    return stdout.decode("utf-8", errors="replace"), proc.returncode


async def scan_network(
    scan_id: str,
    cidr: str,
    db,
    publish,          # callable(event_dict) -> None
    profile: str = "standard",
) -> None:
    """
    Full two-phase scan pipeline.
    publish() sends SSE events to all current subscribers.
    profile: "fast" | "standard" | "deep" | "stealth"
    """
    nmap_ping, nmap_full, nmap_no_os, batch_size = _profile_args(profile)

    async def emit(phase: str, pct: int, msg: str, found: int = 0, scanned: int = 0):
        try:
            await db.execute(
                "UPDATE scans SET phase=?,phase_progress=?,hosts_found=?,hosts_scanned=? WHERE id=?",
                (phase, pct, found, scanned, scan_id),
            )
            await db.commit()
        except Exception:
            pass  # Column may not exist in older schema; safe to skip
        publish({"type": "progress", "scan_id": scan_id,
                 "phase": phase, "phase_progress": pct,
                 "message": msg, "hosts_found": found, "hosts_scanned": scanned})

    try:
        await db.execute(
            "UPDATE scans SET status='running', started_at=? WHERE id=?",
            (_now(), scan_id),
        )
        await db.commit()

        # ── Phase 1: ping sweep ───────────────────────────────────────────────
        await emit("host-discovery", 5, f"Ping sweep on {cidr}… [{profile}]")
        xml1, _ = await _run_nmap(nmap_ping, [cidr])
        live = parse_nmap_xml(xml1)
        live_ips = [h["ip"] for h in live]

        await emit("host-discovery", 100, f"Found {len(live_ips)} live hosts", found=len(live_ips))
        publish({"type": "hosts_discovered", "scan_id": scan_id, "ips": live_ips})

        # Store discovered hosts immediately as placeholders (ports=[]) so the
        # UI fills within seconds — phase 2 enriches each row in place. The
        # rescan-missed endpoint already treats ports='[]' as "not yet scanned".
        for h in live:
            await _store_host(db, scan_id, cidr, h)
            publish({"type": "host_discovered", "scan_id": scan_id, "host": _full(h, pending=True)})

        if not live_ips:
            await db.execute(
                "UPDATE scans SET status='done', completed_at=? WHERE id=?", (_now(), scan_id)
            )
            await db.commit()
            publish({"type": "done", "scan_id": scan_id})
            return

        # ── Phase 2: service/OS scan in profile-defined batches ───────────────
        scanned = 0

        for i in range(0, len(live_ips), batch_size):
            batch = live_ips[i : i + batch_size]
            pct = int((i / len(live_ips)) * 100)
            await emit("port-scan", pct,
                       f"Port/OS/service scan — {scanned}/{len(live_ips)} done",
                       found=len(live_ips), scanned=scanned)

            # Try with OS detection; fall back without if it fails (NET_RAW)
            xml2, rc2 = await _run_nmap(nmap_full, batch)
            if rc2 not in (0, 1):
                xml2, _ = await _run_nmap(nmap_no_os, batch)

            batch_hosts = parse_nmap_xml(xml2)
            for h in batch_hosts:
                await _store_host(db, scan_id, cidr, h)
                # Full host shape (same as the /hosts API) — the old summary
                # payload lacked `ports`/`host_scripts` and crashed the UI cards.
                publish({"type": "host_scanned", "scan_id": scan_id, "host": _full(h)})
            scanned += len(batch_hosts)
            await emit("port-scan", int(((i + len(batch)) / len(live_ips)) * 100),
                       f"Port/OS/service scan — {scanned}/{len(live_ips)} done",
                       found=len(live_ips), scanned=scanned)

        await db.execute(
            "UPDATE scans SET status='done', completed_at=?, hosts_found=?, hosts_scanned=? WHERE id=?",
            (_now(), len(live_ips), scanned, scan_id),
        )
        await db.commit()
        publish({"type": "done", "scan_id": scan_id})

    except asyncio.CancelledError:
        await db.execute(
            "UPDATE scans SET status='cancelled', completed_at=? WHERE id=?", (_now(), scan_id)
        )
        await db.commit()
        publish({"type": "cancelled", "scan_id": scan_id})
    except Exception as exc:
        logger.exception(f"Scan {scan_id} failed")
        await db.execute(
            "UPDATE scans SET status='failed', completed_at=?, error=? WHERE id=?",
            (_now(), str(exc), scan_id),
        )
        await db.commit()
        publish({"type": "error", "scan_id": scan_id, "message": str(exc)})


async def _store_host(db, scan_id: str, cidr: str, h: dict) -> None:
    await db.execute("""
        INSERT OR REPLACE INTO hosts
          (ip, scan_id, cidr, dns_names, mac, vendor, os_name, os_accuracy,
           os_family, os_cpe, device_class, risk_level, risk_score,
           ports, host_scripts, first_seen, last_seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
          COALESCE((SELECT first_seen FROM hosts WHERE ip=? AND scan_id=?), ?),
          ?)
    """, (
        h["ip"], scan_id, cidr,
        json.dumps(h["dns_names"]), h["mac"], h["vendor"],
        h["os_name"], h["os_accuracy"], h["os_family"], h["os_cpe"],
        h["device_class"], h["risk_level"], h["risk_score"],
        json.dumps(h["ports"]), json.dumps(h["host_scripts"]),
        h["ip"], scan_id, _now(), _now(),
    ))
    await db.commit()


def _summary(h: dict) -> dict:
    open_ports = [p for p in h["ports"] if p.get("state") == "open"]
    return {
        "ip": h["ip"],
        "dns_names": h["dns_names"][:2],
        "os_name": h["os_name"],
        "os_accuracy": h["os_accuracy"],
        "device_class": h["device_class"],
        "device_icon": _DEVICE_ICONS.get(h["device_class"], "❓"),
        "risk_level": h["risk_level"],
        "risk_score": h["risk_score"],
        "vendor": h["vendor"],
        "port_count": len(open_ports),
        "top_ports": [{"port": p["port"], "service": p["service"], "version": p["version"]}
                      for p in open_ports[:4]],
    }


def _full(h: dict, pending: bool = False) -> dict:
    """Host event payload matching the /scans/{id}/hosts API row shape, so the
    UI can upsert it straight into the host grid."""
    return {
        "ip": h["ip"],
        "mac": h.get("mac", ""),
        "vendor": h.get("vendor", ""),
        "dns_names": h.get("dns_names", []),
        "os_name": h.get("os_name", ""),
        "os_accuracy": h.get("os_accuracy", 0),
        "os_family": h.get("os_family", ""),
        "os_cpe": h.get("os_cpe", ""),
        "device_class": h.get("device_class", "unknown"),
        "risk_level": h.get("risk_level", "low"),
        "risk_score": h.get("risk_score", 0),
        "ports": h.get("ports", []),
        "host_scripts": h.get("host_scripts", {}),
        "first_seen": _now(),
        "last_seen": _now(),
        "pending": pending,   # True = ping-only placeholder awaiting port scan
    }
