"""
Discovery Engine — FastAPI service for network scanning via nmap.
"""
import asyncio
import base64
import ipaddress
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

try:
    from cryptography.fernet import Fernet as _Fernet, InvalidToken as _InvalidToken
    _FERNET_KEY = os.getenv("ENCRYPTION_KEY", "").encode()
    _fernet = _Fernet(_FERNET_KEY) if _FERNET_KEY else None
except Exception:
    _fernet = None

_REQUIRE_ENCRYPTION = os.getenv("REQUIRE_ENCRYPTION", "false").lower() == "true"
if _fernet is None and _REQUIRE_ENCRYPTION:
    raise RuntimeError(
        "ENCRYPTION_KEY env var is missing or invalid — refusing to start (REQUIRE_ENCRYPTION=true). "
        "Set ENCRYPTION_KEY to a valid Fernet key, or unset REQUIRE_ENCRYPTION to allow base64 fallback."
    )
elif _fernet is None:
    import warnings
    warnings.warn(
        "ENCRYPTION_KEY not set — credentials will be stored as base64 (not encrypted). "
        "Set ENCRYPTION_KEY for production use.",
        stacklevel=1,
    )

import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from network_sources import discover_networks
from scanner import scan_network
from vuln_scanner import run_vuln_scan, SCOPE_PROFILES, estimate_duration
from metrics import (
    vuln_scan_duration, vuln_scans_active,
    network_scan_duration, network_scans_active,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discovery-engine")

DB_PATH = os.getenv("DISCOVERY_DB", "/data/discovery.db")
REDIS_URL = os.getenv("REDIS_URL", "")

app = FastAPI(title="Discovery Engine", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)

# Fallback in-memory subscribers (used when Redis is unavailable)
_subscribers: dict[str, list[asyncio.Queue]] = {}

# Running scan tasks: scan_id → asyncio.Task
_running: dict[str, asyncio.Task] = {}

_redis = None

# Strong references to fire-and-forget tasks (prevents GC before completion)
_pending_tasks: set[asyncio.Task] = set()

async def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    if not REDIS_URL:
        return None
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        await client.ping()
        _redis = client
        logger.info("Redis connected for pub/sub")
    except Exception as e:
        logger.warning("Redis unavailable, using in-memory pub/sub: %s", e)
        return None
    return _redis


async def _next_redis_msg(pubsub):
    while True:
        msg = await pubsub.get_message(ignore_subscribe_messages=True)
        if msg:
            return msg
        await asyncio.sleep(0.05)


# ─── DB init ──────────────────────────────────────────────────────────────────

async def _init_db(db: aiosqlite.Connection):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS scans (
            id TEXT PRIMARY KEY,
            cidr TEXT NOT NULL,
            label TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT,
            completed_at TEXT,
            host_count INTEGER DEFAULT 0,
            hosts_found INTEGER DEFAULT 0,
            hosts_scanned INTEGER DEFAULT 0,
            phase TEXT,
            phase_progress INTEGER DEFAULT 0,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS hosts (
            ip TEXT NOT NULL,
            scan_id TEXT NOT NULL,
            cidr TEXT,
            dns_names TEXT,
            mac TEXT,
            vendor TEXT,
            os_name TEXT,
            os_accuracy INTEGER,
            os_family TEXT,
            os_cpe TEXT,
            device_class TEXT,
            risk_level TEXT,
            risk_score INTEGER,
            ports TEXT,
            host_scripts TEXT,
            first_seen TEXT,
            last_seen TEXT,
            PRIMARY KEY (ip, scan_id)
        );

        CREATE TABLE IF NOT EXISTS manual_networks (
            cidr TEXT PRIMARY KEY,
            label TEXT,
            added_at TEXT
        );

        CREATE TABLE IF NOT EXISTS credentials (
            host_ip TEXT NOT NULL,
            cred_type TEXT NOT NULL DEFAULT 'ssh',
            username TEXT NOT NULL,
            password_b64 TEXT,
            ssh_key_b64 TEXT,
            sudo_password_b64 TEXT,
            note TEXT,
            added_at TEXT,
            PRIMARY KEY (host_ip, cred_type)
        );

        CREATE TABLE IF NOT EXISTS deep_scan_results (
            host_ip TEXT NOT NULL,
            cred_type TEXT NOT NULL DEFAULT 'ssh',
            status TEXT DEFAULT 'pending',
            ran_at TEXT,
            results TEXT,
            error TEXT,
            PRIMARY KEY (host_ip, cred_type)
        );

        CREATE TABLE IF NOT EXISTS vuln_scans (
            id              TEXT PRIMARY KEY,
            label           TEXT,
            scope           TEXT NOT NULL,
            targets         TEXT NOT NULL,
            source_scan_id  TEXT,
            status          TEXT DEFAULT 'running',
            started_at      TEXT,
            completed_at    TEXT,
            total_findings  INTEGER DEFAULT 0,
            critical_count  INTEGER DEFAULT 0,
            high_count      INTEGER DEFAULT 0,
            medium_count    INTEGER DEFAULT 0,
            low_count       INTEGER DEFAULT 0,
            command         TEXT
        );

        CREATE TABLE IF NOT EXISTS vuln_findings (
            id              TEXT PRIMARY KEY,
            vuln_scan_id    TEXT NOT NULL,
            host            TEXT,
            template_id     TEXT,
            template_name   TEXT,
            severity        TEXT,
            tags            TEXT,
            matched_at      TEXT,
            description     TEXT,
            reference       TEXT,
            extracted_results TEXT,
            found_at        TEXT
        );

        CREATE TABLE IF NOT EXISTS vuln_suppressions (
            id          TEXT PRIMARY KEY,
            template_id TEXT,
            host        TEXT,
            reason      TEXT,
            created_at  TEXT,
            created_by  TEXT
        );

        CREATE TABLE IF NOT EXISTS scan_schedules (
            id              TEXT PRIMARY KEY,
            label           TEXT NOT NULL,
            scope           TEXT NOT NULL DEFAULT 'safe',
            cidr            TEXT,
            source_scan_id  TEXT,
            enabled         INTEGER NOT NULL DEFAULT 1,
            cron_expr       TEXT,
            day_of_week     INTEGER,
            hour            INTEGER NOT NULL DEFAULT 2,
            minute          INTEGER NOT NULL DEFAULT 0,
            last_run_at     TEXT,
            next_run_at     TEXT,
            created_at      TEXT NOT NULL
        );
    """)
    # Migrate older scans table that may lack newer columns
    for col, defn in [
        ("hosts_found",    "INTEGER DEFAULT 0"),
        ("hosts_scanned",  "INTEGER DEFAULT 0"),
        ("phase",          "TEXT"),
        ("phase_progress", "INTEGER DEFAULT 0"),
    ]:
        try:
            await db.execute(f"ALTER TABLE scans ADD COLUMN {col} {defn}")
        except Exception:
            pass
    await db.commit()


@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_db(db)
        # Migration: add command column if it doesn't exist yet
        try:
            await db.execute("ALTER TABLE vuln_scans ADD COLUMN command TEXT")
            await db.commit()
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        # Any scan still marked 'running' was orphaned by a pod restart — mark as error
        await db.execute(
            "UPDATE vuln_scans SET status='error', completed_at=? WHERE status='running'",
            (_now(),),
        )
        # Same for network scans — without this they show "Scanning" forever
        await db.execute(
            "UPDATE scans SET status='failed', completed_at=?, error='Orphaned by service restart' "
            "WHERE status IN ('running','pending')",
            (_now(),),
        )
        await db.commit()
    # Start background scheduler
    asyncio.create_task(_schedule_loop())
    logger.info(f"Discovery engine started, DB: {DB_PATH}")


# ─── Schedule loop ─────────────────────────────────────────────────────────────

async def _schedule_loop():
    """Check every 60s for scheduled scans that are due to run."""
    await asyncio.sleep(10)  # let startup finish
    while True:
        try:
            await _fire_due_schedules()
        except Exception as e:
            logger.warning("schedule_loop error: %s", e)
        await asyncio.sleep(60)


def _schedule_due(sched: dict) -> bool:
    """Return True if this schedule should fire now (within the current minute)."""
    if not sched.get("enabled"):
        return False
    now = datetime.now(timezone.utc)
    dow = sched.get("day_of_week")
    if dow is not None and int(dow) != now.weekday():
        return False
    if int(sched.get("hour", 2)) != now.hour:
        return False
    if int(sched.get("minute", 0)) != now.minute:
        return False
    # Avoid double-firing within the same minute
    last = sched.get("last_run_at", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 90:
                return False
        except Exception:
            pass
    return True


async def _fire_due_schedules():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scan_schedules WHERE enabled=1") as cur:
            schedules = [dict(r) for r in await cur.fetchall()]
    for sched in schedules:
        if not _schedule_due(sched):
            continue
        logger.info("scheduler: firing schedule %s (%s)", sched["id"], sched["label"])
        # Mark last_run_at immediately to prevent double-fire
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE scan_schedules SET last_run_at=? WHERE id=?", (_now(), sched["id"])
            )
            await db.commit()
        # Resolve targets from stored cidr or source_scan_id
        targets = []
        if sched.get("cidr"):
            targets = [sched["cidr"]]
        elif sched.get("source_scan_id"):
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT DISTINCT host FROM vuln_findings WHERE vuln_scan_id=?",
                    (sched["source_scan_id"],),
                ) as cur:
                    targets = [r["host"] for r in await cur.fetchall()]
        if not targets:
            logger.warning("scheduler: schedule %s has no targets, skipping", sched["id"])
            continue
        # Trigger scan (reuse existing vuln scan trigger logic)
        vscan_id = str(uuid.uuid4())[:8]
        label = f"Scheduled: {sched['label']} — {_now()[:10]}"
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO vuln_scans (id, label, scope, targets, status, started_at) VALUES (?,?,?,?,?,?)",
                (vscan_id, label, sched["scope"], json.dumps(targets), "running", _now()),
            )
            await db.commit()

        async def _run(vid=vscan_id, tgts=targets, sc=sched["scope"]):
            vuln_scans_active.inc()
            t0 = __import__("time").time()
            try:
                async with aiosqlite.connect(DB_PATH) as _db:
                    await run_vuln_scan(vid, tgts, sc, _db,
                                        lambda ev: _publish_vuln(vid, ev),
                                        redis_url=REDIS_URL)
            finally:
                vuln_scan_duration.labels(scope=sc).observe(__import__("time").time() - t0)
                vuln_scans_active.dec()

        task = asyncio.create_task(_run())
        _vuln_running[vscan_id] = task


# ─── Schedule CRUD endpoints ──────────────────────────────────────────────────

@app.get("/vuln-scans/schedules")
async def list_schedules():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scan_schedules ORDER BY created_at DESC") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"schedules": rows}


class ScheduleRequest(BaseModel):
    label: str
    scope: str = "safe"
    cidr: str = ""
    source_scan_id: str = ""
    enabled: bool = True
    day_of_week: int | None = None  # 0=Mon … 6=Sun; None = daily
    hour: int = 2
    minute: int = 0


@app.post("/vuln-scans/schedules")
async def create_schedule(req: ScheduleRequest):
    sid = str(uuid.uuid4())[:8]
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO scan_schedules
               (id, label, scope, cidr, source_scan_id, enabled, day_of_week, hour, minute, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sid, req.label, req.scope, req.cidr or None, req.source_scan_id or None,
             1 if req.enabled else 0, req.day_of_week, req.hour, req.minute, now),
        )
        await db.commit()
    return {"id": sid, "label": req.label, "scope": req.scope, "enabled": req.enabled}


@app.patch("/vuln-scans/schedules/{sid}")
async def update_schedule(sid: str, request: Request):
    body = await request.json()
    allowed = {"label", "scope", "cidr", "source_scan_id", "enabled", "day_of_week", "hour", "minute"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    sets = ", ".join(f"{k}=?" for k in updates)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE scan_schedules SET {sets} WHERE id=?",
                         (*updates.values(), sid))
        await db.commit()
    return {"ok": True}


@app.delete("/vuln-scans/schedules/{sid}")
async def delete_schedule(sid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM scan_schedules WHERE id=?", (sid,))
        await db.commit()
    return {"ok": True}


# ─── Helpers ──────────────────────────────────────────────────────────────────

_SSRF_BLOCKED = [
    ipaddress.ip_network("127.0.0.0/8"),    # loopback
    ipaddress.ip_network("169.254.0.0/16"), # link-local / cloud metadata (AWS IMDS, Azure IMDS)
    ipaddress.ip_network("0.0.0.0/8"),      # unspecified
    ipaddress.ip_network("::1/128"),        # IPv6 loopback
]

_MAX_VULN_TARGETS = int(os.getenv("MAX_VULN_TARGETS", "50"))


def _validate_scan_target(target: str) -> str | None:
    """Return error string if target is disallowed, else None."""
    try:
        addr = ipaddress.ip_address(target)
        for net in _SSRF_BLOCKED:
            if addr in net:
                return f"Target {target} is in a blocked range ({net})"
    except ValueError:
        pass  # hostnames are allowed; nmap handles resolution
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publish(scan_id: str, event: dict):
    """Publish to local queues and fire-and-forget to Redis channel."""
    subs = _subscribers.get(scan_id, [])
    for q in list(subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            subs.remove(q)
    if _redis is not None:
        t = asyncio.create_task(_redis_publish(scan_id, event))
        _pending_tasks.add(t)
        t.add_done_callback(_pending_tasks.discard)


async def _redis_publish(scan_id: str, event: dict):
    r = _redis
    if r is None:
        return
    try:
        await r.publish(f"scan:{scan_id}", json.dumps(event))
    except Exception as e:
        logger.warning("Redis publish failed: %s", e)


def _enc(s: str | None) -> str | None:
    """Encrypt with Fernet if key available, else base64 fallback."""
    if not s:
        return None
    if _fernet:
        return "F:" + _fernet.encrypt(s.encode()).decode()
    return "B:" + base64.b64encode(s.encode()).decode()


def _dec(s: str | None) -> str | None:
    """Decrypt — handles both Fernet (F:) and legacy base64 (B: or raw)."""
    if not s:
        return None
    if s.startswith("F:"):
        if _fernet:
            try:
                return _fernet.decrypt(s[2:].encode()).decode()
            except Exception:
                return None
        return None
    if s.startswith("B:"):
        try:
            return base64.b64decode(s[2:]).decode()
        except Exception:
            return None
    # Legacy: raw base64 (pre-migration rows)
    try:
        return base64.b64decode(s).decode()
    except Exception:
        return None


# Keep old names as aliases during migration
_b64e = _enc
_b64d = _dec


# ─── Networks ─────────────────────────────────────────────────────────────────

@app.get("/networks")
async def list_networks():
    auto = await discover_networks()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cidr, label, added_at FROM manual_networks ORDER BY added_at DESC") as cur:
            manual = [dict(r) for r in await cur.fetchall()]
    for m in manual:
        m["source"] = "manual"
    manual_cidrs = {m["cidr"] for m in manual}
    filtered_auto = [n for n in auto if n["cidr"] not in manual_cidrs]
    return {"networks": manual + filtered_auto}


class NetworkPayload(BaseModel):
    cidr: str
    label: str = ""


@app.post("/networks")
async def add_network(payload: NetworkPayload):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO manual_networks (cidr, label, added_at) VALUES (?,?,?)",
            (payload.cidr, payload.label or payload.cidr, _now()),
        )
        await db.commit()
    return {"ok": True, "cidr": payload.cidr}


@app.delete("/networks/{cidr:path}")
async def remove_network(cidr: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM manual_networks WHERE cidr=?", (cidr,))
        await db.commit()
    return {"ok": True}


# ─── Scans ────────────────────────────────────────────────────────────────────

@app.get("/scans")
async def list_scans():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scans ORDER BY started_at DESC LIMIT 50"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"scans": rows}


class ScanRequest(BaseModel):
    cidr: str
    label: str = ""
    profile: str = "standard"  # fast | standard | deep | stealth


@app.post("/scans")
async def start_scan(payload: ScanRequest):
    profile = payload.profile if payload.profile in ("fast", "standard", "deep", "stealth") else "standard"
    scan_id = str(uuid.uuid4())[:8]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scans (id, cidr, label, status, started_at) VALUES (?,?,?,?,?)",
            (scan_id, payload.cidr, payload.label or payload.cidr, "running", _now()),
        )
        await db.commit()

    async def _run():
        network_scans_active.inc()
        t0 = __import__("time").time()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await scan_network(scan_id, payload.cidr, db, lambda ev: _publish(scan_id, ev), profile=profile)
        finally:
            network_scan_duration.labels(profile=profile).observe(__import__("time").time() - t0)
            network_scans_active.dec()
            _running.pop(scan_id, None)

    task = asyncio.create_task(_run())
    _running[scan_id] = task
    return {"scan_id": scan_id, "cidr": payload.cidr, "status": "running"}


@app.post("/scans/{scan_id}/stop")
async def stop_scan(scan_id: str):
    """Cancel a running scan (keeps the record in history)."""
    task = _running.pop(scan_id, None)
    if task:
        task.cancel()
        # Wait briefly for cancellation to propagate
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except Exception:
            pass
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scans SET status='cancelled', completed_at=? WHERE id=? AND status IN ('running','pending')",
            (_now(), scan_id),
        )
        await db.commit()
    # Close any waiting SSE subscribers
    _publish(scan_id, {"type": "cancelled", "scan_id": scan_id})
    return {"ok": True, "scan_id": scan_id, "status": "cancelled"}


@app.post("/scans/{scan_id}/rescan-missed")
async def rescan_missed(scan_id: str):
    """Re-scan hosts that didn't respond in the previous scan (phase 2 only)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cidr, profile FROM scans WHERE id=?", (scan_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, detail="Scan not found")
        # Find IPs that were discovered but have no detailed port data
        async with db.execute(
            "SELECT ip FROM hosts WHERE scan_id=? AND (ports IS NULL OR ports='[]')", (scan_id,)
        ) as cur:
            missed = [r["ip"] for r in await cur.fetchall()]

    if not missed:
        return {"message": "No missed hosts found", "count": 0}

    new_id = str(uuid.uuid4())[:8]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scans (id, cidr, label, status, started_at) VALUES (?,?,?,?,?)",
            (new_id, row["cidr"], f"Rescan missed ({len(missed)} hosts)", "running", _now()),
        )
        await db.commit()

    from scanner import scan_network

    async def _run():
        async with aiosqlite.connect(DB_PATH) as _db:
            await scan_network(new_id, row["cidr"], _db, lambda ev: _publish(new_id, ev),
                               profile=row["profile"] or "standard")
        _running.pop(new_id, None)

    task = asyncio.create_task(_run())
    _running[new_id] = task
    return {"scan_id": new_id, "missed_count": len(missed), "status": "running"}


@app.delete("/scans/{scan_id}")
async def delete_scan(scan_id: str):
    """Stop if running, then permanently delete scan record and all its hosts."""
    task = _running.pop(scan_id, None)
    if task:
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except Exception:
            pass
    _publish(scan_id, {"type": "cancelled", "scan_id": scan_id})
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM hosts WHERE scan_id=?", (scan_id,))
        await db.execute("DELETE FROM scans WHERE id=?", (scan_id,))
        await db.commit()
    return {"ok": True, "deleted": scan_id}


@app.get("/scans/{scan_id}/events")
async def scan_events(scan_id: str):
    """SSE stream for live scan progress."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status FROM scans WHERE id=?", (scan_id,)) as cur:
            row = await cur.fetchone()
    if row and row["status"] not in ("running", "pending"):
        async def _immediate():
            yield f"data: {json.dumps({'type': 'done', 'scan_id': scan_id, 'status': row['status']})}\n\n"
        return StreamingResponse(_immediate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    r = await _get_redis()

    if r:
        async def _stream_redis() -> AsyncIterator[str]:
            pubsub = r.pubsub()
            await pubsub.subscribe(f"scan:{scan_id}")
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(_next_redis_msg(pubsub), timeout=30.0)
                        event = json.loads(msg["data"])
                        yield f"data: {json.dumps(event)}\n\n"
                        if event.get("type") in ("done", "error", "cancelled"):
                            break
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                await pubsub.unsubscribe(f"scan:{scan_id}")
                await pubsub.aclose()
        return StreamingResponse(
            _stream_redis(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Fallback: in-memory queue
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.setdefault(scan_id, []).append(q)

    async def _stream_queue() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("done", "error", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subs = _subscribers.get(scan_id, [])
            if q in subs:
                subs.remove(q)

    return StreamingResponse(
        _stream_queue(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/scans/{scan_id}/diff")
async def scan_diff(scan_id: str):
    """Compare this scan to the previous completed scan of the same CIDR:
    which hosts are new, which disappeared."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cidr, started_at, status FROM scans WHERE id=?", (scan_id,)) as cur:
            scan = await cur.fetchone()
        if not scan:
            raise HTTPException(404, detail="Scan not found")
        async with db.execute(
            "SELECT id, started_at FROM scans WHERE cidr=? AND id!=? AND status='done' "
            "AND started_at < ? ORDER BY started_at DESC LIMIT 1",
            (scan["cidr"], scan_id, scan["started_at"] or _now()),
        ) as cur:
            prev = await cur.fetchone()
        if not prev:
            return {"previous_scan_id": None, "new_ips": [], "missing_ips": []}

        async with db.execute("SELECT ip FROM hosts WHERE scan_id=?", (scan_id,)) as cur:
            current_ips = {r["ip"] for r in await cur.fetchall()}
        async with db.execute("SELECT ip FROM hosts WHERE scan_id=?", (prev["id"],)) as cur:
            prev_ips = {r["ip"] for r in await cur.fetchall()}

    return {
        "previous_scan_id": prev["id"],
        "previous_started_at": prev["started_at"],
        "new_ips": sorted(current_ips - prev_ips),
        # Only report missing hosts once this scan finished — mid-scan they may
        # simply not have been reached yet
        "missing_ips": sorted(prev_ips - current_ips) if scan["status"] == "done" else [],
    }


@app.get("/scans/{scan_id}/export")
async def scan_export(scan_id: str):
    """CSV export of all hosts in a scan."""
    import csv
    import io

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM hosts WHERE scan_id=? ORDER BY risk_score DESC, ip", (scan_id,)
        ) as cur:
            rows = await cur.fetchall()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ip", "dns_names", "mac", "vendor", "os_name", "os_accuracy",
                "device_class", "risk_level", "risk_score", "open_ports", "first_seen", "last_seen"])
    for r in rows:
        ports = json.loads(r["ports"] or "[]")
        open_ports = "; ".join(
            f"{p['port']}/{p.get('protocol','tcp')} {p.get('service','')}".strip()
            for p in ports if p.get("state") == "open"
        )
        dns = ", ".join(json.loads(r["dns_names"] or "[]"))
        w.writerow([r["ip"], dns, r["mac"], r["vendor"], r["os_name"], r["os_accuracy"],
                    r["device_class"], r["risk_level"], r["risk_score"], open_ports,
                    r["first_seen"], r["last_seen"]])

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=discovery-{scan_id}.csv"},
    )


# ─── Hosts ────────────────────────────────────────────────────────────────────

@app.get("/scans/{scan_id}/hosts")
async def list_hosts(scan_id: str, risk: str = "", device_class: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM hosts WHERE scan_id=?"
        params: list = [scan_id]
        if risk:
            query += " AND risk_level=?"
            params.append(risk)
        if device_class:
            query += " AND device_class=?"
            params.append(device_class)
        query += " ORDER BY risk_score DESC, ip"
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

    hosts = []
    for r in rows:
        h = dict(r)
        h["ports"] = json.loads(h["ports"] or "[]")
        h["dns_names"] = json.loads(h["dns_names"] or "[]")
        h["host_scripts"] = json.loads(h["host_scripts"] or "[]")
        hosts.append(h)
    return {"hosts": hosts}


@app.get("/hosts/{ip}")
async def get_host(ip: str, scan_id: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if scan_id:
            async with db.execute("SELECT * FROM hosts WHERE ip=? AND scan_id=?", (ip, scan_id)) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT * FROM hosts WHERE ip=? ORDER BY last_seen DESC LIMIT 1", (ip,)
            ) as cur:
                row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Host not found")
    h = dict(row)
    h["ports"] = json.loads(h["ports"] or "[]")
    h["dns_names"] = json.loads(h["dns_names"] or "[]")
    h["host_scripts"] = json.loads(h["host_scripts"] or "[]")
    return h


# ─── Credentials ──────────────────────────────────────────────────────────────

class CredentialPayload(BaseModel):
    cred_type: str = "ssh"       # "ssh" | "winrm"
    username: str
    password: str | None = None
    ssh_key: str | None = None   # PEM private key text
    sudo_password: str | None = None
    note: str | None = None


@app.post("/hosts/{ip}/credentials")
async def save_credentials(ip: str, payload: CredentialPayload):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO credentials
               (host_ip, cred_type, username, password_b64, ssh_key_b64, sudo_password_b64, note, added_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                ip, payload.cred_type, payload.username,
                _b64e(payload.password),
                _b64e(payload.ssh_key),
                _b64e(payload.sudo_password),
                payload.note, _now(),
            ),
        )
        await db.commit()
    return {"ok": True, "host_ip": ip, "cred_type": payload.cred_type}


@app.get("/hosts/{ip}/credentials")
async def list_credentials(ip: str):
    """Returns credential types without exposing secrets."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT cred_type, username, note, added_at, "
            "(password_b64 IS NOT NULL) AS has_password, "
            "(ssh_key_b64 IS NOT NULL) AS has_key, "
            "(sudo_password_b64 IS NOT NULL) AS has_sudo "
            "FROM credentials WHERE host_ip=?", (ip,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"credentials": rows}


@app.delete("/hosts/{ip}/credentials/{cred_type}")
async def delete_credentials(ip: str, cred_type: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM credentials WHERE host_ip=? AND cred_type=?", (ip, cred_type))
        await db.commit()
    return {"ok": True}


# ─── Deep Scan (authenticated) ────────────────────────────────────────────────

@app.post("/hosts/{ip}/deep-scan")
async def trigger_deep_scan(ip: str, cred_type: str = "ssh"):
    """Start an authenticated deep scan using stored credentials."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM credentials WHERE host_ip=? AND cred_type=?", (ip, cred_type)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No credentials stored for this host")

    creds = dict(row)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO deep_scan_results
               (host_ip, cred_type, status, ran_at, results, error)
               VALUES (?,?,?,?,NULL,NULL)""",
            (ip, cred_type, "running", _now()),
        )
        await db.commit()

    async def _run():
        from deep_scanner import run_ssh_deep_scan
        result = await run_ssh_deep_scan(
            ip=ip,
            username=creds["username"],
            password=_b64d(creds.get("password_b64")),
            ssh_key=_b64d(creds.get("ssh_key_b64")),
            sudo_password=_b64d(creds.get("sudo_password_b64")),
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE deep_scan_results SET status=?, ran_at=?, results=?, error=? WHERE host_ip=? AND cred_type=?",
                (
                    result["status"], _now(),
                    json.dumps(result.get("results", {})),
                    result.get("error"),
                    ip, cred_type,
                ),
            )
            await db.commit()

    t = asyncio.create_task(_run())
    _pending_tasks.add(t)
    t.add_done_callback(_pending_tasks.discard)
    return {"ok": True, "host_ip": ip, "status": "running"}


@app.get("/hosts/{ip}/deep-scan")
async def get_deep_scan(ip: str, cred_type: str = "ssh"):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM deep_scan_results WHERE host_ip=? AND cred_type=?", (ip, cred_type)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"status": "not_run", "results": {}}
    r = dict(row)
    r["results"] = json.loads(r.get("results") or "{}")
    return r


# ─── Summary (consumed by scoring engine + LLM) ──────────────────────────────

_DANGEROUS_PORTS = {
    23: "Telnet", 21: "FTP", 3389: "RDP", 5900: "VNC", 5901: "VNC",
    2049: "NFS", 111: "RPC", 512: "rexec", 513: "rlogin", 514: "rsh",
}
_CRITICAL_PORTS = {23, 21, 512, 513, 514}


@app.get("/summary")
async def get_summary():
    """Aggregated findings from all completed scans. Consumed by scoring engine, LLM, fleet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scans WHERE status='done' ORDER BY completed_at DESC LIMIT 20"
        ) as cur:
            scans = [dict(r) for r in await cur.fetchall()]

        if not scans:
            return {
                "scanned": False, "total_hosts": 0, "risk_breakdown": {},
                "dangerous_port_findings": [], "host_ip_map": {}, "scan_count": 0,
            }

        scan_ids = [s["id"] for s in scans]
        placeholders = ",".join("?" * len(scan_ids))
        async with db.execute(
            f"SELECT * FROM hosts WHERE scan_id IN ({placeholders})", scan_ids
        ) as cur:
            all_hosts = [dict(r) for r in await cur.fetchall()]

    latest: dict[str, dict] = {}
    for h in all_hosts:
        existing = latest.get(h["ip"])
        if not existing or h.get("last_seen", "") > existing.get("last_seen", ""):
            latest[h["ip"]] = h

    hosts = list(latest.values())

    risk_breakdown: dict[str, int] = {}
    for h in hosts:
        rl = h.get("risk_level", "low") or "low"
        risk_breakdown[rl] = risk_breakdown.get(rl, 0) + 1

    dangerous: list[dict] = []
    for h in hosts:
        ports = json.loads(h.get("ports") or "[]")
        for p in ports:
            if p.get("state") != "open":
                continue
            port_num = int(p.get("port", 0))
            if port_num in _DANGEROUS_PORTS:
                dangerous.append({
                    "ip": h["ip"],
                    "dns_names": json.loads(h.get("dns_names") or "[]"),
                    "device_class": h.get("device_class", "unknown"),
                    "port": port_num,
                    "service": _DANGEROUS_PORTS[port_num],
                    "severity": "critical" if port_num in _CRITICAL_PORTS else "high",
                })

    host_ip_map = {
        h["ip"]: {
            "os_name": h.get("os_name", ""),
            "device_class": h.get("device_class", "unknown"),
            "risk_level": h.get("risk_level", "low"),
            "risk_score": h.get("risk_score", 0),
            "open_port_count": len([
                p for p in json.loads(h.get("ports") or "[]") if p.get("state") == "open"
            ]),
            "top_ports": [
                p["port"] for p in json.loads(h.get("ports") or "[]")
                if p.get("state") == "open"
            ][:5],
        }
        for h in hosts
    }

    return {
        "scanned": True,
        "total_hosts": len(hosts),
        "scan_count": len(scans),
        "risk_breakdown": risk_breakdown,
        "dangerous_port_findings": dangerous,
        "host_ip_map": host_ip_map,
        "last_scan": scans[0].get("completed_at") if scans else None,
    }


# ─── Vulnerability Scans ──────────────────────────────────────────────────────

# Running vuln scan tasks: vuln_scan_id → asyncio.Task
_vuln_running: dict[str, asyncio.Task] = {}
_vuln_subscribers: dict[str, list[asyncio.Queue]] = {}


def _publish_vuln(vscan_id: str, event: dict):
    subs = _vuln_subscribers.get(vscan_id, [])
    for q in list(subs):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            subs.remove(q)
    if _redis is not None:
        t = asyncio.create_task(_redis_publish(f"vscan:{vscan_id}", event))
        _pending_tasks.add(t)
        t.add_done_callback(_pending_tasks.discard)


class VulnScanRequest(BaseModel):
    scope: str = "standard"
    targets: list[str] = []
    source_scan_id: str = ""
    source_risk_filter: str = ""
    label: str = ""


@app.get("/vuln-scans/scopes")
async def vuln_scan_scopes():
    """Return scope descriptions and estimates (call before starting a scan)."""
    return {
        "scopes": [
            {
                "id": sid,
                "label": p["label"],
                "description": p["description"],
                "risk_note": p["risk_note"],
                "est_seconds_per_host": p["est_seconds_per_host"],
                "resources": p["resources"],
            }
            for sid, p in SCOPE_PROFILES.items()
        ]
    }


@app.post("/vuln-scans/estimate")
async def vuln_scan_estimate(payload: VulnScanRequest):
    """Return time/effort estimate for the given scope + target count."""
    targets = list(payload.targets)
    if payload.source_scan_id:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            q = "SELECT ip FROM hosts WHERE scan_id=?"
            params: list = [payload.source_scan_id]
            if payload.source_risk_filter:
                q += " AND risk_level=?"
                params.append(payload.source_risk_filter)
            async with db.execute(q, params) as cur:
                rows = await cur.fetchall()
        targets += [r["ip"] for r in rows]
    deduped = list(dict.fromkeys(t.strip() for t in targets if t.strip()))
    return estimate_duration(payload.scope, len(deduped))


@app.get("/vuln-scans")
async def list_vuln_scans():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vuln_scans ORDER BY started_at DESC LIMIT 50"
        ) as cur:
            rows = await cur.fetchall()
    scans = []
    for r in rows:
        s = dict(r)
        s["targets"] = json.loads(s.get("targets") or "[]")
        scans.append(s)
    return {"vuln_scans": scans}


@app.post("/vuln-scans")
async def start_vuln_scan(payload: VulnScanRequest):
    if payload.scope not in SCOPE_PROFILES:
        raise HTTPException(400, detail=f"Unknown scope '{payload.scope}'. Use: safe, standard, full")

    targets = list(payload.targets)
    if payload.source_scan_id:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            q = "SELECT ip FROM hosts WHERE scan_id=?"
            params: list = [payload.source_scan_id]
            if payload.source_risk_filter:
                q += " AND risk_level=?"
                params.append(payload.source_risk_filter)
            async with db.execute(q, params) as cur:
                rows = await cur.fetchall()
        targets += [r["ip"] for r in rows]

    targets = list(dict.fromkeys(t.strip() for t in targets if t.strip()))
    if not targets:
        raise HTTPException(400, detail="No targets specified. Provide targets or a source_scan_id.")
    if len(targets) > _MAX_VULN_TARGETS:
        raise HTTPException(400, detail=f"Too many targets ({len(targets)}). Maximum is {_MAX_VULN_TARGETS}.")
    for tgt in targets:
        err = _validate_scan_target(tgt)
        if err:
            raise HTTPException(400, detail=err)

    vscan_id = str(uuid.uuid4())[:8]
    label = payload.label or f"{SCOPE_PROFILES[payload.scope]['label']} scan — {_now()[:10]}"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO vuln_scans
               (id, label, scope, targets, source_scan_id, status, started_at)
               VALUES (?,?,?,?,?,?,?)""",
            (vscan_id, label, payload.scope, json.dumps(targets),
             payload.source_scan_id or None, "running", _now()),
        )
        await db.commit()

    async def _run():
        vuln_scans_active.inc()
        t0 = __import__("time").time()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await run_vuln_scan(vscan_id, targets, payload.scope, db,
                                    lambda ev: _publish_vuln(vscan_id, ev),
                                    redis_url=REDIS_URL)
        finally:
            vuln_scan_duration.labels(scope=payload.scope).observe(__import__("time").time() - t0)
            vuln_scans_active.dec()
            _vuln_running.pop(vscan_id, None)

    task = asyncio.create_task(_run())
    _vuln_running[vscan_id] = task
    return {"vuln_scan_id": vscan_id, "scope": payload.scope, "target_count": len(targets), "status": "running"}


@app.post("/vuln-scans/{vscan_id}/stop")
async def stop_vuln_scan(vscan_id: str):
    task = _vuln_running.pop(vscan_id, None)
    if task:
        task.cancel()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vuln_scans SET status='cancelled', completed_at=? WHERE id=?",
            (_now(), vscan_id),
        )
        await db.commit()
    _publish_vuln(vscan_id, {"type": "cancelled", "vuln_scan_id": vscan_id})
    return {"ok": True, "vuln_scan_id": vscan_id}


@app.delete("/vuln-scans/{vscan_id}")
async def delete_vuln_scan(vscan_id: str):
    task = _vuln_running.pop(vscan_id, None)
    if task:
        task.cancel()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM vuln_findings WHERE vuln_scan_id=?", (vscan_id,))
        await db.execute("DELETE FROM vuln_scans WHERE id=?", (vscan_id,))
        await db.commit()
    return {"ok": True, "deleted": vscan_id}


@app.get("/vuln-scans/{vscan_id}/events")
async def vuln_scan_events(vscan_id: str):
    """SSE stream for live vuln scan progress."""
    # Subscribe first, THEN check status — prevents race where scan completes
    # between the SELECT and subscribe, causing the client to miss the done event.
    r = await _get_redis()
    if r:
        async def _redis_stream():
            pubsub = r.pubsub()
            await pubsub.subscribe(f"vscan:{vscan_id}")
            try:
                # Check if already completed after subscribing
                async with aiosqlite.connect(DB_PATH) as _db:
                    _db.row_factory = aiosqlite.Row
                    async with _db.execute("SELECT status FROM vuln_scans WHERE id=?", (vscan_id,)) as cur:
                        row = await cur.fetchone()
                if row and row["status"] not in ("running",):
                    yield f"data: {json.dumps({'type': 'done', 'vuln_scan_id': vscan_id, 'status': row['status']})}\n\n"
                    return
                while True:
                    try:
                        msg = await asyncio.wait_for(_next_redis_msg(pubsub), timeout=30.0)
                        event = json.loads(msg["data"])
                        yield f"data: {json.dumps(event)}\n\n"
                        if event.get("type") in ("done", "error", "cancelled"):
                            break
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                await pubsub.unsubscribe(f"vscan:{vscan_id}")
                await pubsub.aclose()
        return StreamingResponse(_redis_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # In-memory path: subscribe first, then check status
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _vuln_subscribers.setdefault(vscan_id, []).append(q)

    async def _queue_stream():
        try:
            # Check if already completed after registering the queue
            async with aiosqlite.connect(DB_PATH) as _db:
                _db.row_factory = aiosqlite.Row
                async with _db.execute("SELECT status FROM vuln_scans WHERE id=?", (vscan_id,)) as cur:
                    row = await cur.fetchone()
            if row and row["status"] not in ("running",):
                yield f"data: {json.dumps({'type': 'done', 'vuln_scan_id': vscan_id, 'status': row['status']})}\n\n"
                return
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("done", "error", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subs = _vuln_subscribers.get(vscan_id, [])
            if q in subs:
                subs.remove(q)

    return StreamingResponse(_queue_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/vuln-scans/{vscan_id}/findings")
async def vuln_scan_findings(vscan_id: str, severity: str = "", host: str = "",
                              include_suppressed: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM vuln_findings WHERE vuln_scan_id=?"
        params: list = [vscan_id]
        if severity:
            q += " AND severity=?"
            params.append(severity)
        if host:
            q += " AND host=?"
            params.append(host)
        q += " ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5 END, found_at"
        async with db.execute(q, params) as cur:
            rows = await cur.fetchall()

        # Load suppression rules
        async with db.execute("SELECT template_id, host FROM vuln_suppressions") as cur:
            suppressions = {(r["template_id"], r["host"]) for r in await cur.fetchall()}

    findings = []
    for r in rows:
        f = dict(r)
        f["tags"] = json.loads(f.get("tags") or "[]")
        f["reference"] = json.loads(f.get("reference") or "[]")
        f["extracted_results"] = json.loads(f.get("extracted_results") or "[]")
        suppressed = (f.get("template_id"), f.get("host")) in suppressions or \
                     (f.get("template_id"), None) in suppressions
        f["suppressed"] = suppressed
        if not suppressed or include_suppressed:
            findings.append(f)
    return {"findings": findings}


class SuppressionRequest(BaseModel):
    template_id: str
    host: str = ""
    reason: str = ""
    created_by: str = ""


@app.post("/vuln-suppressions")
async def create_suppression(req: SuppressionRequest):
    sid = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO vuln_suppressions (id, template_id, host, reason, created_at, created_by) VALUES (?,?,?,?,?,?)",
            (sid, req.template_id, req.host or None, req.reason, _now(), req.created_by),
        )
        await db.commit()
    return {"id": sid, "template_id": req.template_id, "host": req.host}


@app.get("/vuln-suppressions")
async def list_suppressions():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vuln_suppressions ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
    return {"suppressions": [dict(r) for r in rows]}


@app.delete("/vuln-suppressions/{sup_id}")
async def delete_suppression(sup_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM vuln_suppressions WHERE id=?", (sup_id,))
        await db.commit()
    return {"ok": True}


class VerifyFixRequest(BaseModel):
    target: str
    template_id: str


@app.post("/vuln-scans/{vscan_id}/verify-fix")
async def verify_fix(vscan_id: str, req: VerifyFixRequest, request: Request):
    """Run a targeted single-template re-scan to confirm a fix."""
    user = request.headers.get("x-forwarded-preferred-username", "")
    new_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO vuln_scans (id, label, scope, targets, status, started_at) VALUES (?,?,?,?,?,?)",
            (new_id, f"Verify fix: {req.template_id}", "verify", req.target, "running", _now()),
        )
        await db.commit()

    from vuln_scanner import run_vuln_scan

    async def _run():
        async with aiosqlite.connect(DB_PATH) as _db:
            _db.row_factory = aiosqlite.Row
            await run_vuln_scan(
                new_id, [req.target], "safe", _db,
                lambda ev: _publish_vuln(new_id, ev),
                redis_url=REDIS_URL,
            )

    t = asyncio.create_task(_run())
    _pending_tasks.add(t)
    t.add_done_callback(_pending_tasks.discard)
    _vuln_running[new_id] = t
    return {"vuln_scan_id": new_id, "template_id": req.template_id, "target": req.target}


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "discovery-engine"}
