"""Tests for GET /clusters/{id}/images (Loop 36)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name, ns, image, phase="Running"):
    return {
        "metadata": {"name": name, "namespace": ns},
        "status": {"phase": phase},
        "spec": {"containers": [{"name": "app", "image": image}], "initContainers": []},
    }


@patch("main._cluster")
@patch("main.kube_list")
def test_images_returns_list(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("a", "default", "nginx:1.21"),
        _pod("b", "default", "nginx:1.21"),
        _pod("c", "prod", "redis:7.0"),
    ])

    resp = client.get("/clusters/ns/cluster/images")
    assert resp.status_code == 200
    body = resp.json()
    assert "images" in body
    assert "total" in body
    assert body["total"] == 2


@patch("main._cluster")
@patch("main.kube_list")
def test_images_sorted_by_pod_count(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("a1", "default", "nginx:1.21"),
        _pod("a2", "default", "nginx:1.21"),
        _pod("a3", "default", "nginx:1.21"),
        _pod("b1", "default", "redis:7.0"),
    ])

    resp = client.get("/clusters/ns/cluster/images")
    images = resp.json()["images"]
    assert images[0]["pod_count"] == 3
    assert images[0]["short"] == "nginx"
    assert images[1]["pod_count"] == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_images_latest_tag_detection(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("a", "default", "nginx:latest"),
        _pod("b", "default", "redis:7.0"),
    ])

    resp = client.get("/clusters/ns/cluster/images")
    images = resp.json()["images"]
    latest_img = next(i for i in images if "nginx" in i["image"])
    versioned_img = next(i for i in images if "redis" in i["image"])
    assert latest_img["is_latest"] is True
    assert versioned_img["is_latest"] is False


@patch("main._cluster")
@patch("main.kube_list")
def test_images_no_tag_treated_as_latest(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("a", "default", "my-registry.io/myapp"),
    ])

    resp = client.get("/clusters/ns/cluster/images")
    img = resp.json()["images"][0]
    assert img["tag"] == "latest"
    assert img["is_latest"] is True


@patch("main._cluster")
@patch("main.kube_list")
def test_images_pinned_sha_detection(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("a", "default", "nginx@sha256:abc123def456"),
    ])

    resp = client.get("/clusters/ns/cluster/images")
    img = resp.json()["images"][0]
    assert img["is_pinned"] is True
    assert img["is_latest"] is False


@patch("main._cluster")
@patch("main.kube_list")
def test_images_namespace_spread(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("a", "dev", "nginx:1.21"),
        _pod("b", "prod", "nginx:1.21"),
        _pod("c", "staging", "nginx:1.21"),
    ])

    resp = client.get("/clusters/ns/cluster/images")
    img = resp.json()["images"][0]
    assert sorted(img["namespaces"]) == ["dev", "prod", "staging"]
    assert img["pod_count"] == 3


@patch("main._cluster")
@patch("main.kube_list")
def test_images_empty(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])

    resp = client.get("/clusters/ns/cluster/images")
    assert resp.status_code == 200
    body = resp.json()
    assert body["images"] == []
    assert body["total"] == 0


@patch("main._cluster")
@patch("main.kube_list")
def test_images_includes_init_containers(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        {
            "metadata": {"name": "pod-a", "namespace": "default"},
            "status": {"phase": "Running"},
            "spec": {
                "containers": [{"name": "main", "image": "nginx:1.21"}],
                "initContainers": [{"name": "init", "image": "busybox:1.34"}],
            },
        }
    ])

    resp = client.get("/clusters/ns/cluster/images")
    images = resp.json()["images"]
    image_names = [i["image"] for i in images]
    assert "nginx:1.21" in image_names
    assert "busybox:1.34" in image_names
