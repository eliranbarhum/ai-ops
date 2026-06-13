"""
Authenticated deep-scan via SSH/WinRM.
Runs after an initial nmap scan to collect OS-level intelligence.
"""
import asyncio
import json
import logging

logger = logging.getLogger("deep_scanner")

# Commands to run over SSH — returns dict[label → output]
_SSH_COMMANDS = {
    "system":    "uname -a 2>/dev/null; echo '---'; cat /etc/os-release 2>/dev/null || cat /etc/redhat-release 2>/dev/null || echo 'OS info unavailable'",
    "users":     "getent passwd 2>/dev/null | awk -F: '$3>=1000 || $1==\"root\" {print $1\":\"$3\":\"$6\":\"$7}' | head -30",
    "sudoers":   "sudo -l -n 2>/dev/null || echo 'sudo check requires tty or NOPASSWD'",
    "listening": "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || echo 'No ss/netstat'",
    "processes": "ps aux --no-header 2>/dev/null | head -25",
    "disk":      "df -h 2>/dev/null",
    "network":   "ip addr 2>/dev/null | grep -E '(inet |inet6 |^[0-9])' | head -20",
    "packages":  "(dpkg -l 2>/dev/null | awk 'NR>5{print $2}' | head -40) || (rpm -qa 2>/dev/null | head -40) || echo 'No package manager found'",
    "crontabs":  "for u in $(cut -d: -f1 /etc/passwd); do crontab -u $u -l 2>/dev/null && echo \"--- $u ---\"; done | head -40",
    "env":       "env 2>/dev/null | grep -iE '(path|home|user|shell|lang)' | head -20",
}


async def run_ssh_deep_scan(ip: str, username: str, password: str | None,
                             ssh_key: str | None, sudo_password: str | None) -> dict:
    """
    SSH into host and run enumeration commands.
    Returns {status, results{label: output}, error}.
    """
    try:
        import asyncssh  # type: ignore
    except ImportError:
        return {"status": "error", "error": "asyncssh not installed", "results": {}}

    conn_kwargs: dict = {
        "host": ip,
        "username": username,
        "known_hosts": None,
        "connect_timeout": 10,
    }

    if password:
        conn_kwargs["password"] = password
    if ssh_key:
        import tempfile, os
        kf = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        kf.write(ssh_key)
        kf.close()
        os.chmod(kf.name, 0o600)
        conn_kwargs["client_keys"] = [kf.name]

    try:
        async with asyncssh.connect(**conn_kwargs) as conn:
            results: dict[str, str] = {}
            for label, cmd in _SSH_COMMANDS.items():
                # Prefix with sudo if sudo_password provided
                if sudo_password and label in ("sudoers", "crontabs"):
                    full_cmd = f"echo '{sudo_password}' | sudo -S bash -c {repr(cmd)} 2>/dev/null || {cmd}"
                else:
                    full_cmd = cmd
                try:
                    r = await asyncio.wait_for(conn.run(full_cmd, check=False), timeout=15)
                    results[label] = (r.stdout or "").strip()
                except asyncio.TimeoutError:
                    results[label] = "TIMEOUT"
                except Exception as e:
                    results[label] = f"ERROR: {e}"

        return {"status": "done", "results": results}

    except Exception as e:
        logger.warning(f"SSH deep scan {ip} failed: {e}")
        return {"status": "failed", "error": str(e), "results": {}}

    finally:
        # Clean up temp key file if created
        kf_name = conn_kwargs.get("client_keys", [None])[0] if conn_kwargs.get("client_keys") else None
        if kf_name and kf_name != ssh_key:
            import os
            try:
                os.unlink(kf_name)
            except OSError:
                pass
