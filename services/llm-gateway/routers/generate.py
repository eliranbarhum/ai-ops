import json
import logging
import re
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from providers import _get_cfg, _call_anthropic, _call_openai, _call_gemini, _call_ollama, get_env_context
from rag import retriever, vcf_docs, sddc_api
from vcenter_context import get_vcenter_context, format_context_for_prompt
from intent import (
    is_vm_create_request, build_vm_create_spec, INTENT_EXTRACT_SYSTEM,
    needs_powercli, build_powercli_spec, build_powercli_context_prompt,
    POWERCLI_GENERATE_SYSTEM, fix_ps_cmdlet_names,
)

logger = logging.getLogger("llm-gateway")
router = APIRouter()


def _repair_json(text: str) -> str:
    stack = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == '}' and stack and stack[-1] == '{':
            stack.pop()
        elif ch == ']' and stack and stack[-1] == '[':
            stack.pop()
    for opener in reversed(stack):
        text += '}' if opener == '{' else ']'
    return text


_GENERATE_SYSTEM = """\
You are a senior VMware / Broadcom engineer with deep hands-on expertise in VCF 9.x, vCenter 8.x, SDDC Manager, NSX, and vSAN.
Translate the user's request into a precise, executable API call using ONLY the endpoints provided in the RETRIEVED ENDPOINTS section below.
If no retrieved endpoint matches, pick the closest one shown — NEVER invent a path that is not in the retrieved list or the examples below.

OUTPUT FORMAT — return ONLY this JSON, nothing else, no markdown, no explanation:
{"target":"vcenter","method":"GET","path":"/vcenter/vm","description":"...","body":null,"query_params":{}}

═══ TARGET ROUTING ═══
"target" selects which system receives the call:
  "vcenter"      → vCenter REST API — paths like /vcenter/..., /appliance/..., /rest/vcenter/vm (VM create only)
  "sddc_manager" → SDDC Manager API — paths like /v1/domains, /v1/upgrades, /v1/hosts, /v1/clusters, /v1/bundles
  "vrops"        → vRealize Operations — paths like /resources, /api/alertdefinitions

SDDC Manager handles: domains, upgrades, prechecks, bundle/depot, hosts (commissioning/decommissioning),
  clusters, credentials, NSX-T clusters, backup/restore, support bundles, NTP/DNS config, licenses.
vCenter handles: VMs, ESXi hosts (inventory), datastores, networks, clusters (compute), namespaces, tags, storage policies.

═══ DECISION TREE ═══
1. Is this about VCF lifecycle, upgrades, domains, bundles, SDDC inventory? → target: "sddc_manager", path: /v1/...
2. Is this about VM inventory, datastores, networks, compute clusters? → target: "vcenter", path: /vcenter/...
3. Does the user give a specific resource ID? YES → use the /{id} endpoint. NO → use the LIST endpoint.
4. Does the user give a resource NAME? → LIST endpoint + names: ["the-name"] in query_params.

═══ RULES ═══
- method: GET, POST, or PUT only. NEVER DELETE or PATCH.
- NEVER put placeholder IDs like {id}, {vm}, <datastore-id> in URL paths — use the LIST endpoint instead.
- ONLY use query_params that are explicitly listed for the endpoint in RETRIEVED ENDPOINTS.
  If none are listed, use query_params: {}. NEVER invent query params (e.g. cpu_usage, memory_usage do NOT exist on /vcenter/vm).
- NEVER use filter.* prefixed params — they do not exist on VCF 9.x.
- VM creation MUST use path "/rest/vcenter/vm" — this /rest/ prefix applies ONLY to VM creation, no other endpoint.
- ALL other vCenter endpoints use /vcenter/... or /appliance/... directly, never /rest/ or /api/.
- If RETRIEVED ENDPOINTS are provided, the path in your answer MUST exactly match one of them.

═══ VALID QUERY PARAMS BY ENDPOINT ═══
/vcenter/vm          → power_states (e.g. ["POWERED_ON"]), names (e.g. ["web-01"])  — no other params exist
/vcenter/host        → connection_states (e.g. ["CONNECTED"]), names (e.g. ["host.fqdn"])
/vcenter/datastore   → types (e.g. ["VMFS"]), names (e.g. ["ds-name"])
/vcenter/network     → types (e.g. ["DISTRIBUTED_PORTGROUP"])
/vcenter/cluster     → names (e.g. ["cluster-name"])

═══ FEW-SHOT EXAMPLES ═══

Q: List all virtual machines with power state
A: {"target":"vcenter","method":"GET","path":"/vcenter/vm","description":"List all VMs with power state","body":null,"query_params":{}}

Q: Get powered-on VMs
A: {"target":"vcenter","method":"GET","path":"/vcenter/vm","description":"List powered-on VMs","body":null,"query_params":{"power_states":["POWERED_ON"]}}

Q: List all ESXi hosts
A: {"target":"vcenter","method":"GET","path":"/vcenter/host","description":"List all ESXi hosts","body":null,"query_params":{}}

Q: List connected ESXi hosts
A: {"target":"vcenter","method":"GET","path":"/vcenter/host","description":"List connected ESXi hosts","body":null,"query_params":{"connection_states":["CONNECTED"]}}

Q: Get vCenter version and build
A: {"target":"vcenter","method":"GET","path":"/appliance/system/version","description":"Get vCenter version and build number","body":null,"query_params":{}}

Q: Get datastore capacity and free space
A: {"target":"vcenter","method":"GET","path":"/vcenter/datastore","description":"List all datastores with capacity and free space","body":null,"query_params":{}}

Q: List all clusters
A: {"target":"vcenter","method":"GET","path":"/vcenter/cluster","description":"List all clusters","body":null,"query_params":{}}

Q: List all networks and port groups
A: {"target":"vcenter","method":"GET","path":"/vcenter/network","description":"List all networks and port groups","body":null,"query_params":{}}

Q: List all supervisor namespaces
A: {"target":"vcenter","method":"GET","path":"/vcenter/namespaces/instances","description":"List all vSphere supervisor namespaces","body":null,"query_params":{}}

Q: List storage policies
A: {"target":"vcenter","method":"GET","path":"/vcenter/storage/policies","description":"List vCenter storage policies","body":null,"query_params":{}}

Q: Get vCenter appliance health
A: {"target":"vcenter","method":"GET","path":"/appliance/health/system","description":"Get vCenter appliance health status","body":null,"query_params":{}}

Q: List all VCF domains
A: {"target":"sddc_manager","method":"GET","path":"/v1/domains","description":"List all VCF workload domains","body":null,"query_params":{}}

Q: Get SDDC Manager version
A: {"target":"sddc_manager","method":"GET","path":"/v1/system","description":"Get SDDC Manager system info and version","body":null,"query_params":{}}

Q: List all hosts managed by SDDC Manager
A: {"target":"sddc_manager","method":"GET","path":"/v1/hosts","description":"List all hosts in SDDC Manager inventory","body":null,"query_params":{}}

Q: Get upgrade precheck status for a domain
A: {"target":"sddc_manager","method":"GET","path":"/v1/upgrades","description":"List all upgrades and their precheck status","body":null,"query_params":{}}

Q: List available upgrade bundles
A: {"target":"sddc_manager","method":"GET","path":"/v1/bundles","description":"List available upgrade bundles in SDDC Manager","body":null,"query_params":{}}

Q: List SDDC Manager clusters
A: {"target":"sddc_manager","method":"GET","path":"/v1/clusters","description":"List all clusters managed by SDDC Manager","body":null,"query_params":{}}

Q: List NTP configuration
A: {"target":"sddc_manager","method":"GET","path":"/v1/system/ntp-configuration","description":"Get SDDC Manager NTP configuration","body":null,"query_params":{}}

Q: Create a VM named test-vm with 4 vCPU 16GB RAM 100GB disk Photon OS
A: {"target":"vcenter","method":"POST","path":"/rest/vcenter/vm","description":"Create Photon OS VM with 4 vCPU 16 GB RAM 100 GB disk","body":{"spec":{"name":"test-vm","guest_OS":"VMWARELINUX_64","placement":{"cluster":"domain-c8","datastore":"datastore-1","folder":"group-v4"},"cpu":{"count":4,"cores_per_socket":1,"hot_add_enabled":false,"hot_remove_enabled":false},"memory":{"size_MiB":16384,"hot_add_enabled":false},"disks":[{"type":"SCSI","new_vmdk":{"capacity":107374182400,"name":"disk0"}}],"nics":[]}},"query_params":{}}

═══ UNIT CONVERSIONS ═══
RAM: GB × 1024 = MiB  (16 GB → 16384 MiB)
Disk: GB × 1073741824 = bytes  (100 GB → 107374182400 bytes, 200 GB → 214748364800 bytes)

═══ GUEST OS IDs ═══
Windows Server 2025 → WINDOWS_SERVER_2025_64 | Windows Server 2022 → WINDOWS_SERVER_2022_64
Windows Server 2019 → WINDOWS_SERVER_2019_64 | Windows Server 2016 → WINDOWS_SERVER_2016_64
Windows 11 → WINDOWS_11_64 | Windows 10 → WINDOWS_10_64
RHEL 9 → RHEL_9_64 | RHEL 8 → RHEL_8_64
Ubuntu → UBUNTU_64 | CentOS 7 → CENTOS_7_64 | Photon OS → VMWARELINUX_64
\
"""


def _build_ollama_system(ctx: dict) -> str:
    clusters   = ctx.get("clusters", [])
    datastores = ctx.get("datastores", [])
    folders    = ctx.get("folders", [])

    cluster_id   = clusters[0]["cluster"]     if clusters   else "domain-c8"
    datastore_id = datastores[0]["datastore"] if datastores else "datastore-1"
    folder_id    = folders[0]["folder"]       if folders    else "group-v4"

    return f"""\
You are a senior VMware/Broadcom engineer. Return ONLY a raw JSON object — no markdown, no explanation.

OUTPUT FORMAT:
{{"target":"vcenter","method":"GET","path":"/vcenter/vm","description":"...","body":null,"query_params":{{}}}}

TARGET ROUTING:
- "vcenter"      → vCenter REST API: VMs, hosts, datastores, networks, clusters, namespaces → paths /vcenter/... or /appliance/...
- "sddc_manager" → SDDC Manager API: domains, upgrades, bundles, VCF hosts, clusters, NTP/DNS → paths /v1/...
- "vrops"        → vROps: performance metrics, alerts → paths /resources, /api/...

RULES:
- method: GET, POST, or PUT only
- body: null for GET calls
- VM creation MUST use path "/rest/vcenter/vm" — this /rest/ prefix applies ONLY to VM creation, NO other endpoint
- ALL other vCenter paths use /vcenter/... or /appliance/... — NEVER add /rest/ or /api/ prefix
- SDDC Manager paths use /v1/... only — NEVER add /sddc-manager/ prefix
- If RETRIEVED ENDPOINTS are provided below, use the EXACT path from those — they override your training memory
- ONLY use query_params listed in RETRIEVED ENDPOINTS. If none listed, use {{}}. NEVER invent params like cpu_usage, memory_usage
- NEVER use filter.* prefixed query params — use bare names: power_states, names, types, connection_states

PLACEMENT FIELD NAMES — the JSON keys are exactly: "cluster", "datastore", "folder"
  ✗ WRONG: cluster_id, datastore_id, folder_id, clusterId, datastoreId, folderId
  ✓ RIGHT:  cluster,   datastore,   folder

REAL IDs FROM THIS vCENTER (copy these exact strings into placement):
  "cluster"   → "{cluster_id}"
  "datastore" → "{datastore_id}"
  "folder"    → "{folder_id}"

PRECOMPUTED SIZES — use these exact numbers, DO NOT calculate or derive them:
  RAM size_MiB:  4 GB→4096 | 8 GB→8192 | 16 GB→16384 | 32 GB→32768 | 64 GB→65536
  Disk capacity (bytes):
    20 GB→21474836480 | 50 GB→53687091200 | 100 GB→107374182400
    150 GB→161061273600 | 200 GB→214748364800 | 500 GB→536870912000

DISK — exact structure: {{"type":"SCSI","new_vmdk":{{"capacity":<bytes from table>,"name":"disk0"}}}}
  ✗ WRONG keys: size_GiB, capacity_GiB, size_gb, disk_size, capacity_GB
  ✓ RIGHT key:  new_vmdk.capacity (bytes only, integer, from table above)
  ✗ WRONG: "capacity": 209715200000  (that is NOT 200 GB)
  ✓ RIGHT: "capacity": 214748364800  (that IS 200 GB — copy from the table)

GUEST OS — string value, NOT an array:
  ✗ WRONG: "guest_OS": ["UBUNTU_64"]
  ✓ RIGHT:  "guest_OS": "UBUNTU_64"
  Ubuntu→UBUNTU_64 | RHEL 9→RHEL_9_64 | Windows Server 2022→WINDOWS_SERVER_2019 | Windows Server 2019→WINDOWS_SERVER_2019 | Windows Server 2016→WINDOWS_9_SERVER_64

NICS — always an empty array, never add NIC objects:
  ✗ WRONG: "nics": [{{}}]  or  "nics": [{{"vmnic":{{}}}}]
  ✓ RIGHT:  "nics": []

EXAMPLE — Create Ubuntu VM (4 vCPU, 8 GB RAM, 100 GB disk) — copy this structure exactly:
{{"target":"vcenter","method":"POST","path":"/rest/vcenter/vm","description":"Create Ubuntu VM","body":{{"spec":{{"name":"my-vm","guest_OS":"UBUNTU_64","placement":{{"cluster":"{cluster_id}","datastore":"{datastore_id}","folder":"{folder_id}"}},"cpu":{{"count":4,"cores_per_socket":1,"hot_add_enabled":false,"hot_remove_enabled":false}},"memory":{{"size_MiB":8192,"hot_add_enabled":false}},"disks":[{{"type":"SCSI","new_vmdk":{{"capacity":107374182400,"name":"disk0"}}}}],"nics":[]}}}},"query_params":{{}}}}

EXAMPLE — Create Windows Server 2022 VM (2 vCPU, 16 GB RAM, 200 GB disk):
{{"target":"vcenter","method":"POST","path":"/rest/vcenter/vm","description":"Create Windows Server 2022 VM","body":{{"spec":{{"name":"win-server","guest_OS":"WINDOWS_SERVER_2019","placement":{{"cluster":"{cluster_id}","datastore":"{datastore_id}","folder":"{folder_id}"}},"cpu":{{"count":2,"cores_per_socket":1,"hot_add_enabled":false,"hot_remove_enabled":false}},"memory":{{"size_MiB":16384,"hot_add_enabled":false}},"disks":[{{"type":"SCSI","new_vmdk":{{"capacity":214748364800,"name":"disk0"}}}}],"nics":[]}}}},"query_params":{{}}}}

EXAMPLE — List all VMs:
{{"target":"vcenter","method":"GET","path":"/vcenter/vm","description":"List all VMs","body":null,"query_params":{{}}}}

EXAMPLE — List powered-on VMs:
{{"target":"vcenter","method":"GET","path":"/vcenter/vm","description":"List powered-on VMs","body":null,"query_params":{{"power_states":["POWERED_ON"]}}}}

EXAMPLE — List ESXi hosts:
{{"target":"vcenter","method":"GET","path":"/vcenter/host","description":"List all ESXi hosts","body":null,"query_params":{{}}}}

EXAMPLE — Get datastore capacity:
{{"target":"vcenter","method":"GET","path":"/vcenter/datastore","description":"List datastores with capacity","body":null,"query_params":{{}}}}

EXAMPLE — Get vCenter version:
{{"target":"vcenter","method":"GET","path":"/appliance/system/version","description":"Get vCenter version","body":null,"query_params":{{}}}}
"""


def _build_env_section(env_ctx: dict) -> str:
    lines = ["═══ LIVE ENVIRONMENT CONTEXT (auto-synced) ═══"]
    sddc = env_ctx.get("sddc_manager", {})
    k8s = env_ctx.get("kubernetes", {})
    ollama = env_ctx.get("ollama", {})

    if sddc.get("vcf_version"):
        lines.append(f"VCF Version: {sddc['vcf_version']}")
    if sddc.get("domains"):
        lines.append("VCF Domains: " + ", ".join(d['name'] for d in sddc['domains']))
    if k8s.get("storage_classes"):
        lines.append("K8s Storage Classes (VKS): " + ", ".join(sc['name'] for sc in k8s['storage_classes']))
    if k8s.get("namespaces"):
        lines.append("K8s Namespaces: " + ", ".join(k8s['namespaces'][:10]))
    if ollama.get("available_models"):
        lines.append("Ollama Models: " + ", ".join(ollama['available_models']))
    if env_ctx.get("updated_at"):
        lines.append(f"Context updated: {env_ctx['updated_at']}")
    return "\n".join(lines) if len(lines) > 1 else ""


class GenerateApiRequest(BaseModel):
    description: str


@router.post("/generate")
async def generate_api_call(request: GenerateApiRequest):
    cfg = await _get_cfg()
    provider = cfg.get("llm_provider", "anthropic")

    ctx = await get_vcenter_context(cfg)
    env_ctx = await get_env_context()
    if env_ctx and not ctx.get("clusters") and env_ctx.get("vcenter", {}).get("clusters"):
        vc = env_ctx["vcenter"]
        ctx["clusters"]   = vc.get("clusters", [])
        ctx["datastores"] = vc.get("datastores", [])
        ctx["folders"]    = vc.get("folders", [])
        ctx["networks"]   = vc.get("networks", [])

    if is_vm_create_request(request.description):
        intent = await _extract_vm_intent(cfg, provider, request.description)
        spec = build_vm_create_spec(intent, ctx)
        return {"spec": spec}

    if needs_powercli(request.description):
        script = await _generate_powercli(cfg, provider, request.description, ctx)
        spec = build_powercli_spec(script, request.description)
        return {"spec": spec}

    retrieved = retriever.search(request.description, top_k=3)
    rag_section = retriever.format_for_prompt(retrieved)
    ctx_section = format_context_for_prompt(ctx)

    sddc_section = ""
    if any(kw in request.description.lower() for kw in (
        "sddc", "sddc manager", "lifecycle", "workload domain", "vcf manager",
        "cluster expand", "host commission", "host decommission",
        "upgrade", "precheck", "bundle", "depot", "support bundle",
        "ntp", "dns config", "license", "backup restore", "vcf domain",
        "commission", "decommission",
    )):
        sddc_chunks = sddc_api.search(request.description, top_k=4)
        sddc_section = sddc_api.format_for_prompt(sddc_chunks)

    reminder = "\n\n⚠ FINAL REMINDER: Your response must be ONE raw JSON object and nothing else."
    env_section = _build_env_section(env_ctx) if env_ctx else ""

    if provider == "ollama":
        system = (
            _build_ollama_system(ctx)
            + (f"\n\n{rag_section}" if rag_section else "")
            + (f"\n\n{sddc_section}" if sddc_section else "")
            + (f"\n\n{env_section}" if env_section else "")
            + reminder
        )
    else:
        system = (
            _GENERATE_SYSTEM
            + (f"\n\n{rag_section}" if rag_section else "")
            + (f"\n\n{sddc_section}" if sddc_section else "")
            + (f"\n\n{ctx_section}" if ctx_section else "")
            + (f"\n\n{env_section}" if env_section else "")
            + reminder
        )

    user = f"Generate a vCenter API call for: {request.description}"

    try:
        if provider == "anthropic":
            text = await _call_anthropic(cfg, system, user, max_tokens=2048)
        elif provider == "openai":
            text = await _call_openai(cfg, system, user, max_tokens=2048)
        elif provider == "gemini":
            text = await _call_gemini(cfg, system, user, max_tokens=2048)
        elif provider == "ollama":
            text = await _call_ollama(cfg, system, user, max_tokens=1024, temperature=0.0)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown LLM provider: {provider}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Generate LLM error ({provider}): {e}")
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")

    clean = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    clean = re.sub(r'```\s*$', '', clean.strip(), flags=re.MULTILINE).strip()
    start = clean.find('{')
    if start == -1:
        raise HTTPException(status_code=502, detail="LLM did not return valid JSON")
    try:
        spec, _ = json.JSONDecoder().raw_decode(clean, start)
    except json.JSONDecodeError:
        from json_repair import repair_json
        try:
            repaired_str = repair_json(clean[start:], return_objects=False)
            spec = json.loads(repaired_str)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"LLM returned malformed JSON: {e}")

    raw_method = spec.get("method", "GET")
    clean_method = raw_method.strip(".,-: ").upper()
    if clean_method not in ("GET", "POST", "PUT", "PATCH"):
        clean_method = "GET"
    spec["method"] = clean_method

    return {"spec": spec}


async def _extract_vm_intent(cfg: dict, provider: str, description: str) -> dict:
    reminder = "\nReturn ONLY a JSON object, no explanation."
    system = INTENT_EXTRACT_SYSTEM + reminder
    user = f"Extract VM parameters from: {description}"
    try:
        if provider == "anthropic":
            text = await _call_anthropic(cfg, system, user, max_tokens=256)
        elif provider == "openai":
            text = await _call_openai(cfg, system, user, max_tokens=256)
        elif provider == "gemini":
            text = await _call_gemini(cfg, system, user, max_tokens=256)
        else:
            text = await _call_ollama(cfg, system, user, max_tokens=256, temperature=0.0)
    except Exception as e:
        logger.warning(f"Intent extraction failed ({provider}): {e} — using defaults")
        return {"name": "new-vm", "os": "ubuntu", "cpu": 2, "ram_gb": 4, "disk_gb": 50}

    clean = re.sub(r'```[a-z]*', '', text).strip()
    start = clean.find('{')
    if start == -1:
        return {"name": "new-vm", "os": "ubuntu", "cpu": 2, "ram_gb": 4, "disk_gb": 50}
    try:
        intent, _ = json.JSONDecoder().raw_decode(clean, start)
    except Exception:
        try:
            from json_repair import repair_json
            intent = json.loads(repair_json(clean[start:]))
        except Exception:
            intent = {}

    intent.setdefault("name", "new-vm")
    intent.setdefault("os", "ubuntu")
    intent.setdefault("cpu", 2)
    intent.setdefault("ram_gb", 4)
    intent.setdefault("disk_gb", 50)
    return intent


async def _generate_powercli(cfg: dict, provider: str, description: str, ctx: dict) -> str:
    ctx_block = build_powercli_context_prompt(ctx)
    doc_chunks = vcf_docs.search(description, top_k=3)
    doc_block = vcf_docs.format_for_prompt(doc_chunks, label="VCF 9.1 POWERCLI & API REFERENCE") if doc_chunks else ""
    system = POWERCLI_GENERATE_SYSTEM + (f"\n\n{doc_block}" if doc_block else "") + f"\n\n{ctx_block}"
    user = f"Generate a PowerCLI script for: {description}"
    try:
        if provider == "anthropic":
            text = await _call_anthropic(cfg, system, user, max_tokens=1024)
        elif provider == "openai":
            text = await _call_openai(cfg, system, user, max_tokens=1024)
        elif provider == "gemini":
            text = await _call_gemini(cfg, system, user, max_tokens=1024)
        else:
            text = await _call_ollama(cfg, system, user, max_tokens=512, temperature=0.0)
    except Exception as e:
        logger.warning(f"PowerCLI generation failed ({provider}): {e} — using fallback")
        return "Get-VM | Select-Object Name, PowerState | ConvertTo-Json"

    clean = re.sub(r'```(?:powershell|ps1|pwsh)?\s*', '', text.strip(), flags=re.IGNORECASE)
    clean = re.sub(r'```\s*$', '', clean.strip(), flags=re.MULTILINE).strip()
    clean = fix_ps_cmdlet_names(clean)
    return clean or "Get-VM | Select-Object Name, PowerState | ConvertTo-Json"


_GUEST_OS_MAP = {
    "ubuntu": "UBUNTU_64", "ubuntu_64": "UBUNTU_64", "ubuntu64": "UBUNTU_64",
    "ubuntu_22": "UBUNTU_64", "ubuntu_20": "UBUNTU_64", "ubuntu_18": "UBUNTU_64",
    "rhel": "RHEL_9_64", "rhel_9": "RHEL_9_64", "rhel9": "RHEL_9_64",
    "rhel_8": "RHEL_8_64", "centos": "CENTOS_8_64", "rocky": "RHEL_9_64", "alma": "RHEL_9_64",
    "debian": "DEBIAN_12_64",
    "windows_server_2025": "WINDOWS_SERVER_2019",
    "windows_server_2022": "WINDOWS_SERVER_2019",
    "windows_server_2022_64": "WINDOWS_SERVER_2019",
    "windows2022": "WINDOWS_SERVER_2019",
    "windows_server_2019": "WINDOWS_SERVER_2019",
    "windows_server_2019_64": "WINDOWS_SERVER_2019",
    "windows2019": "WINDOWS_SERVER_2019",
    "windows_server_2016": "WINDOWS_9_SERVER_64",
    "windows2016": "WINDOWS_9_SERVER_64",
    "windows_server": "WINDOWS_SERVER_2019",
    "windows10": "WINDOWS_9_64", "windows11": "WINDOWS_9_64",
    "other": "OTHER_64", "other_linux": "OTHER_LINUX_64", "linux": "OTHER_LINUX_64",
}


def _normalize_guest_os(raw: str) -> str:
    if not isinstance(raw, str):
        return "OTHER_LINUX_64"
    clean = raw.strip()
    if clean.isupper() or all(c.isupper() or c.isdigit() or c == "_" for c in clean):
        return clean
    key = clean.lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    if key in _GUEST_OS_MAP:
        return _GUEST_OS_MAP[key]
    for k, v in _GUEST_OS_MAP.items():
        if k in key:
            return v
    return "OTHER_LINUX_64"


def _fix_vm_spec(spec: dict, ctx: dict) -> dict:
    body = spec.get("body")
    if not isinstance(body, dict):
        return spec
    vm_spec = body.get("spec")
    if not isinstance(vm_spec, dict):
        return spec

    known_clusters   = {c["cluster"]   for c in ctx.get("clusters", [])}
    known_datastores = {d["datastore"] for d in ctx.get("datastores", [])}
    known_folders    = {f["folder"]    for f in ctx.get("folders", [])}

    default_cluster   = ctx["clusters"][0]["cluster"]     if ctx.get("clusters")   else None
    default_datastore = ctx["datastores"][0]["datastore"] if ctx.get("datastores") else None
    default_folder    = ctx["folders"][0]["folder"]       if ctx.get("folders")    else None

    placement = vm_spec.get("placement", {})
    if isinstance(placement, dict):
        if known_clusters and placement.get("cluster", "") not in known_clusters:
            placement["cluster"] = default_cluster
        if known_datastores and placement.get("datastore", "") not in known_datastores:
            placement["datastore"] = default_datastore
        if known_folders and placement.get("folder", "") not in known_folders:
            placement["folder"] = default_folder
        vm_spec["placement"] = placement

    vm_spec["guest_OS"] = _normalize_guest_os(vm_spec.get("guest_OS", ""))

    if isinstance(vm_spec.get("memory"), dict):
        mib = vm_spec["memory"].get("size_MiB", 0)
        try:
            mib = int(mib)
        except (TypeError, ValueError):
            mib = 4096
        if mib > 524288:
            mib = max(1024, mib // 1024)
        if mib <= 0:
            mib = 4096
        vm_spec["memory"]["size_MiB"] = mib

    if isinstance(vm_spec.get("nics"), list):
        vm_spec["nics"] = [n for n in vm_spec["nics"] if isinstance(n, dict) and "backing" in n]

    body.pop("query_params", None)
    spec["body"] = body
    return spec


_KUBECTL_SYSTEM = """\
You are a Kubernetes CLI expert specializing in VMware vSphere Kubernetes Services (VKS).
Your sole task is to convert the user's natural language request into a valid, precise, and executable kubectl command or Kubernetes YAML manifest.
The user will supply a [Context] tag indicating which cluster they are targeting:
- supervisor: cluster-scoped; use -A or no namespace flag; valid resources include namespaces, nodes, vspherekubernetesclusters, pods in vmware-system-cpi / vmware-system-csi.
- workload: namespace-scoped; always include -n <namespace> for namespace-scoped resources.
CRITICAL: Respond ONLY with the raw executable command or YAML block. No markdown backticks, no explanations, no preamble."""

_KUBECTL_EXAMPLES_WORKLOAD = [
    ("list all pods in vcf-ai-ops namespace",
     "kubectl get pods -n vcf-ai-ops"),
    ("show pods that are in a failed or pending state",
     "kubectl get pods -n vcf-ai-ops --field-selector=status.phase!=Running,status.phase!=Succeeded"),
    ("get logs from the api-gateway deployment",
     "kubectl logs -n vcf-ai-ops deployment/api-gateway --tail=100"),
    ("get the last 50 lines of logs from a crashed pod",
     "kubectl logs -n vcf-ai-ops deployment/api-gateway --previous --tail=50"),
    ("describe all events in vcf-ai-ops sorted by time",
     "kubectl get events -n vcf-ai-ops --sort-by=.lastTimestamp"),
    ("show resource usage for all pods",
     "kubectl top pods -n vcf-ai-ops"),
    ("rollout restart the scoring-engine deployment",
     "kubectl rollout restart deployment/scoring-engine -n vcf-ai-ops"),
    ("scale api-gateway to 3 replicas",
     "kubectl scale deployment/api-gateway --replicas=3 -n vcf-ai-ops"),
    ("get all deployments and their image versions",
     "kubectl get deployments -n vcf-ai-ops -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{.spec.template.spec.containers[0].image}{\"\\n\"}{end}'"),
]

_KUBECTL_EXAMPLES_SUPERVISOR = [
    ("list all namespaces",
     "kubectl get namespaces"),
    ("show all nodes with their status",
     "kubectl get nodes -o wide"),
    ("list all VKS workload clusters",
     "kubectl get vspherekubernetesclusters -A"),
    ("show all pods across every namespace",
     "kubectl get pods -A -o wide"),
    ("check CPI system pods",
     "kubectl get pods -n vmware-system-cpi -o wide"),
    ("check CSI system pods",
     "kubectl get pods -n vmware-system-csi -o wide"),
    ("find pods that are not running or succeeded",
     "kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded"),
]


def _build_kubectl_prompt(description: str, cluster: str = "workload", namespace: str = "vcf-ai-ops") -> str:
    if cluster == "supervisor":
        ctx = "[Context: supervisor cluster — cluster-scoped, no -n flag unless targeting a specific system namespace]"
        examples = _KUBECTL_EXAMPLES_SUPERVISOR
    else:
        ctx = f"[Context: workload cluster — default namespace is {namespace}]"
        examples = [(u, a.replace("vcf-ai-ops", namespace)) for u, a in _KUBECTL_EXAMPLES_WORKLOAD]
    shots = "\n".join(f"User: {u}\nAssistant: {a}\n" for u, a in examples)
    return f"{shots}User: {ctx} {description}\nAssistant:"


class KubectlGenerateRequest(BaseModel):
    description: str
    cluster: str = "workload"
    namespace: str = "vcf-ai-ops"


@router.post("/generate/kubectl")
async def generate_kubectl(request: KubectlGenerateRequest):
    cfg = await _get_cfg()
    ollama_url = (cfg.get("agent_ollama_url") or cfg.get("vllm_url") or "http://vllm-server:11434").rstrip("/")
    model = cfg.get("agent_ollama_model") or cfg.get("vllm_model") or "qwen2.5-coder:7b"
    prompt = _build_kubectl_prompt(request.description.strip(), request.cluster, request.namespace)
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model, "prompt": prompt, "system": _KUBECTL_SYSTEM,
                    "stream": False, "keep_alive": -1, "options": {"temperature": 0.0, "num_predict": 512},
                },
            )
            resp.raise_for_status()
            command = resp.json().get("response", "").strip()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

    output_type = "yaml" if command.lstrip().startswith("apiVersion") else "command"
    return {"command": command, "type": output_type, "model": model}


class KubectlExplainRequest(BaseModel):
    command: str
    output: str


@router.post("/generate/kubectl/explain")
async def generate_kubectl_explain(request: KubectlExplainRequest):
    cfg = await _get_cfg()
    ollama_url = (cfg.get("agent_ollama_url") or cfg.get("vllm_url") or "http://vllm-server:11434").rstrip("/")
    model = cfg.get("agent_ollama_model") or cfg.get("vllm_model") or "qwen2.5-coder:7b"

    prompt = (
        f"Kubectl command run:\n{request.command}\n\n"
        f"Output:\n{request.output}\n\n"
        "Explain briefly what this command does, what the output means, "
        "and highlight anything notable or concerning."
    )
    system = "You are a Kubernetes expert. Give concise, plain-English explanations. No markdown headers."

    async def _stream():
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST", f"{ollama_url}/api/generate",
                    json={"model": model, "prompt": prompt, "system": system,
                          "stream": True, "keep_alive": -1, "options": {"temperature": 0.3}},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("response", "")
                            if token:
                                yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
                            if chunk.get("done"):
                                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        except Exception:
                            pass
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_GUEST_SCRIPT_SYSTEM = """\
You are an expert Windows and Linux system administrator writing scripts that run INSIDE a virtual machine guest OS via VMware Tools (Invoke-VMScript).
Rules:
- Output ONLY the raw script body. No markdown backticks, no explanations, no preamble.
- PowerShell scripts: pure Windows PowerShell only. No Connect-VIServer, no PowerCLI cmdlets.
- Bash scripts: POSIX-compatible shell. No VMware-specific commands.
- Output JSON where useful (ConvertTo-Json / python -c json.dumps), so output is easy to parse.
- Keep scripts concise. Do not add destructive operations unless explicitly requested."""

_GUEST_SCRIPT_EXAMPLES: dict[str, list[tuple[str, str]]] = {
    "PowerShell": [
        ("check disk space",
         "Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{N='Used_GB';E={[math]::Round($_.Used/1GB,2)}},@{N='Free_GB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json -AsArray"),
        ("list running services",
         "Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object Name,DisplayName | ConvertTo-Json -AsArray"),
        ("check CPU and memory usage",
         "[PSCustomObject]@{cpu_pct=(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average; mem_free_gb=[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,2)} | ConvertTo-Json"),
        ("list installed software",
         "Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | Where-Object {$_.DisplayName} | Select-Object DisplayName,DisplayVersion | ConvertTo-Json -AsArray"),
        ("get hostname and OS version",
         "[PSCustomObject]@{hostname=$env:COMPUTERNAME; os=(Get-CimInstance Win32_OperatingSystem).Caption; uptime=(New-TimeSpan -Start (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).ToString()} | ConvertTo-Json"),
        ("check windows event log for errors in last 24 hours",
         "Get-EventLog -LogName System -EntryType Error -Newest 50 | Select-Object TimeGenerated,Source,Message | ConvertTo-Json -AsArray"),
    ],
    "Bash": [
        ("check disk space", "df -h | awk 'NR==1 || /^\\//{print}' | column -t"),
        ("list top CPU processes", "ps aux --sort=-%cpu | head -15"),
        ("check memory usage", "free -h && echo '---' && awk '/MemTotal|MemFree|MemAvailable/{print}' /proc/meminfo"),
        ("show listening ports", "ss -tulnp"),
        ("check system logs for errors", "journalctl -p err --since '24 hours ago' --no-pager | tail -50"),
        ("get OS version and uptime",
         "echo \"{\\\"hostname\\\": \\\"$(hostname)\\\", \\\"os\\\": \\\"$(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\\\"')\\\", \\\"uptime\\\": \\\"$(uptime -p)\\\"}\""),
    ],
}


def _build_guest_script_prompt(description: str, script_type: str, os_hint: str) -> str:
    examples = _GUEST_SCRIPT_EXAMPLES.get(script_type, _GUEST_SCRIPT_EXAMPLES["PowerShell"])
    shots = "\n".join(f"User: {u}\nAssistant: {a}\n" for u, a in examples)
    ctx = f" The target VM is running {os_hint}." if os_hint else ""
    return f"{shots}User: {description}{ctx}\nAssistant:"


class GuestScriptRequest(BaseModel):
    description: str
    script_type: str = "PowerShell"
    os_hint: str = ""


@router.post("/generate/guest-script")
async def generate_guest_script(request: GuestScriptRequest):
    cfg = await _get_cfg()
    provider = cfg.get("llm_provider", "anthropic")
    prompt = _build_guest_script_prompt(request.description.strip(), request.script_type, request.os_hint.strip())

    try:
        if provider == "anthropic":
            script = await _call_anthropic(cfg, _GUEST_SCRIPT_SYSTEM, prompt, max_tokens=1024)
        elif provider == "openai":
            script = await _call_openai(cfg, _GUEST_SCRIPT_SYSTEM, prompt, max_tokens=1024)
        elif provider == "gemini":
            script = await _call_gemini(cfg, _GUEST_SCRIPT_SYSTEM, prompt, max_tokens=1024)
        else:
            ollama_url = (cfg.get("agent_ollama_url") or cfg.get("vllm_url") or "http://vllm-server:11434").rstrip("/")
            model = cfg.get("agent_ollama_model") or cfg.get("vllm_model") or "qwen2.5-coder:7b"
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{ollama_url}/api/generate",
                    json={"model": model, "prompt": prompt, "system": _GUEST_SCRIPT_SYSTEM,
                          "stream": False, "keep_alive": -1, "options": {"temperature": 0.1, "num_predict": 1024}},
                )
                resp.raise_for_status()
                script = resp.json().get("response", "").strip()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    script = re.sub(r'```(?:powershell|ps1|pwsh|bash|sh)?\s*', '', script.strip(), flags=re.IGNORECASE)
    script = re.sub(r'```\s*$', '', script.strip(), flags=re.MULTILINE).strip()
    if request.script_type == "PowerShell":
        script = fix_ps_cmdlet_names(script)

    return {"script": script, "script_type": request.script_type}
