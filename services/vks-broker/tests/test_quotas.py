"""Tests for GET /clusters/{id}/quotas endpoint."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_QUOTA_OBJ = {
    "metadata": {"name": "default", "namespace": "test-ns", "creationTimestamp": "2024-01-01T00:00:00Z"},
    "status": {
        "hard": {
            "requests.cpu": "4",
            "limits.cpu": "8",
            "requests.memory": "4Gi",
            "limits.memory": "8Gi",
            "pods": "20",
        },
        "used": {
            "requests.cpu": "1",
            "limits.cpu": "2",
            "requests.memory": "1Gi",
            "limits.memory": "2Gi",
            "pods": "5",
        },
    },
}


@patch("main._cluster")
@patch("main.kube_list")
def test_quotas_returns_list(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None): return [_QUOTA_OBJ]
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/quotas?namespace=test-ns")
    assert resp.status_code == 200
    body = resp.json()
    assert "quotas" in body
    assert len(body["quotas"]) == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_quotas_format_includes_hard_used(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None): return [_QUOTA_OBJ]
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/quotas")
    quota = resp.json()["quotas"][0]
    assert quota["name"] == "default"
    assert quota["namespace"] == "test-ns"
    assert "requests.cpu" in quota["hard"]
    assert quota["hard"]["requests.cpu"] == "4"
    assert quota["used"]["pods"] == "5"


@patch("main._cluster")
@patch("main.kube_list")
def test_quotas_empty_namespace(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None): return []
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/quotas")
    assert resp.status_code == 200
    assert resp.json()["quotas"] == []


@patch("main._cluster")
@patch("main.kube_list")
def test_quotas_includes_created_at(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None): return [_QUOTA_OBJ]
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/quotas")
    quota = resp.json()["quotas"][0]
    assert quota.get("created_at") == "2024-01-01T00:00:00Z"
