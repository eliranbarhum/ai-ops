"""Tests for DELETE /secrets/{name} endpoint (confirm-token flow)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


# ── DELETE secret — requires confirm token ────────────────────────────────────

@patch("main._cluster")
def test_delete_secret_issues_confirm_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.delete("/clusters/ns/cluster/secrets/my-secret?namespace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body
    assert "delete" in body.get("params", {}).get("description", "").lower()


@patch("main._cluster")
@patch("main.kube_delete")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_delete_secret_executes_with_valid_token(mock_audit, mock_delete, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_delete(client, kind, name, ns): return None
    mock_delete.side_effect = fake_delete

    # Step 1: get token
    resp = client.delete("/clusters/ns/cluster/secrets/my-secret?namespace=default")
    token = resp.json()["token"]

    # Step 2: execute with token
    resp2 = client.delete(
        f"/clusters/ns/cluster/secrets/my-secret?namespace=default&token={token}",
        headers={"x-forwarded-user": "alice"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True
    mock_delete.assert_called_once()
    mock_audit.assert_called_once()


@patch("main._cluster")
def test_delete_secret_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.delete(
        "/clusters/ns/cluster/secrets/my-secret?namespace=default&token=bad-token"
    )
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.kube_delete")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_delete_secret_token_is_single_use(mock_audit, mock_delete, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_delete(client, kind, name, ns): return None
    mock_delete.side_effect = fake_delete

    resp = client.delete("/clusters/ns/cluster/secrets/my-secret?namespace=default")
    token = resp.json()["token"]

    # First use succeeds
    client.delete(f"/clusters/ns/cluster/secrets/my-secret?namespace=default&token={token}")

    # Second use must fail
    resp2 = client.delete(f"/clusters/ns/cluster/secrets/my-secret?namespace=default&token={token}")
    assert resp2.status_code == 400
