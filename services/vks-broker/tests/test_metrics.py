"""Tests for /metrics Prometheus endpoint."""
import pytest
from unittest.mock import AsyncMock, patch
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def test_metrics_returns_text_plain():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_metrics_contains_expected_metric_names():
    resp = client.get("/metrics")
    body = resp.text
    assert "vks_broker_requests_total" in body
    assert "vks_broker_errors_total" in body
    assert "vks_broker_imported_clusters" in body


def test_metrics_records_requests():
    client.get("/health")
    resp = client.get("/metrics")
    body = resp.text
    # health endpoint should appear in request counts
    assert "/health" in body


def test_metrics_imported_cluster_gauge():
    resp = client.get("/metrics")
    body = resp.text
    # gauge line should exist with a numeric value
    gauge_lines = [l for l in body.splitlines() if "vks_broker_imported_clusters" in l and not l.startswith("#")]
    assert len(gauge_lines) == 1
    val = gauge_lines[0].split()[-1]
    assert val.isdigit()
