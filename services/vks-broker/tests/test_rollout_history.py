"""Tests for GET /clusters/{id}/deployments/{name}/history (Loop 27)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_DEPLOYMENT_OBJ = {
    "metadata": {"name": "my-app", "namespace": "default", "creationTimestamp": "2024-01-01T00:00:00Z"},
    "spec": {
        "selector": {"matchLabels": {"app": "my-app"}},
        "replicas": 2,
    },
    "status": {"readyReplicas": 2},
}

def _make_rs(revision: int, image: str, ready: int = 1) -> dict:
    return {
        "metadata": {
            "name": f"my-app-{revision}abc",
            "namespace": "default",
            "creationTimestamp": f"2024-01-0{revision}T00:00:00Z",
            "annotations": {
                "deployment.kubernetes.io/revision": str(revision),
            },
            "ownerReferences": [
                {"kind": "Deployment", "name": "my-app", "apiVersion": "apps/v1"},
            ],
        },
        "spec": {
            "replicas": 2,
            "template": {"spec": {"containers": [{"name": "app", "image": image}]}},
        },
        "status": {"readyReplicas": ready},
    }


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
def test_history_returns_revisions(mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_get.side_effect = AsyncMock(return_value=_DEPLOYMENT_OBJ)
    mock_list.side_effect = AsyncMock(return_value=[
        _make_rs(1, "my-app:v1"),
        _make_rs(2, "my-app:v2"),
        _make_rs(3, "my-app:v3"),
    ])

    resp = client.get("/clusters/ns/cluster/deployments/my-app/history?namespace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert "revisions" in body
    revisions = body["revisions"]
    assert len(revisions) == 3
    # Should be sorted newest first
    assert revisions[0]["revision"] == 3
    assert revisions[1]["revision"] == 2
    assert revisions[2]["revision"] == 1


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
def test_history_revision_format(mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_get.side_effect = AsyncMock(return_value=_DEPLOYMENT_OBJ)
    mock_list.side_effect = AsyncMock(return_value=[_make_rs(5, "my-app:v5")])

    resp = client.get("/clusters/ns/cluster/deployments/my-app/history?namespace=default")
    rev = resp.json()["revisions"][0]
    assert rev["revision"] == 5
    assert rev["images"] == ["my-app:v5"]
    assert "created_at" in rev
    assert "replicas" in rev


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
def test_history_excludes_foreign_rs(mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_get.side_effect = AsyncMock(return_value=_DEPLOYMENT_OBJ)
    # RS owned by a different deployment
    foreign_rs = _make_rs(1, "other-app:v1")
    foreign_rs["metadata"]["ownerReferences"][0]["name"] = "other-deployment"
    mock_list.side_effect = AsyncMock(return_value=[
        _make_rs(3, "my-app:v3"),
        foreign_rs,
    ])

    resp = client.get("/clusters/ns/cluster/deployments/my-app/history?namespace=default")
    revisions = resp.json()["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["revision"] == 3


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
def test_history_empty_when_no_replicasets(mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_get.side_effect = AsyncMock(return_value=_DEPLOYMENT_OBJ)
    mock_list.side_effect = AsyncMock(return_value=[])

    resp = client.get("/clusters/ns/cluster/deployments/my-app/history?namespace=default")
    assert resp.json()["revisions"] == []
