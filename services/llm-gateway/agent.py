"""
MCP AI Agent — multi-step tool-calling loop for cloud LLMs (Anthropic, OpenAI, Gemini).

Each provider runs its own tool-use loop:
  1. LLM decides which tool(s) to call
  2. Tools execute against live vCenter / SDDC Manager / vROps / K8s
  3. Results fed back to LLM
  4. Repeat until LLM has enough data to answer
  5. Stream final response tokens

SSE events yielded:
  {type: "tool_call",   tool: str, params: dict}
  {type: "tool_result", tool: str, summary: str, ok: bool}
  {type: "token",       text: str}
  {type: "done"}
  {type: "error",       message: str}
"""

import json
import logging
import os
import asyncio
from typing import AsyncIterator

import httpx

# ─── Kubernetes in-cluster config ─────────────────────────────────────────────
_K8S_HOST = f"https://{os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')}:{os.getenv('KUBERNETES_SERVICE_PORT', '443')}"
_K8S_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_K8S_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_NS = os.getenv("POD_NAMESPACE", "mco")

try:
    _K8S_TOKEN: str = open(_K8S_TOKEN_PATH).read().strip()
except Exception:
    _K8S_TOKEN = ""

logger = logging.getLogger("llm-gateway.agent")

CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")

def _load_prompt(filename: str, fallback: str) -> str:
    from pathlib import Path
    p = Path(__file__).parent / "prompts" / filename
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return fallback


AGENT_SYSTEM = _load_prompt(
    "agent_system_v1.md",
    "You are a VMware VCF 9.x operations assistant. Always call tools before answering.",
)

AGENT_SYSTEM_OLLAMA = _load_prompt(
    "agent_system_ollama_v1.md",
    "You are a VMware VCF assistant. Call a tool first, then answer with exact data.",
)

# ─── Canonical tool specs ─────────────────────────────────────────────────────

TOOL_SPECS = [
    # vCenter
    {
        "name": "vcenter_list_vms",
        "description": "List all virtual machines with power state, vCPU count, and memory allocation. Call this to understand VM inventory or count.",
        "properties": {
            "power_state": {
                "type": "string",
                "enum": ["POWERED_ON", "POWERED_OFF", "SUSPENDED"],
                "description": "Optional: filter by power state",
            }
        },
    },
    {
        "name": "vcenter_list_hosts",
        "description": "List all ESXi hosts with connection state, CPU model, and total memory. Use to check host health or inventory.",
        "properties": {
            "connection_state": {
                "type": "string",
                "enum": ["CONNECTED", "DISCONNECTED", "NOT_RESPONDING"],
                "description": "Optional: filter by connection state",
            }
        },
    },
    {
        "name": "vcenter_list_clusters",
        "description": "List all compute clusters with DRS and HA configuration.",
        "properties": {},
    },
    {
        "name": "vcenter_list_datastores",
        "description": "List all datastores with type, total capacity, and free space. Use to check storage health.",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["VMFS", "NFS", "VSAN", "VVOL"],
                "description": "Optional: filter by datastore type",
            }
        },
    },
    {
        "name": "vcenter_list_networks",
        "description": "List all networks and distributed port groups.",
        "properties": {},
    },
    {
        "name": "vcenter_list_namespaces",
        "description": "List all vSphere Supervisor Namespaces with CPU, memory, and storage usage.",
        "properties": {},
    },
    {
        "name": "vcenter_get_version",
        "description": "Get the vCenter Server version and build number.",
        "properties": {},
    },
    {
        "name": "vcenter_get_health",
        "description": "Get vCenter appliance system health status.",
        "properties": {},
    },
    # SDDC Manager
    {
        "name": "sddc_list_domains",
        "description": "List all VCF workload domains with type and status. Use for VCF topology overview.",
        "properties": {},
    },
    {
        "name": "sddc_list_hosts",
        "description": "List all ESXi hosts managed by SDDC Manager with their assigned domain and status.",
        "properties": {},
    },
    {
        "name": "sddc_list_clusters",
        "description": "List all clusters managed by SDDC Manager.",
        "properties": {},
    },
    {
        "name": "sddc_list_nsxt_clusters",
        "description": "List all NSX-T clusters managed by SDDC Manager.",
        "properties": {},
    },
    {
        "name": "sddc_get_system_info",
        "description": "Get SDDC Manager version, build number, and system information.",
        "properties": {},
    },
    {
        "name": "sddc_list_upgrades",
        "description": "List current and historical upgrades with status and precheck results.",
        "properties": {},
    },
    {
        "name": "sddc_list_bundles",
        "description": "List available upgrade bundles in the SDDC Manager depot.",
        "properties": {},
    },
    {
        "name": "sddc_list_failed_tasks",
        "description": "List recent failed tasks from SDDC Manager — upgrade failures, validation errors, certificate issues, deployment problems. Call this when diagnosing operational failures or 'what went wrong' questions.",
        "properties": {},
    },
    # VCF Operations (vROps)
    {
        "name": "vrops_get_alerts",
        "description": "Get active alerts from VCF Operations (vROps/Aria Operations). Shows health issues across the environment: CRITICAL, WARNING, and INFO severity. Includes alert name, affected resource, and start time. Call this FIRST for any 'what's wrong', 'health issues', 'alerts', or 'problems' questions.",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "Maximum number of alerts to return (default: 30)",
            }
        },
    },
    # Kubernetes / VKS debug tools
    {
        "name": "kubectl_get_pods",
        "description": "List all pods in the vcf-ai-ops namespace (or another namespace) with phase, ready count, and restart count. Use to check if a service is running or crashing.",
        "properties": {
            "namespace": {
                "type": "string",
                "description": "Kubernetes namespace (default: vcf-ai-ops)",
            }
        },
    },
    {
        "name": "kubectl_get_deployments",
        "description": "List all deployments in the vcf-ai-ops namespace with desired/ready/available replica counts. Use to check rollout status or degraded services.",
        "properties": {
            "namespace": {
                "type": "string",
                "description": "Kubernetes namespace (default: vcf-ai-ops)",
            }
        },
    },
    {
        "name": "kubectl_pod_logs",
        "description": "Get the last N log lines from a specific pod. Set previous=true to get logs from the previous (crashed) container. Use after kubectl_get_pods to investigate errors.",
        "properties": {
            "pod_name": {
                "type": "string",
                "description": "Full pod name (e.g. api-gateway-7d9f8b-xk2p4)",
            },
            "namespace": {
                "type": "string",
                "description": "Kubernetes namespace (default: vcf-ai-ops)",
            },
            "tail_lines": {
                "type": "integer",
                "description": "Number of lines to return from the end of the log (default: 100)",
            },
            "previous": {
                "type": "boolean",
                "description": "If true, return logs from the previous (terminated) container instance",
            },
        },
    },
    {
        "name": "kubectl_get_events",
        "description": "Get recent Kubernetes events in the vcf-ai-ops namespace sorted newest-first. Shows warnings, errors, pod scheduling issues, OOMKills, image pull failures. Always call this when diagnosing a problem.",
        "properties": {
            "namespace": {
                "type": "string",
                "description": "Kubernetes namespace (default: vcf-ai-ops)",
            }
        },
    },
    {
        "name": "kubectl_describe_pod",
        "description": "Get detailed status of a specific pod: container states, restart reasons, readiness conditions. Use to find exactly why a pod is crashing or not ready.",
        "properties": {
            "pod_name": {
                "type": "string",
                "description": "Full pod name (e.g. api-gateway-7d9f8b-xk2p4)",
            },
            "namespace": {
                "type": "string",
                "description": "Kubernetes namespace (default: vcf-ai-ops)",
            },
        },
    },
]


# ─── Tool format converters ───────────────────────────────────────────────────

def to_anthropic_tools() -> list:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": {"type": "object", "properties": t.get("properties", {}), "required": []},
        }
        for t in TOOL_SPECS
    ]


def to_openai_tools() -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {"type": "object", "properties": t.get("properties", {}), "required": []},
            },
        }
        for t in TOOL_SPECS
    ]


def to_gemini_tools() -> list:
    """Return tools in google-generativeai dict format."""
    decls = []
    for t in TOOL_SPECS:
        params: dict = {"type": "object", "properties": {}}
        for pname, pdef in t.get("properties", {}).items():
            prop: dict = {"type": pdef.get("type", "string")}
            if "description" in pdef:
                prop["description"] = pdef["description"]
            if "enum" in pdef:
                prop["enum"] = pdef["enum"]
            params["properties"][pname] = prop
        decl: dict = {"name": t["name"], "description": t["description"]}
        if params["properties"]:
            decl["parameters"] = params
        decls.append(decl)
    return [{"function_declarations": decls}]


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

async def _vcenter_get(cfg: dict, path: str, params: dict | None = None) -> list | dict:
    host = cfg.get("vcenter_host", "")
    verify = cfg.get("vcenter_verify_ssl", False)
    async with httpx.AsyncClient(verify=verify, timeout=20.0) as c:
        sr = await c.post(
            f"https://{host}/api/session",
            auth=(cfg.get("vcenter_user", ""), cfg.get("vcenter_password", "")),
        )
        sr.raise_for_status()
        token = sr.json()
        resp = await c.get(
            f"https://{host}/api{path}",
            headers={"vmware-api-session-id": token},
            params=params or {},
        )
        resp.raise_for_status()
        return resp.json()


async def _sddc_get(cfg: dict, path: str, params: dict | None = None) -> list | dict:
    host = cfg.get("sddc_host", "")
    if not host:
        raise ValueError("SDDC Manager not configured")
    verify = cfg.get("sddc_verify_ssl", False)
    async with httpx.AsyncClient(verify=verify, timeout=20.0) as c:
        tr = await c.post(
            f"https://{host}/v1/tokens",
            json={"username": cfg.get("sddc_user", ""), "password": cfg.get("sddc_password", "")},
            headers={"Content-Type": "application/json"},
        )
        tr.raise_for_status()
        token = tr.json()["accessToken"]
        resp = await c.get(
            f"https://{host}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params or {},
        )
        resp.raise_for_status()
        return resp.json()


async def _vrops_get(cfg: dict, path: str, params: dict | None = None) -> dict:
    host = cfg.get("vrops_host", "")
    if not host:
        raise ValueError("VCF Operations not configured")
    verify = cfg.get("vrops_verify_ssl", False)
    async with httpx.AsyncClient(verify=verify, timeout=20.0) as c:
        tr = await c.post(
            f"https://{host}/suite-api/api/auth/token/acquire",
            json={"username": cfg.get("vrops_user", "admin"), "password": cfg.get("vrops_password", ""), "authSource": "LOCAL"},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        tr.raise_for_status()
        token = tr.json().get("token", "")
        resp = await c.get(
            f"https://{host}/suite-api/api{path}",
            headers={"Authorization": f"vRealizeOpsToken {token}", "Accept": "application/json"},
            params=params or {},
        )
        resp.raise_for_status()
        return resp.json()


async def _k8s_get(path: str, params: dict | None = None) -> dict:
    """Call the in-cluster Kubernetes API using the pod's service account token."""
    token = _K8S_TOKEN
    async with httpx.AsyncClient(verify=_K8S_CA_PATH, timeout=15.0) as c:
        r = await c.get(
            f"{_K8S_HOST}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


async def _k8s_get_text(path: str, params: dict | None = None) -> str:
    """Fetch plain-text response from K8s API (used for pod logs)."""
    token = _K8S_TOKEN
    async with httpx.AsyncClient(verify=_K8S_CA_PATH, timeout=30.0) as c:
        r = await c.get(
            f"{_K8S_HOST}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        r.raise_for_status()
        return r.text


async def _k8s_post(path: str, body: dict) -> dict:
    """POST to K8s API (used for creating jobs)."""
    token = _K8S_TOKEN
    async with httpx.AsyncClient(verify=_K8S_CA_PATH, timeout=15.0) as c:
        r = await c.post(
            f"{_K8S_HOST}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        return r.json()


async def _k8s_patch(path: str, body: dict) -> dict:
    """Strategic merge patch to K8s API (used for updating ConfigMaps)."""
    token = _K8S_TOKEN
    async with httpx.AsyncClient(verify=_K8S_CA_PATH, timeout=15.0) as c:
        r = await c.patch(
            f"{_K8S_HOST}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/strategic-merge-patch+json",
            },
            json=body,
        )
        r.raise_for_status()
        return r.json()


def _trim(data: list | dict, max_items: int = 50) -> tuple[list | dict, int]:
    """Trim list results; return (trimmed_data, total_count)."""
    if isinstance(data, list):
        total = len(data)
        return data[:max_items], total
    return data, 1


# ─── Tool result cache (30s TTL, keyed by tool+params hash) ──────────────────
import hashlib as _hashlib
import time as _time

_TOOL_CACHE: dict[str, tuple[float, str, bool]] = {}  # key → (expires_at, result, ok)
_TOOL_CACHE_TTL = 30.0
_CACHEABLE_TOOLS = frozenset({
    "vcenter_list_vms", "vcenter_list_hosts", "vcenter_list_clusters",
    "vcenter_list_datastores", "vcenter_list_networks", "vcenter_list_folders",
    "kubectl_get_pods", "kubectl_get_nodes", "kubectl_get_namespaces",
})


def _tool_cache_key(name: str, params: dict) -> str:
    raw = f"{name}:{sorted(params.items())}"
    return _hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Tool executor ────────────────────────────────────────────────────────────

async def execute_tool(name: str, params: dict, cfg: dict) -> tuple[str, bool]:
    """
    Execute a tool and return (result_json_string, success).
    result_json is sent back to the LLM; keeps data concise.
    """
    # Short-TTL cache for read-only tools to avoid redundant API calls within one agent turn
    if name in _CACHEABLE_TOOLS:
        ck = _tool_cache_key(name, params)
        cached = _TOOL_CACHE.get(ck)
        if cached and _time.monotonic() < cached[0]:
            return cached[1], cached[2]

    try:
        if name == "vcenter_list_vms":
            qp = {}
            if ps := params.get("power_state"):
                qp["power_states"] = [ps]
            data = await _vcenter_get(cfg, "/vcenter/vm", qp)
            trimmed, total = _trim(data)
            result = {"total": total, "showing": len(trimmed), "vms": trimmed}

        elif name == "vcenter_list_hosts":
            qp = {}
            if cs := params.get("connection_state"):
                qp["connection_states"] = [cs]
            data = await _vcenter_get(cfg, "/vcenter/host", qp)
            trimmed, total = _trim(data)
            result = {"total": total, "showing": len(trimmed), "hosts": trimmed}

        elif name == "vcenter_list_clusters":
            data = await _vcenter_get(cfg, "/vcenter/cluster")
            trimmed, total = _trim(data)
            result = {"total": total, "clusters": trimmed}

        elif name == "vcenter_list_datastores":
            qp = {}
            if t := params.get("type"):
                qp["types"] = [t]
            data = await _vcenter_get(cfg, "/vcenter/datastore", qp)
            trimmed, total = _trim(data)
            result = {"total": total, "datastores": trimmed}

        elif name == "vcenter_list_networks":
            data = await _vcenter_get(cfg, "/vcenter/network")
            trimmed, total = _trim(data)
            result = {"total": total, "networks": trimmed}

        elif name == "vcenter_list_namespaces":
            data = await _vcenter_get(cfg, "/vcenter/namespaces/instances")
            trimmed, total = _trim(data)
            result = {"total": total, "namespaces": trimmed}

        elif name == "vcenter_get_version":
            result = await _vcenter_get(cfg, "/appliance/system/version")

        elif name == "vcenter_get_health":
            result = await _vcenter_get(cfg, "/appliance/health/system")

        elif name == "sddc_list_domains":
            data = await _sddc_get(cfg, "/v1/domains")
            items = data.get("elements", data) if isinstance(data, dict) else data
            trimmed, total = _trim(items)
            result = {"total": total, "domains": trimmed}

        elif name == "sddc_list_hosts":
            data = await _sddc_get(cfg, "/v1/hosts")
            items = data.get("elements", data) if isinstance(data, dict) else data
            trimmed, total = _trim(items)
            result = {"total": total, "hosts": trimmed}

        elif name == "sddc_list_clusters":
            data = await _sddc_get(cfg, "/v1/clusters")
            items = data.get("elements", data) if isinstance(data, dict) else data
            trimmed, total = _trim(items)
            result = {"total": total, "clusters": trimmed}

        elif name == "sddc_list_nsxt_clusters":
            data = await _sddc_get(cfg, "/v1/nsxt-clusters")
            items = data.get("elements", data) if isinstance(data, dict) else data
            trimmed, total = _trim(items)
            result = {"total": total, "nsxt_clusters": trimmed}

        elif name == "sddc_get_system_info":
            result = await _sddc_get(cfg, "/v1/system")

        elif name == "sddc_list_upgrades":
            data = await _sddc_get(cfg, "/v1/upgrades")
            items = data.get("elements", data) if isinstance(data, dict) else data
            trimmed, total = _trim(items, max_items=20)
            result = {"total": total, "upgrades": trimmed}

        elif name == "sddc_list_bundles":
            data = await _sddc_get(cfg, "/v1/bundles")
            items = data.get("elements", data) if isinstance(data, dict) else data
            trimmed, total = _trim(items, max_items=20)
            result = {"total": total, "bundles": trimmed}

        elif name == "sddc_list_failed_tasks":
            data = await _sddc_get(cfg, "/v1/tasks?status=FAILED&pageSize=20")
            items = data.get("elements", []) if isinstance(data, dict) else data
            tasks = []
            for t in items[:20]:
                errors = t.get("errors") or []
                error_msg = "; ".join(e.get("message", "") for e in errors if e.get("message")) or t.get("name", "")
                tasks.append({
                    "id": t.get("id", ""),
                    "name": t.get("name", ""),
                    "type": t.get("type", ""),
                    "created": t.get("creationTimestamp", ""),
                    "error": error_msg[:250],
                })
            result = {"total": len(tasks), "failed_tasks": tasks}

        elif name == "vrops_get_alerts":
            max_results = int(params.get("max_results", 30))
            data = await _vrops_get(cfg, "/alerts", {"pageSize": max_results, "activeOnly": "true"})
            alerts = []
            for a in data.get("alerts", [])[:max_results]:
                # Resolve resource name when available
                resource_name = a.get("resourceKey", {}).get("name", "") if isinstance(a.get("resourceKey"), dict) else ""
                alerts.append({
                    "alert_id": a.get("alertId", ""),
                    "name": a.get("alertDefinitionName", ""),
                    "level": a.get("alertLevel", ""),
                    "status": a.get("status", ""),
                    "start_time_utc": a.get("startTimeUTC"),
                    "resource_name": resource_name,
                    "resource_id": a.get("resourceId", ""),
                })
            # Sort by severity: CRITICAL first
            level_order = {"CRITICAL": 0, "WARNING": 1, "IMMEDIATE": 1, "INFO": 2}
            alerts.sort(key=lambda x: level_order.get(x["level"], 3))
            by_level = {}
            for a in alerts:
                by_level[a["level"]] = by_level.get(a["level"], 0) + 1
            result = {"total": len(alerts), "by_level": by_level, "alerts": alerts}

        elif name == "kubectl_get_pods":
            ns = params.get("namespace", _K8S_NS)
            data = await _k8s_get(f"/api/v1/namespaces/{ns}/pods")
            pods = []
            for p in data.get("items", []):
                cs = p.get("status", {}).get("containerStatuses", [])
                restarts = sum(c.get("restartCount", 0) for c in cs)
                ready = sum(1 for c in cs if c.get("ready", False))
                pods.append({
                    "name": p["metadata"]["name"],
                    "phase": p["status"].get("phase", "Unknown"),
                    "ready": f"{ready}/{len(cs)}",
                    "restarts": restarts,
                    "node": p["spec"].get("nodeName", ""),
                })
            result = {"namespace": ns, "total": len(pods), "pods": pods}

        elif name == "kubectl_get_deployments":
            ns = params.get("namespace", _K8S_NS)
            data = await _k8s_get(f"/apis/apps/v1/namespaces/{ns}/deployments")
            deps = []
            for d in data.get("items", []):
                s = d.get("status", {})
                deps.append({
                    "name": d["metadata"]["name"],
                    "desired": d["spec"].get("replicas", 1),
                    "ready": s.get("readyReplicas", 0),
                    "available": s.get("availableReplicas", 0),
                    "up_to_date": s.get("updatedReplicas", 0),
                })
            result = {"namespace": ns, "total": len(deps), "deployments": deps}

        elif name == "kubectl_pod_logs":
            pod = params.get("pod_name", "")
            if not pod:
                result = {"error": "pod_name is required"}
            else:
                ns = params.get("namespace", _K8S_NS)
                tail = int(params.get("tail_lines", 100))
                previous = params.get("previous", False)
                qp = {"tailLines": tail}
                if previous:
                    qp["previous"] = "true"
                log_text = await _k8s_get_text(f"/api/v1/namespaces/{ns}/pods/{pod}/log", qp)
                result = {"pod": pod, "namespace": ns, "previous": previous, "logs": log_text}

        elif name == "kubectl_get_events":
            ns = params.get("namespace", _K8S_NS)
            data = await _k8s_get(f"/api/v1/namespaces/{ns}/events")
            events = []
            for e in sorted(data.get("items", []),
                            key=lambda x: x.get("lastTimestamp") or x.get("eventTime") or "",
                            reverse=True)[:40]:
                events.append({
                    "type": e.get("type", ""),
                    "reason": e.get("reason", ""),
                    "object": f"{e['involvedObject'].get('kind','')}/{e['involvedObject'].get('name','')}",
                    "message": e.get("message", "")[:200],
                    "count": e.get("count", 1),
                    "last_seen": e.get("lastTimestamp", e.get("eventTime", "")),
                })
            result = {"namespace": ns, "total": len(events), "events": events}

        elif name == "kubectl_describe_pod":
            pod = params.get("pod_name", "")
            if not pod:
                result = {"error": "pod_name is required"}
            else:
                ns = params.get("namespace", _K8S_NS)
                data = await _k8s_get(f"/api/v1/namespaces/{ns}/pods/{pod}")
                status = data.get("status", {})
                containers = []
                for c in data.get("spec", {}).get("containers", []):
                    cs_match = next((s for s in status.get("containerStatuses", []) if s["name"] == c["name"]), {})
                    state = cs_match.get("state", {})
                    state_str = list(state.keys())[0] if state else "unknown"
                    waiting = state.get("waiting", {})
                    containers.append({
                        "name": c["name"],
                        "image": c["image"],
                        "state": state_str,
                        "reason": waiting.get("reason", state.get("terminated", {}).get("reason", "")),
                        "restarts": cs_match.get("restartCount", 0),
                        "ready": cs_match.get("ready", False),
                    })
                conditions = [
                    {"type": c["type"], "status": c["status"], "reason": c.get("reason", ""), "message": c.get("message", "")[:150]}
                    for c in status.get("conditions", [])
                ]
                result = {
                    "name": pod,
                    "namespace": ns,
                    "phase": status.get("phase", "Unknown"),
                    "node": data["spec"].get("nodeName", ""),
                    "containers": containers,
                    "conditions": conditions,
                }

        else:
            return json.dumps({"error": f"Unknown tool: {name}"}), False

        # Truncate result to ~8000 chars to stay within context limits
        text = json.dumps(result)
        if len(text) > 8000:
            text = text[:8000] + '... (truncated)'

        # Populate cache for cacheable tools
        if name in _CACHEABLE_TOOLS:
            ck = _tool_cache_key(name, params)
            _TOOL_CACHE[ck] = (_time.monotonic() + _TOOL_CACHE_TTL, text, True)

        return text, True

    except Exception as e:
        logger.warning(f"Tool {name} failed: {e}")
        return json.dumps({"error": str(e)}), False


def _tool_summary(name: str, result_text: str, ok: bool) -> str:
    if not ok:
        try:
            return json.loads(result_text).get("error", "failed")
        except Exception:
            return "failed"
    try:
        d = json.loads(result_text)

        # Tool-specific summaries
        if name == "vrops_get_alerts":
            total = d.get("total", 0)
            if total == 0:
                return "no active alerts"
            by_level = d.get("by_level", {})
            level_order = {"CRITICAL": 0, "WARNING": 1, "IMMEDIATE": 1, "INFO": 2}
            parts = [f"{v} {k}" for k, v in sorted(by_level.items(), key=lambda x: level_order.get(x[0], 3)) if v > 0]
            return f"{total} alerts — {', '.join(parts)}" if parts else f"{total} alerts"

        if name == "sddc_list_failed_tasks":
            total = d.get("total", 0)
            return f"{total} failed task{'s' if total != 1 else ''}" if total > 0 else "no failed tasks"

        if name == "vcenter_get_health":
            return str(d) if isinstance(d, str) else "health data received"

        # Generic
        if "total" in d:
            keys = [k for k in d if k not in ("total", "showing", "namespace", "window_hours", "timestamp")]
            key = keys[0] if keys else "items"
            return f"{d['total']} {key}"
        return "OK"
    except Exception:
        return "OK"


# ─── History conversion ───────────────────────────────────────────────────────

def _to_anthropic_messages(history: list[dict], current: str) -> list[dict]:
    msgs = []
    for m in history:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": current})
    return msgs


def _to_openai_messages(history: list[dict], current: str, system: str) -> list[dict]:
    msgs = [{"role": "system", "content": system}]
    for m in history:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": current})
    return msgs


# ─── Anthropic agent loop ─────────────────────────────────────────────────────

async def _anthropic_loop(cfg: dict, messages: list[dict]) -> AsyncIterator[dict]:
    import anthropic
    key = cfg.get("agent_anthropic_api_key") or cfg.get("anthropic_api_key", "")
    model = cfg.get("agent_anthropic_model") or cfg.get("anthropic_model", "claude-sonnet-4-6")
    if not key:
        yield {"type": "error", "message": "Anthropic API key not configured for MCP AI Agent. Add it in Settings → MCP AI Agent LLM."}
        return

    client = anthropic.AsyncAnthropic(api_key=key)
    tools = to_anthropic_tools()

    while True:
        # Single streaming pass: text tokens are emitted as they arrive.
        # When stop_reason is "tool_use", text_stream yields nothing and we
        # process the tool calls after the stream closes, then loop.
        # When stop_reason is "end_turn", the final answer was already streamed.
        async with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=AGENT_SYSTEM,
            tools=tools,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield {"type": "token", "text": text}
            final = await stream.get_final_message()

        if final.stop_reason != "tool_use":
            break  # Final answer was already streamed above

        # Collect and execute tool calls, then loop for the final answer
        tool_results = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            yield {"type": "tool_call", "tool": block.name, "params": dict(block.input)}
            result_text, ok = await execute_tool(block.name, dict(block.input), cfg)
            summary = _tool_summary(block.name, result_text, ok)
            try:
                result_data = json.loads(result_text)
            except Exception:
                result_data = None
            yield {"type": "tool_result", "tool": block.name, "summary": summary, "ok": ok, "data": result_data}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })
        messages = messages + [
            {"role": "assistant", "content": final.content},
            {"role": "user", "content": tool_results},
        ]


# ─── OpenAI agent loop ────────────────────────────────────────────────────────

async def _openai_loop(cfg: dict, messages: list[dict], system: str) -> AsyncIterator[dict]:
    from openai import AsyncOpenAI
    key = cfg.get("agent_openai_api_key") or cfg.get("openai_api_key", "")
    model = cfg.get("agent_openai_model") or cfg.get("openai_model", "gpt-4o")
    if not key:
        yield {"type": "error", "message": "OpenAI API key not configured for MCP AI Agent. Add it in Settings → MCP AI Agent LLM."}
        return

    client = AsyncOpenAI(api_key=key)
    tools = to_openai_tools()
    oai_messages = messages  # already includes system

    while True:
        response = await client.chat.completions.create(
            model=model,
            tools=tools,
            tool_choice="auto",
            messages=oai_messages,
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            oai_messages = oai_messages + [msg]
            tool_results = []
            for tc in msg.tool_calls:
                params = json.loads(tc.function.arguments) if tc.function.arguments else {}
                yield {"type": "tool_call", "tool": tc.function.name, "params": params}
                result_text, ok = await execute_tool(tc.function.name, params, cfg)
                summary = _tool_summary(tc.function.name, result_text, ok)
                try:
                    result_data = json.loads(result_text)
                except Exception:
                    result_data = None
                yield {"type": "tool_result", "tool": tc.function.name, "summary": summary, "ok": ok, "data": result_data}
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })
            oai_messages = oai_messages + tool_results

        else:
            # Emit the response text directly — tool results are already in oai_messages,
            # so no second API call is needed. Chunk to give a streaming feel.
            text = msg.content or ""
            for i in range(0, len(text), 25):
                yield {"type": "token", "text": text[i:i + 25]}
            break


# ─── Gemini agent loop ────────────────────────────────────────────────────────

async def _gemini_loop(cfg: dict, history: list[dict], current: str) -> AsyncIterator[dict]:
    import google.generativeai as genai
    key = cfg.get("agent_gemini_api_key") or cfg.get("gemini_api_key", "")
    model_name = cfg.get("agent_gemini_model") or cfg.get("gemini_model", "gemini-2.0-flash")
    if not key:
        yield {"type": "error", "message": "Gemini API key not configured for MCP AI Agent. Add it in Settings → MCP AI Agent LLM."}
        return

    genai.configure(api_key=key)
    tools = to_gemini_tools()
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=AGENT_SYSTEM,
        tools=tools,
    )

    # Build Gemini history format
    gemini_history = []
    for m in history:
        role = "user" if m["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [{"text": m["content"]}]})

    def _sync_chat():
        chat = model.start_chat(history=gemini_history)
        resp = chat.send_message(current)
        results = []
        # Tool call loop (sync)
        while True:
            calls = []
            for part in resp.parts:
                if hasattr(part, "function_call") and part.function_call.name:
                    fc = part.function_call
                    calls.append((fc.name, dict(fc.args)))
            if not calls:
                break
            results.append(("calls", calls))
            # Execute all tools synchronously (will be run in thread)
            tool_responses = []
            for tname, tparams in calls:
                result_text, ok = asyncio.get_event_loop().run_until_complete(
                    execute_tool(tname, tparams, cfg)
                )
                results.append(("result", tname, tparams, result_text, ok))
                import google.generativeai.types as gtypes
                tool_responses.append(
                    gtypes.Part.from_function_response(
                        name=tname,
                        response={"result": result_text},
                    )
                )
            resp = chat.send_message(tool_responses)

        final_text = "".join(p.text for p in resp.parts if hasattr(p, "text"))
        results.append(("text", final_text))
        return results

    # Run Gemini synchronously in a thread (it doesn't have a proper async client)
    events = await asyncio.to_thread(_sync_chat)
    for event in events:
        if event[0] == "calls":
            for tname, tparams in event[1]:
                yield {"type": "tool_call", "tool": tname, "params": tparams}
        elif event[0] == "result":
            _, tname, _, result_text, ok = event
            try:
                result_data = json.loads(result_text)
            except Exception:
                result_data = None
            yield {"type": "tool_result", "tool": tname,
                   "summary": _tool_summary(tname, result_text, ok), "ok": ok, "data": result_data}
        elif event[0] == "text":
            # Yield text in chunks for a streaming feel
            text = event[1]
            chunk_size = 20
            for i in range(0, len(text), chunk_size):
                yield {"type": "token", "text": text[i:i + chunk_size]}


# ─── Ollama helpers ───────────────────────────────────────────────────────────

def _select_tools_for_ollama(message: str) -> list[dict]:
    """Return a focused subset of TOOL_SPECS based on message keywords.
    Small models reliably call tools only when the list is short and obvious."""
    msg = message.lower()
    if any(w in msg for w in ["alert", "wrong", "issue", "problem", "health", "critical", "warning", "broken"]):
        names = {"vrops_get_alerts", "sddc_list_failed_tasks", "vcenter_get_health"}
        return [t for t in TOOL_SPECS if t["name"] in names]
    if any(w in msg for w in ["pod", "crash", "restart", "log", "event", "deployment", "kubernetes", "k8s", "container"]):
        prefixes = ["kubectl_"]
    elif any(w in msg for w in ["vm", "virtual machine", "host", "esxi", "datastore", "vcenter", "network", "storage"]):
        prefixes = ["vcenter_"]
    elif any(w in msg for w in ["sddc", "domain", "upgrade", "bundle", "nsx", "vcf", "task", "fail"]):
        prefixes = ["sddc_"]
    else:
        # General: one representative tool per category
        names = {"vrops_get_alerts", "vcenter_get_health", "kubectl_get_pods", "sddc_list_domains"}
        return [t for t in TOOL_SPECS if t["name"] in names]
    return [t for t in TOOL_SPECS if any(t["name"].startswith(p) for p in prefixes)]


def _to_openai_tools_from_specs(specs: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {"type": "object", "properties": t.get("properties", {}), "required": []},
            },
        }
        for t in specs
    ]


def _extract_narrated_tool(text: str, valid_names: list[str]) -> str | None:
    """If the model wrote a tool name in text/code instead of calling it, return that name."""
    import re
    # Match tool names inside code blocks, backticks, or bare in the text
    patterns = [
        r'`{1,3}(\w+)`{1,3}',           # `tool_name` or ```tool_name```
        r'(?:call|execute|run|use)\s+[`"\']?(\w+)[`"\']?',  # "call vcenter_list_vms"
        r'^(\w+)\s*$',                    # bare word on its own line
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.MULTILINE | re.IGNORECASE):
            candidate = m.group(1)
            if candidate in valid_names:
                return candidate
    # Plain substring match as last resort
    for name in valid_names:
        if name in text:
            return name
    return None


# ─── Ollama agent loop ────────────────────────────────────────────────────────

async def _ollama_loop(cfg: dict, messages: list[dict], system: str) -> AsyncIterator[dict]:
    from openai import AsyncOpenAI
    base_url = (cfg.get("agent_ollama_url") or "http://vllm-server:11434").rstrip("/") + "/v1"
    model = cfg.get("agent_ollama_model") or "qwen2.5-coder:7b"
    client = AsyncOpenAI(base_url=base_url, api_key="ollama")

    # Use only a focused subset of tools — many tools overwhelm 7b models and cause narration.
    original_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    focused_tools = _to_openai_tools_from_specs(_select_tools_for_ollama(original_user_msg))

    # Inject a strong tool-call directive into the user message for the first turn.
    tool_names = [t["function"]["name"] for t in focused_tools]
    oai_messages = messages[:-1] + [{
        "role": "user",
        "content": (
            f"Call the appropriate tool immediately. Do NOT write any text first.\n"
            f"Available tools: {', '.join(tool_names)}\n\n"
            f"{original_user_msg}"
        ),
    }]

    tool_calls_made = 0
    narration_retries = 0

    while True:
        # Always use tool_choice="auto" — "required" causes qwen2.5-coder:7b to return HTTP 500
        # after ~90s, exhausting the timeout before any fallback can run.
        # Narration detection below rescues cases where the model writes tool names in text.
        response = await client.chat.completions.create(
            model=model,
            tools=focused_tools,
            tool_choice="auto",
            messages=oai_messages,
            timeout=60.0,
        )
        if not response.choices:
            logger.warning("Ollama returned empty choices")
            yield {"type": "error", "message": "Local model returned an empty response. Try rephrasing your question."}
            return
        msg = response.choices[0].message
        finish = response.choices[0].finish_reason
        logger.info(f"Ollama response: finish_reason={finish} tool_calls={bool(msg.tool_calls)} content_len={len(msg.content or '')}")

        if msg.tool_calls:
            narration_retries = 0
            oai_messages = oai_messages + [msg]
            tool_results = []
            for tc in msg.tool_calls:
                params = json.loads(tc.function.arguments) if tc.function.arguments else {}
                yield {"type": "tool_call", "tool": tc.function.name, "params": params}
                result_text, ok = await execute_tool(tc.function.name, params, cfg)
                summary = _tool_summary(tc.function.name, result_text, ok)
                try:
                    result_data = json.loads(result_text)
                except Exception:
                    result_data = None
                yield {"type": "tool_result", "tool": tc.function.name, "summary": summary, "ok": ok, "data": result_data}
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })
                tool_calls_made += 1
            oai_messages = oai_messages + tool_results

        elif tool_calls_made == 0 and narration_retries < 2:
            narration_retries += 1
            content = msg.content or ""
            logger.warning(f"Ollama narrated (retry {narration_retries}): finish={finish} content={content[:200]!r}")

            # Try to rescue: if the model named a tool in its text, execute it directly.
            narrated_tool = _extract_narrated_tool(content, tool_names)
            if narrated_tool:
                logger.info(f"Ollama narrated tool '{narrated_tool}' — executing it directly")
                yield {"type": "tool_call", "tool": narrated_tool, "params": {}}
                result_text, ok = await execute_tool(narrated_tool, {}, cfg)
                summary = _tool_summary(narrated_tool, result_text, ok)
                try:
                    result_data = json.loads(result_text)
                except Exception:
                    result_data = None
                yield {"type": "tool_result", "tool": narrated_tool, "summary": summary, "ok": ok, "data": result_data}
                import uuid as _uuid
                fake_tc_id = str(_uuid.uuid4())
                # Trim to 4000 chars — enough for ~40 pods/VMs while staying within local model context
                if len(result_text) > 4000:
                    result_text = result_text[:4000] + '... (truncated)'
                oai_messages = oai_messages + [
                    {"role": "assistant", "content": None, "tool_calls": [{
                        "id": fake_tc_id,
                        "type": "function",
                        "function": {"name": narrated_tool, "arguments": "{}"},
                    }]},
                    {"role": "tool", "tool_call_id": fake_tc_id, "content": result_text},
                ]
                tool_calls_made += 1
            else:
                # Pure narration with no detectable tool name — inject correction and retry
                oai_messages = oai_messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"STOP. Do not write text. Call one of these tools now: {', '.join(tool_names[:3])}"},
                ]

        else:
            # Final answer — emit the content directly (tool results are in oai_messages)
            final = await client.chat.completions.create(
                model=model,
                messages=oai_messages,
                stream=False,
                timeout=90.0,
            )
            if final.choices:
                text = final.choices[0].message.content or ""
                chunk_size = 30
                for i in range(0, len(text), chunk_size):
                    yield {"type": "token", "text": text[i:i + chunk_size]}
            break


# ─── Public entry point ───────────────────────────────────────────────────────

async def run_agent_stream(
    provider: str,
    cfg: dict,
    history: list[dict],
    message: str,
) -> AsyncIterator[dict]:
    """
    Main entry point. Yields SSE-style dicts.
    history: list of {role: user|assistant, content: str} — text only, no tool details.
    """
    logger.info(f"Agent request: provider={provider!r} message={message[:80]!r}")
    try:
        if provider == "anthropic":
            msgs = _to_anthropic_messages(history, message)
            async for event in _anthropic_loop(cfg, msgs):
                yield event

        elif provider == "openai":
            msgs = _to_openai_messages(history, message, AGENT_SYSTEM)
            async for event in _openai_loop(cfg, msgs, AGENT_SYSTEM):
                yield event

        elif provider == "gemini":
            async for event in _gemini_loop(cfg, history, message):
                yield event

        elif provider == "ollama":
            msgs = _to_openai_messages(history, message, AGENT_SYSTEM_OLLAMA)
            async for event in _ollama_loop(cfg, msgs, AGENT_SYSTEM_OLLAMA):
                yield event

        else:
            yield {"type": "error", "message": f"Provider '{provider}' does not support tool calling. Use anthropic, openai, gemini, or ollama."}
            return

    except Exception as e:
        logger.error(f"Agent stream error: {e}", exc_info=True)
        yield {"type": "error", "message": str(e)}

    yield {"type": "done"}
