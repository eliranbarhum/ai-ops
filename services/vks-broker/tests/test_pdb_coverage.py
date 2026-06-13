"""Tests for PDB Coverage Analyzer endpoint (Loop 57)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _dep(name, ns="default", replicas=2, labels=None):
    sel = labels or {"app": name}
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"replicas": replicas, "template": {"metadata": {"labels": sel}}},
        "status": {"readyReplicas": replicas},
    }


def _sts(name, ns="default", replicas=3, labels=None):
    sel = labels or {"app": name}
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"replicas": replicas, "template": {"metadata": {"labels": sel}}},
        "status": {"readyReplicas": replicas},
    }


def _pdb(name, ns="default", selector=None, min_available=1, max_unavailable=None):
    spec = {"selector": {"matchLabels": selector or {"app": name}}}
    if max_unavailable is not None:
        spec["maxUnavailable"] = max_unavailable
    else:
        spec["minAvailable"] = min_available
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": spec,
        "status": {"currentHealthy": min_available, "disruptionsAllowed": 1},
    }


def _setup(mock_list, mock_cluster, deps=None, sts=None, pdbs=None):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    data = {
        "deployments": deps or [],
        "statefulsets": sts or [],
        "poddisruptionbudgets": pdbs or [],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_empty_cluster(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster)
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    assert body["summary"]["total_workloads"] == 0
    assert body["summary"]["uncovered"] == 0
    assert body["workloads"] == []


# ── Covered deployment shows covered=True ─────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_covered_deployment(mock_list, mock_cluster):
    dep = _dep("web", labels={"app": "web"})
    pdb = _pdb("web-pdb", selector={"app": "web"}, min_available=1)
    _setup(mock_list, mock_cluster, deps=[dep], pdbs=[pdb])
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    r = body["workloads"][0]
    assert r["covered"] is True
    assert r["pdb_name"] == "web-pdb"
    assert body["summary"]["covered"] == 1
    assert body["summary"]["uncovered"] == 0


# ── Uncovered deployment flagged with no_pdb ─────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_uncovered_deployment_flagged(mock_list, mock_cluster):
    dep = _dep("api", labels={"app": "api"})
    _setup(mock_list, mock_cluster, deps=[dep])
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    r = body["workloads"][0]
    assert r["covered"] is False
    assert "no_pdb" in r["issues"]
    assert body["summary"]["uncovered"] == 1


# ── StatefulSet covered by matching PDB ──────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_statefulset_covered(mock_list, mock_cluster):
    st = _sts("db", labels={"app": "db"})
    pdb = _pdb("db-pdb", selector={"app": "db"}, min_available=2)
    _setup(mock_list, mock_cluster, sts=[st], pdbs=[pdb])
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    r = body["workloads"][0]
    assert r["kind"] == "StatefulSet"
    assert r["covered"] is True


# ── Namespace mismatch: PDB in other ns doesn't cover ─────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_namespace_mismatch(mock_list, mock_cluster):
    dep = _dep("web", ns="prod", labels={"app": "web"})
    pdb = _pdb("web-pdb", ns="staging", selector={"app": "web"})
    _setup(mock_list, mock_cluster, deps=[dep], pdbs=[pdb])
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    r = body["workloads"][0]
    assert r["covered"] is False


# ── Misconfigured PDB: min_available >= replicas ──────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_misconfigured_pdb_detected(mock_list, mock_cluster):
    dep = _dep("web", replicas=2, labels={"app": "web"})
    pdb = _pdb("web-pdb", selector={"app": "web"}, min_available=2)  # == replicas
    _setup(mock_list, mock_cluster, deps=[dep], pdbs=[pdb])
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    r = body["workloads"][0]
    assert r["covered"] is True
    assert r["pdb_quality"] == "misconfigured"
    assert "pdb_misconfigured" in r["issues"]
    assert body["summary"]["misconfigured_pdbs"] == 1


# ── min_replicas filter excludes single-replica workloads ────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_min_replicas_filter(mock_list, mock_cluster):
    dep_single = _dep("singleton", replicas=1, labels={"app": "singleton"})
    dep_multi  = _dep("cluster", replicas=3, labels={"app": "cluster"})
    _setup(mock_list, mock_cluster, deps=[dep_single, dep_multi])
    body = client.get("/clusters/ns/cluster/pdb-coverage?min_replicas=2").json()
    names = [r["name"] for r in body["workloads"]]
    assert "singleton" not in names
    assert "cluster" in names


# ── Uncovered workloads sorted first ─────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_uncovered_sorted_first(mock_list, mock_cluster):
    dep_ok  = _dep("covered", labels={"app": "covered"})
    dep_bad = _dep("naked", labels={"app": "naked"})
    pdb = _pdb("covered-pdb", selector={"app": "covered"})
    _setup(mock_list, mock_cluster, deps=[dep_ok, dep_bad], pdbs=[pdb])
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    assert body["workloads"][0]["name"] == "naked"


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    dep = _dep("api", labels={"app": "api"})
    pdb = _pdb("api-pdb", selector={"app": "api"}, min_available=1)
    _setup(mock_list, mock_cluster, deps=[dep], pdbs=[pdb])
    body = client.get("/clusters/ns/cluster/pdb-coverage").json()
    assert "workloads" in body
    assert "pdbs" in body
    assert "summary" in body
    r = body["workloads"][0]
    for field in ["kind", "name", "namespace", "replicas", "ready",
                  "covered", "pdb_name", "pdb_min_available", "pdb_max_unavailable",
                  "pdb_quality", "issues"]:
        assert field in r, f"missing field: {field}"
    s = body["summary"]
    for key in ["total_workloads", "covered", "uncovered", "misconfigured_pdbs", "total_pdbs"]:
        assert key in s, f"missing summary key: {key}"
