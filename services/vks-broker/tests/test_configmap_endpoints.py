"""Tests for configmap list, get, and update endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


_DEFAULT_DATA = {"key1": "value1", "key2": "value2"}

def _make_cm(name="app-config", namespace="default", data=_DEFAULT_DATA):
    return {
        "metadata": {
            "name": name, "namespace": namespace,
            "creationTimestamp": "2026-01-01T00:00:00Z",
        },
        "data": data,
    }


# ── List configmaps ───────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_list_configmaps_returns_keys(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, kind, ns=None):
        return [_make_cm()]
    mock_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/configmaps?namespace=default")
    assert resp.status_code == 200
    data = resp.json()
    assert "configmaps" in data
    cm = data["configmaps"][0]
    assert cm["name"] == "app-config"
    assert cm["key_count"] == 2
    assert "key1" in cm["keys"]
    assert cm["data"]["key1"] == "value1"


@patch("main._cluster")
@patch("main.kube_list")
def test_list_configmaps_empty_data(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, kind, ns=None):
        return [{"metadata": {"name": "empty-cm", "namespace": "default", "creationTimestamp": "2026-01-01T00:00:00Z"}}]
    mock_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/configmaps")
    assert resp.status_code == 200
    cm = resp.json()["configmaps"][0]
    assert cm["key_count"] == 0
    assert cm["keys"] == []


# ── Get single configmap ──────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_get")
def test_get_configmap_returns_data(mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_get(client, kind, name, ns): return _make_cm(data={"db_url": "postgres://localhost"})
    mock_get.side_effect = fake_get

    resp = client.get("/clusters/ns/cluster/configmaps/app-config?namespace=default")
    assert resp.status_code == 200
    assert resp.json()["data"]["db_url"] == "postgres://localhost"


# ── Update configmap ──────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_update_configmap_patches_data(mock_audit, mock_patch, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_patch(client, kind, name, patch, ns): return {"metadata": {"name": name}}
    mock_patch.side_effect = fake_patch

    resp = client.put(
        "/clusters/ns/cluster/configmaps/app-config?namespace=default",
        json={"data": {"key1": "new-value"}},
        headers={"x-forwarded-user": "alice"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"
    mock_audit.assert_called_once()


@patch("main._cluster")
def test_update_configmap_missing_data_returns_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.put(
        "/clusters/ns/cluster/configmaps/app-config?namespace=default",
        json={},
    )
    assert resp.status_code == 400


@patch("main._cluster")
def test_update_configmap_non_dict_data_returns_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.put(
        "/clusters/ns/cluster/configmaps/app-config",
        json={"data": "not-a-dict"},
    )
    assert resp.status_code == 400
