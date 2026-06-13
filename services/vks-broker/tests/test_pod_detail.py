"""Tests for /pods/detail endpoint (Loop 4)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _make_pod(name="my-pod", namespace="default", phase="Running"):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "nodeName": "node-1",
            "containers": [{
                "name": "app",
                "image": "nginx:latest",
                "env": [
                    {"name": "DB_URL", "value": "postgres://localhost/db"},
                    {"name": "SECRET_KEY", "valueFrom": {"secretKeyRef": {"name": "my-secret", "key": "key"}}},
                    {"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
                    {"name": "CM_VAL", "valueFrom": {"configMapKeyRef": {"name": "my-cm", "key": "val"}}},
                ],
                "volumeMounts": [{"name": "data", "mountPath": "/data", "readOnly": True}],
                "livenessProbe": {
                    "httpGet": {"path": "/health", "port": 8080},
                    "initialDelaySeconds": 5, "periodSeconds": 10, "failureThreshold": 3,
                },
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "500m", "memory": "512Mi"},
                },
            }],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "my-pvc"}}],
        },
        "status": {
            "phase": phase,
            "conditions": [{"type": "Ready", "status": "True", "reason": "", "message": ""}],
            "containerStatuses": [{
                "name": "app", "ready": True, "restartCount": 0,
                "state": {"running": {}},
            }],
        },
    }


@patch("main._cluster")
def test_pod_detail_returns_containers(mock_cluster):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=MagicMock(
        status_code=200, json=lambda: _make_pod()
    ))
    mock_cluster.return_value = mock_client
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    r = client.get("/clusters/ns/cluster/pods/detail?name=my-pod&namespace=default")
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "my-pod"
    assert d["node"] == "node-1"
    assert len(d["containers"]) == 1
    c = d["containers"][0]
    assert c["name"] == "app"
    assert c["image"] == "nginx:latest"


@patch("main._cluster")
def test_pod_detail_env_vars_parsed(mock_cluster):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=MagicMock(
        status_code=200, json=lambda: _make_pod()
    ))
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    r = client.get("/clusters/ns/cluster/pods/detail?name=my-pod&namespace=default")
    env = r.json()["containers"][0]["env"]
    plain = next(e for e in env if e["name"] == "DB_URL")
    assert plain["value"] == "postgres://localhost/db"
    assert plain["source"] is None

    secret_env = next(e for e in env if e["name"] == "SECRET_KEY")
    assert secret_env["value"] == "••••"
    assert "secret:" in secret_env["source"]

    field_env = next(e for e in env if e["name"] == "POD_NAME")
    assert "field:" in field_env["source"]

    cm_env = next(e for e in env if e["name"] == "CM_VAL")
    assert "configmap:" in cm_env["source"]


@patch("main._cluster")
def test_pod_detail_volume_mounts(mock_cluster):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=MagicMock(
        status_code=200, json=lambda: _make_pod()
    ))
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    r = client.get("/clusters/ns/cluster/pods/detail?name=my-pod&namespace=default")
    vm = r.json()["containers"][0]["volume_mounts"][0]
    assert vm["mount_path"] == "/data"
    assert vm["read_only"] is True
    assert vm["name"] == "data"


@patch("main._cluster")
def test_pod_detail_liveness_probe(mock_cluster):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=MagicMock(
        status_code=200, json=lambda: _make_pod()
    ))
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    r = client.get("/clusters/ns/cluster/pods/detail?name=my-pod&namespace=default")
    probe = r.json()["containers"][0]["liveness_probe"]
    assert probe["type"] == "httpGet"
    assert probe["path"] == "/health"
    assert probe["port"] == 8080
    assert probe["initial_delay"] == 5
    assert probe["period"] == 10


@patch("main._cluster")
def test_pod_detail_resources(mock_cluster):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=MagicMock(
        status_code=200, json=lambda: _make_pod()
    ))
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    r = client.get("/clusters/ns/cluster/pods/detail?name=my-pod&namespace=default")
    res = r.json()["containers"][0]["resources"]
    assert res["req_cpu"] == "100m"
    assert res["lim_mem"] == "512Mi"


@patch("main._cluster")
def test_pod_detail_404(mock_cluster):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=MagicMock(status_code=404))
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    r = client.get("/clusters/ns/cluster/pods/detail?name=missing-pod&namespace=default")
    assert r.status_code == 404


@patch("main._cluster")
def test_pod_detail_volumes_list(mock_cluster):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=MagicMock(
        status_code=200, json=lambda: _make_pod()
    ))
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    r = client.get("/clusters/ns/cluster/pods/detail?name=my-pod&namespace=default")
    vols = r.json()["volumes"]
    assert len(vols) == 1
    assert vols[0]["name"] == "data"
    assert vols[0]["type"] == "persistentVolumeClaim"
