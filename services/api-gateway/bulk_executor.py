"""
Bulk operations executor — CSV parsing and batch dispatch for VM and AD user provisioning.
Max 20 rows per batch (synchronous response, no background job needed).
"""
import csv
import io
import logging
import asyncio
import httpx

logger = logging.getLogger("api-gateway.bulk")

_BULK_SEM = asyncio.Semaphore(20)  # max 20 concurrent vCenter/AD calls

CSV_VM_COLUMNS = ["name", "os", "cpu", "ram_gb", "disk_gb", "network", "folder", "datastore"]
CSV_VM_OPTIONAL = ["owner_tag", "env_tag"]

CSV_AD_COLUMNS = ["first_name", "last_name", "username", "email", "temp_password", "ou"]
CSV_AD_OPTIONAL = ["groups", "vcenter_role"]

MAX_ROWS = 20

POWERCLI_URL = "http://powercli:8010"


def parse_csv_vms(content: bytes) -> tuple[list[dict], list[dict]]:
    rows, errors = [], []
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return [], [{"row": 0, "error": "Empty or invalid CSV"}]
        missing = [c for c in CSV_VM_COLUMNS if c not in reader.fieldnames]
        if missing:
            return [], [{"row": 0, "error": f"Missing required columns: {', '.join(missing)}"}]
        for i, row in enumerate(reader, start=1):
            if i > MAX_ROWS:
                errors.append({"row": i, "error": f"Row limit {MAX_ROWS} exceeded — truncated"})
                break
            entry = {c: row.get(c, "").strip() for c in CSV_VM_COLUMNS + CSV_AD_OPTIONAL}
            row_errors = []
            if not entry["name"]:
                row_errors.append("name is required")
            if not entry["os"]:
                row_errors.append("os is required")
            try:
                entry["cpu"] = int(entry.get("cpu", ""))
                if entry["cpu"] < 1:
                    row_errors.append("cpu must be >= 1")
            except ValueError:
                row_errors.append("cpu must be an integer")
            try:
                entry["ram_gb"] = int(entry.get("ram_gb", ""))
                if entry["ram_gb"] < 1:
                    row_errors.append("ram_gb must be >= 1")
            except ValueError:
                row_errors.append("ram_gb must be an integer")
            try:
                entry["disk_gb"] = int(entry.get("disk_gb", ""))
                if entry["disk_gb"] < 1:
                    row_errors.append("disk_gb must be >= 1")
            except ValueError:
                row_errors.append("disk_gb must be an integer")
            entry["_row"] = i
            if row_errors:
                entry["_status"] = "error"
                entry["_error"] = "; ".join(row_errors)
                errors.append({"row": i, "error": entry["_error"]})
            else:
                entry["_status"] = "valid"
            rows.append(entry)
    except Exception as e:
        return [], [{"row": 0, "error": f"CSV parse error: {e}"}]
    return rows, errors


def parse_csv_ad_users(content: bytes) -> tuple[list[dict], list[dict]]:
    rows, errors = [], []
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return [], [{"row": 0, "error": "Empty or invalid CSV"}]
        missing = [c for c in CSV_AD_COLUMNS if c not in reader.fieldnames]
        if missing:
            return [], [{"row": 0, "error": f"Missing required columns: {', '.join(missing)}"}]
        for i, row in enumerate(reader, start=1):
            if i > MAX_ROWS:
                errors.append({"row": i, "error": f"Row limit {MAX_ROWS} exceeded — truncated"})
                break
            entry = {c: row.get(c, "").strip() for c in CSV_AD_COLUMNS + CSV_AD_OPTIONAL}
            entry["_row"] = i
            row_errors = []
            for field in ["first_name", "last_name", "username", "email", "temp_password", "ou"]:
                if not entry.get(field):
                    row_errors.append(f"{field} is required")
            if row_errors:
                entry["_status"] = "error"
                entry["_error"] = "; ".join(row_errors)
                errors.append({"row": i, "error": entry["_error"]})
            else:
                entry["_status"] = "valid"
            rows.append(entry)
    except Exception as e:
        return [], [{"row": 0, "error": f"CSV parse error: {e}"}]
    return rows, errors


_GUEST_OS_MAP = {
    "windows2022": "WINDOWS_SERVER_2022_64",
    "windows2019": "WINDOWS_SERVER_2019_64",
    "windows2016": "WINDOWS_SERVER_2016_64",
    "windows11":   "WINDOWS_11_64",
    "windows10":   "WINDOWS_10_64",
    "rhel9":       "RHEL_9_64",
    "rhel8":       "RHEL_8_64",
    "ubuntu22":    "UBUNTU_64",
    "ubuntu20":    "UBUNTU_64",
    "debian12":    "DEBIAN_11_64",
    "centos8":     "CENTOS_8_64",
    "rocky9":      "OTHER_LINUX_64",
    "other":       "OTHER_GUEST_64",
}


def _build_vm_powercli(row: dict) -> str:
    name   = row["name"].replace("'", "''")
    os_key = row["os"].lower().replace(" ", "").replace("-", "").replace("_", "")
    guest  = _GUEST_OS_MAP.get(os_key, "OTHER_GUEST_64")
    cpu    = row["cpu"]
    ram_mb = row["ram_gb"] * 1024
    disk   = row["disk_gb"]
    net    = row["network"].replace("'", "''")
    folder = row.get("folder", "").replace("'", "''")
    ds     = row["datastore"].replace("'", "''")
    return f"""
$ds = Get-Datastore -Name '{ds}' | Select-Object -First 1
$net = Get-VirtualPortGroup -Name '{net}' | Select-Object -First 1
$vmParams = @{{
    Name            = '{name}'
    NumCpu          = {cpu}
    MemoryMB        = {ram_mb}
    DiskMB          = {disk * 1024}
    DiskStorageFormat = 'Thin'
    GuestId         = '{guest}'
    Datastore       = $ds
    NetworkName     = $net.Name
}}
{f"$vmParams['Location'] = Get-Folder -Name '{folder}' | Select-Object -First 1" if folder else ""}
$vm = New-VM @vmParams
[PSCustomObject]@{{ name='{name}'; status='created'; id=$vm.Id }} | ConvertTo-Json
"""


async def execute_vm_batch(rows: list[dict], cfg: dict) -> list[dict]:
    valid = [r for r in rows if r.get("_status") == "valid"]

    async def _create_one(row: dict) -> dict:
        async with _BULK_SEM:
            script = _build_vm_powercli(row)
            payload = {
                "script": script,
                "vcenter_host": cfg.get("vcenter_host", ""),
                "vcenter_user": cfg.get("vcenter_user", ""),
                "vcenter_password": cfg.get("vcenter_password", ""),
                "verify_ssl": cfg.get("vcenter_verify_ssl", False),
            }
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(f"{POWERCLI_URL}/run", json=payload)
                data = resp.json()
                if data.get("exit_code", -1) == 0:
                    return {"name": row["name"], "status": "done", "output": data.get("output", "")}
                return {"name": row["name"], "status": "failed", "error": data.get("error") or data.get("output", "unknown error")}
            except Exception as e:
                return {"name": row["name"], "status": "failed", "error": str(e)}

    return list(await asyncio.gather(*[_create_one(r) for r in valid]))


def _build_ad_user_powercli(row: dict, cfg: dict) -> str:
    ad_host   = cfg.get("ad_host", "").replace("'", "''")
    ad_user   = cfg.get("ad_user", "").replace("'", "''")
    ad_pass   = cfg.get("ad_password", "").replace("'", "''")
    ad_domain = cfg.get("ad_domain", "").replace("'", "''")
    first  = row["first_name"].replace("'", "''")
    last   = row["last_name"].replace("'", "''")
    uname  = row["username"].replace("'", "''")
    email  = row["email"].replace("'", "''")
    pwd    = row["temp_password"].replace("'", "''")
    ou     = row["ou"].replace("'", "''")
    groups = [g.strip() for g in row.get("groups", "").split(",") if g.strip()]

    group_lines = ""
    for g in groups:
        g_safe = g.replace("'", "''")
        group_lines += f"""
$group = [ADSI]"LDAP://$adHost/CN={g_safe},{ou}"
$group.Member.Add("CN={first} {last},{ou}") | Out-Null
"""

    return f"""
$ErrorActionPreference = 'Stop'
$adHost = '{ad_host}'
$adUser = '{ad_user}'
$adPass = ConvertTo-SecureString '{ad_pass}' -AsPlainText -Force
$adCred = New-Object System.Management.Automation.PSCredential($adUser, $adPass)
$ouPath = "LDAP://$adHost/{ou}"
$root = New-Object DirectoryServices.DirectoryEntry($ouPath, $adUser, '{ad_pass}')
$newUser = $root.Children.Add("CN={first} {last}", "user")
$newUser.Properties["sAMAccountName"].Value = "{uname}"
$newUser.Properties["userPrincipalName"].Value = "{uname}@{ad_domain}"
$newUser.Properties["givenName"].Value = "{first}"
$newUser.Properties["sn"].Value = "{last}"
$newUser.Properties["mail"].Value = "{email}"
$newUser.Properties["displayName"].Value = "{first} {last}"
$newUser.CommitChanges()
$newUser.Invoke("SetPassword", "{pwd}")
$newUser.Properties["userAccountControl"].Value = 512
$newUser.CommitChanges()
{group_lines}
[PSCustomObject]@{{ username="{uname}"; status="created" }} | ConvertTo-Json
"""


async def execute_ad_batch(rows: list[dict], cfg: dict) -> list[dict]:
    valid = [r for r in rows if r.get("_status") == "valid"]

    async def _create_one(row: dict) -> dict:
        async with _BULK_SEM:
            script = _build_ad_user_powercli(row, cfg)
            payload = {
                "script": script,
                "skip_vcenter_connect": True,
                "ad_host": cfg.get("ad_host", ""),
                "ad_user": cfg.get("ad_user", ""),
                "ad_password": cfg.get("ad_password", ""),
            }
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(f"{POWERCLI_URL}/run", json=payload)
                data = resp.json()
                if data.get("exit_code", -1) == 0:
                    return {"username": row["username"], "status": "done", "output": data.get("output", "")}
                return {"username": row["username"], "status": "failed", "error": data.get("error") or data.get("output", "unknown error")}
            except Exception as e:
                return {"username": row["username"], "status": "failed", "error": str(e)}

    return list(await asyncio.gather(*[_create_one(r) for r in valid]))
