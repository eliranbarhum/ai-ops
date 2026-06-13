"""Tests for node cordon, uncordon, and drain endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


# ── Cordon ────────────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_cordon_issues_confirm_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/nodes/worker-1/cordon")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_cordon_executes_with_valid_token(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_patch(client, kind, name, body, ns=None): return {}
    mock_patch.side_effect = fake_patch

    resp = client.post("/clusters/ns/cluster/nodes/worker-1/cordon")
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/nodes/worker-1/cordon?token={token}",
        headers={"x-forwarded-user": "alice"},
    )
    assert resp2.status_code == 200
    assert resp2.json().get("ok") is True
    mock_patch.assert_called_once()
    mock_audit.assert_called_once()


@patch("main._cluster")
def test_cordon_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/nodes/worker-1/cordon?token=bad")
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_cordon_token_single_use(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_patch(client, kind, name, body, ns=None): return {}
    mock_patch.side_effect = fake_patch

    resp = client.post("/clusters/ns/cluster/nodes/worker-1/cordon")
    token = resp.json()["token"]

    client.post(f"/clusters/ns/cluster/nodes/worker-1/cordon?token={token}")
    resp2 = client.post(f"/clusters/ns/cluster/nodes/worker-1/cordon?token={token}")
    assert resp2.status_code == 400


# ── Uncordon ──────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_uncordon_executes_immediately(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_patch(client, kind, name, body, ns=None): return {}
    mock_patch.side_effect = fake_patch

    resp = client.post(
        "/clusters/ns/cluster/nodes/worker-1/uncordon",
        headers={"x-forwarded-user": "alice"},
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True
    mock_audit.assert_called_once()
