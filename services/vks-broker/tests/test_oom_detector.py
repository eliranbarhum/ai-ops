"""Tests for OOM kill detector endpoint (Loop 47)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name, ns, container_statuses, phase="Running", spec_resources=None):
    resources = spec_resources or {"requests": {"memory": "256Mi"}, "limits": {"memory": "512Mi"}}
    containers = [{"name": cs["name"], "resources": resources} for cs in container_statuses]
    return {
        "metadata": {"name": name, "namespace": ns},
        "status": {
            "phase": phase,
            "containerStatuses": container_statuses,
            "initContainerStatuses": [],
        },
        "spec": {"containers": containers},
    }


def _cs(name, restarts=0, last_reason="", exit_code=None, cur_reason=""):
    cs = {"name": name, "restartCount": restarts, "lastState": {}, "state": {}}
    if last_reason:
        cs["lastState"] = {"terminated": {"reason": last_reason, "exitCode": exit_code or 137, "finishedAt": "2024-01-01T00:00:00Z"}}
    if cur_reason:
        cs["state"] = {"terminated": {"reason": cur_reason, "exitCode": exit_code or 137}}
    return cs


# ── No issues ────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_oom_no_issues(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("healthy", "default", [_cs("app", restarts=0)]),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector")
    body = resp.json()
    assert body["total_flagged"] == 0
    assert body["pods"] == []


# ── OOMKilled last state ──────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_oom_detected_last_state(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("crashed", "default", [_cs("app", restarts=3, last_reason="OOMKilled")]),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector")
    body = resp.json()
    assert body["total_flagged"] == 1
    assert body["oom_pods"] == 1
    pod = body["pods"][0]
    assert pod["name"] == "crashed"
    assert pod["has_oom"] is True
    assert pod["containers"][0]["is_oom"] is True
    assert pod["containers"][0]["last_reason"] == "OOMKilled"


# ── High restart count ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_high_restart_count_flagged(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("restarty", "default", [_cs("app", restarts=10)]),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector?restart_threshold=5")
    body = resp.json()
    assert body["total_flagged"] == 1
    assert body["pods"][0]["total_restarts"] == 10
    assert body["pods"][0]["has_oom"] is False


# ── Below threshold not flagged ───────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_below_threshold_not_flagged(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("ok", "default", [_cs("app", restarts=2)]),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector?restart_threshold=5")
    assert resp.json()["total_flagged"] == 0


# ── Suggested memory limit ────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_suggested_limit_computed(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("oom-pod", "default",
             [_cs("app", restarts=1, last_reason="OOMKilled")],
             spec_resources={"requests": {"memory": "256Mi"}, "limits": {"memory": "512Mi"}}),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector")
    c = resp.json()["pods"][0]["containers"][0]
    assert c["lim_mem_mib"] == 512
    assert c["suggested_limit_mib"] is not None
    assert c["suggested_limit_mib"] > 512


# ── Sorted OOM pods first ─────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_oom_pods_sorted_first(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("high-restart", "default", [_cs("app", restarts=20)]),
        _pod("oom-killed", "default", [_cs("app", restarts=2, last_reason="OOMKilled")]),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector?restart_threshold=5")
    pods = resp.json()["pods"]
    assert pods[0]["name"] == "oom-killed"  # OOM sorted before high-restart


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_oom_response_structure(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("p1", "default", [_cs("app", restarts=10)]),
    ])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector?restart_threshold=5")
    body = resp.json()
    assert "pods" in body
    assert "total_flagged" in body
    assert "oom_pods" in body
    assert "metrics_available" in body
    p = body["pods"][0]
    c = p["containers"][0]
    for field in ["name", "restart_count", "is_oom", "last_reason", "req_mem_mib", "lim_mem_mib", "suggested_limit_mib"]:
        assert field in c


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_oom_empty_cluster(mock_metrics, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])
    mock_metrics.side_effect = AsyncMock(return_value={"available": False, "pods": []})
    resp = client.get("/clusters/ns/cluster/oom-detector")
    assert resp.status_code == 200
    assert resp.json()["total_flagged"] == 0
