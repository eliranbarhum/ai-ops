"""Tests for /pod-resources and /pods/batch-restart endpoints (Loop 40)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name, ns, cpu_req="100m", mem_req="128Mi", cpu_lim="500m", mem_lim="256Mi", phase="Running"):
    return {
        "metadata": {"name": name, "namespace": ns},
        "status": {"phase": phase},
        "spec": {
            "containers": [{
                "name": "app",
                "resources": {
                    "requests": {"cpu": cpu_req, "memory": mem_req},
                    "limits": {"cpu": cpu_lim, "memory": mem_lim},
                },
            }]
        },
    }


# ── Pod Resources Tests ───────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_pod_resources_structure(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("api", "default"),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={
        "available": True, "pods": [{"name": "api", "namespace": "default", "cpu_m": 80, "mem_mib": 100}]
    })

    resp = client.get("/clusters/ns/cluster/pod-resources")
    assert resp.status_code == 200
    body = resp.json()
    assert "pods" in body
    assert "metrics_available" in body
    assert body["metrics_available"] is True


@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_pod_resources_live_data(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("api", "default", cpu_req="200m", mem_req="256Mi", cpu_lim="1000m", mem_lim="512Mi"),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={
        "available": True, "pods": [{"name": "api", "namespace": "default", "cpu_m": 800, "mem_mib": 400}]
    })

    resp = client.get("/clusters/ns/cluster/pod-resources")
    pod = resp.json()["pods"][0]
    assert pod["live_cpu_m"] == 800
    assert pod["live_mem_mib"] == 400
    assert pod["req_cpu_m"] == 200
    assert pod["lim_cpu_m"] == 1000
    assert pod["cpu_pct"] == 80
    assert pod["mem_pct"] == 78


@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_pod_resources_no_metrics(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_pod("api", "default")])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})

    resp = client.get("/clusters/ns/cluster/pod-resources")
    body = resp.json()
    assert body["metrics_available"] is False
    pod = body["pods"][0]
    assert pod["live_cpu_m"] is None
    assert pod["cpu_pct"] is None


@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_pod_resources_sorted_by_cpu(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("low", "default"),
        _pod("high", "default"),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={
        "available": True, "pods": [
            {"name": "low", "namespace": "default", "cpu_m": 10, "mem_mib": 10},
            {"name": "high", "namespace": "default", "cpu_m": 800, "mem_mib": 200},
        ]
    })

    resp = client.get("/clusters/ns/cluster/pod-resources")
    pods = resp.json()["pods"]
    assert pods[0]["name"] == "high"
    assert pods[1]["name"] == "low"


@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_pod_resources_no_limits_no_pct(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("api", "default", cpu_lim="0", mem_lim="0"),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={
        "available": True, "pods": [{"name": "api", "namespace": "default", "cpu_m": 100, "mem_mib": 100}]
    })

    resp = client.get("/clusters/ns/cluster/pod-resources")
    pod = resp.json()["pods"][0]
    assert pod["cpu_pct"] is None
    assert pod["mem_pct"] is None


# ── Batch Restart Tests ───────────────────────────────────────────────────────

@patch("main._cluster")
def test_batch_restart_issues_confirm(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/pods/batch-restart",
                       json={"pods": [{"name": "pod-1", "namespace": "default"},
                                       {"name": "pod-2", "namespace": "default"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
def test_batch_restart_empty_list_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/pods/batch-restart", json={"pods": []})
    assert resp.status_code == 400


@patch("main._cluster")
def test_batch_restart_too_many_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    pods = [{"name": f"pod-{i}", "namespace": "default"} for i in range(51)]
    resp = client.post("/clusters/ns/cluster/pods/batch-restart", json={"pods": pods})
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_batch_restart_executes_with_token(mock_audit, mock_cluster):
    mock_http = MagicMock()
    del_resp = MagicMock()
    del_resp.status_code = 200
    mock_http.delete = AsyncMock(return_value=del_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post("/clusters/ns/cluster/pods/batch-restart",
                       json={"pods": [{"name": "pod-1", "namespace": "default"}]})
    token = resp.json()["token"]

    resp2 = client.post(f"/clusters/ns/cluster/pods/batch-restart?token={token}",
                        json={"pods": [{"name": "pod-1", "namespace": "default"}]})
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["ok"] is True
    assert len(body["results"]) == 1
    assert body["results"][0]["ok"] is True
    mock_audit.assert_called_once()
