"""Tests for StatefulSet and DaemonSet restart endpoints (Loop 24)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


# ── StatefulSet restart ───────────────────────────────────────────────────────

@patch("main._cluster")
def test_statefulset_restart_issues_confirm_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/statefulsets/my-sts/restart?namespace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_statefulset_restart_executes_with_valid_token(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_patch(client, kind, name, ns, body): return {}
    mock_patch.side_effect = fake_patch

    resp = client.post("/clusters/ns/cluster/statefulsets/my-sts/restart?namespace=default")
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/statefulsets/my-sts/restart?namespace=default&token={token}",
        headers={"x-forwarded-user": "alice"},
    )
    assert resp2.status_code == 200
    assert resp2.json().get("ok") is True
    mock_audit.assert_called_once()
    call_args = mock_patch.call_args
    assert call_args[0][1] == "statefulsets"


@patch("main._cluster")
def test_statefulset_restart_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/statefulsets/my-sts/restart?namespace=default&token=bad")
    assert resp.status_code == 400


# ── DaemonSet restart ─────────────────────────────────────────────────────────

@patch("main._cluster")
def test_daemonset_restart_issues_confirm_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/daemonsets/my-ds/restart?namespace=kube-system")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_daemonset_restart_executes_with_valid_token(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_patch(client, kind, name, ns, body): return {}
    mock_patch.side_effect = fake_patch

    resp = client.post("/clusters/ns/cluster/daemonsets/my-ds/restart?namespace=kube-system")
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/daemonsets/my-ds/restart?namespace=kube-system&token={token}",
        headers={"x-forwarded-user": "bob"},
    )
    assert resp2.status_code == 200
    assert resp2.json().get("ok") is True
    mock_audit.assert_called_once()
    call_args = mock_patch.call_args
    assert call_args[0][1] == "daemonsets"


@patch("main._cluster")
def test_daemonset_restart_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/daemonsets/my-ds/restart?namespace=kube-system&token=invalid")
    assert resp.status_code == 400
