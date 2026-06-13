"""Tests for POST /nodes/{node}/labels and /taints (Loop 34)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_NODE_RESP = {
    "metadata": {"name": "worker-1", "labels": {"app": "existing"}},
    "spec": {
        "taints": [
            {"key": "dedicated", "effect": "NoSchedule", "value": "gpu"}
        ]
    },
}


def _make_http_mock(node_resp=None):
    mock_http = MagicMock()
    if node_resp is not None:
        get_resp = MagicMock()
        get_resp.json.return_value = node_resp
        mock_http.get = AsyncMock(return_value=get_resp)
    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {}
    mock_http.patch = AsyncMock(return_value=patch_resp)
    return mock_http


# ── Label tests ───────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_add_label_issues_confirm(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/nodes/worker-1/labels", json={"add": {"env": "prod"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_add_label_executes_with_token(mock_audit, mock_cluster):
    mock_http = _make_http_mock()
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post("/clusters/ns/cluster/nodes/worker-1/labels", json={"add": {"env": "prod"}})
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/nodes/worker-1/labels?token={token}",
        json={"add": {"env": "prod"}},
    )
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True
    mock_audit.assert_called_once()


@patch("main._cluster")
def test_add_label_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/nodes/worker-1/labels?token=bad",
        json={"add": {"k": "v"}},
    )
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_remove_label_executes_with_token(mock_audit, mock_cluster):
    mock_http = _make_http_mock()
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post("/clusters/ns/cluster/nodes/worker-1/labels", json={"remove": ["app"]})
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/nodes/worker-1/labels?token={token}",
        json={"remove": ["app"]},
    )
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True


# ── Taint tests ───────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_add_taint_issues_confirm(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/nodes/worker-1/taints",
        json={"action": "add", "taint": {"key": "test", "effect": "NoSchedule"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_add_taint_executes_with_token(mock_audit, mock_cluster):
    mock_http = _make_http_mock(node_resp=_NODE_RESP)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post(
        "/clusters/ns/cluster/nodes/worker-1/taints",
        json={"action": "add", "taint": {"key": "test", "effect": "NoSchedule"}},
    )
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/nodes/worker-1/taints?token={token}",
        json={"action": "add", "taint": {"key": "test", "effect": "NoSchedule"}},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["ok"] is True
    assert any(t["key"] == "test" for t in body["taints"])
    mock_audit.assert_called_once()


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_remove_taint_removes_matching(mock_audit, mock_cluster):
    mock_http = _make_http_mock(node_resp=_NODE_RESP)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post(
        "/clusters/ns/cluster/nodes/worker-1/taints",
        json={"action": "remove", "taint": {"key": "dedicated", "effect": "NoSchedule"}},
    )
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/nodes/worker-1/taints?token={token}",
        json={"action": "remove", "taint": {"key": "dedicated", "effect": "NoSchedule"}},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["ok"] is True
    assert not any(t["key"] == "dedicated" for t in body["taints"])


@patch("main._cluster")
def test_invalid_taint_action_returns_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/nodes/worker-1/taints?token=x",
        json={"action": "invalid", "taint": {"key": "k", "effect": "NoSchedule"}},
    )
    assert resp.status_code == 400


@patch("main._cluster")
def test_taint_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/nodes/worker-1/taints?token=bad",
        json={"action": "add", "taint": {"key": "k", "effect": "NoSchedule"}},
    )
    assert resp.status_code == 400
