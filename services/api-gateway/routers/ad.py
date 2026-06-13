import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException
from metrics import ad_query_duration

router = APIRouter()

_DOMAIN_RE = re.compile(r"^[A-Za-z0-9.\-]+$")

_CONFIG_STORE = "http://config-store:8009"
_CACHE: dict = {}
_CACHE_TTL = 300  # 5 minutes


async def _get_cfg() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{_CONFIG_STORE}/config/raw")
        r.raise_for_status()
        return r.json()


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _CACHE[key] = {"ts": time.time(), "data": data}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_dt(val) -> datetime | None:
    """ldap3 returns datetime objects when schema is loaded, or raw FILETIME ints otherwise."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.year <= 1601:
            return None
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        else:
            val = val.astimezone(timezone.utc)
        return val
    try:
        v = int(val)
        if v <= 0 or v >= 9223372036854775807:
            return None
        return (datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=v // 10))
    except Exception:
        return None


def _is_locked(lockout_val) -> bool:
    """lockoutTime > 0 (or non-epoch datetime) means account is locked."""
    if lockout_val is None:
        return False
    if isinstance(lockout_val, datetime):
        return lockout_val.year > 1601
    try:
        return int(lockout_val) > 0
    except Exception:
        return False


def _days_ago(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return max(0, (_utcnow() - dt).days)


def _attr(entry, name: str, default=""):
    try:
        v = getattr(entry, name).value
        return v if v is not None else default
    except Exception:
        return default


def _ldap_overview(conn, base_dn: str, domain: str) -> dict:
    from ldap3 import SUBTREE

    stale_dt = _utcnow() - timedelta(days=90)

    conn.search(base_dn, "(&(objectClass=user)(objectCategory=person))",
                attributes=["sAMAccountName", "userAccountControl", "lockoutTime", "lastLogonTimestamp"],
                search_scope=SUBTREE, paged_size=2000)
    all_users = list(conn.entries)

    total_users = len(all_users)
    disabled = locked = stale = pwd_never = 0
    for u in all_users:
        uac = int(_attr(u, "userAccountControl") or 0)
        if uac & 0x2:
            disabled += 1
        if uac & 0x10000:
            pwd_never += 1
        if _is_locked(_attr(u, "lockoutTime", None)):
            locked += 1
        dt = _to_dt(_attr(u, "lastLogonTimestamp", None))
        if dt and dt < stale_dt:
            stale += 1

    conn.search(base_dn, "(objectClass=computer)",
                attributes=["name", "userAccountControl", "lastLogonTimestamp", "servicePrincipalName"],
                search_scope=SUBTREE, paged_size=2000)
    all_computers = list(conn.entries)
    total_computers = len(all_computers)
    stale_computers = 0
    for c in all_computers:
        dt = _to_dt(_attr(c, "lastLogonTimestamp", None))
        if dt and dt < stale_dt:
            stale_computers += 1

    conn.search(
        base_dn,
        "(&(objectClass=computer)(userAccountControl:1.2.840.113556.1.4.803:=8192))",
        attributes=["dNSHostName", "name"],
        search_scope=SUBTREE, paged_size=100,
    )
    dcs = [str(_attr(e, "dNSHostName") or _attr(e, "name")) for e in conn.entries]

    conn.search(
        base_dn,
        "(&(objectClass=computer)(servicePrincipalName=DNS/*))",
        attributes=["dNSHostName", "name"],
        search_scope=SUBTREE, paged_size=100,
    )
    dns_servers = [str(_attr(e, "dNSHostName") or _attr(e, "name")) for e in conn.entries]

    conn.search(
        base_dn,
        f"(&(objectClass=user)(objectCategory=person)(memberOf:1.2.840.113556.1.4.1941:=CN=Domain Admins,CN=Users,{base_dn}))",
        attributes=["sAMAccountName"],
        search_scope=SUBTREE, paged_size=200,
    )
    da_count = len(conn.entries)

    # Service accounts / Kerberoastable accounts: user objects with SPNs (not computer accounts)
    conn.search(
        base_dn,
        "(&(objectClass=user)(objectCategory=person)(servicePrincipalName=*))",
        attributes=["sAMAccountName", "servicePrincipalName"],
        search_scope=SUBTREE, paged_size=200,
    )
    svc_count = len(conn.entries)
    kerberoastable = [str(_attr(e, "sAMAccountName")) for e in conn.entries]

    return {
        "domain": domain,
        "total_users": total_users,
        "enabled_users": total_users - disabled,
        "disabled_users": disabled,
        "locked_users": locked,
        "stale_users": stale,
        "pwd_never_expires_users": pwd_never,
        "service_accounts": svc_count,
        "kerberoastable_count": len(kerberoastable),
        "kerberoastable_accounts": kerberoastable,
        "total_computers": total_computers,
        "stale_computers": stale_computers,
        "domain_admins_count": da_count,
        "domain_controllers": dcs,
        "dns_servers": dns_servers,
    }


def _ldap_users(conn, base_dn: str) -> dict:
    from ldap3 import SUBTREE

    stale_dt = _utcnow() - timedelta(days=90)

    conn.search(
        base_dn,
        "(&(objectClass=user)(objectCategory=person))",
        attributes=["sAMAccountName", "displayName", "mail", "department",
                    "userAccountControl", "lockoutTime", "lastLogonTimestamp",
                    "pwdLastSet", "whenCreated", "title"],
        search_scope=SUBTREE, paged_size=2000,
    )

    users = []
    for e in conn.entries:
        uac = int(_attr(e, "userAccountControl") or 0)
        disabled = bool(uac & 0x2)
        pwd_never = bool(uac & 0x10000)

        lt = _attr(e, "lockoutTime", None)
        locked = _is_locked(lt)
        locked_since = None
        if locked:
            lock_dt = _to_dt(lt)
            locked_since = lock_dt.isoformat() if lock_dt else None

        last_logon = _to_dt(_attr(e, "lastLogonTimestamp", None))
        pwd_set = _to_dt(_attr(e, "pwdLastSet", None))
        stale = last_logon is not None and last_logon < stale_dt

        users.append({
            "username": str(_attr(e, "sAMAccountName")),
            "display_name": str(_attr(e, "displayName")),
            "email": str(_attr(e, "mail")),
            "department": str(_attr(e, "department")),
            "title": str(_attr(e, "title")),
            "enabled": not disabled,
            "locked": bool(locked),
            "locked_since": locked_since,
            "stale": stale,
            "last_logon_days": _days_ago(last_logon),
            "password_never_expires": pwd_never,
            "password_last_set_days": _days_ago(pwd_set),
        })

    return {"users": users}


def _ldap_computers(conn, base_dn: str) -> dict:
    from ldap3 import SUBTREE

    stale_dt = _utcnow() - timedelta(days=90)

    conn.search(
        base_dn,
        "(objectClass=computer)",
        attributes=["name", "dNSHostName", "operatingSystem", "operatingSystemVersion",
                    "userAccountControl", "lastLogonTimestamp", "servicePrincipalName",
                    "distinguishedName"],
        search_scope=SUBTREE, paged_size=2000,
    )

    computers = []
    for e in conn.entries:
        uac = int(_attr(e, "userAccountControl") or 0)
        is_dc = bool(uac & 0x2000)
        disabled = bool(uac & 0x2)

        spns = []
        try:
            spns = e.servicePrincipalName.values or []
        except Exception:
            pass
        is_dns = any(str(s).startswith("DNS/") for s in spns)

        last_logon = _to_dt(_attr(e, "lastLogonTimestamp", None))
        stale = last_logon is not None and last_logon < stale_dt

        dn = str(_attr(e, "distinguishedName"))
        ou_parts = [p.split("=", 1)[1] for p in dn.split(",") if p.upper().startswith("OU=")]
        ou = " / ".join(reversed(ou_parts)) if ou_parts else "Computers"

        computers.append({
            "name": str(_attr(e, "name")),
            "dns_hostname": str(_attr(e, "dNSHostName")),
            "os": str(_attr(e, "operatingSystem")),
            "os_version": str(_attr(e, "operatingSystemVersion")),
            "enabled": not disabled,
            "is_dc": is_dc,
            "is_dns_server": is_dns,
            "stale": stale,
            "last_logon_days": _days_ago(last_logon),
            "ou": ou,
        })

    return {"computers": computers}


def _ldap_privileged(conn, base_dn: str) -> dict:
    from ldap3 import SUBTREE

    groups = [
        ("Domain Admins",     f"CN=Domain Admins,CN=Users,{base_dn}"),
        ("Enterprise Admins", f"CN=Enterprise Admins,CN=Users,{base_dn}"),
        ("Schema Admins",     f"CN=Schema Admins,CN=Users,{base_dn}"),
        ("Administrators",    f"CN=Administrators,CN=Builtin,{base_dn}"),
        ("DNS Admins",        f"CN=DnsAdmins,CN=Users,{base_dn}"),
        ("Account Operators", f"CN=Account Operators,CN=Builtin,{base_dn}"),
    ]

    out = []
    for grp_name, grp_dn in groups:
        try:
            conn.search(
                base_dn,
                f"(&(objectClass=user)(objectCategory=person)(memberOf:1.2.840.113556.1.4.1941:={grp_dn}))",
                attributes=["sAMAccountName", "displayName", "userAccountControl", "mail"],
                search_scope=SUBTREE, paged_size=500,
            )
            members = []
            for e in conn.entries:
                uac = int(_attr(e, "userAccountControl") or 0)
                members.append({
                    "username": str(_attr(e, "sAMAccountName")),
                    "display_name": str(_attr(e, "displayName")),
                    "email": str(_attr(e, "mail")),
                    "enabled": not bool(uac & 0x2),
                })
            out.append({"group": grp_name, "member_count": len(members), "members": members})
        except Exception:
            out.append({"group": grp_name, "member_count": 0, "members": []})

    return {"groups": out}


def _run_ldap_query(cfg: dict, query_type: str) -> dict:
    from ldap3 import Server, Connection, ALL, NTLM, SIMPLE, AUTO_BIND_NO_TLS

    host = cfg.get("ad_host", "")
    user = cfg.get("ad_user", "")
    password = cfg.get("ad_password", "")
    domain = cfg.get("ad_domain", "")

    if not host or not user or not password:
        raise ValueError("AD credentials not configured in Settings")

    if not domain and "@" in user:
        domain = user.split("@", 1)[1]

    if domain and not _DOMAIN_RE.match(domain):
        raise ValueError(f"AD domain contains invalid characters: {domain!r}")

    base_dn = ",".join(f"DC={p}" for p in domain.split(".")) if domain else ""

    use_tls = cfg.get("ad_use_tls", True)
    port = int(cfg.get("ad_port", 636 if use_tls else 389))
    if use_tls:
        from ldap3 import Tls
        import ssl
        tls_config = Tls(validate=ssl.CERT_NONE)  # trust enterprise CAs implicitly
        server = Server(host, port=port, use_ssl=True, tls=tls_config, get_info=ALL, connect_timeout=8)
        auto_bind = "NO_TLS"  # TLS handled at socket level by use_ssl=True
    else:
        server = Server(host, port=port, get_info=ALL, connect_timeout=8)
        auto_bind = AUTO_BIND_NO_TLS

    auth_method = NTLM if "\\" in user else SIMPLE

    conn = Connection(
        server, user=user, password=password,
        authentication=auth_method,
        auto_bind=auto_bind,
        receive_timeout=60,
    )
    if not conn.bind():
        raise ValueError(f"AD bind failed: {conn.result.get('description', 'unknown')}")

    if not base_dn:
        base_dn = server.info.other.get("defaultNamingContext", [""])[0]

    try:
        if query_type == "overview":
            return _ldap_overview(conn, base_dn, domain)
        elif query_type == "users":
            return _ldap_users(conn, base_dn)
        elif query_type == "computers":
            return _ldap_computers(conn, base_dn)
        elif query_type == "privileged":
            return _ldap_privileged(conn, base_dn)
        else:
            raise ValueError(f"Unknown query type: {query_type}")
    finally:
        conn.unbind()


async def _query(query_type: str) -> dict:
    cached = _cache_get(query_type)
    if cached is not None:
        return cached
    cfg = await _get_cfg()
    loop = asyncio.get_event_loop()
    t0 = time.time()
    try:
        data = await loop.run_in_executor(None, _run_ldap_query, cfg, query_type)
    except ImportError:
        raise HTTPException(503, detail="ldap3 library not installed in this container")
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(503, detail=f"AD query failed: {e}")
    finally:
        ad_query_duration.labels(query_type=query_type).observe(time.time() - t0)
    _cache_set(query_type, data)
    return data


@router.get("/api/v1/ad/overview")
async def ad_overview():
    return await _query("overview")


@router.get("/api/v1/ad/stale-privileged")
async def ad_stale_privileged():
    """Cross-reference privileged group members with user last-logon data."""
    priv_data, user_data = await asyncio.gather(_query("privileged"), _query("users"))
    user_map = {u["username"]: u for u in user_data.get("users", [])}
    stale = []
    for grp in priv_data.get("groups", []):
        for m in grp.get("members", []):
            u = user_map.get(m["username"])
            days = u["last_logon_days"] if u else None
            if days is None or days >= 90:
                stale.append({
                    "group": grp["group"],
                    "username": m["username"],
                    "display_name": m["display_name"],
                    "email": m["email"],
                    "enabled": m["enabled"],
                    "last_logon_days": days,
                })
    stale.sort(key=lambda x: (x["last_logon_days"] is None, x.get("last_logon_days", 0)), reverse=True)
    return {"stale_privileged": stale, "count": len(stale)}


@router.get("/api/v1/ad/users")
async def ad_users():
    return await _query("users")


@router.get("/api/v1/ad/computers")
async def ad_computers():
    return await _query("computers")


@router.get("/api/v1/ad/privileged")
async def ad_privileged():
    return await _query("privileged")


# ── CSV export endpoints ─────────────────────────────────────────────────────

def _to_csv(rows: list[dict], fieldnames: list[str]) -> str:
    import csv, io
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


@router.get("/api/v1/ad/users.csv")
async def ad_users_csv():
    from fastapi.responses import Response as _Resp
    data = await _query("users")
    fields = ["username", "display_name", "email", "department", "title",
              "enabled", "locked", "stale", "last_logon_days",
              "password_never_expires", "password_last_set_days"]
    csv_body = _to_csv(data.get("users", []), fields)
    return _Resp(content=csv_body, media_type="text/csv",
                 headers={"Content-Disposition": "attachment; filename=ad_users.csv"})


@router.get("/api/v1/ad/computers.csv")
async def ad_computers_csv():
    from fastapi.responses import Response as _Resp
    data = await _query("computers")
    fields = ["name", "dns_hostname", "os", "os_version", "enabled",
              "is_dc", "is_dns_server", "stale", "last_logon_days", "ou"]
    csv_body = _to_csv(data.get("computers", []), fields)
    return _Resp(content=csv_body, media_type="text/csv",
                 headers={"Content-Disposition": "attachment; filename=ad_computers.csv"})


@router.get("/api/v1/ad/privileged.csv")
async def ad_privileged_csv():
    from fastapi.responses import Response as _Resp
    data = await _query("privileged")
    rows = []
    for g in data.get("groups", []):
        for m in g.get("members", []):
            rows.append({**m, "group": g["group"]})
    fields = ["group", "username", "display_name", "email", "enabled"]
    csv_body = _to_csv(rows, fields)
    return _Resp(content=csv_body, media_type="text/csv",
                 headers={"Content-Disposition": "attachment; filename=ad_privileged.csv"})


@router.post("/api/v1/ad/refresh")
async def ad_refresh():
    _CACHE.clear()
    return {"ok": True, "message": "AD cache cleared — next request will re-query"}
