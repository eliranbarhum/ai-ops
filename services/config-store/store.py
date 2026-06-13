"""
Persistent encrypted config store.
Reads/writes a single JSON blob encrypted with Fernet at /data/mco-config.enc.
All field values are stored encrypted individually so the file is opaque even
if partially read.

Bootstrapping: on every load(), any field that is still empty is filled from
the corresponding environment variable (injected via the mco-secrets
Kubernetes Secret). This means credentials are always available from day one
without requiring the user to re-enter them after pod restarts or redeployments.
"""

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from crypto import encrypt, decrypt, CONFIG_FILE


def _atomic_write(path: Path, data) -> None:
    """Write JSON atomically: write to a sibling temp file, then os.replace().
    Prevents torn writes and read-modify-write races from corrupting JSON files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp~")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise

_PG_URL = os.getenv("POSTGRES_URL", "")
_pg_pool = None


async def _get_pool():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    if not _PG_URL:
        return None
    try:
        import asyncpg
        _pg_pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=5, command_timeout=10)
        logging.getLogger("config-store.store").info("asyncpg pool connected")
    except Exception as e:
        logging.getLogger("config-store.store").warning("PostgreSQL unavailable: %s", e)
        return None
    return _pg_pool

SCANS_FILE           = CONFIG_FILE.parent / "mco-scans.json"
WORKSPACE_FILE       = CONFIG_FILE.parent / "mco-workspace.json"
CONVERSATIONS_FILE   = CONFIG_FILE.parent / "mco-conversations.json"
PINNED_CMDS_FILE     = CONFIG_FILE.parent / "mco-pinned-cmds.json"
ALERT_CHANNELS_FILE  = CONFIG_FILE.parent / "mco-alert-channels.json"
ALERT_RULES_FILE     = CONFIG_FILE.parent / "mco-alert-rules.json"
MAINT_WINDOWS_FILE   = CONFIG_FILE.parent / "mco-maint-windows.json"

logger = logging.getLogger("config-store.store")

DEFAULT_CONFIG = {
    "vcenter_host": "",
    "vcenter_user": "administrator@vsphere.local",
    "vcenter_password": "",
    "vcenter_verify_ssl": False,
    "vrops_host": "",
    "vrops_user": "admin",
    "vrops_password": "",
    "vrops_verify_ssl": False,
    "loginsight_host": "",
    "loginsight_user": "admin",
    "loginsight_password": "",
    "loginsight_verify_ssl": False,
    "sddc_host": "",
    "sddc_user": "administrator@vsphere.local",
    "sddc_password": "",
    "sddc_verify_ssl": False,
    "nsx_host": "",
    "nsx_user": "admin",
    "nsx_password": "",
    "nsx_verify_ssl": False,
    "llm_provider": "anthropic",
    "anthropic_api_key": "",
    "anthropic_model": "claude-sonnet-4-6",
    "openai_api_key": "",
    "openai_model": "gpt-4o",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
    "vllm_url": "http://vllm-server:11434",
    "vllm_model": "qwen2.5:14b",
    "vcf_target_version": "9.0",
    # Active Directory integration
    "ad_host": "",
    "ad_user": "",
    "ad_password": "",
    "ad_domain": "",
    # MCP AI Agent — fully independent cloud LLM (own provider, own keys, own models)
    "agent_llm_provider": "anthropic",
    "agent_anthropic_api_key": "",
    "agent_anthropic_model": "claude-sonnet-4-6",
    "agent_openai_api_key": "",
    "agent_openai_model": "gpt-4o",
    "agent_gemini_api_key": "",
    "agent_gemini_model": "gemini-2.0-flash",
    # Local Ollama (vllm-server in-cluster)
    "agent_ollama_url": "http://vllm-server:11434",
    "agent_ollama_model": "qwen2.5-coder:7b",
}

_SENSITIVE = {
    "vcenter_password", "vrops_password", "loginsight_password", "sddc_password",
    "nsx_password",
    "anthropic_api_key", "openai_api_key", "gemini_api_key",
    "agent_anthropic_api_key", "agent_openai_api_key", "agent_gemini_api_key",
    "ad_password",
}

# Map config field → environment variable name (set via mco-secrets secretRef).
# Any field that is empty after loading from the PVC file is filled from here,
# so credentials survive pod restarts, rollbacks, and fresh deployments.
_ENV_SEED = {
    "vcenter_host":       "VCENTER_HOST",
    "vcenter_user":       "VCENTER_USER",
    "vcenter_password":   "VCENTER_PASSWORD",
    "vrops_host":         "VROPS_HOST",
    "vrops_user":         "VROPS_USER",
    "vrops_password":     "VROPS_PASSWORD",
    "loginsight_host":    "LOGINSIGHT_HOST",
    "loginsight_user":    "LOGINSIGHT_USER",
    "loginsight_password": "LOGINSIGHT_PASSWORD",
    "nsx_host":           "NSX_HOST",
    "nsx_user":           "NSX_USER",
    "nsx_password":       "NSX_PASSWORD",
    "anthropic_api_key":  "ANTHROPIC_API_KEY",
}


def _seed_from_env(result: dict) -> dict:
    """Fill any empty field from the corresponding env var (mco-secrets)."""
    for field, env_var in _ENV_SEED.items():
        if not result.get(field):
            val = os.getenv(env_var, "")
            if val:
                result[field] = val
                logger.debug("Seeded %s from environment", field)
    return result


def load() -> dict:
    if not CONFIG_FILE.exists():
        return _seed_from_env(dict(DEFAULT_CONFIG))
    try:
        raw = CONFIG_FILE.read_text()
        encrypted_map: dict = json.loads(raw)
        result = {}
        for k, v in encrypted_map.items():
            try:
                result[k] = decrypt(v) if k in _SENSITIVE else v
            except Exception:
                result[k] = ""
        # Fill any missing keys from defaults (handles schema additions)
        for k, default_v in DEFAULT_CONFIG.items():
            result.setdefault(k, default_v)
        return _seed_from_env(result)
    except Exception as e:
        logger.error("Failed to load config: %s — returning defaults", e)
        return _seed_from_env(dict(DEFAULT_CONFIG))


def save(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    serializable = {}
    for k, v in config.items():
        serializable[k] = encrypt(str(v)) if k in _SENSITIVE else v
    _atomic_write(CONFIG_FILE, serializable)
    CONFIG_FILE.chmod(0o600)
    logger.info("Config saved to %s", CONFIG_FILE)


def load_scans() -> list:
    try:
        if SCANS_FILE.exists():
            return json.loads(SCANS_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load scans: %s", e)
    return []


def save_scan(target: str, query: str, result: dict) -> dict:
    scans = load_scans()
    scan = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "query": query,
        "result": result,
    }
    scans.insert(0, scan)
    scans = scans[:200]  # keep last 200
    SCANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(SCANS_FILE, scans)
    return scan


def delete_scan(scan_id: str) -> bool:
    scans = load_scans()
    filtered = [s for s in scans if s.get("id") != scan_id]
    if len(filtered) == len(scans):
        return False
    _atomic_write(SCANS_FILE, filtered)
    return True


def load_workspace() -> list:
    try:
        if WORKSPACE_FILE.exists():
            return json.loads(WORKSPACE_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load workspace history: %s", e)
    return []


def save_workspace_entry(description: str, spec: dict, result: dict) -> dict:
    entries = load_workspace()
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "description": description,
        "spec": spec,
        "result": result,
    }
    entries.insert(0, entry)
    entries = entries[:500]
    WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(WORKSPACE_FILE, entries)
    return entry


def delete_workspace_entry(entry_id: str) -> bool:
    entries = load_workspace()
    filtered = [e for e in entries if e.get("id") != entry_id]
    if len(filtered) == len(entries):
        return False
    _atomic_write(WORKSPACE_FILE, filtered)
    return True


async def load_conversations() -> list:
    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, title, provider, messages, created_at, updated_at "
                    "FROM agent_conversations ORDER BY updated_at DESC LIMIT 200"
                )
            result = []
            for row in rows:
                r = dict(row)
                r["messages"] = json.loads(r["messages"] or "[]")
                r["created_at"] = r["created_at"].isoformat() if r["created_at"] else ""
                r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else ""
                result.append(r)
            return result
        except Exception as e:
            logger.warning("PostgreSQL conversations read failed, using file: %s", e)

    try:
        if CONVERSATIONS_FILE.exists():
            return json.loads(CONVERSATIONS_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load conversations: %s", e)
    return []


async def save_conversation(conversation_id: str, title: str, messages: list, provider: str) -> dict:
    now = datetime.now(timezone.utc)
    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO agent_conversations (id, title, provider, messages, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       ON CONFLICT (id) DO UPDATE SET
                         title = EXCLUDED.title,
                         messages = EXCLUDED.messages,
                         updated_at = EXCLUDED.updated_at
                       RETURNING id, title, provider, messages, created_at, updated_at""",
                    conversation_id, title, provider, json.dumps(messages), now, now,
                )
            r = dict(row)
            r["messages"] = json.loads(r["messages"] or "[]")
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else ""
            r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else ""
            return r
        except Exception as e:
            logger.warning("PostgreSQL conversation save failed, using file: %s", e)

    # File fallback
    convs = []
    try:
        if CONVERSATIONS_FILE.exists():
            convs = json.loads(CONVERSATIONS_FILE.read_text())
    except Exception:
        pass
    existing = next((c for c in convs if c.get("id") == conversation_id), None)
    now_iso = now.isoformat()
    if existing:
        existing["messages"] = messages
        existing["updated_at"] = now_iso
        existing["title"] = title
        conv = existing
    else:
        conv = {
            "id": conversation_id, "title": title, "provider": provider,
            "created_at": now_iso, "updated_at": now_iso, "messages": messages,
        }
        convs.insert(0, conv)
    convs = convs[:200]
    CONVERSATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(CONVERSATIONS_FILE, convs)
    return conv


async def delete_conversation(conversation_id: str) -> bool:
    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM agent_conversations WHERE id = $1", conversation_id
                )
                # asyncpg returns "DELETE N" string
                if int(result.split()[-1]) > 0:
                    return True
        except Exception as e:
            logger.warning("PostgreSQL conversation delete failed, using file: %s", e)

    # File fallback
    try:
        if CONVERSATIONS_FILE.exists():
            convs = json.loads(CONVERSATIONS_FILE.read_text())
            filtered = [c for c in convs if c.get("id") != conversation_id]
            if len(filtered) == len(convs):
                return False
            _atomic_write(CONVERSATIONS_FILE, filtered)
            return True
    except Exception as e:
        logger.error("Failed to delete conversation: %s", e)
    return False


def load_pinned_commands(user_id: str) -> list:
    try:
        if PINNED_CMDS_FILE.exists():
            data = json.loads(PINNED_CMDS_FILE.read_text())
            return data.get(user_id, [])
    except Exception as e:
        logger.error("Failed to load pinned commands: %s", e)
    return []


def save_pinned_command(user_id: str, command: str, label: str) -> dict:
    try:
        data = {}
        if PINNED_CMDS_FILE.exists():
            data = json.loads(PINNED_CMDS_FILE.read_text())
        user_cmds = data.get(user_id, [])
        import uuid as _uuid
        entry = {"id": str(_uuid.uuid4()), "command": command, "label": label}
        user_cmds.append(entry)
        data[user_id] = user_cmds[-50:]  # keep last 50 per user
        _atomic_write(PINNED_CMDS_FILE, data)
        return entry
    except Exception as e:
        logger.error("Failed to save pinned command: %s", e)
        return {}


def delete_pinned_command(user_id: str, cmd_id: str) -> bool:
    try:
        if not PINNED_CMDS_FILE.exists():
            return False
        data = json.loads(PINNED_CMDS_FILE.read_text())
        user_cmds = data.get(user_id, [])
        filtered = [c for c in user_cmds if c.get("id") != cmd_id]
        if len(filtered) == len(user_cmds):
            return False
        data[user_id] = filtered
        _atomic_write(PINNED_CMDS_FILE, data)
        return True
    except Exception as e:
        logger.error("Failed to delete pinned command: %s", e)
    return False


def mask(config: dict) -> dict:
    """Return config with sensitive fields masked for API responses."""
    out = dict(config)
    for k in _SENSITIVE:
        if out.get(k):
            out[k] = "••••••••"
    return out


# ---------------------------------------------------------------------------
# Alert channels (F5)
# ---------------------------------------------------------------------------

def load_alert_channels() -> list:
    try:
        if ALERT_CHANNELS_FILE.exists():
            return json.loads(ALERT_CHANNELS_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load alert channels: %s", e)
    return []


def save_alert_channel(name: str, channel_type: str, config: dict) -> dict:
    channels = load_alert_channels()
    channel = {
        "id": str(uuid.uuid4()),
        "name": name,
        "type": channel_type,
        "config": config,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    channels.append(channel)
    ALERT_CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(ALERT_CHANNELS_FILE, channels)
    return channel


def delete_alert_channel(channel_id: str) -> bool:
    channels = load_alert_channels()
    filtered = [c for c in channels if c.get("id") != channel_id]
    if len(filtered) == len(channels):
        return False
    _atomic_write(ALERT_CHANNELS_FILE, filtered)
    return True


# ---------------------------------------------------------------------------
# Alert rules (F5)
# ---------------------------------------------------------------------------

def load_alert_rules() -> list:
    try:
        if ALERT_RULES_FILE.exists():
            return json.loads(ALERT_RULES_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load alert rules: %s", e)
    return []


def save_alert_rule(name: str, event_type: str, condition: dict,
                    channel_ids: list, enabled: bool = True) -> dict:
    rules = load_alert_rules()
    rule = {
        "id": str(uuid.uuid4()),
        "name": name,
        "event_type": event_type,
        "condition": condition,
        "channel_ids": channel_ids,
        "enabled": enabled,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rules.append(rule)
    ALERT_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(ALERT_RULES_FILE, rules)
    return rule


def update_alert_rule(rule_id: str, updates: dict) -> bool:
    rules = load_alert_rules()
    for r in rules:
        if r.get("id") == rule_id:
            r.update(updates)
            _atomic_write(ALERT_RULES_FILE, rules)
            return True
    return False


def delete_alert_rule(rule_id: str) -> bool:
    rules = load_alert_rules()
    filtered = [r for r in rules if r.get("id") != rule_id]
    if len(filtered) == len(rules):
        return False
    _atomic_write(ALERT_RULES_FILE, filtered)
    return True


# ---------------------------------------------------------------------------
# Maintenance windows (F2)
# ---------------------------------------------------------------------------

def load_maintenance_windows() -> list:
    try:
        if MAINT_WINDOWS_FILE.exists():
            return json.loads(MAINT_WINDOWS_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load maintenance windows: %s", e)
    return []


def save_maintenance_window(name: str, day_of_week: int, start_hour: int,
                             start_minute: int, duration_minutes: int,
                             enabled: bool = True) -> dict:
    windows = load_maintenance_windows()
    window = {
        "id": str(uuid.uuid4()),
        "name": name,
        "day_of_week": day_of_week,   # 0=Mon … 6=Sun
        "start_hour": start_hour,
        "start_minute": start_minute,
        "duration_minutes": duration_minutes,
        "enabled": enabled,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    windows.append(window)
    MAINT_WINDOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(MAINT_WINDOWS_FILE, windows)
    return window


def update_maintenance_window(window_id: str, updates: dict) -> bool:
    windows = load_maintenance_windows()
    for w in windows:
        if w.get("id") == window_id:
            w.update(updates)
            _atomic_write(MAINT_WINDOWS_FILE, windows)
            return True
    return False


def delete_maintenance_window(window_id: str) -> bool:
    windows = load_maintenance_windows()
    filtered = [w for w in windows if w.get("id") != window_id]
    if len(filtered) == len(windows):
        return False
    _atomic_write(MAINT_WINDOWS_FILE, filtered)
    return True
