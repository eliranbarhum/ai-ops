"""Tests for POST /clusters/{id}/cronjobs/{name}/trigger (Loop 31)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_CRONJOB = {
    "metadata": {"name": "my-cj", "namespace": "default"},
    "spec": {
        "schedule": "0 * * * *",
        "jobTemplate": {
            "spec": {
                "template": {
                    "spec": {"containers": [{"name": "app", "image": "my-app:latest"}]}
                }
            }
        },
    },
}


@patch("main._cluster")
def test_trigger_issues_confirm_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/cronjobs/my-cj/trigger?namespace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_trigger_executes_with_valid_token(mock_audit, mock_get, mock_cluster):
    mock_get.side_effect = AsyncMock(return_value=_CRONJOB)

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201
    mock_post_resp.json.return_value = {"metadata": {"name": "my-cj-manual-123"}}

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_post_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post("/clusters/ns/cluster/cronjobs/my-cj/trigger?namespace=default")
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/cronjobs/my-cj/trigger?namespace=default&token={token}",
        headers={"x-forwarded-user": "alice"},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body.get("ok") is True
    assert "job_name" in body
    assert body["job_name"].startswith("my-cj-manual-")
    mock_audit.assert_called_once()


@patch("main._cluster")
def test_trigger_rejects_invalid_token(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post("/clusters/ns/cluster/cronjobs/my-cj/trigger?namespace=default&token=bad")
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_trigger_token_is_single_use(mock_audit, mock_get, mock_cluster):
    mock_get.side_effect = AsyncMock(return_value=_CRONJOB)

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_post_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post("/clusters/ns/cluster/cronjobs/my-cj/trigger?namespace=default")
    token = resp.json()["token"]

    client.post(f"/clusters/ns/cluster/cronjobs/my-cj/trigger?namespace=default&token={token}")
    resp2 = client.post(f"/clusters/ns/cluster/cronjobs/my-cj/trigger?namespace=default&token={token}")
    assert resp2.status_code == 400
