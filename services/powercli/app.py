"""
PowerCLI runner service.
Executes PowerCLI scripts against vCenter by wrapping them in a Connect/Disconnect
session and invoking pwsh as a subprocess with a hard timeout.

Credentials are injected by the caller (workspace_executor) — never from the LLM.
"""
import os
import re
import asyncio
import tempfile
import logging
import time

# Matches PS string literals and comments first (returned unchanged), then verb-noun
# patterns to insert dashes.  Alternation order ensures strings/comments are never
# transformed (e.g. "GetVM status" must not become "Get-VM status").
_PS_FIX_RE = re.compile(
    r"'[^']*'"           # single-quoted PS string
    r'|"(?:[^"\\]|\\.)*"'  # double-quoted string (backslash escapes)
    r"|#[^\n]*"           # line comment
    r"|(?<![$.])\b"
    r"(Add|Approve|Assert|Backup|Block|Build|Checkpoint|Clear|Close|Compare|Complete|"
    r"Compress|ConvertFrom|ConvertTo|Copy|Debug|Deny|Deploy|Disable|Disconnect|Dismount|"
    r"Edit|Enable|Enter|Exit|Expand|Export|Find|Format|Get|Grant|Group|Hide|Import|"
    r"Initialize|Install|Invoke|Join|Limit|Lock|Measure|Merge|Mount|Move|New|Open|"
    r"Optimize|Out|Ping|Pop|Protect|Publish|Push|Read|Receive|Redo|Register|Remove|"
    r"Rename|Reset|Resize|Resolve|Restart|Restore|Resume|Revoke|Save|Search|Select|"
    r"Send|Set|Show|Skip|Sort|Start|Step|Stop|Submit|Suspend|Switch|Sync|Test|Trace|"
    r"Unblock|Undo|Uninstall|Unlock|Unprotect|Unpublish|Unregister|Update|Use|Wait|"
    r"Watch|Where|Write)"
    r"([A-Z][a-zA-Z0-9]+)\b"
)


def _apply_ps_verb_fix(script: str) -> str:
    """Insert dashes in verb-noun cmdlets while skipping string literals and comments."""
    def _repl(m: re.Match) -> str:
        if m.group(1):
            return f"{m.group(1)}-{m.group(2)}"
        return m.group(0)
    return _PS_FIX_RE.sub(_repl, script)


# Limit concurrent PowerCLI subprocesses to avoid overloading the vCenter session pool.
_SEMAPHORE = asyncio.Semaphore(3)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("powercli")

app = FastAPI(title="MCO PowerCLI Runner", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])

SCRIPT_TIMEOUT = int(os.getenv("SCRIPT_TIMEOUT_SECONDS", "120"))


class RunRequest(BaseModel):
    script: str
    vcenter_host: str = ""
    vcenter_user: str = ""
    vcenter_password: str = ""
    verify_ssl: bool = False
    skip_vcenter_connect: bool = False
    ad_host: str = ""
    ad_user: str = ""
    ad_password: str = ""
    allow_writes: bool = False  # must be True to execute scripts with destructive verbs


def _wrap_script(req: RunRequest) -> tuple[str, dict]:
    """Return (script_text, env_vars). Credentials passed via env, never embedded in script."""
    host = req.vcenter_host
    user = req.vcenter_user
    script = _apply_ps_verb_fix(req.script)
    wrapped = f"""\
Import-Module VMware.VimAutomation.Core -Force -ErrorAction SilentlyContinue
$ErrorActionPreference = 'Stop'
$_pwd = ConvertTo-SecureString $env:VCENTER_PASSWORD -AsPlainText -Force
$_cred = New-Object System.Management.Automation.PSCredential("{user}", $_pwd)
Connect-VIServer -Server '{host}' -Credential $_cred -Force | Out-Null
try {{
{script}
}} finally {{
    Disconnect-VIServer -Confirm:$false -Force -ErrorAction SilentlyContinue | Out-Null
}}
"""
    env = {"VCENTER_PASSWORD": req.vcenter_password}
    return wrapped, env


def _wrap_ad_script(req: RunRequest) -> tuple[str, dict]:
    """Wrap AD script — credentials via env, never embedded in script text."""
    ad_host = req.ad_host
    ad_user = req.ad_user
    script = _apply_ps_verb_fix(req.script)
    wrapped = f"""\
$ErrorActionPreference = 'Stop'
$adHost = '{ad_host}'
$adUser = '{ad_user}'
$adPass = ConvertTo-SecureString $env:AD_PASSWORD -AsPlainText -Force
$adCred = New-Object System.Management.Automation.PSCredential($adUser, $adPass)
{script}
"""
    env = {"AD_PASSWORD": req.ad_password}
    return wrapped, env


# Destructive PowerShell verbs — require explicit allow_writes flag
_WRITE_VERBS = re.compile(
    r'\b(Remove|Set|New|Stop|Restart|Move|Update|Suspend|Start|Delete|'
    r'Invoke-VMScript|Format-Disk|Clear-Disk)\b',
    re.IGNORECASE,
)


@app.post("/run")
async def run_script(req: RunRequest):
    start = time.monotonic()
    if not req.script.strip():
        return {"status_code": 400, "output": "", "error": "Empty script", "exit_code": 1}

    if not req.skip_vcenter_connect:
        missing = [f for f, v in [("vcenter_host", req.vcenter_host), ("vcenter_user", req.vcenter_user), ("vcenter_password", req.vcenter_password)] if not v]
        if missing:
            return {"status_code": 400, "output": "", "error": f"vCenter credentials not configured: {', '.join(missing)}. Set them in Settings → vCenter before running scripts.", "exit_code": 1}

    # Detect destructive verbs — block unless caller explicitly opts in
    write_matches = _WRITE_VERBS.findall(req.script)
    if write_matches and not getattr(req, "allow_writes", False):
        unique_verbs = sorted(set(v.lower() for v in write_matches))
        return {
            "status_code": 422,
            "output": "",
            "error": (
                f"Script contains potentially destructive verbs: {', '.join(unique_verbs)}. "
                "Set allow_writes=true in the request to confirm execution."
            ),
            "exit_code": 1,
            "write_verbs_detected": unique_verbs,
        }

    wrapped, extra_env = _wrap_ad_script(req) if req.skip_vcenter_connect else _wrap_script(req)
    logger.info(f"Running PowerCLI script ({len(wrapped)} chars) against {req.vcenter_host}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False) as f:
        f.write(wrapped)
        script_path = f.name

    # Merge credentials into subprocess environment (not the script file)
    proc_env = {**os.environ, **extra_env}

    async with _SEMAPHORE:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pwsh", "-NonInteractive", "-NoProfile", "-File", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SCRIPT_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                elapsed = round((time.monotonic() - start) * 1000)
                return {
                    "status_code": 504,
                    "output": "",
                    "error": f"Script timed out after {SCRIPT_TIMEOUT}s",
                    "exit_code": -1,
                    "elapsed_ms": elapsed,
                }

            exit_code = proc.returncode
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            elapsed = round((time.monotonic() - start) * 1000)

            logger.info(f"PowerCLI exit={exit_code} elapsed={elapsed}ms output={len(out)}chars")
            return {
                "status_code": 200 if exit_code == 0 else 500,
                "output": out,
                "error": err if err else None,
                "exit_code": exit_code,
                "elapsed_ms": elapsed,
            }
        except FileNotFoundError:
            elapsed = round((time.monotonic() - start) * 1000)
            return {
                "status_code": 503,
                "output": "",
                "error": "pwsh not found — PowerShell is not installed in this container",
                "exit_code": -1,
                "elapsed_ms": elapsed,
            }
        finally:
            os.unlink(script_path)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "powercli"}
