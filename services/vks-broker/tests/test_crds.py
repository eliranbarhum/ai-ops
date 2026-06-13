"""Tests for CRD browser and resource YAML endpoints (Loop 38)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _crd(name, group, kind, plural, scope="Namespaced", versions=None):
    return {
        "metadata": {"name": name, "creationTimestamp": "2024-01-01T00:00:00Z"},
        "spec": {
            "group": group,
            "scope": scope,
            "names": {"kind": kind, "plural": plural},
            "versions": [{"name": v, "served": True} for v in (versions or ["v1"])],
        },
        "status": {
            "conditions": [{"type": "Established", "status": "True", "reason": "InitialNamesAccepted"}]
        },
    }


def _make_client_mock(items=None, crd=None):
    mock_http = MagicMock()
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = {"items": items or []}
    mock_http.get = AsyncMock(return_value=list_resp)
    if crd is not None:
        crd_resp = MagicMock()
        crd_resp.status_code = 200
        crd_resp.json.return_value = crd
        inst_resp = MagicMock()
        inst_resp.status_code = 200
        inst_resp.json.return_value = {"items": []}

        def side_effect(url, **kwargs):
            if "customresourcedefinitions/" in url and not url.endswith("customresourcedefinitions"):
                return crd_resp
            return inst_resp

        mock_http.get = AsyncMock(side_effect=lambda url, **kw: (
            crd_resp if ("customresourcedefinitions/" in url and "/instances" not in url and not url.endswith("customresourcedefinitions"))
            else list_resp
        ))
    return mock_http


@patch("main._cluster")
def test_list_crds_structure(mock_cluster):
    mock_http = _make_client_mock(items=[
        _crd("myresources.example.com", "example.com", "MyResource", "myresources"),
    ])
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/crds")
    assert resp.status_code == 200
    body = resp.json()
    assert "crds" in body
    assert "total" in body
    assert body["total"] == 1


@patch("main._cluster")
def test_list_crds_fields(mock_cluster):
    mock_http = _make_client_mock(items=[
        _crd("myresources.example.com", "example.com", "MyResource", "myresources", "Namespaced", ["v1", "v1beta1"]),
    ])
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/crds")
    crd = resp.json()["crds"][0]
    assert crd["name"] == "myresources.example.com"
    assert crd["group"] == "example.com"
    assert crd["kind"] == "MyResource"
    assert crd["scope"] == "Namespaced"
    assert "v1" in crd["versions"]
    assert crd["established"] == "True"


@patch("main._cluster")
def test_list_crds_cluster_scope(mock_cluster):
    mock_http = _make_client_mock(items=[
        _crd("clusterresources.example.com", "example.com", "ClusterResource", "clusterresources", "Cluster"),
    ])
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/crds")
    crd = resp.json()["crds"][0]
    assert crd["scope"] == "Cluster"


@patch("main._cluster")
def test_list_crds_empty(mock_cluster):
    mock_http = _make_client_mock(items=[])
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/crds")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["crds"] == []


@patch("main._cluster")
def test_list_crds_sorted_alphabetically(mock_cluster):
    mock_http = _make_client_mock(items=[
        _crd("zoo.example.com", "example.com", "Zoo", "zoos"),
        _crd("alpha.example.com", "example.com", "Alpha", "alphas"),
    ])
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/crds")
    names = [c["name"] for c in resp.json()["crds"]]
    assert names == sorted(names)


@patch("main._cluster")
def test_crd_instances_structure(mock_cluster):
    crd_obj = _crd("myresources.example.com", "example.com", "MyResource", "myresources")
    mock_http = MagicMock()
    crd_resp = MagicMock()
    crd_resp.status_code = 200
    crd_resp.json.return_value = crd_obj
    inst_resp = MagicMock()
    inst_resp.status_code = 200
    inst_resp.json.return_value = {
        "items": [
            {"metadata": {"name": "inst-1", "namespace": "default", "creationTimestamp": "2024-01-01T00:00:00Z", "labels": {"env": "prod"}}},
        ]
    }

    call_count = [0]
    async def get_side_effect(url, **kw):
        call_count[0] += 1
        if "customresourcedefinitions/myresources" in url:
            return crd_resp
        return inst_resp

    mock_http.get = AsyncMock(side_effect=get_side_effect)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/crds/myresources.example.com/instances")
    assert resp.status_code == 200
    body = resp.json()
    assert "instances" in body
    assert body["total"] == 1
    assert body["instances"][0]["name"] == "inst-1"


@patch("main._cluster")
def test_crd_instances_404(mock_cluster):
    mock_http = MagicMock()
    not_found = MagicMock()
    not_found.status_code = 404
    not_found.text = "Not Found"
    mock_http.get = AsyncMock(return_value=not_found)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/crds/nonexistent.example.com/instances")
    assert resp.status_code == 404


@patch("main._cluster")
def test_resource_yaml_endpoint(mock_cluster):
    mock_http = MagicMock()
    yaml_resp = MagicMock()
    yaml_resp.status_code = 200
    yaml_resp.json.return_value = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "my-pod", "namespace": "default"},
        "spec": {"containers": [{"name": "app", "image": "nginx:1.21"}]},
        "status": {"phase": "Running"},
    }
    mock_http.get = AsyncMock(return_value=yaml_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/resource-yaml?api_path=/api/v1/namespaces/default/pods/my-pod")
    assert resp.status_code == 200
    body = resp.json()
    assert "yaml" in body
    assert body["kind"] == "Pod"
    assert body["name"] == "my-pod"
    assert "apiVersion" in body["yaml"]


@patch("main._cluster")
def test_resource_yaml_scrubs_secrets(mock_cluster):
    mock_http = MagicMock()
    secret_resp = MagicMock()
    secret_resp.status_code = 200
    secret_resp.json.return_value = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "my-secret", "namespace": "default"},
        "data": {"password": "c3VwZXJzZWNyZXQ=", "username": "YWRtaW4="},
    }
    mock_http.get = AsyncMock(return_value=secret_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.get("/clusters/ns/cluster/resource-yaml?api_path=/api/v1/namespaces/default/secrets/my-secret")
    assert resp.status_code == 200
    body = resp.json()
    # Secret data should be scrubbed
    assert "***" in body["yaml"]
    assert "c3VwZXJzZWNyZXQ=" not in body["yaml"]


@patch("main._cluster")
def test_resource_yaml_invalid_path(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.get("/clusters/ns/cluster/resource-yaml?api_path=/invalid/path")
    assert resp.status_code == 400
