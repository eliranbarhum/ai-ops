"""Tests for workload list, scale, restart, and delete endpoints including confirm-token flow."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient
import confirm as confirm_module

client = TestClient(app_module.app)


def _make_deployment(name, namespace="default", replicas=2, ready=2):
    return {
        "metadata": {"name": name, "namespace": namespace,
                     "creationTimestamp": "2026-01-01T00:00:00Z", "labels": {}},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": name}},
            "template": {"spec": {"containers": [{"image": f"nginx:1.25", "name": "app"}]}},
        },
        "status": {"readyReplicas": ready, "availableReplicas": ready, "updatedReplicas": ready},
    }


# ── List workloads ────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_list_deployments(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    async def fake_kube_list(client, kind, ns=None):
        return [_make_deployment("frontend"), _make_deployment("backend")]

    mock_kube_list.side_effect = fake_kube_list

    resp = client.get("/clusters/ns/cluster/workloads?kind=deployments")
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "deployments"
    assert len(data["items"]) == 2
    assert data["items"][0]["name"] == "frontend"


@patch("main._cluster")
@patch("main.kube_list")
def test_list_invalid_kind_returns_400(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.get("/clusters/ns/cluster/workloads?kind=badkind")
    assert resp.status_code == 400


# ── Scale ─────────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_scale_issues_confirm_token(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_patch.return_value = AsyncMock(return_value={})()

    resp = client.post(
        "/clusters/ns/cluster/deployments/frontend/scale?namespace=default",
        json={"replicas": 5},
        headers={"x-forwarded-user": "alice"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requires_confirm"] is True
    assert "token" in data
    assert data["action"] == "scale"


@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_scale_executes_with_valid_token(mock_audit, mock_patch, mock_cluster):
    mock_client = MagicMock()
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)
    mock_patch.return_value = AsyncMock(return_value={"status": "ok"})()

    # Step 1: get token
    r1 = client.post(
        "/clusters/ns/cluster/deployments/frontend/scale?namespace=default",
        json={"replicas": 3},
        headers={"x-forwarded-user": "alice"},
    )
    token = r1.json()["token"]

    # Step 2: confirm with token
    r2 = client.post(
        f"/clusters/ns/cluster/deployments/frontend/scale?namespace=default&token={token}",
        json={"replicas": 3},
        headers={"x-forwarded-user": "alice"},
    )
    assert r2.status_code == 200


def test_scale_rejects_invalid_token():
    resp = client.post(
        "/clusters/ns/cluster/deployments/frontend/scale?namespace=default&token=not-a-real-token",
        json={"replicas": 3},
    )
    assert resp.status_code == 400
    assert "Invalid confirm token" in resp.json()["detail"]


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_scale_invalid_replicas(mock_audit, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    resp = client.post(
        "/clusters/ns/cluster/deployments/frontend/scale?namespace=default",
        json={"replicas": -1},
    )
    assert resp.status_code == 400


# ── Restart ───────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_restart_issues_confirm_token(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_patch.return_value = AsyncMock(return_value={})()

    resp = client.post(
        "/clusters/ns/cluster/deployments/frontend/restart?namespace=default",
        json={},
        headers={"x-forwarded-user": "alice"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requires_confirm"] is True
    assert data["action"] == "restart"


# ── Delete workload ───────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_delete")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_delete_workload_issues_confirm(mock_audit, mock_delete, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_delete.return_value = AsyncMock(return_value={})()

    resp = client.post(
        "/clusters/ns/cluster/workloads/deployments/frontend/delete?namespace=default",
        json={},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requires_confirm"] is True


@patch("main._cluster")
@patch("main.kube_delete")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_delete_workload_executes_with_token(mock_audit, mock_delete, mock_cluster):
    mock_client = MagicMock()
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)
    mock_delete.return_value = AsyncMock(return_value={"status": "ok"})()

    # Get token
    r1 = client.post(
        "/clusters/ns/cluster/workloads/deployments/frontend/delete?namespace=default",
        json={},
    )
    token = r1.json()["token"]

    # Execute
    r2 = client.post(
        f"/clusters/ns/cluster/workloads/deployments/frontend/delete?namespace=default&token={token}",
        json={},
    )
    assert r2.status_code == 200
    mock_delete.assert_called_once()


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_token_single_use_on_delete(mock_audit, mock_cluster):
    """Confirm token cannot be reused."""
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    r1 = client.post(
        "/clusters/ns/cluster/workloads/deployments/frontend/delete?namespace=default",
        json={},
    )
    assert r1.status_code == 200
    token = r1.json()["token"]

    async def fake_delete(client, kind, name, ns):
        return {}

    with patch("main.kube_delete", side_effect=fake_delete):
        r2 = client.post(
            f"/clusters/ns/cluster/workloads/deployments/frontend/delete?namespace=default&token={token}",
            json={},
        )
        assert r2.status_code == 200

    # Second use must fail with 400 (token consumed)
    r3 = client.post(
        f"/clusters/ns/cluster/workloads/deployments/frontend/delete?namespace=default&token={token}",
        json={},
    )
    assert r3.status_code == 400
    assert "Invalid confirm token" in r3.json()["detail"]
