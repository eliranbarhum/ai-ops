import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared import LLM_GATEWAY_URL, _get_cfg
from workspace_executor import _call_powercli as _ws_powercli

router = APIRouter()


@router.post("/api/v1/guest/inventory")
async def guest_inventory():
    cfg = await _get_cfg()
    script = """
Get-VM | Where-Object {$_.PowerState -eq 'PoweredOn'} | ForEach-Object {
    $vm = $_
    $guest = $vm.Guest
    [PSCustomObject]@{
        name      = $vm.Name
        os        = $guest.OSFullName
        hostname  = $guest.Hostname
        ip        = ($guest.IPAddress | Where-Object {$_ -notmatch ':'} | Select-Object -First 1)
        tools     = $vm.ExtensionData.Guest.ToolsVersionStatus
        tools_ver = $vm.ExtensionData.Guest.ToolsVersion
        power     = $vm.PowerState.ToString()
        cluster   = if ($vm.VMHost.Parent) { $vm.VMHost.Parent.Name } else { '' }
        host      = $vm.VMHost.Name
    }
} | ConvertTo-Json -Depth 3 -AsArray"""
    result = await _ws_powercli(cfg, {"script": script})
    return {"status_code": result["status_code"], "response": result["response"]}


class GuestScriptGenerateRequest(BaseModel):
    description: str
    script_type: str = "PowerShell"
    os_hint: str = ""


@router.post("/api/v1/guest/generate")
async def guest_generate(request: GuestScriptGenerateRequest):
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{LLM_GATEWAY_URL}/generate/guest-script", json=request.model_dump())
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="LLM gateway timed out")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=e.response.text)


class GuestToolsRequest(BaseModel):
    vm_name: str


@router.post("/api/v1/guest/tools")
async def guest_tools_update(req: GuestToolsRequest):
    cfg = await _get_cfg()
    vm_safe = req.vm_name.replace("'", "''")
    script = f"""
$vm = Get-VM -Name '{vm_safe}' -ErrorAction Stop
Update-Tools -VM $vm -NoReboot | Out-Null
[PSCustomObject]@{{ vm = $vm.Name; message = 'VMware Tools update initiated. A reboot may be required.' }} | ConvertTo-Json"""
    result = await _ws_powercli(cfg, {"script": script})
    return {"status_code": result["status_code"], "response": result["response"]}


class GuestRunRequest(BaseModel):
    vm_name: str
    script: str
    script_type: str = "PowerShell"
    guest_username: str
    guest_password: str


@router.post("/api/v1/guest/run")
async def guest_run(req: GuestRunRequest):
    cfg = await _get_cfg()
    vm_safe   = req.vm_name.replace("'", "''")
    user_safe = req.guest_username.replace("'", "''")
    pass_safe = req.guest_password.replace("'", "''")
    ps_script = f"""
$guestCred = New-Object System.Management.Automation.PSCredential(
    '{user_safe}',
    (ConvertTo-SecureString '{pass_safe}' -AsPlainText -Force))
$result = Invoke-VMScript -VM '{vm_safe}' -ScriptText @'
{req.script}
'@ -GuestCredential $guestCred -ScriptType {req.script_type}
[PSCustomObject]@{{ output=$result.ScriptOutput; exit_code=$result.ExitCode }} | ConvertTo-Json"""
    result = await _ws_powercli(cfg, {"script": ps_script})
    return {"status_code": result["status_code"], "response": result["response"]}
