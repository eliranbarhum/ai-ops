#!/usr/bin/env python3
"""Refresh vSphere Supervisor kubeconfig token stored in the kubectl-config K8s secret.

Calls the WCP login REST API to get a fresh session token, injects it into
the kubeconfig that is stored as a K8s secret, and updates the secret in-place.
Kubernetes automatically propagates secret updates to mounted volumes within ~60s,
so the api-gateway pod picks up the new token on its next kubectl call.
"""
import base64
import os
import sys

import requests
import urllib3
import yaml
from kubernetes import client, config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PASSWORD   = os.environ["VSPHERE_PASSWORD"]
NAMESPACE  = os.environ["TARGET_NAMESPACE"]
SECRET     = os.environ.get("SECRET_NAME", "kubectl-config")
_VSPHERE_HOST     = os.environ.get("VSPHERE_HOST", "").strip()
_VSPHERE_USERNAME = os.environ.get("VSPHERE_USERNAME", "").strip()


def get_vsphere_token(supervisor: str, username: str) -> str:
    resp = requests.post(
        f"https://{supervisor}/wcp/login",
        auth=(username, PASSWORD),
        verify=False,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("session_id") or data.get("token")
    if not token:
        raise ValueError(f"No token field in WCP response: {list(data.keys())}")
    return token


def _derive_host_username_from_kubeconfig(v1) -> tuple[str, str]:
    """Read kubectl-config secret and derive supervisor host + username.

    The WCP user name has format wcp:<control-plane-ip>:<username>, but the
    Kubernetes API VIP (server field in clusters[]) is the correct login endpoint.
    Falls back to the user-name IP if the server field is absent.
    """
    try:
        secret = v1.read_namespaced_secret(SECRET, NAMESPACE)
    except Exception as e:
        print(f"[token-refresher] ERROR — cannot read secret {SECRET!r} to derive host/username: {e}")
        sys.exit(1)
    raw = base64.b64decode(secret.data["config"]).decode("utf-8")
    kube_cfg = yaml.safe_load(raw)
    wcp_user = next((u for u in kube_cfg.get("users", []) if u.get("name", "").startswith("wcp:")), None)
    if not wcp_user:
        print("[token-refresher] ERROR — no WCP user found in kubeconfig")
        sys.exit(1)
    parts = wcp_user["name"].split(":", 2)
    if len(parts) != 3:
        print(f"[token-refresher] ERROR — unexpected WCP user name: {wcp_user['name']!r}")
        sys.exit(1)
    _, user_ip, username = parts

    # The kubeconfig may have multiple clusters. Prefer the supervisor VIP (named "supervisor-*"
    # or on port 443) over control-plane node entries (port 6443) which may not be accessible.
    from urllib.parse import urlparse
    clusters = kube_cfg.get("clusters") or []
    server = ""
    for c in clusters:
        if c.get("name", "").startswith("supervisor-"):
            server = c["cluster"].get("server", "")
            break
    if not server:
        for c in clusters:
            parsed_port = urlparse(c["cluster"].get("server", "")).port
            if parsed_port in (443, None):
                server = c["cluster"].get("server", "")
                break
    if not server and clusters:
        server = clusters[0]["cluster"].get("server", "")

    host = urlparse(server).hostname or user_ip

    # Re-find WCP user matching this host (may differ from user_ip derived above)
    wcp_user_for_host = next(
        (u for u in kube_cfg.get("users", []) if u.get("name", "").startswith(f"wcp:{host}:")),
        wcp_user
    )
    parts2 = wcp_user_for_host["name"].split(":", 2)
    username = parts2[2] if len(parts2) == 3 else username
    return host, username


def main():
    config.load_incluster_config()
    v1 = client.CoreV1Api()

    SUPERVISOR = _VSPHERE_HOST or None
    USERNAME   = _VSPHERE_USERNAME or None

    if not SUPERVISOR or not USERNAME:
        SUPERVISOR, USERNAME = _derive_host_username_from_kubeconfig(v1)

    print(f"[token-refresher] Logging in as {USERNAME} @ {SUPERVISOR}")
    try:
        token = get_vsphere_token(SUPERVISOR, USERNAME)
    except Exception as e:
        print(f"[token-refresher] ERROR — WCP login failed: {e}")
        sys.exit(1)
    print("[token-refresher] Token obtained")

    try:
        secret = v1.read_namespaced_secret(SECRET, NAMESPACE)
    except Exception as e:
        print(f"[token-refresher] ERROR — cannot read secret {SECRET!r}: {e}")
        sys.exit(1)

    raw = base64.b64decode(secret.data["config"]).decode("utf-8")
    kube_cfg = yaml.safe_load(raw)

    user_key = f"wcp:{SUPERVISOR}:{USERNAME}"
    updated = False
    for user in kube_cfg.get("users", []):
        if user.get("name") == user_key:
            user["user"]["token"] = token
            updated = True
            break

    if not updated:
        print(f"[token-refresher] ERROR — user {user_key!r} not found in kubeconfig users list")
        sys.exit(1)

    new_b64 = base64.b64encode(
        yaml.dump(kube_cfg, default_flow_style=False).encode()
    ).decode()
    secret.data["config"] = new_b64

    try:
        v1.replace_namespaced_secret(SECRET, NAMESPACE, secret)
    except Exception as e:
        print(f"[token-refresher] ERROR — cannot update secret: {e}")
        sys.exit(1)

    print(f"[token-refresher] Secret {SECRET!r} updated — new token active in ~60s")


if __name__ == "__main__":
    main()
