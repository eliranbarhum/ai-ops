"""
Intent-based API call builder.

The LLM extracts a minimal intent (what the user wants, in human terms).
This module resolves that intent against live vCenter context to produce
a guaranteed-valid API call — no hallucinated IDs, no unit errors, no
wrong enums.

Works in any vCenter environment because all IDs come from the live
context, never from the LLM.
"""

import logging
import re

# ---------------------------------------------------------------------------
# PowerShell cmdlet name sanitizer.
# LLMs routinely drop the hyphen in Verb-Noun cmdlet names (GetDate → Get-Date).
# Apply this to all generated scripts before returning or executing them.
# Negative lookbehind on $ excludes variable names like $GetResult.
# ---------------------------------------------------------------------------
_PS_VERB_RE = re.compile(
    r'(?<![\$.])\b'
    r'(Add|Approve|Assert|Backup|Block|Build|Checkpoint|Clear|Close|Compare|Complete|'
    r'Compress|ConvertFrom|ConvertTo|Copy|Debug|Deny|Deploy|Disable|Disconnect|Dismount|'
    r'Edit|Enable|Enter|Exit|Expand|Export|Find|Format|Get|Grant|Group|Hide|Import|'
    r'Initialize|Install|Invoke|Join|Limit|Lock|Measure|Merge|Mount|Move|New|Open|'
    r'Optimize|Out|Ping|Pop|Protect|Publish|Push|Read|Receive|Redo|Register|Remove|'
    r'Rename|Reset|Resize|Resolve|Restart|Restore|Resume|Revoke|Save|Search|Select|'
    r'Send|Set|Show|Skip|Sort|Start|Step|Stop|Submit|Suspend|Switch|Sync|Test|Trace|'
    r'Unblock|Undo|Uninstall|Unlock|Unprotect|Unpublish|Unregister|Update|Use|Wait|'
    r'Watch|Where|Write)'
    r'([A-Z][a-zA-Z0-9]+)\b'
)


def fix_ps_cmdlet_names(script: str) -> str:
    """Insert missing hyphens in PowerShell Verb-Noun cmdlet names."""
    return _PS_VERB_RE.sub(r'\1-\2', script)


logger = logging.getLogger("llm-gateway.intent")

# ---------------------------------------------------------------------------
# Guest OS mapping — maps natural language to vCenter enum values.
# Add entries here when a new vCenter version introduces new valid enums.
# ---------------------------------------------------------------------------
_GUEST_OS_MAP: dict[str, str] = {
    # Ubuntu
    "ubuntu": "UBUNTU_64",
    "ubuntu_64": "UBUNTU_64",
    "ubuntu_18": "UBUNTU_64",
    "ubuntu_20": "UBUNTU_64",
    "ubuntu_22": "UBUNTU_64",
    "ubuntu_24": "UBUNTU_64",
    # RHEL / CentOS / Rocky / Alma
    "rhel": "RHEL_9_64",
    "rhel_9": "RHEL_9_64",
    "rhel_8": "RHEL_8_64",
    "centos": "CENTOS_8_64",
    "rocky": "RHEL_9_64",
    "almalinux": "RHEL_9_64",
    "alma": "RHEL_9_64",
    # Debian / generic Linux
    "debian": "DEBIAN_12_64",
    "linux": "OTHER_LINUX_64",
    "other_linux": "OTHER_LINUX_64",
    # Windows Server — use the highest enum valid on this vCenter (9.0.2)
    "windows": "WINDOWS_SERVER_2019",
    "windows_server": "WINDOWS_SERVER_2019",
    "windows_server_2025": "WINDOWS_SERVER_2019",
    "windows_server_2022": "WINDOWS_SERVER_2019",
    "windows_server_2022_64": "WINDOWS_SERVER_2019",
    "windows_server_2019": "WINDOWS_SERVER_2019",
    "windows_server_2019_64": "WINDOWS_SERVER_2019",
    "windows_server_2016": "WINDOWS_9_SERVER_64",
    "windows_2022": "WINDOWS_SERVER_2019",
    "windows_2019": "WINDOWS_SERVER_2019",
    "windows_2016": "WINDOWS_9_SERVER_64",
    "win2022": "WINDOWS_SERVER_2019",
    "win2019": "WINDOWS_SERVER_2019",
    "win2016": "WINDOWS_9_SERVER_64",
    # Generic
    "other": "OTHER_64",
}


def resolve_guest_os(raw: str) -> str:
    """Map any user-facing OS string to the correct vCenter guest_OS enum."""
    if not raw:
        return "UBUNTU_64"
    clean = raw.strip().lower()
    # Already a valid-looking uppercase enum — pass through
    if re.match(r'^[A-Z0-9_]+$', raw.strip()):
        return raw.strip()
    # Normalize and look up
    key = re.sub(r'[\s\-/\.]+', '_', clean)
    if key in _GUEST_OS_MAP:
        return _GUEST_OS_MAP[key]
    # Substring match
    for k, v in _GUEST_OS_MAP.items():
        if k in key or key in k:
            return v
    return "OTHER_LINUX_64"


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def ram_gb_to_mib(gb: float) -> int:
    return max(512, int(gb * 1024))


def disk_gb_to_bytes(gb: float) -> int:
    return int(gb * 1024 * 1024 * 1024)


# ---------------------------------------------------------------------------
# Context-aware placement resolution
# ---------------------------------------------------------------------------

# Folder names that are safe for user VM placement (preferred over arbitrary first-in-list)
_PREFERRED_FOLDER_NAMES = ("user-vms", "user_vms", "vm", "vms", "virtual machines",
                            "workload", "production", "dev", "test", "default")

# Valid vCenter ID patterns — reject anything that doesn't match
import re as _re
_CLUSTER_RE = _re.compile(r'^domain-c\d+$')
_DATASTORE_RE = _re.compile(r'^datastore-\d+$')
_FOLDER_RE = _re.compile(r'^group-v\d+$')


def _is_valid_cluster(v: str) -> bool:
    return bool(_CLUSTER_RE.match(str(v or "")))


def _is_valid_datastore(v: str) -> bool:
    return bool(_DATASTORE_RE.match(str(v or "")))


def _is_valid_folder(v: str) -> bool:
    return bool(_FOLDER_RE.match(str(v or "")))


def best_placement(ctx: dict, preferred_folder: str | None = None,
                   preferred_datastore: str | None = None) -> dict:
    """
    Pick the best cluster/datastore/folder from live context.
    Only returns IDs that match valid vCenter ID patterns.
    Prefers well-known folder names over arbitrary first-in-list.
    """
    clusters = ctx.get("clusters", [])
    datastores = ctx.get("datastores", [])
    folders = ctx.get("folders", [])

    # Cluster: filter to valid IDs, use first
    valid_clusters = [c for c in clusters if _is_valid_cluster(c.get("cluster", ""))]
    cluster_id = valid_clusters[0]["cluster"] if valid_clusters else None

    # Datastore: filter to valid IDs, prefer user-specified name, else most free space
    valid_datastores = [d for d in datastores if _is_valid_datastore(d.get("datastore", ""))]
    datastore_id = None
    if preferred_datastore:
        for d in valid_datastores:
            if preferred_datastore.lower() in d.get("name", "").lower():
                datastore_id = d["datastore"]
                break
    if not datastore_id:
        # Most free space is already first (vcenter_context sorts by free_space_MB desc)
        datastore_id = valid_datastores[0]["datastore"] if valid_datastores else None

    # Folder: filter to valid IDs, prefer user-specified name, then well-known names, else first
    valid_folders = [f for f in folders if _is_valid_folder(f.get("folder", ""))]
    folder_id = None
    if preferred_folder:
        for f in valid_folders:
            if preferred_folder.lower() in f.get("name", "").lower():
                folder_id = f["folder"]
                break
    if not folder_id:
        # Prefer well-known user-friendly folder names
        for pref in _PREFERRED_FOLDER_NAMES:
            for f in valid_folders:
                if f.get("name", "").lower() == pref:
                    folder_id = f["folder"]
                    break
            if folder_id:
                break
    if not folder_id:
        folder_id = valid_folders[0]["folder"] if valid_folders else None

    return {
        "cluster": cluster_id,
        "datastore": datastore_id,
        "folder": folder_id,
    }


def best_network(ctx: dict, preferred_network: str | None = None) -> str | None:
    """Return a network ID if one can be resolved, else None (no NIC attached)."""
    networks = ctx.get("networks", [])
    if not networks:
        return None
    if preferred_network:
        for n in networks:
            if preferred_network.lower() in n.get("name", "").lower():
                return n["network"]
    return None


# ---------------------------------------------------------------------------
# VM create spec builder — the core of the intent pipeline
# ---------------------------------------------------------------------------

def _safe_float(val, default: float, min_val: float, max_val: float) -> float:
    """Parse a number from LLM output with bounds checking."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if v < min_val or v > max_val:
        return default
    return v


def _safe_int(val, default: int, min_val: int, max_val: int) -> int:
    return int(_safe_float(val, default, min_val, max_val))


def build_vm_create_spec(intent: dict, ctx: dict) -> dict:
    """
    Build a guaranteed-valid POST /rest/vcenter/vm spec from an extracted
    intent and live vCenter context.

    intent keys: name, os, cpu (int), ram_gb (float), disk_gb (float),
                 [folder_name], [network_name], [datastore_name]

    Placement IDs always come from live context — never from the LLM.
    """
    name = str(intent.get("name") or "new-vm").strip() or "new-vm"
    cpu = _safe_int(intent.get("cpu"), default=2, min_val=1, max_val=128)
    ram_gb = _safe_float(intent.get("ram_gb"), default=4.0, min_val=0.5, max_val=1024.0)
    disk_gb = _safe_float(intent.get("disk_gb"), default=50.0, min_val=1.0, max_val=65536.0)

    guest_os = resolve_guest_os(str(intent.get("os") or "ubuntu"))
    placement = best_placement(
        ctx,
        preferred_folder=intent.get("folder_name"),
        preferred_datastore=intent.get("datastore_name"),
    )
    network_id = best_network(ctx, intent.get("network_name"))

    nics = []
    if network_id:
        nics = [{"type": "VMXNET3", "backing": {"type": "STANDARD_PORTGROUP", "network": network_id}}]

    vm_spec: dict = {
        "name": name,
        "guest_OS": guest_os,
        "placement": {k: v for k, v in placement.items() if v},
        "cpu": {
            "count": cpu,
            "cores_per_socket": 1,
            "hot_add_enabled": False,
            "hot_remove_enabled": False,
        },
        "memory": {
            "size_MiB": ram_gb_to_mib(ram_gb),
            "hot_add_enabled": False,
        },
        "disks": [
            {
                "type": "SCSI",
                "new_vmdk": {
                    "capacity": disk_gb_to_bytes(disk_gb),
                    "name": "disk0",
                },
            }
        ],
        "nics": nics,
    }

    desc_parts = [f"Create {intent.get('os', 'Linux')} VM '{name}'",
                  f"{cpu} vCPU", f"{ram_gb:.0f}GB RAM", f"{disk_gb:.0f}GB disk"]
    if intent.get("folder_name"):
        desc_parts.append(f"in folder '{intent['folder_name']}'")

    return {
        "target": "vcenter",
        "method": "POST",
        "path": "/rest/vcenter/vm",
        "description": ", ".join(desc_parts),
        "body": {"spec": vm_spec},
        "query_params": {},
    }


# ---------------------------------------------------------------------------
# Action classifier — detect what the user wants without touching the LLM
# ---------------------------------------------------------------------------

_CREATE_KEYWORDS = re.compile(
    r'\b(creat|deploy|provisi|spin.?up|build|launch)\w*\b.*\bvm\b'
    r'|\bvm\b.*\b(creat|deploy|provisi|spin.?up|build|launch)\w*\b'
    r'|\bnew\s+vm\b'
    r'|\bspawn\s+vm\b'
    r'|\badd\s+a?\s*vm\b',
    re.IGNORECASE,
)


def is_vm_create_request(description: str) -> bool:
    return bool(_CREATE_KEYWORDS.search(description))


# ---------------------------------------------------------------------------
# Intent extraction prompt — minimal, structured, model-agnostic
# ---------------------------------------------------------------------------

INTENT_EXTRACT_SYSTEM = """\
GOAL: Extract VM creation parameters from the user request and return them as a single JSON object.
CRITICAL: Output ONLY the JSON object. No markdown, no explanation, no extra text.

JSON fields:
  name          : string  — VM hostname/name
  os            : string  — OS keyword: "ubuntu", "windows server 2022", "rhel", "debian", etc.
  cpu           : integer — number of vCPUs
  ram_gb        : number  — RAM in gigabytes (not MiB, not MiB)
  disk_gb       : number  — primary disk in gigabytes
  folder_name   : string  — only if user named a folder
  datastore_name: string  — only if user named a datastore
  network_name  : string  — only if user named a network/portgroup

Defaults if not mentioned: name="new-api-vm", os="ubuntu", cpu=2, ram_gb=4, disk_gb=50

[EXAMPLES]

User: create ubuntu vm named web-api-01 with 4 vcpu 8gb ram 50gb disk
Assistant: {"name":"web-api-01","os":"ubuntu","cpu":4,"ram_gb":8,"disk_gb":50}

User: deploy windows server 2022 vm prod-db with 8 vcpu 32gb ram 200gb disk in the prod folder
Assistant: {"name":"prod-db","os":"windows server 2022","cpu":8,"ram_gb":32,"disk_gb":200,"folder_name":"prod"}

User: create rhel 9 vm app-server-03 2 cpu 16gb ram 100gb disk on ds-fast datastore
Assistant: {"name":"app-server-03","os":"rhel 9","cpu":2,"ram_gb":16,"disk_gb":100,"datastore_name":"ds-fast"}

User: new vm
Assistant: {"name":"new-api-vm","os":"ubuntu","cpu":2,"ram_gb":4,"disk_gb":50}

User: create a windows 11 workstation with 4 cores 8gb ram
Assistant: {"name":"new-api-vm","os":"windows 11","cpu":4,"ram_gb":8,"disk_gb":50}
"""


# ---------------------------------------------------------------------------
# PowerCLI support — for operations the REST API cannot handle
# ---------------------------------------------------------------------------

_POWERCLI_RE = re.compile(
    r'\b('
    r'last\s+\d+\s+(day|hour|week|month)s?'
    r'|last\s+(week|month|day|hour|year)'
    r'|past\s+\d+\s+(day|hour|week|month)s?'
    r'|past\s+(week|month|day)'
    r'|created\s+(in|since|within|last|recently)'
    r'|recently\s+created'
    r'|snapshot'
    r'|clone(\s+vm)?'
    r'|from\s+template'
    r'|template'
    r'|event\s+(log|histor)'
    r'|task\s+(log|histor|event)'
    r'|vm\s+event'
    r'|hardware\s+version'
    r'|vmtools|vm\s+tools'
    r'|top\s+\d+\s+(vm|virtual)'
    r'|most\s+(cpu|memory|disk|network|resource)'
    r'|guest\s+os\s+(info|detail|version)'
    r'|advanced\s+setting'
    r'|report\s+(all|vm|host|cluster)'
    r'|sort(ed)?\s+by\s+(cpu|mem|disk|network|name|date)'
    r')',
    re.IGNORECASE,
)


def needs_powercli(description: str) -> bool:
    """
    Return True for requests that require PowerCLI because the vCenter REST API
    cannot handle them — e.g., date-based filtering, snapshots, templates, clones.
    """
    return bool(_POWERCLI_RE.search(description))


def build_powercli_spec(script: str, description: str) -> dict:
    """Return a workspace spec with target='powercli' for execution by workspace_executor."""
    return {
        "target": "powercli",
        "method": "POST",
        "path": "/run",
        "description": description,
        "body": {"script": script.strip()},
        "query_params": {},
    }


def build_powercli_context_prompt(ctx: dict) -> str:
    """
    Format live vCenter inventory as a compact PowerCLI-friendly context block.
    Provides cluster/datastore/network names (not IDs) since PowerCLI uses names.
    """
    lines = ["LIVE vCenter INVENTORY (use these exact names in your script):"]
    clusters = ctx.get("clusters", [])
    if clusters:
        names = [c.get("name", "") for c in clusters[:5] if c.get("name")]
        lines.append(f"  Clusters: {', '.join(names)}")
    datastores = ctx.get("datastores", [])
    if datastores:
        names = [d.get("name", "") for d in datastores[:5] if d.get("name")]
        lines.append(f"  Datastores: {', '.join(names)}")
    networks = ctx.get("networks", [])
    if networks:
        names = [n.get("name", "") for n in networks[:5] if n.get("name")]
        lines.append(f"  Networks: {', '.join(names)}")
    return "\n".join(lines)


POWERCLI_GENERATE_SYSTEM = """\
GOAL: Convert the user's natural language request into an executable VMware PowerCLI script body.
CRITICAL: Output ONLY the raw PowerShell script. No markdown, no backticks, no explanation, no comments.

HARD CONSTRAINTS:
- NEVER include Connect-VIServer or Disconnect-VIServer — connection is pre-established
- NEVER include Set-PowerCLIConfiguration or Import-Module — already handled
- ALWAYS use hyphenated cmdlet names: Get-VM not GetVM, Sort-Object not SortObject
- ALWAYS end with ConvertTo-Json or Write-Output for machine-readable output
- Use -ErrorAction Stop on commands that create or modify objects

[EXAMPLES]

User: list all VMs created in the last 7 days
Assistant:
$cutoff = (Get-Date).AddDays(-7)
Get-VM | Where-Object { $_.ExtensionData.Config.CreateDate -gt $cutoff } |
    Select-Object Name, @{N='Created';E={$_.ExtensionData.Config.CreateDate}}, PowerState |
    ConvertTo-Json

User: show all snapshots with their size
Assistant:
Get-VM | Get-Snapshot | Select-Object VM, Name, Created, SizeMB | ConvertTo-Json

User: top 10 VMs by CPU usage
Assistant:
Get-VM | Sort-Object -Descending { $_.ExtensionData.Summary.QuickStats.OverallCpuUsage } |
    Select-Object -First 10 Name,
        @{N='CPU_MHz';E={$_.ExtensionData.Summary.QuickStats.OverallCpuUsage}},
        @{N='Mem_MB';E={$_.ExtensionData.Summary.QuickStats.GuestMemoryUsage}} |
    ConvertTo-Json

User: clone vm web-01 from template ubuntu-template in cluster-prod on ds-main
Assistant:
$template = Get-Template -Name "ubuntu-template"
$cluster  = Get-Cluster  -Name "cluster-prod"
$ds       = Get-Datastore -Name "ds-main"
New-VM -Name "web-01" -Template $template -ResourcePool $cluster -Datastore $ds -ErrorAction Stop |
    Select-Object Name, PowerState | ConvertTo-Json

User: show VM events from the last 24 hours
Assistant:
$cutoff = (Get-Date).AddHours(-24)
Get-VIEvent -Start $cutoff -MaxSamples 200 |
    Select-Object CreatedTime, UserName, FullFormattedMessage | ConvertTo-Json

User: list all VMs with their hardware version and VMware Tools status
Assistant:
Get-VM | Select-Object Name,
    @{N='HWVersion';E={$_.ExtensionData.Config.Version}},
    @{N='ToolsStatus';E={$_.ExtensionData.Guest.ToolsStatus}},
    @{N='ToolsVersion';E={$_.ExtensionData.Guest.ToolsVersion}} |
    ConvertTo-Json

User: show datastores below 20 percent free space
Assistant:
Get-Datastore | Where-Object { ($_.FreeSpaceGB / $_.CapacityGB) -lt 0.20 } |
    Select-Object Name,
        @{N='CapacityGB';E={[math]::Round($_.CapacityGB,1)}},
        @{N='FreeGB';E={[math]::Round($_.FreeSpaceGB,1)}},
        @{N='FreePercent';E={[math]::Round(($_.FreeSpaceGB/$_.CapacityGB)*100,1)}} |
    ConvertTo-Json
"""
