"""Tests for GET /clusters/{id}/topology (Loop 37)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _deploy(name, ns, selector):
    return {
        "metadata": {"name": name, "namespace": ns},
        "kind": "Deployment",
        "spec": {"selector": {"matchLabels": selector}},
    }


def _svc(name, ns, selector, svc_type="ClusterIP", ports=None):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "selector": selector,
            "type": svc_type,
            "ports": ports or [{"port": 80}],
        },
    }


def _ingress(name, ns, rules):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "rules": [
                {
                    "host": r.get("host", ""),
                    "http": {
                        "paths": [
                            {
                                "path": r.get("path", "/"),
                                "backend": {
                                    "service": {
                                        "name": r["service"],
                                        "port": {"number": r.get("port", 80)},
                                    }
                                },
                            }
                        ]
                    },
                }
                for r in rules
            ]
        },
    }


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_returns_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "deployments": [_deploy("api", "default", {"app": "api"})],
        "statefulsets": [],
        "services": [_svc("api-svc", "default", {"app": "api"})],
        "ingresses": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/topology")
    assert resp.status_code == 200
    body = resp.json()
    assert "workloads" in body
    assert "services" in body
    assert "ingresses" in body


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_service_targets_matching_workload(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "deployments": [_deploy("api", "default", {"app": "api", "tier": "backend"})],
        "statefulsets": [],
        "services": [_svc("api-svc", "default", {"app": "api"})],
        "ingresses": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/topology")
    body = resp.json()
    svc = body["services"][0]
    assert len(svc["targets"]) == 1
    assert "default/api" in svc["targets"][0] or svc["targets"][0].endswith("api")


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_service_no_match_when_selector_mismatch(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "deployments": [_deploy("api", "default", {"app": "api"})],
        "statefulsets": [],
        "services": [_svc("other-svc", "default", {"app": "other"})],
        "ingresses": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/topology")
    body = resp.json()
    svc = body["services"][0]
    assert svc["targets"] == []


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_ingress_rules_present(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "deployments": [],
        "statefulsets": [],
        "services": [_svc("frontend", "prod", {"app": "frontend"})],
        "ingresses": [_ingress("web-ing", "prod", [{"host": "app.example.com", "service": "frontend", "port": 80}])],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/topology")
    body = resp.json()
    assert len(body["ingresses"]) == 1
    ing = body["ingresses"][0]
    assert ing["name"] == "web-ing"
    assert len(ing["rules"]) == 1
    assert ing["rules"][0]["host"] == "app.example.com"
    assert ing["rules"][0]["paths"][0]["service"] == "frontend"


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_statefulsets_included(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "deployments": [],
        "statefulsets": [
            {
                "metadata": {"name": "db", "namespace": "default"},
                "kind": "StatefulSet",
                "spec": {"selector": {"matchLabels": {"app": "db"}}},
            }
        ],
        "services": [_svc("db-svc", "default", {"app": "db"})],
        "ingresses": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/topology")
    body = resp.json()
    workload_kinds = [w["kind"] for w in body["workloads"]]
    assert "StatefulSet" in workload_kinds
    svc = body["services"][0]
    assert len(svc["targets"]) == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_empty_cluster(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])

    resp = client.get("/clusters/ns/cluster/topology")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workloads"] == []
    assert body["services"] == []
    assert body["ingresses"] == []


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_cross_namespace_no_match(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "deployments": [_deploy("api", "ns-a", {"app": "api"})],
        "statefulsets": [],
        "services": [_svc("api-svc", "ns-b", {"app": "api"})],
        "ingresses": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/topology")
    body = resp.json()
    # Service in ns-b should NOT match workload in ns-a
    svc = body["services"][0]
    assert svc["targets"] == []


@patch("main._cluster")
@patch("main.kube_list")
def test_topology_workload_id_format(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "deployments": [_deploy("api", "default", {"app": "api"})],
        "statefulsets": [],
        "services": [],
        "ingresses": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/topology")
    body = resp.json()
    w = body["workloads"][0]
    assert "id" in w
    assert w["name"] == "api"
    assert w["namespace"] == "default"
    assert w["kind"] == "Deployment"
