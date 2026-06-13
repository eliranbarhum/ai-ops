"""
Confirm-token system for destructive VKS operations.
Tokens are HMAC-signed, single-use, and expire after 90 seconds.
"""
import hashlib
import hmac
import json
import os
import time
from typing import Any

_SECRET = os.getenv("CONFIRM_SECRET", "vks-broker-default-secret-change-in-prod").encode()
_TTL = 90  # seconds

# In-memory store of pending tokens: {token: (payload, issued_at)}
_pending: dict[str, tuple[dict, float]] = {}

_MAX_PENDING = 1000  # cap to prevent memory leak


def _sign(payload_json: str, issued_at: float) -> str:
    msg = f"{issued_at}:{payload_json}".encode()
    return hmac.new(_SECRET, msg, hashlib.sha256).hexdigest()


def issue_token(action: str, target: str, params: dict[str, Any]) -> dict:
    """Create a confirm token describing the action to be taken.
    Returns {token, action, target, params, expires_at}.
    """
    # Evict expired
    now = time.time()
    expired = [t for t, (_, issued) in _pending.items() if now - issued > _TTL]
    for t in expired:
        _pending.pop(t, None)

    if len(_pending) >= _MAX_PENDING:
        raise RuntimeError("Too many pending confirmations")

    payload = {"action": action, "target": target, "params": params}
    payload_json = json.dumps(payload, sort_keys=True)
    sig = _sign(payload_json, now)
    token = f"{now:.3f}:{sig}"

    _pending[token] = (payload, now)

    return {
        "token": token,
        "action": action,
        "target": target,
        "params": params,
        "expires_at": now + _TTL,
        "requires_confirm": True,
    }


def consume_token(token: str) -> dict:
    """Validate and consume a confirm token.
    Returns the payload dict on success. Raises ValueError on failure.
    """
    entry = _pending.pop(token, None)
    if not entry:
        raise ValueError("Token not found or already used")

    payload, issued = entry
    now = time.time()
    if now - issued > _TTL:
        raise ValueError(f"Token expired ({int(now - issued)}s ago)")

    # Verify signature
    payload_json = json.dumps(payload, sort_keys=True)
    expected_sig = _sign(payload_json, issued)
    provided_sig = token.split(":", 1)[1] if ":" in token else ""
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise ValueError("Token signature invalid")

    return payload
