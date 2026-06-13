"""Tests for DELETE /pvcs/{name} and GET /audit endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


# ── DELETE /pvcs/{name} — confirm-token flow ──────────────────────────────────

@patch("main._cluster")
def test_delete_pvc_issues_confirm_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.delete("/clusters/ns/cluster/pvcs/my-pvc?namespace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body
    desc = body.get("params", {}).get("description", "")
    assert "my-pvc" in desc


@patch("main._cluster")
@patch("main.kube_delete")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_delete_pvc_executes_with_valid_token(mock_audit, mock_delete, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_delete(client, kind, name, ns): return None
    mock_delete.side_effect = fake_delete

    resp = client.delete("/clusters/ns/cluster/pvcs/my-pvc?namespace=default")
    token = resp.json()["token"]

    resp2 = client.delete(
        f"/clusters/ns/cluster/pvcs/my-pvc?namespace=default&token={token}",
        headers={"x-forwarded-user": "alice"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True
    mock_delete.assert_called_once()
    mock_audit.assert_called_once()


@patch("main._cluster")
def test_delete_pvc_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.delete(
        "/clusters/ns/cluster/pvcs/my-pvc?namespace=default&token=bad-token"
    )
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.kube_delete")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_delete_pvc_token_is_single_use(mock_audit, mock_delete, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_delete(client, kind, name, ns): return None
    mock_delete.side_effect = fake_delete

    resp = client.delete("/clusters/ns/cluster/pvcs/my-pvc?namespace=default")
    token = resp.json()["token"]

    client.delete(f"/clusters/ns/cluster/pvcs/my-pvc?namespace=default&token={token}")

    resp2 = client.delete(f"/clusters/ns/cluster/pvcs/my-pvc?namespace=default&token={token}")
    assert resp2.status_code == 400


# ── GET /audit endpoint ───────────────────────────────────────────────────────

def test_audit_returns_events_list():
    resp = client.get("/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body
    assert isinstance(body["events"], list)


def test_audit_respects_limit():
    resp = client.get("/audit?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) <= 5


def test_audit_clamps_limit_to_200():
    resp = client.get("/audit?limit=999")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) <= 200


@patch("main.audit_emit", new_callable=AsyncMock)
@patch("main._cluster")
@patch("main.kube_delete")
def test_audit_contains_delete_event_after_pvc_delete(mock_delete, mock_cluster, mock_audit_emit):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_delete(client, kind, name, ns): return None
    mock_delete.side_effect = fake_delete

    resp = client.delete("/clusters/ns/cluster/pvcs/audit-test-pvc?namespace=default")
    token = resp.json()["token"]
    client.delete(
        f"/clusters/ns/cluster/pvcs/audit-test-pvc?namespace=default&token={token}",
        headers={"x-forwarded-user": "tester"},
    )
    mock_audit_emit.assert_called_once()
    args = mock_audit_emit.call_args
    assert args[0][1] == "delete-pvc"
    assert args[0][5] == "audit-test-pvc"
