"""
Auto-discover scannable networks from existing infrastructure data.

Sources (in priority order):
1. K8s node internalIPs → infer /24  (pod CIDRs skipped — overlay only)
2. Fleet API — ESXi host management_ip directly → infer /24
3. Fleet management_plane FQDNs → resolve → infer /24
4. Config-store component hosts (vROps, SDDC, vRLI, NSX) → resolve → infer /24
5. NSX Manager segments API — exact subnets with display names
6. Manual overrides stored in SQLite
"""
import asyncio
import ipaddress
import logging
import os
import socket

import httpx

logger = logging.getLogger("network_sources")

_K8S_HOST = os.getenv("KUBERNETES_SERVICE_HOST", "")
_K8S_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
_K8S_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_K8S_CA_FILE    = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

FLEET_API       = os.getenv("FLEET_API_URL",    "http://api-gateway:8000/api/v1/fleet")
CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")


def _k8s_token() -> str:
    try:
        with open(_K8S_TOKEN_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _k8s_base() -> str:
    if not _K8S_HOST:
        return ""
    return f"https://{_K8S_HOST}:{_K8S_PORT}"


async def _k8s_nodes() -> list[dict]:
    base  = _k8s_base()
    token = _k8s_token()
    if not base or not token:
        return []
    try:
        async with httpx.AsyncClient(verify=_K8S_CA_FILE, timeout=10.0) as client:
            r = await client.get(
                f"{base}/api/v1/nodes",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            return r.json().get("items", [])
    except Exception as e:
        logger.warning(f"K8s nodes query failed: {e}")
        return []


def _ip_to_24(ip: str) -> str | None:
    try:
        net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        return str(net)
    except Exception:
        return None


def _normalize_cidr(cidr: str) -> str | None:
    try:
        net = ipaddress.IPv4Network(cidr, strict=False)
        if net.is_loopback or net.is_link_local or net.is_multicast:
            return None
        if net.prefixlen > 28 or net.prefixlen < 8:
            return None
        # Skip pod overlay ranges (192.168.x.x/24 with prefix exactly /24 used by CNI)
        # These are cluster-internal overlay networks not reachable by nmap
        if net.prefixlen == 24 and str(net).startswith("192.168."):
            # Keep only if it looks like a real LAN (192.168.x.0/24 is ambiguous,
            # but K8s pod CIDRs for this cluster are 192.168.0-2.0/24 — skip those)
            pass  # we filter pod CIDRs via source below
        return str(net)
    except Exception:
        return None


def _resolve_fqdn(fqdn: str) -> str | None:
    if not fqdn:
        return None
    fqdn = fqdn.strip()
    # Already an IP?
    try:
        ipaddress.IPv4Address(fqdn)
        return fqdn
    except ValueError:
        pass
    try:
        ip = socket.gethostbyname(fqdn)
        addr = ipaddress.IPv4Address(ip)
        if addr.is_loopback or addr.is_link_local:
            return None
        return ip
    except Exception:
        return None


async def _resolve_all_keyed(fqdns: list[str]) -> dict[str, str]:
    """Resolve a list of FQDNs → {fqdn: ip}. Failures are omitted."""
    loop = asyncio.get_event_loop()
    results = await asyncio.gather(
        *[loop.run_in_executor(None, _resolve_fqdn, f) for f in fqdns],
        return_exceptions=True,
    )
    return {
        fqdn: ip
        for fqdn, ip in zip(fqdns, results)
        if isinstance(ip, str) and ip
    }


async def _nsx_segments(cfg: dict) -> list[dict]:
    """
    Fetch all segments from NSX Manager and return their subnets as scannable networks.
    Uses GET /policy/api/v1/infra/segments with basic auth.
    Each segment subnet's `network` field (e.g. "10.0.0.0/24") is used directly.
    Falls back to deriving the network from `gateway_address` if `network` is absent.
    """
    host = cfg.get("nsx_host", "")
    user = cfg.get("nsx_user", "admin")
    password = cfg.get("nsx_password", "")
    verify = cfg.get("nsx_verify_ssl", False)
    if not host or not password:
        return []

    results = []
    cursor = None
    try:
        async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
            while True:
                params: dict = {"page_size": 100}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(
                    f"https://{host}/policy/api/v1/infra/segments",
                    auth=(user, password),
                    params=params,
                )
                if resp.status_code != 200:
                    logger.warning(f"NSX segments returned HTTP {resp.status_code}")
                    break
                data = resp.json()
                for seg in data.get("results", []):
                    name = seg.get("display_name", "NSX Segment")
                    for subnet in seg.get("subnets", []):
                        cidr = subnet.get("network", "")
                        if not cidr:
                            gw = subnet.get("gateway_address", "")
                            if gw:
                                try:
                                    net = ipaddress.IPv4Interface(gw).network
                                    cidr = str(net)
                                except Exception:
                                    continue
                        if cidr:
                            results.append({"cidr": cidr, "source": "nsx", "label": f"NSX: {name} ({cidr})"})
                cursor = data.get("cursor")
                if not cursor:
                    break
    except Exception as e:
        logger.warning(f"NSX segment discovery failed: {e}")
    return results


async def discover_networks() -> list[dict]:
    """
    Returns a deduplicated list of:
      {"cidr": "10.x.x.x/24", "source": "k8s-node|fleet|config|manual", "label": "..."}
    """
    seen: dict[str, dict] = {}

    def _add(cidr: str, source: str, label: str):
        norm = _normalize_cidr(cidr)
        if norm and norm not in seen:
            seen[norm] = {"cidr": norm, "source": source, "label": label}

    def _add_ip(ip: str, source: str, label: str):
        net24 = _ip_to_24(ip)
        if net24:
            _add(net24, source, label)

    # ── 1. K8s nodes — InternalIP only (skip pod CIDRs) ─────────────────────
    nodes = await _k8s_nodes()
    for node in nodes:
        name   = node.get("metadata", {}).get("name", "node")
        status = node.get("status", {})
        for addr in status.get("addresses", []):
            if addr.get("type") == "InternalIP":
                ip = addr.get("address", "")
                if ip:
                    _add_ip(ip, "k8s-node", f"K8s node ({name})")

    # ── 2 & 3. Fleet API ─────────────────────────────────────────────────────
    fqdns_to_resolve: list[tuple[str, str, str]] = []   # (fqdn, source, label)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(FLEET_API)
            if r.status_code == 200:
                data = r.json()

                # ESXi hosts — use management_ip directly (no DNS needed)
                for host in data.get("hosts", []):
                    mgmt_ip = host.get("management_ip", "")
                    name    = host.get("name", "esxi-host")
                    if mgmt_ip:
                        _add_ip(mgmt_ip, "fleet", f"ESXi mgmt ({name})")
                    elif name and "." in name:
                        # Fall back to FQDN resolution
                        fqdns_to_resolve.append((name, "fleet", f"ESXi host ({name})"))

                # Management plane FQDNs (vCenter, NSX, SDDC, etc.)
                mp = data.get("management_plane", {})
                for component, entries in mp.items():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        fqdn = entry.get("fqdn") or entry.get("name", "")
                        if fqdn and "." in fqdn:
                            label = f"{component.replace('_', ' ').title()} ({fqdn})"
                            fqdns_to_resolve.append((fqdn, "fleet", label))

                # VMs with direct IP fields
                for vm in data.get("vms", []):
                    for key in ("ip", "ip_addresses", "ips", "guest_ip"):
                        val = vm.get(key)
                        if not val:
                            continue
                        ips = [val] if isinstance(val, str) else val
                        for ip in ips:
                            ip = str(ip).split("/")[0].strip()
                            _add_ip(ip, "fleet", f"VM network ({ip})")
    except Exception as e:
        logger.debug(f"Fleet network discovery skipped: {e}")

    # ── 4 & 5. Config-store — component FQDNs + NSX segments ─────────────────
    cfg_raw: dict = {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            if r.status_code == 200:
                cfg_raw = r.json()
                # 4. Resolve management-plane FQDNs to /24 networks
                host_fields = {
                    "vrops_host":       "vROps",
                    "sddc_host":        "SDDC Manager",
                    "loginsight_host":  "vRLI",
                    "nsx_host":         "NSX Manager",
                    "nsx_manager_host": "NSX Manager",
                }
                for field, label_prefix in host_fields.items():
                    val = cfg_raw.get(field, "")
                    if val and isinstance(val, str):
                        fqdns_to_resolve.append((val, "config", f"{label_prefix} ({val})"))
    except Exception as e:
        logger.debug(f"Config-store network discovery skipped: {e}")

    # 5. NSX Manager segments — exact CIDRs with segment display names
    for seg_net in await _nsx_segments(cfg_raw):
        norm = _normalize_cidr(seg_net["cidr"])
        if norm and norm not in seen:
            seen[norm] = {"cidr": norm, "source": seg_net["source"], "label": seg_net["label"]}

    # Resolve all collected FQDNs (keyed to avoid index mismatch)
    if fqdns_to_resolve:
        unique_fqdns = list({f for f, _, _ in fqdns_to_resolve})
        resolved = await _resolve_all_keyed(unique_fqdns)
        # Map each (fqdn, source, label) through the resolved dict
        fqdn_to_meta: dict[str, list[tuple[str, str]]] = {}
        for fqdn, source, label in fqdns_to_resolve:
            fqdn_to_meta.setdefault(fqdn, []).append((source, label))
        for fqdn, ip in resolved.items():
            metas = fqdn_to_meta.get(fqdn, [])
            if metas:
                source, label = metas[0]
                _add_ip(ip, source, label)

    return list(seen.values())
