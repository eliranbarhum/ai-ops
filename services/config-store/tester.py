"""
Connection testers — each returns {"ok": bool, "message": str}.
Called by the /config/test/* endpoints after credentials are saved.
"""

import asyncio
import httpx
import logging

logger = logging.getLogger("config-store.tester")


async def test_vcenter(cfg: dict) -> dict:
    host = cfg.get("vcenter_host", "")
    user = cfg.get("vcenter_user", "")
    password = cfg.get("vcenter_password", "")
    verify = cfg.get("vcenter_verify_ssl", False)
    if not host or not password:
        return {"ok": False, "message": "Host or password not configured"}
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0) as client:
            resp = await client.post(
                f"https://{host}/api/session",
                auth=(user, password),
            )
        if resp.status_code == 201:
            return {"ok": True, "message": f"Connected to vCenter {host}"}
        return {"ok": False, "message": f"vCenter returned HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Cannot reach vCenter at {host}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_vrops(cfg: dict) -> dict:
    host = cfg.get("vrops_host", "")
    user = cfg.get("vrops_user", "admin")
    password = cfg.get("vrops_password", "")
    verify = cfg.get("vrops_verify_ssl", False)
    if not host or not password:
        return {"ok": False, "message": "Host or password not configured"}
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0) as client:
            resp = await client.post(
                f"https://{host}/suite-api/api/auth/token/acquire",
                json={"username": user, "password": password, "authSource": "LOCAL"},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        if resp.status_code == 200:
            return {"ok": True, "message": f"Connected to VCF Operations at {host}"}
        return {"ok": False, "message": f"VCF Operations returned HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Cannot reach VCF Operations at {host}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_ollama(cfg: dict) -> dict:
    url = cfg.get("vllm_url", "http://vllm-server:11434").rstrip("/")
    model = cfg.get("vllm_model", "smollm2:1.7b")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": "hi", "stream": False},
            )
        if resp.status_code == 200:
            reply = resp.json().get("response", "").strip()
            short = reply[:80] + ("…" if len(reply) > 80 else "")
            return {"ok": True, "message": f"Model responded: \"{short}\""}
        return {"ok": False, "message": f"Ollama returned HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Cannot reach Ollama at {url} — is it deployed?"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Ollama did not respond within 120 s — model may still be loading"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_agent_ollama(cfg: dict) -> dict:
    url = cfg.get("agent_ollama_url", "http://vllm-server:11434").rstrip("/")
    model = cfg.get("agent_ollama_model", "qwen2.5-coder:7b")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": "hi", "stream": False},
            )
        if resp.status_code == 200:
            reply = resp.json().get("response", "").strip()
            short = reply[:80] + ("…" if len(reply) > 80 else "")
            return {"ok": True, "message": f"Model responded: \"{short}\""}
        return {"ok": False, "message": f"Ollama returned HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Cannot reach Ollama at {url} — is it deployed?"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Ollama did not respond within 120 s — model may still be loading"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_sddc(cfg: dict) -> dict:
    host = cfg.get("sddc_host", "")
    user = cfg.get("sddc_user", "administrator@vsphere.local")
    password = cfg.get("sddc_password", "")
    verify = cfg.get("sddc_verify_ssl", False)
    if not host or not password:
        return {"ok": False, "message": "Host or password not configured"}
    try:
        async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
            resp = await client.post(
                f"https://{host}/v1/tokens",
                json={"username": user, "password": password},
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code == 200:
            return {"ok": True, "message": f"Connected to SDDC Manager at {host}"}
        return {"ok": False, "message": f"SDDC Manager returned HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Cannot reach SDDC Manager at {host}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_nsx(cfg: dict) -> dict:
    host = cfg.get("nsx_host", "")
    user = cfg.get("nsx_user", "admin")
    password = cfg.get("nsx_password", "")
    verify = cfg.get("nsx_verify_ssl", False)
    if not host or not password:
        return {"ok": False, "message": "Host or password not configured"}
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0) as client:
            resp = await client.get(
                f"https://{host}/api/v1/node",
                auth=(user, password),
            )
        if resp.status_code == 200:
            data = resp.json()
            version = data.get("product_version", "")
            return {"ok": True, "message": f"Connected to NSX Manager {host}" + (f" ({version})" if version else "")}
        return {"ok": False, "message": f"NSX Manager returned HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Cannot reach NSX Manager at {host}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_anthropic(cfg: dict) -> dict:
    key = cfg.get("anthropic_api_key", "")
    model = cfg.get("anthropic_model", "claude-sonnet-4-6")
    if not key:
        return {"ok": False, "message": "Anthropic API key not configured"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": "Hi"}]},
            )
        if resp.status_code == 200:
            reply = resp.json().get("content", [{}])[0].get("text", "").strip()
            short = reply[:80] + ("…" if len(reply) > 80 else "")
            return {"ok": True, "message": f"Model responded: \"{short}\""}
        data = resp.json()
        err = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
        return {"ok": False, "message": err}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_openai(cfg: dict) -> dict:
    key = cfg.get("openai_api_key", "")
    model = cfg.get("openai_model", "gpt-4o")
    if not key:
        return {"ok": False, "message": "OpenAI API key not configured"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": "Hi"}]},
            )
        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            short = reply[:80] + ("…" if len(reply) > 80 else "")
            return {"ok": True, "message": f"Model responded: \"{short}\""}
        data = resp.json()
        err = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
        return {"ok": False, "message": err}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_gemini(cfg: dict) -> dict:
    key = cfg.get("gemini_api_key", "")
    model = cfg.get("gemini_model", "gemini-2.0-flash")
    if not key:
        return {"ok": False, "message": "Gemini API key not configured"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": "Hi"}]}]},
            )
        if resp.status_code == 200:
            reply = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            short = reply[:80] + ("…" if len(reply) > 80 else "")
            return {"ok": True, "message": f"Model responded: \"{short}\""}
        data = resp.json()
        err = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
        return {"ok": False, "message": err}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def test_ad(cfg: dict) -> dict:
    """
    Bind to Active Directory and run a real LDAP search to verify query access.
    Supports both UPN format (user@domain.local) and sAMAccountName (DOMAIN\\user).
    """
    host   = cfg.get("ad_host", "")
    user   = cfg.get("ad_user", "")
    password = cfg.get("ad_password", "")
    domain = cfg.get("ad_domain", "")
    if not host or not user or not password:
        return {"ok": False, "message": "Host, username, and password are required"}

    # Derive base DN from domain (example.com → DC=example,DC=com)
    if not domain and "@" in user:
        domain = user.split("@", 1)[1]
    base_dn = ",".join(f"DC={part}" for part in domain.split(".")) if domain else ""

    def _ldap_test() -> dict:
        try:
            from ldap3 import Server, Connection, ALL, NTLM, SIMPLE, AUTO_BIND_NO_TLS, Tls
            import ssl as _ssl
        except ImportError:
            return {"ok": False, "message": "ldap3 library not installed"}

        server = Server(host, port=389, get_info=ALL, connect_timeout=8)
        # Choose auth method based on username format
        if "\\" in user:
            auth_method = NTLM
        else:
            auth_method = SIMPLE

        try:
            conn = Connection(
                server, user=user, password=password,
                authentication=auth_method,
                auto_bind=AUTO_BIND_NO_TLS,
                receive_timeout=10,
            )
            if not conn.bind():
                return {"ok": False, "message": f"Bind failed: {conn.result.get('description', 'unknown error')}"}
        except Exception as e:
            err = str(e)
            if "invalidCredentials" in err or "52e" in err:
                return {"ok": False, "message": "Invalid credentials (LDAP 49 — wrong username or password)"}
            if "Connection refused" in err or "timed out" in err.lower():
                return {"ok": False, "message": f"Cannot reach AD at {host}:389"}
            return {"ok": False, "message": f"Bind error: {err}"}

        # Real query: search for up to 5 user objects in the base DN
        search_base = base_dn or server.info.other.get("defaultNamingContext", [""])[0]
        conn.search(
            search_base=search_base,
            search_filter="(objectClass=user)",
            attributes=["sAMAccountName", "displayName"],
            size_limit=5,
        )
        count = len(conn.entries)
        sample = conn.entries[0]["sAMAccountName"].value if count > 0 else None
        conn.unbind()

        msg = f"Connected to {host} — found {count} user object(s)"
        if sample:
            msg += f" (e.g. {sample})"
        return {"ok": True, "message": msg}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _ldap_test)
