"""Tests for Workload Restart Timeline endpoint (Loop 56)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _dep(name, ns="default", selector=None):
    sel = selector or {"app": name}
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"selector": {"matchLabels": sel}, "replicas": 1},
        "status": {},
    }


def _pod(name, ns="default", labels=None, restarts=0, last_restart=""):
    cs = [{"name": "app", "restartCount": restarts,
           "lastState": {"terminated": {"finishedAt": last_restart}} if last_restart else {}}]
    return {
        "metadata": {"name": name, "namespace": ns, "labels": labels or {"app": name.rsplit("-", 1)[0]}},
        "status": {"phase": "Running", "containerStatuses": cs},
        "spec": {},
    }


def _setup(mock_list, mock_cluster, pods, deps=None, sts=None, dsets=None):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    data = {
        "pods": pods,
        "deployments": deps or [],
        "statefulsets": sts or [],
        "daemonsets": dsets or [],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_empty_cluster(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster, pods=[])
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline").json()
    assert body["summary"]["total_restarts"] == 0
    assert body["workloads"] == []


# ── Pod with no restarts not included when min_restarts=1 ────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_zero_restart_pod_excluded(mock_list, mock_cluster):
    pod = _pod("app-abc", restarts=0)
    dep = _dep("app", selector={"app": "app"})
    _setup(mock_list, mock_cluster, pods=[pod], deps=[dep])
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline?min_restarts=1").json()
    assert body["summary"]["total_restarts"] == 0
    assert body["workloads"] == []


# ── Pod with restarts aggregated under deployment ─────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_deployment_restart_aggregated(mock_list, mock_cluster):
    pod = _pod("app-abc", labels={"app": "myapp"}, restarts=3, last_restart="2026-06-10T10:00:00Z")
    dep = _dep("myapp", selector={"app": "myapp"})
    _setup(mock_list, mock_cluster, pods=[pod], deps=[dep])
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline").json()
    assert body["summary"]["total_restarts"] == 3
    r = body["workloads"][0]
    assert r["kind"] == "Deployment"
    assert r["name"] == "myapp"
    assert r["total_restarts"] == 3
    assert r["last_restart"] == "2026-06-10T10:00:00Z"


# ── Multiple pods summed ──────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_multiple_pods_summed(mock_list, mock_cluster):
    pods = [
        _pod("app-0", labels={"app": "myapp"}, restarts=2),
        _pod("app-1", labels={"app": "myapp"}, restarts=5),
    ]
    dep = _dep("myapp", selector={"app": "myapp"})
    _setup(mock_list, mock_cluster, pods=pods, deps=[dep])
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline").json()
    assert body["workloads"][0]["total_restarts"] == 7
    assert body["workloads"][0]["pod_count"] == 2


# ── Sorted by restarts descending ────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_sorted_by_restarts_desc(mock_list, mock_cluster):
    pods = [
        _pod("light-abc", labels={"app": "light"}, restarts=1),
        _pod("heavy-abc", labels={"app": "heavy"}, restarts=10),
    ]
    deps = [_dep("light", selector={"app": "light"}), _dep("heavy", selector={"app": "heavy"})]
    _setup(mock_list, mock_cluster, pods=pods, deps=deps)
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline").json()
    assert body["workloads"][0]["name"] == "heavy"
    assert body["workloads"][1]["name"] == "light"


# ── Standalone pod (no owner) shows as kind=Pod ───────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_standalone_pod_kind(mock_list, mock_cluster):
    pod = _pod("orphan-pod", restarts=4)
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline").json()
    r = body["workloads"][0]
    assert r["kind"] == "Pod"
    assert r["total_restarts"] == 4


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    pod = _pod("app-abc", labels={"app": "myapp"}, restarts=2)
    dep = _dep("myapp", selector={"app": "myapp"})
    _setup(mock_list, mock_cluster, pods=[pod], deps=[dep])
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline").json()
    assert "workloads" in body and "summary" in body
    s = body["summary"]
    for key in ["total_workloads", "workloads_with_restarts", "total_restarts"]:
        assert key in s
    r = body["workloads"][0]
    for key in ["kind", "name", "namespace", "total_restarts", "pod_count", "last_restart", "top_pods"]:
        assert key in r
    p = r["top_pods"][0]
    for key in ["pod", "namespace", "restarts", "last_restart"]:
        assert key in p


# ── top_pods capped at 5 ──────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_top_pods_capped_at_5(mock_list, mock_cluster):
    pods = [_pod(f"app-{i}", labels={"app": "big"}, restarts=i+1) for i in range(8)]
    dep = _dep("big", selector={"app": "big"})
    _setup(mock_list, mock_cluster, pods=pods, deps=[dep])
    body = client.get("/clusters/ns/cluster/workloads/restart-timeline").json()
    assert len(body["workloads"][0]["top_pods"]) <= 5
