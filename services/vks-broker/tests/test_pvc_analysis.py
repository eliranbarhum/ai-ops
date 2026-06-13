"""Tests for PVC Analysis endpoint (Loop 54)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pvc(name, ns="default", phase="Bound", capacity="10Gi", access_modes=None, sc="standard"):
    return {
        "metadata": {"name": name, "namespace": ns, "creationTimestamp": "2026-01-01T00:00:00Z"},
        "spec": {
            "accessModes": access_modes or ["ReadWriteOnce"],
            "storageClassName": sc,
            "resources": {"requests": {"storage": capacity}},
            "volumeName": f"pv-{name}",
        },
        "status": {"phase": phase, "capacity": {"storage": capacity}},
    }


def _pod(name, ns="default", pvc_claims: list[str] = None):
    vols = [{"name": f"vol-{c}", "persistentVolumeClaim": {"claimName": c}} for c in (pvc_claims or [])]
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"volumes": vols},
        "status": {"phase": "Running"},
    }


def _setup(mock_list, mock_cluster, pvcs, pods):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    data = {"pvcs": pvcs, "pods": pods}
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_empty_cluster(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster, pvcs=[], pods=[])
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    assert body["summary"]["total"] == 0
    assert body["pvcs"] == []


# ── Mounted PVC not orphaned ──────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_mounted_pvc_not_orphaned(mock_list, mock_cluster):
    pvc = _pvc("db-data", ns="default", phase="Bound")
    pod = _pod("db", ns="default", pvc_claims=["db-data"])
    _setup(mock_list, mock_cluster, pvcs=[pvc], pods=[pod])
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    result = body["pvcs"][0]
    assert result["orphaned"] is False
    assert result["mount_count"] == 1
    assert "db" in result["mounting_pods"]
    assert body["summary"]["orphaned"] == 0


# ── Bound PVC with no pod → orphaned ─────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_bound_unmounted_is_orphaned(mock_list, mock_cluster):
    pvc = _pvc("old-pvc", ns="default", phase="Bound")
    _setup(mock_list, mock_cluster, pvcs=[pvc], pods=[])
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    r = body["pvcs"][0]
    assert r["orphaned"] is True
    assert "bound_not_mounted" in r["issues"]
    assert body["summary"]["orphaned"] == 1


# ── Pending PVC ───────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_pending_pvc_flagged(mock_list, mock_cluster):
    pvc = _pvc("stuck-pvc", phase="Pending")
    _setup(mock_list, mock_cluster, pvcs=[pvc], pods=[])
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    r = body["pvcs"][0]
    assert "pending" in r["issues"]
    assert body["summary"]["pending"] == 1


# ── RWO PVC mounted by multiple pods → multi_mount_rwo ───────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_rwo_multi_mount_detected(mock_list, mock_cluster):
    pvc = _pvc("shared-rwo", access_modes=["ReadWriteOnce"])
    pods = [
        _pod("app-0", pvc_claims=["shared-rwo"]),
        _pod("app-1", pvc_claims=["shared-rwo"]),
    ]
    _setup(mock_list, mock_cluster, pvcs=[pvc], pods=pods)
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    r = body["pvcs"][0]
    assert "rwo_multi_mount" in r["issues"]
    assert body["summary"]["multi_mount_rwo"] == 1
    assert r["mount_count"] == 2


# ── ReadWriteMany with multiple pods is fine ─────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_rwx_multi_mount_ok(mock_list, mock_cluster):
    pvc = _pvc("shared-rwx", access_modes=["ReadWriteMany"])
    pods = [_pod("a", pvc_claims=["shared-rwx"]), _pod("b", pvc_claims=["shared-rwx"])]
    _setup(mock_list, mock_cluster, pvcs=[pvc], pods=pods)
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    r = body["pvcs"][0]
    assert "rwo_multi_mount" not in r["issues"]
    assert body["summary"]["multi_mount_rwo"] == 0


# ── Sorting: lost first, pending, orphaned, then ok ──────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_sort_order(mock_list, mock_cluster):
    pvcs = [
        _pvc("ok-pvc", phase="Bound"),
        _pvc("pending-pvc", phase="Pending"),
        _pvc("lost-pvc", phase="Lost"),
        _pvc("orphan-pvc", phase="Bound"),
    ]
    pods = [_pod("app", pvc_claims=["ok-pvc"])]
    _setup(mock_list, mock_cluster, pvcs=pvcs, pods=pods)
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    phases = [r["phase"] for r in body["pvcs"]]
    assert phases[0] == "Lost"
    assert phases[1] == "Pending"


# ── Capacity conversion ───────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_capacity_gib(mock_list, mock_cluster):
    pvc = _pvc("big-pvc", capacity="20Gi")
    _setup(mock_list, mock_cluster, pvcs=[pvc], pods=[])
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    assert body["pvcs"][0]["capacity_gib"] == pytest.approx(20.0, abs=0.1)
    assert body["summary"]["total_capacity_gib"] == pytest.approx(20.0, abs=0.1)


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    pvc = _pvc("test-pvc")
    pod = _pod("app", pvc_claims=["test-pvc"])
    _setup(mock_list, mock_cluster, pvcs=[pvc], pods=[pod])
    body = client.get("/clusters/ns/cluster/pvcs/analysis").json()
    assert "pvcs" in body and "summary" in body
    r = body["pvcs"][0]
    for field in ["name", "namespace", "phase", "access_modes", "storage_class",
                  "capacity", "capacity_gib", "mounting_pods", "mount_count",
                  "orphaned", "issues"]:
        assert field in r, f"missing field: {field}"
    s = body["summary"]
    for key in ["total", "bound", "pending", "orphaned", "multi_mount_rwo", "total_capacity_gib"]:
        assert key in s, f"missing summary key: {key}"
