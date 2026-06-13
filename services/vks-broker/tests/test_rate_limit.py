"""Tests for token-bucket rate limiting middleware."""
import pytest
from unittest.mock import AsyncMock, patch
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _reset_rate(burst: int = 3):
    """Clear buckets and set a tight burst so tests can reach the limit quickly."""
    app_module._rate_buckets.clear()
    app_module._RATE_BURST = burst
    app_module._RATE_LIMIT = 100  # high RPS so bucket refills slowly in tests


def _restore_rate():
    app_module._rate_buckets.clear()
    app_module._RATE_BURST = 60
    app_module._RATE_LIMIT = 30


def test_requests_within_burst_succeed():
    _reset_rate(burst=3)
    try:
        ip = "1.2.3.4"
        for _ in range(3):
            assert app_module._check_rate(ip) is True
    finally:
        _restore_rate()


def test_request_exceeding_burst_gets_429():
    _reset_rate(burst=3)
    try:
        ip = "2.3.4.5"
        for _ in range(3):
            app_module._check_rate(ip)
        assert app_module._check_rate(ip) is False
    finally:
        _restore_rate()


def test_rate_limit_depletes_correctly():
    _reset_rate(burst=2)
    try:
        ip = "4.5.6.7"
        assert app_module._check_rate(ip) is True
        assert app_module._check_rate(ip) is True
        assert app_module._check_rate(ip) is False  # bucket empty
    finally:
        _restore_rate()
