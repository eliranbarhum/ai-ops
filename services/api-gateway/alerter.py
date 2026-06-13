"""
Background alerter — subscribes to mco:events on Redis, matches rules, fires webhooks.
Started as a lifespan task in main.py.
"""
import asyncio
import json
import logging
import time

import httpx

logger = logging.getLogger("api-gateway.alerter")

CONFIG_STORE_URL = "http://config-store:8009"

_last_fired: dict[str, float] = {}  # in-memory fallback when Redis unavailable
_DEBOUNCE_S = 300                   # max one alert per rule per 5 minutes (also used as Redis TTL)


async def _load_rules_and_channels() -> tuple[list, dict]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            rules_r, chans_r = await asyncio.gather(
                client.get(f"{CONFIG_STORE_URL}/alert-rules"),
                client.get(f"{CONFIG_STORE_URL}/alert-channels"),
            )
        rules = rules_r.json().get("rules", []) if rules_r.status_code == 200 else []
        channels_list = chans_r.json().get("channels", []) if chans_r.status_code == 200 else []
        channels = {c["id"]: c for c in channels_list}
        return rules, channels
    except Exception as e:
        logger.debug("alerter: failed to load rules/channels: %s", e)
        return [], {}


def _matches(rule: dict, event: dict) -> bool:
    if not rule.get("enabled"):
        return False
    if rule.get("event_type") != event.get("type"):
        return False
    cond = rule.get("condition", {})
    if not cond:
        return True
    field = cond.get("field")
    op = cond.get("op", "lt")
    threshold = cond.get("threshold")
    if field and threshold is not None:
        val = event.get(field)
        if val is None:
            return False
        try:
            if op == "lt":
                return float(val) < float(threshold)
            if op == "gt":
                return float(val) > float(threshold)
            if op == "eq":
                return str(val) == str(threshold)
        except (TypeError, ValueError):
            return False
    return True


def _build_payload(channel: dict, event: dict, rule: dict) -> dict:
    text = event.get("summary", f"MCO Alert: {rule.get('name', 'rule triggered')}")
    ch_type = channel.get("type", "webhook")
    if ch_type == "slack":
        return {
            "text": f":rotating_light: *MCO Alert* — {rule['name']}",
            "attachments": [{"text": text, "color": "danger"}],
        }
    if ch_type == "teams":
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "FF0000",
            "summary": rule["name"],
            "sections": [{"activityTitle": f"MCO Alert: {rule['name']}", "text": text}],
        }
    # generic webhook / pagerduty
    return {
        "event_action": "trigger",
        "payload": {
            "summary": text,
            "severity": event.get("severity", "critical"),
            "source": "mco-platform",
            "custom_details": event,
        },
        "routing_key": channel.get("config", {}).get("routing_key", ""),
    }


async def _fire(channel: dict, event: dict, rule: dict) -> None:
    cfg = channel.get("config", {})
    url = cfg.get("webhook_url") or cfg.get("url", "")
    if not url:
        return
    payload = _build_payload(channel, event, rule)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
            logger.info("alerter: fired %s → %s → %d", rule["name"], channel["name"], r.status_code)
    except Exception as e:
        logger.warning("alerter: fire failed for channel %s: %s", channel.get("name"), e)


async def _process_event(event: dict, redis_client=None) -> None:
    rules, channels = await _load_rules_and_channels()
    now = time.time()
    for rule in rules:
        if not _matches(rule, event):
            continue
        rule_id = rule["id"]
        # Debounce via Redis (survives restarts); fall back to in-memory
        if redis_client is not None:
            key = f"alerter:fired:{rule_id}"
            already_fired = await redis_client.set(key, "1", nx=True, ex=_DEBOUNCE_S)
            if not already_fired:
                continue
        else:
            if now - _last_fired.get(rule_id, 0) < _DEBOUNCE_S:
                continue
            _last_fired[rule_id] = now
        for ch_id in rule.get("channel_ids", []):
            ch = channels.get(ch_id)
            if ch:
                asyncio.create_task(_fire(ch, event, rule))


async def run_alerter() -> None:
    """Main loop — subscribes to Redis mco:events, falls back to no-op if Redis unavailable."""
    logger.info("alerter: starting")
    while True:
        try:
            import redis.asyncio as aioredis
            import os
            redis_url = os.getenv("REDIS_URL", "")
            if not redis_url:
                await asyncio.sleep(60)
                continue
            client = aioredis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3)
            await client.ping()
            pubsub = client.pubsub()
            await pubsub.subscribe("mco:events")
            logger.info("alerter: subscribed to mco:events")
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    await _process_event(event, redis_client=client)
                except Exception as e:
                    logger.debug("alerter: event parse error: %s", e)
        except Exception as e:
            logger.warning("alerter: Redis error, retrying in 30s: %s", e)
            await asyncio.sleep(30)
