"""Tests for GET /clusters/{id}/pods/metrics and /nodes/metrics (Loop 25)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_METRICS_PODS_RESP = {
    "items": [
        {
            "metadata": {"name": "my-pod", "namespace": "default"},
            "containers": [
                {"name": "app", "usage": {"cpu": "150m", "memory": "256Mi"}},
                {"name": "sidecar", "usage": {"cpu": "10m", "memory": "32Mi"}},
            ],
        }
    ]
}

_METRICS_NODES_RESP = {
    "items": [
        {
            "metadata": {"name": "worker-1"},
            "usage": {"cpu": "1200m", "memory": "4096Mi"},
        }
    ]
}


# ── Pod metrics ───────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_pod_metrics_available(mock_cluster):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _METRICS_PODS_RESP

    async def fake_get(url, **kwargs):
        return mock_resp

    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=fake_get)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/pods/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["pods"]) == 1
    pod = body["pods"][0]
    assert pod["name"] == "my-pod"
    assert pod["cpu_m"] == 160   # 150 + 10
    assert pod["mem_mib"] == 288  # 256 + 32


@patch("main._cluster")
def test_pod_metrics_unavailable_when_404(mock_cluster):
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    async def fake_get(url, **kwargs):
        return mock_resp

    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=fake_get)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/pods/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["pods"] == []


@patch("main._cluster")
def test_pod_metrics_namespace_filter(mock_cluster):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": []}

    captured_urls = []

    async def fake_get(url, **kwargs):
        captured_urls.append(url)
        return mock_resp

    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=fake_get)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    client.get("/clusters/ns/cluster/pods/metrics?namespace=production")
    assert any("namespaces/production" in u for u in captured_urls)


# ── Node metrics ──────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_node_metrics_available(mock_cluster):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _METRICS_NODES_RESP

    async def fake_get(url, **kwargs):
        return mock_resp

    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=fake_get)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/nodes/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["nodes"]) == 1
    node = body["nodes"][0]
    assert node["name"] == "worker-1"
    assert node["cpu_m"] == 1200
    assert node["mem_mib"] == 4096


@patch("main._cluster")
def test_node_metrics_unavailable_when_503(mock_cluster):
    mock_resp = MagicMock()
    mock_resp.status_code = 503

    async def fake_get(url, **kwargs):
        return mock_resp

    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=fake_get)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/nodes/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
