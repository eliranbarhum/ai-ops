"""Tests for the confirm-token system."""
import time
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import confirm


def test_issue_and_consume():
    result = confirm.issue_token("scale", "ns/deploy", {"replicas": 3})
    assert result["requires_confirm"] is True
    assert result["action"] == "scale"
    token = result["token"]
    payload = confirm.consume_token(token)
    assert payload["action"] == "scale"
    assert payload["params"]["replicas"] == 3


def test_single_use():
    result = confirm.issue_token("restart", "ns/deploy", {})
    token = result["token"]
    confirm.consume_token(token)
    with pytest.raises(ValueError, match="not found or already used"):
        confirm.consume_token(token)


def test_invalid_token():
    with pytest.raises(ValueError):
        confirm.consume_token("not-a-real-token")


def test_tampered_token():
    result = confirm.issue_token("delete", "ns/pod", {})
    token = result["token"]
    tampered = token[:-4] + "xxxx"
    with pytest.raises(ValueError):
        confirm.consume_token(tampered)


def test_expired_token(monkeypatch):
    result = confirm.issue_token("cordon", "ns/node", {})
    token = result["token"]
    # Backdate the issued_at so it appears expired
    payload, _ = confirm._pending[token]
    confirm._pending[token] = (payload, time.time() - 200)
    with pytest.raises(ValueError, match="expired"):
        confirm.consume_token(token)


def test_evict_expired_on_issue():
    """Issuing a new token should evict expired ones."""
    old = confirm.issue_token("drain", "ns/node", {})
    tok = old["token"]
    payload, _ = confirm._pending[tok]
    confirm._pending[tok] = (payload, time.time() - 200)
    before = len(confirm._pending)
    confirm.issue_token("scale", "ns/d", {"replicas": 1})
    assert len(confirm._pending) <= before  # expired evicted
