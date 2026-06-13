"""Tests for Pod Anti-Affinity Coverage endpoint (Loop 58)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _dep(name, ns="default", replicas=3, anti_affinity=None, tsc=None):
    pod_spec: dict = {}
    if anti_affinity:
        pod_spec["affinity"] = {"podAntiAffinity": anti_affinity}
    if tsc:
        pod_spec["topologySpreadConstraints"] = tsc
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"replicas": replicas, "template": {"metadata": {"labels": {"app": name}}, "spec": pod_spec}},
        "status": {"readyReplicas": replicas},
    }


def _required_anti(key="kubernetes.io/hostname"):
    return {"requiredDuringSchedulingIgnoredDuringExecution": [{"topologyKey": key}]}


def _preferred_anti(key="kubernetes.io/hostname"):
    return {"preferredDuringSchedulingIgnoredDuringExecution": [{"weight": 100, "podAffinityTerm": {"topologyKey": key}}]}


def _tsc_required(key="kubernetes.io/hostname"):
    return [{"maxSkew": 1, "topologyKey": key, "whenUnsatisfiable": "DoNotSchedule", "labelSelector": {}}]


def _tsc_preferred(key="kubernetes.io/hostname"):
    return [{"maxSkew": 1, "topologyKey": key, "whenUnsatisfiable": "ScheduleAnyway", "labelSelector": {}}]


def _setup(mock_list, mock_cluster, deps=None, sts=None):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    data = {"deployments": deps or [], "statefulsets": sts or []}
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_empty_cluster(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster)
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    assert body["summary"]["total_workloads"] == 0
    assert body["workloads"] == []


# ── Deployment with no anti-affinity is unprotected ──────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_no_anti_affinity_unprotected(mock_list, mock_cluster):
    dep = _dep("web")
    _setup(mock_list, mock_cluster, deps=[dep])
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    r = body["workloads"][0]
    assert r["protection"] == "none"
    assert "no_anti_affinity" in r["issues"]
    assert body["summary"]["unprotected"] == 1


# ── Required podAntiAffinity → fully protected ────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_required_anti_affinity_protected(mock_list, mock_cluster):
    dep = _dep("api", anti_affinity=_required_anti())
    _setup(mock_list, mock_cluster, deps=[dep])
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    r = body["workloads"][0]
    assert r["protection"] == "required"
    assert r["required_anti_affinity"] is True
    assert body["summary"]["fully_protected"] == 1
    assert body["summary"]["unprotected"] == 0


# ── Preferred podAntiAffinity → preferred only ───────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_preferred_anti_affinity_partial(mock_list, mock_cluster):
    dep = _dep("api", anti_affinity=_preferred_anti())
    _setup(mock_list, mock_cluster, deps=[dep])
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    r = body["workloads"][0]
    assert r["protection"] == "preferred"
    assert body["summary"]["preferred_only"] == 1


# ── TSC with DoNotSchedule → required protection ─────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_tsc_required_protection(mock_list, mock_cluster):
    dep = _dep("db", tsc=_tsc_required())
    _setup(mock_list, mock_cluster, deps=[dep])
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    r = body["workloads"][0]
    assert r["protection"] == "required"
    assert r["has_tsc"] is True
    assert r["tsc_count"] == 1


# ── TSC with ScheduleAnyway → preferred protection ───────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_tsc_preferred_protection(mock_list, mock_cluster):
    dep = _dep("db", tsc=_tsc_preferred())
    _setup(mock_list, mock_cluster, deps=[dep])
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    r = body["workloads"][0]
    assert r["protection"] == "preferred"


# ── min_replicas filter excludes single-replica workloads ────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_min_replicas_filter(mock_list, mock_cluster):
    dep_single = _dep("singleton", replicas=1)
    dep_multi  = _dep("cluster", replicas=4)
    _setup(mock_list, mock_cluster, deps=[dep_single, dep_multi])
    body = client.get("/clusters/ns/cluster/affinity-coverage?min_replicas=2").json()
    names = [r["name"] for r in body["workloads"]]
    assert "singleton" not in names
    assert "cluster" in names


# ── Unprotected sorted before protected ──────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_unprotected_sorted_first(mock_list, mock_cluster):
    dep_protected = _dep("safe", anti_affinity=_required_anti())
    dep_naked     = _dep("naked")
    _setup(mock_list, mock_cluster, deps=[dep_protected, dep_naked])
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    assert body["workloads"][0]["name"] == "naked"


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    dep = _dep("api")
    _setup(mock_list, mock_cluster, deps=[dep])
    body = client.get("/clusters/ns/cluster/affinity-coverage").json()
    assert "workloads" in body and "summary" in body
    r = body["workloads"][0]
    for field in ["kind", "name", "namespace", "replicas", "ready", "protection",
                  "has_anti_affinity", "has_tsc", "tsc_count",
                  "required_anti_affinity", "preferred_anti_affinity", "issues"]:
        assert field in r, f"missing field: {field}"
    s = body["summary"]
    for key in ["total_workloads", "unprotected", "preferred_only", "fully_protected"]:
        assert key in s, f"missing summary key: {key}"
