"""Tests for Workload Deep Edit endpoints (Loop 2):
resources patch, env patch, image patch, rollback, workload diagnose."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _dep(name="web", ns="default", replicas=3):
    return {
        "metadata": {"name": name, "namespace": ns, "resourceVersion": "1234"},
        "spec": {
            "replicas": replicas,
            "template": {
                "metadata": {},
                "spec": {"containers": [{"name": "app", "image": "nginx:1.25", "resources": {}}]},
            },
        },
        "status": {"readyReplicas": replicas, "conditions": []},
    }


def _rs(name="web-rs-abc"):
    return {
        "metadata": {"name": name, "namespace": "default"},
        "spec": {
            "template": {
                "metadata": {"labels": {"app": "web"}},
                "spec": {"containers": [{"name": "app", "image": "nginx:1.24"}]},
            }
        },
    }


def _setup_cluster(mock_cluster, mock_patch_fn=None):
    cluster_client = MagicMock()
    if mock_patch_fn:
        cluster_client.patch = mock_patch_fn
    mock_cluster.side_effect = AsyncMock(return_value=cluster_client)
    return cluster_client


# ── Resources patch ───────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
def test_resources_patch_requires_confirm(mock_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/resources?namespace=default",
        json={"containers": [{"name": "app", "requests": {"cpu": "200m"}, "limits": {"memory": "256Mi"}}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.kube_patch")
def test_resources_patch_with_token_applies(mock_kube_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kube_patch.side_effect = AsyncMock(return_value={})
    # Get token first
    r1 = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/resources?namespace=default",
        json={"containers": [{"name": "app", "requests": {"cpu": "200m"}, "limits": {"memory": "256Mi"}}]},
    )
    token = r1.json()["token"]
    r2 = client.patch(
        f"/clusters/ns/cluster/workloads/deployments/web/resources?namespace=default&token={token}",
        json={"containers": [{"name": "app", "requests": {"cpu": "200m"}, "limits": {"memory": "256Mi"}}]},
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    mock_kube_patch.assert_called_once()
    call_patch = mock_kube_patch.call_args[0][4]
    assert "containers" in call_patch["spec"]["template"]["spec"]


@patch("main._cluster")
def test_resources_patch_missing_containers_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/resources?namespace=default",
        json={},
    )
    assert r.status_code == 400


# ── Env patch ─────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
def test_env_patch_requires_confirm(mock_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/env?namespace=default",
        json={"container": "app", "env": [{"name": "LOG_LEVEL", "value": "debug"}]},
    )
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_patch")
def test_env_patch_with_token_applies(mock_kube_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kube_patch.side_effect = AsyncMock(return_value={})
    r1 = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/env?namespace=default",
        json={"container": "app", "env": [{"name": "LOG_LEVEL", "value": "debug"}]},
    )
    token = r1.json()["token"]
    r2 = client.patch(
        f"/clusters/ns/cluster/workloads/deployments/web/env?namespace=default&token={token}",
        json={"container": "app", "env": [{"name": "LOG_LEVEL", "value": "debug"}]},
    )
    assert r2.json()["ok"] is True
    patch_body = mock_kube_patch.call_args[0][4]
    containers = patch_body["spec"]["template"]["spec"]["containers"]
    assert containers[0]["name"] == "app"
    assert containers[0]["env"] == [{"name": "LOG_LEVEL", "value": "debug"}]


@patch("main._cluster")
def test_env_patch_missing_container_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/env?namespace=default",
        json={"env": [{"name": "X", "value": "y"}]},
    )
    assert r.status_code == 400


# ── Image patch ───────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
def test_image_patch_requires_confirm(mock_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/image?namespace=default",
        json={"container": "app", "image": "nginx:1.26"},
    )
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_patch")
def test_image_patch_with_token_applies(mock_kube_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kube_patch.side_effect = AsyncMock(return_value={})
    r1 = client.patch(
        "/clusters/ns/cluster/workloads/deployments/web/image?namespace=default",
        json={"container": "app", "image": "nginx:1.26"},
    )
    token = r1.json()["token"]
    r2 = client.patch(
        f"/clusters/ns/cluster/workloads/deployments/web/image?namespace=default&token={token}",
        json={"container": "app", "image": "nginx:1.26"},
    )
    assert r2.json()["ok"] is True
    patch_body = mock_kube_patch.call_args[0][4]
    containers = patch_body["spec"]["template"]["spec"]["containers"]
    assert containers[0]["image"] == "nginx:1.26"


# ── Rollback ──────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_patch")
def test_rollback_requires_confirm(mock_kube_patch, mock_kube_get, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/deployments/web/rollback?namespace=default",
        json={"rs_name": "web-rs-abc123"},
    )
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_patch")
def test_rollback_with_token_patches_from_rs(mock_kube_patch, mock_kube_get, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kube_get.side_effect = AsyncMock(return_value=_rs())
    mock_kube_patch.side_effect = AsyncMock(return_value={})
    r1 = client.post(
        "/clusters/ns/cluster/deployments/web/rollback?namespace=default",
        json={"rs_name": "web-rs-abc"},
    )
    token = r1.json()["token"]
    r2 = client.post(
        f"/clusters/ns/cluster/deployments/web/rollback?namespace=default&token={token}",
        json={"rs_name": "web-rs-abc"},
    )
    assert r2.json()["ok"] is True
    mock_kube_patch.assert_called_once()
    patch_body = mock_kube_patch.call_args[0][4]
    assert "template" in patch_body["spec"]


@patch("main._cluster")
def test_rollback_missing_rs_name_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/deployments/web/rollback?namespace=default",
        json={},
    )
    assert r.status_code == 400


# ── Workload diagnose ─────────────────────────────────────────────────────────

def _setup_workload_diagnose(mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_get.side_effect = AsyncMock(return_value=_dep())
    mock_list.side_effect = AsyncMock(return_value=[])


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_workload_diagnose_returns_event_stream(mock_http, mock_list, mock_get, mock_cluster):
    _setup_workload_diagnose(mock_list, mock_get, mock_cluster)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"text": "Deployment is degraded because…"}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/clusters/ns/cluster/workloads/deployments/web/diagnose?namespace=default")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_workload_diagnose_teach_mode(mock_http, mock_list, mock_get, mock_cluster):
    _setup_workload_diagnose(mock_list, mock_get, mock_cluster)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"text": "ReplicaSets manage pod templates…"}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/clusters/ns/cluster/workloads/deployments/web/diagnose?namespace=default&mode=teach")
    assert r.status_code == 200
