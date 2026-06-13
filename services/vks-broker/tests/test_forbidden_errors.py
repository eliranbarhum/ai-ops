"""Tests for KubeForbiddenError → structured JSON response chain."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient
from broker import KubeForbiddenError

client = TestClient(app_module.app)


@patch("main._cluster")
def test_forbidden_list_returns_structured_403(mock_cluster):
    """When kube_list raises KubeForbiddenError, endpoint returns 403 with JSON body."""
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    with patch("main.kube_list", side_effect=KubeForbiddenError(403, "list", "pods", "default")):
        resp = client.get("/clusters/ns/cluster/pods?namespace=default")

    assert resp.status_code == 403
    body = resp.json()
    assert body["error_type"] == "forbidden"
    assert body["verb"] == "list"
    assert body["resource"] == "pods"
    assert body["namespace"] == "default"
    assert "detail" in body


@patch("main._cluster")
def test_overview_degrades_gracefully_on_forbidden(mock_cluster):
    """Overview uses return_exceptions=True so KubeForbiddenError → empty data, still 200."""
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    with patch("main.kube_list", side_effect=KubeForbiddenError(403, "list", "nodes")):
        resp = client.get("/clusters/ns/cluster/overview")

    # Overview deliberately degrades rather than failing; sections that can't load show zeros
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body


@patch("main._cluster")
def test_forbidden_nodes_returns_verb_and_resource(mock_cluster):
    """403 response body includes verb and resource for RBAC troubleshooting."""
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    with patch("main.kube_list", side_effect=KubeForbiddenError(403, "get", "nodes")):
        resp = client.get("/clusters/ns/cluster/nodes")

    assert resp.status_code == 403
    body = resp.json()
    assert body["verb"] == "get"
    assert body["resource"] == "nodes"


@patch("main._cluster")
def test_forbidden_workloads_returns_403(mock_cluster):
    """KubeForbiddenError raised by workloads endpoint returns 403."""
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    with patch("main.kube_list", side_effect=KubeForbiddenError(403, "list", "deployments", "staging")):
        resp = client.get("/clusters/ns/cluster/workloads?kind=deployments&namespace=staging")

    assert resp.status_code == 403
    body = resp.json()
    assert body["namespace"] == "staging"


def test_kubeforidddenerror_str():
    """KubeForbiddenError includes namespace in message when present."""
    e = KubeForbiddenError(403, "list", "pods", "default")
    assert "pods" in str(e)
    assert "default" in str(e)

    e2 = KubeForbiddenError(403, "list", "nodes")
    assert "nodes" in str(e2)
    assert "in" not in str(e2)
