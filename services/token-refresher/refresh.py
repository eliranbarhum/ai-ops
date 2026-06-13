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

SUPERVISOR = os.environ["VSPHERE_HOST"]
USERNAME   = os.environ["VSPHERE_USERNAME"]
PASSWORD   = os.environ["VSPHERE_PASSWORD"]
NAMESPACE  = os.environ["TARGET_NAMESPACE"]
SECRET     = os.environ.get("SECRET_NAME", "kubectl-config")


def get_vsphere_token() -> str:
    resp = requests.post(
        f"https://{SUPERVISOR}/wcp/login",
        auth=(USERNAME, PASSWORD),
        verify=False,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("session_id") or data.get("token")
    if not token:
        raise ValueError(f"No token field in WCP response: {list(data.keys())}")
    return token


def main():
    print(f"[token-refresher] Logging in as {USERNAME} @ {SUPERVISOR}")
    try:
        token = get_vsphere_token()
    except Exception as e:
        print(f"[token-refresher] ERROR — WCP login failed: {e}")
        sys.exit(1)
    print("[token-refresher] Token obtained")

    config.load_incluster_config()
    v1 = client.CoreV1Api()

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
