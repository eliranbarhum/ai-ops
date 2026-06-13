"""
Sync AD settings from config-store into the live Dex OIDC config secret,
then restart Dex so it picks up the new LDAP connector.

Uses the in-cluster service account token — no extra Python packages needed.
"""

import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger("api-gateway.dex-sync")

_TOKEN_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_CA_FILE    = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
_NS_FILE    = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")

_K8S_BASE   = "https://kubernetes.default.svc"
_DEX_SECRET = "dex-config"
_DEX_DEPLOY = "dex"

_AD_FIELDS  = {"ad_host", "ad_user", "ad_password", "ad_domain"}


def _k8s_headers() -> dict:
    token = _TOKEN_FILE.read_text().strip()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _namespace() -> str:
    return _NS_FILE.read_text().strip() if _NS_FILE.exists() else os.getenv("NAMESPACE", "vcf-ai-ops")


def _build_ldap_connector(ad: dict) -> dict | None:
    host   = (ad.get("ad_host") or "").strip()
    user   = (ad.get("ad_user") or "").strip()
    pw     = (ad.get("ad_password") or "").strip()
    domain = (ad.get("ad_domain") or "").strip()

    if not all([host, user, pw, domain]):
        return None

    dc_parts = ",".join(f"DC={p}" for p in domain.split("."))

    return {
        "type": "ldap",
        "id":   "ldap",
        "name": "Active Directory",
        "config": {
            "host":           f"{host}:389",
            "insecureNoSSL":  True,
            "bindDN":         user,
            "bindPW":         pw,
            "usernamePrompt": "AD Username",
            "userSearch": {
                "baseDN":    dc_parts,
                "filter":    "(objectClass=person)",
                "username":  "sAMAccountName",
                "idAttr":    "sAMAccountName",
                "emailAttr": "mail",
                "nameAttr":  "displayName",
            },
            "groupSearch": {
                "baseDN":     dc_parts,
                "filter":     "(objectClass=group)",
                "userAttr":   "DN",
                "groupAttr":  "member",
                "nameAttr":   "cn",
            },
        },
    }


async def sync_dex_config(new_cfg: dict) -> dict:
    """
    Called after config-store is updated. If AD fields changed, patch the
    dex-config secret and restart the dex deployment.
    Returns {"changed": bool, "sso_active": bool, "error": str|None}.
    """
    if not _TOKEN_FILE.exists():
        return {"changed": False, "sso_active": False, "error": "not running in-cluster"}

    ns      = _namespace()
    headers = _k8s_headers()
    verify  = str(_CA_FILE) if _CA_FILE.exists() else False

    try:
        async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
            # ── 1. Fetch current dex-config secret ──────────────────────────
            r = await client.get(
                f"{_K8S_BASE}/api/v1/namespaces/{ns}/secrets/{_DEX_SECRET}",
                headers=headers,
            )
            r.raise_for_status()
            secret = r.json()

            raw_b64 = secret["data"].get("config.yaml", "")
            current_yaml = base64.b64decode(raw_b64).decode() if raw_b64 else ""
            dex_cfg = yaml.safe_load(current_yaml) or {}

            # ── 2. Build new connector block ─────────────────────────────────
            connector = _build_ldap_connector(new_cfg)
            sso_active = connector is not None

            old_connectors = dex_cfg.get("connectors", [])
            new_connectors = [connector] if connector else []

            if old_connectors == new_connectors:
                return {"changed": False, "sso_active": sso_active, "error": None}

            # ── 3. Patch the secret ──────────────────────────────────────────
            if connector:
                dex_cfg["connectors"] = new_connectors
            else:
                dex_cfg.pop("connectors", None)

            updated_yaml = yaml.dump(dex_cfg, default_flow_style=False, allow_unicode=True)
            patch = {"data": {"config.yaml": base64.b64encode(updated_yaml.encode()).decode()}}

            r = await client.patch(
                f"{_K8S_BASE}/api/v1/namespaces/{ns}/secrets/{_DEX_SECRET}",
                headers={**headers, "Content-Type": "application/merge-patch+json"},
                content=json.dumps(patch),
            )
            r.raise_for_status()

            # ── 4. Restart Dex deployment ────────────────────────────────────
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            restart_patch = {
                "spec": {"template": {"metadata": {"annotations": {
                    "kubectl.kubernetes.io/restartedAt": ts
                }}}}
            }
            r = await client.patch(
                f"{_K8S_BASE}/apis/apps/v1/namespaces/{ns}/deployments/{_DEX_DEPLOY}",
                headers={**headers, "Content-Type": "application/merge-patch+json"},
                content=json.dumps(restart_patch),
            )
            r.raise_for_status()

            action = "activated" if sso_active else "deactivated"
            logger.info("dex-sync: AD SSO %s — connector updated, Dex restarting", action)
            return {"changed": True, "sso_active": sso_active, "error": None}

    except Exception as e:
        logger.error("dex-sync: failed to sync Dex config: %s", e)
        return {"changed": False, "sso_active": False, "error": str(e)}
