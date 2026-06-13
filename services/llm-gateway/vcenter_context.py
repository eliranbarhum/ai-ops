"""
Fetches and caches the live vCenter resource inventory.
Called before every /generate request so the LLM has real IDs
for clusters, datastores, folders, networks, hosts, and resource pools.
Cache TTL is 5 minutes; stale data is returned on fetch failure.
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger("llm-gateway.context")

_CACHE_TTL = 300  # seconds
_cache: dict = {"data": {}, "ts": 0.0}


async def _get_token(client: httpx.AsyncClient, host: str, user: str, password: str) -> str | None:
    try:
        r = await client.post(f"https://{host}/api/session", auth=(user, password))
        return r.json() if r.status_code == 201 else None
    except Exception:
        return None


# System-generated folder name prefixes that are not useful for VM placement
_SYSTEM_FOLDER_PREFIXES = (
    "ClonePrep", "svc-", "vSpherePods", "Namespace", "kubernetes-cluster",
    "admin-oneclick", "sc", "tm-",
)


def _pick_vm_folders(folders: list) -> list:
    user_folders = [
        f for f in folders
        if f.get("type") == "VIRTUAL_MACHINE"
        and not any(f.get("name", "").startswith(p) for p in _SYSTEM_FOLDER_PREFIXES)
    ]
    # Fallback: if filtering removed everything, return all VM folders
    return user_folders[:15] if user_folders else [f for f in folders if f.get("type") == "VIRTUAL_MACHINE"][:10]


def _top_datastores(datastores: list, n: int = 10) -> list:
    return sorted(datastores, key=lambda d: d.get("free_space_MB", 0), reverse=True)[:n]


async def refresh_vcenter_context(cfg: dict) -> dict:
    host = cfg.get("vcenter_host", "")
    user = cfg.get("vcenter_user", "administrator@vsphere.local")
    password = cfg.get("vcenter_password", "")
    verify = cfg.get("vcenter_verify_ssl", False)

    if not host or not password:
        return {}

    async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
        token = await _get_token(client, host, user, password)
        if not token:
            logger.warning("vCenter context: could not acquire session token")
            return {}

        headers = {"vmware-api-session-id": token}
        base = f"https://{host}/api"

        endpoints = {
            "clusters":      f"{base}/vcenter/cluster",
            "datastores":    f"{base}/vcenter/datastore",
            "hosts":         f"{base}/vcenter/host",
            "folders":       f"{base}/vcenter/folder",
            "networks":      f"{base}/vcenter/network",
            "resource_pools": f"{base}/vcenter/resource-pool",
            "datacenters":   f"{base}/vcenter/datacenter",
        }

        responses = await asyncio.gather(
            *[client.get(url, headers=headers) for url in endpoints.values()],
            return_exceptions=True,
        )

        data: dict = {}
        for key, resp in zip(endpoints.keys(), responses):
            if isinstance(resp, Exception):
                logger.warning(f"vCenter context: {key} fetch failed: {resp}")
                continue
            if resp.status_code == 200:
                data[key] = resp.json()
            else:
                logger.warning(f"vCenter context: {key} returned {resp.status_code}")

    # Post-process: keep only what's useful for LLM prompts
    if "folders" in data:
        data["folders"] = _pick_vm_folders(data["folders"])
    if "datastores" in data:
        data["datastores"] = _top_datastores(data["datastores"])

    counts = {k: len(v) for k, v in data.items()}
    logger.info(f"vCenter context refreshed: {counts}")
    return data


async def get_vcenter_context(cfg: dict) -> dict:
    now = time.monotonic()
    if _cache["data"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]

    data = await refresh_vcenter_context(cfg)
    if data:
        _cache["data"] = data
        _cache["ts"] = now
    return _cache["data"]


def format_context_for_prompt(ctx: dict) -> str:
    if not ctx:
        return ""

    clusters   = ctx.get("clusters", [])
    datastores = ctx.get("datastores", [])
    folders    = ctx.get("folders", [])
    hosts      = ctx.get("hosts", [])
    networks   = ctx.get("networks", [])

    lines = ["=== LIVE vCenter INVENTORY ==="]
    lines.append("Copy the quoted string values exactly as shown — they are real IDs, not names.")
    lines.append("")

    # --- VM placement defaults (most important for create/deploy) ---
    if clusters or datastores or folders:
        default_cluster   = clusters[0]["cluster"] if clusters else "MISSING"
        default_datastore = datastores[0]["datastore"] if datastores else "MISSING"
        default_folder    = folders[0]["folder"] if folders else "MISSING"
        default_cl_name   = clusters[0]["name"] if clusters else "?"
        default_ds_name   = datastores[0]["name"] if datastores else "?"
        default_fo_name   = folders[0]["name"] if folders else "?"

        lines.append("DEFAULT placement IDs for VM creation (put these exact strings in the JSON body):")
        lines.append(f'  cluster   = "{default_cluster}"   ({default_cl_name})')
        lines.append(f'  datastore = "{default_datastore}"   ({default_ds_name})')
        lines.append(f'  folder    = "{default_folder}"   ({default_fo_name})')
        lines.append("")

    # --- All datastores for reference ---
    if datastores:
        lines.append("All datastores (use the quoted ID string, not the name):")
        for d in datastores:
            free_gb = d.get("free_space_MB", 0) // 1024
            cap_gb  = d.get("capacity_MB", 0) // 1024
            lines.append(f'  "{d["datastore"]}"  ({d["name"]}, {d.get("type","")}, {free_gb}GB free of {cap_gb}GB)')
        lines.append("")

    # --- All folders for reference ---
    if folders:
        lines.append("All VM folders (use the quoted ID string, not the name):")
        for f in folders:
            lines.append(f'  "{f["folder"]}"  ({f["name"]})')
        lines.append("")

    # --- Networks ---
    user_nets = [n for n in networks if len(n.get("name", "")) < 50 and "_" not in n.get("name", "")[-20:]]
    show_nets = (user_nets or networks)[:8]
    if show_nets:
        lines.append("Networks (use the quoted ID string for NIC backing):")
        for n in show_nets:
            lines.append(f'  "{n["network"]}"  ({n["name"]}, {n.get("type","")})')
        lines.append("")

    # --- Hosts ---
    if hosts:
        lines.append("ESXi hosts:")
        for h in hosts[:6]:
            lines.append(f'  "{h["host"]}"  ({h["name"]}, {h.get("connection_state","")})')
        lines.append("")

    # --- Unit conversion reminder ---
    lines.append("MANDATORY unit conversions for VM creation:")
    lines.append("  RAM:  multiply GB × 1024 to get MiB   → 8 GB = 8192,  16 GB = 16384,  32 GB = 32768")
    lines.append("  Disk: multiply GB × 1073741824 to get bytes → 50 GB = 53687091200,  100 GB = 107374182400,  200 GB = 214748364800")
    lines.append("")
    lines.append("RULES: If the user names a specific resource (e.g. 'put in the prod folder'), find it by name above.")
    lines.append("If it is NOT listed, generate a GET discovery call first. Never invent IDs.")

    return "\n".join(lines)
