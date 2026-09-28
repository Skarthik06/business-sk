"""Remote worker pool (laptop, phone, any machine): heartbeat, pull-based jobs with leases, results.

Workers only make OUTBOUND requests (they work behind NAT / carrier CGNAT). Flow:
  worker → POST /v1/workers/heartbeat            (every ~20 s; also implied by every lease call)
  worker → POST /v1/workers/jobs/lease (long-poll) ← jobs targeted at this worker
  worker → POST /v1/workers/jobs/{attempt}/result
The engine pushes a job to scraper:queue:worker:{id} and waits (bounded) for the result; an
abandoned lease simply expires and the engine fails over to the next route."""
from __future__ import annotations

import base64
import gzip
import hmac
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from . import config, metrics, store

HB = "scraper:worker:heartbeat:"
QW = "scraper:queue:worker:"
LEASE = "scraper:worker:lease:"
RESULT = "scraper:result:"
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def _num(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


ONLINE_SECS = _num("SCRAPER_WORKER_ONLINE_SECS", 60)
STALE_SECS = _num("SCRAPER_WORKER_STALE_SECS", 120)
PICKUP_SECS = _num("SCRAPER_WORKER_PICKUP_SECS", 10)     # a live worker long-polls → picks up in <1 s
UNRESP = "scraper:worker:unresponsive:"                  # missed a pickup → no jobs until next heartbeat


def tokens() -> List[str]:
    return [t.strip() for t in (os.getenv("SCRAPER_WORKER_TOKENS") or "").split(",") if t.strip()]


def token_ok(tok: str) -> bool:
    tok = (tok or "").strip()
    return bool(tok) and any(hmac.compare_digest(tok, t) for t in tokens())


def valid_id(worker_id: str) -> bool:
    return bool(_ID.match(worker_id or ""))


def allows(worker: Dict[str, Any], host: str) -> bool:
    """A worker that declares `allowed_domains` (the phone app does) only gets jobs for those sites
    (the phone refuses anything else anyway). No list = any site."""
    doms = worker.get("allowed_domains")
    if not doms:
        return True
    h = (host or "").lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in doms)


def state_of(age: float) -> str:
    return "online" if age < ONLINE_SECS else ("stale" if age < STALE_SECS else "offline")


_LAST_DB: Dict[str, float] = {}


async def heartbeat(worker_id: str, info: Dict[str, Any]) -> None:
    now = time.time()
    caps = [c for c in (info.get("capabilities") or ["http"]) if c in ("http", "browser")] or ["http"]
    rec = {"worker_id": worker_id, "t": now, "status": str(info.get("status") or "healthy")[:20],
           "capabilities": caps, "active_jobs": int(info.get("active_jobs") or 0),
           "cpu_percent": info.get("cpu_percent"), "memory_percent": info.get("memory_percent"),
           "kind": str(info.get("kind") or "remote")[:20], "version": str(info.get("version") or "")[:20]}
    # phone / device telemetry (all optional)
    for k, cast, lim in (("battery_percent", float, None), ("data_today_mb", float, None), ("jobs_today", int, None),
                         ("charging", bool, None), ("network", str, 20), ("paused_reason", str, 80)):
        v = info.get(k)
        if v is not None:
            try:
                rec[k] = cast(v)[:lim] if (cast is str and lim) else cast(v)
            except (TypeError, ValueError):
                pass
    doms = info.get("allowed_domains")
    if isinstance(doms, list):
        rec["allowed_domains"] = [str(d).lower().strip()[:60] for d in doms if str(d).strip()][:20]
    await metrics.redis.set(HB + worker_id, json.dumps(rec), ex=STALE_SECS * 10)
    await metrics.redis.delete(UNRESP + worker_id)        # alive again → eligible again
    if now - _LAST_DB.get(worker_id, 0) > 60:              # persist at most once a minute
        _LAST_DB[worker_id] = now
        await store.q("""insert into workers (worker_id, kind, capabilities, last_heartbeat, info)
                         values (%s,%s,%s,now(),%s)
                         on conflict (worker_id) do update set kind=excluded.kind,
                           capabilities=excluded.capabilities, last_heartbeat=now(), info=excluded.info""",
                      (worker_id, rec["kind"], ",".join(caps), json.dumps(rec)))


async def remove(worker_id: str) -> bool:
    """Forget a worker: its live status, queued jobs and stored row. A paired phone's token is
    revoked by the caller first, so it can't simply come back."""
    n = await metrics.redis.delete(HB + worker_id, QW + worker_id, UNRESP + worker_id)
    row = await store.q("delete from workers where worker_id = %s returning worker_id", (worker_id,), one=True)
    _LAST_DB.pop(worker_id, None)
    return bool(n or row)


async def list_workers() -> List[Dict[str, Any]]:
    out = []
    now = time.time()
    async for key in metrics.redis.scan_iter(HB + "*"):
        raw = await metrics.redis.get(key)
        if not raw:
            continue
        rec = json.loads(raw)
        age = now - float(rec.get("t") or 0)
        state = state_of(age)
        if state != "offline" and await metrics.redis.exists(UNRESP + rec["worker_id"]):
            state = "offline"                               # missed a job since its last heartbeat
        out.append({**rec, "age_s": int(age), "state": state})
    return sorted(out, key=lambda r: r["worker_id"])


async def _blpop(keys: List[str], seconds: float):
    """BLPOP in short slices (the Redis client's read timeout is ~5 s): wait up to `seconds`."""
    deadline = time.time() + max(0.0, seconds)
    while True:
        left = deadline - time.time()
        item = await metrics.redis.blpop(keys, timeout=max(1, min(4, int(left)))) if left > 0 else None
        if item or time.time() >= deadline:
            return item


def _fail(url: str, msg: str) -> Dict[str, Any]:
    return {"status": None, "html": "", "final_url": url, "content_type": "", "headers": {}, "error": msg}


async def dispatch(worker_id: str, attempt_id: str, url: str, mode: str, timeout: int) -> Dict[str, Any]:
    """Queue a fetch for one worker and wait (bounded) for its result. Never raises."""
    job = {"attempt_id": attempt_id, "url": url, "mode": mode, "timeout": timeout, "queued": time.time()}
    qkey = QW + worker_id
    try:
        await metrics.redis.rpush(qkey, json.dumps(job))
        await metrics.redis.expire(qkey, 3600)
        deadline = time.time() + PICKUP_SECS
        while not await metrics.redis.exists(LEASE + attempt_id):    # 1) the worker picks it up
            if time.time() > deadline:
                await metrics.redis.lrem(qkey, 0, json.dumps(job))   # stale job must never run later
                await metrics.redis.set(UNRESP + worker_id, str(time.time()), ex=3600)
                return _fail(url, f"worker {worker_id} did not pick up the job in {PICKUP_SECS}s")
            item = await _blpop([RESULT + attempt_id], 1)
            if item:
                return json.loads(item[1])
        # 2) the result (the worker may retry a throttled page within `timeout`, then uploads)
        item = await _blpop([RESULT + attempt_id], timeout + 25)
        return json.loads(item[1]) if item else _fail(url, f"worker {worker_id} timed out")
    except Exception as e:                                          # noqa: BLE001 — failover, never 500
        return _fail(url, f"worker dispatch error: {type(e).__name__}")


async def lease(worker_id: str, max_jobs: int, wait: float) -> List[Dict[str, Any]]:
    """Long-poll: hand this worker up to max_jobs of ITS queued jobs, each with a lease."""
    out: List[Dict[str, Any]] = []
    qkey = QW + worker_id
    item = await _blpop([qkey], max(1.0, float(wait)))
    while item and len(out) < max_jobs:
        job = json.loads(item[1])
        await metrics.redis.set(LEASE + job["attempt_id"], worker_id, ex=int(job.get("timeout", 30)) + 45)
        out.append({k: job[k] for k in ("attempt_id", "url", "mode", "timeout")})
        raw = await metrics.redis.lpop(qkey)
        item = (qkey, raw) if raw else None
    return out


async def submit(worker_id: str, attempt_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    owner = await metrics.redis.get(LEASE + attempt_id)
    if owner != worker_id:
        return {"ok": False, "error": "no active lease for this worker"}
    html = body.get("html") or ""
    if body.get("gz") and html:
        try:
            raw = gzip.decompress(base64.b64decode(html))
            html = raw[: config.MAX_BYTES].decode("utf-8", "ignore")
        except Exception:
            html, body["error"] = "", body.get("error") or "bad gzip payload"
    elif len(html) > config.MAX_BYTES:
        html = html[: config.MAX_BYTES]
    res = {"status": body.get("status_code"), "html": html, "final_url": body.get("final_url") or "",
           "content_type": body.get("content_type") or "text/html", "headers": {},
           "error": (str(body.get("error"))[:200] if body.get("error") else None)}
    await metrics.redis.rpush(RESULT + attempt_id, json.dumps(res))
    await metrics.redis.expire(RESULT + attempt_id, 120)
    await metrics.redis.delete(LEASE + attempt_id)
    return {"ok": True}
