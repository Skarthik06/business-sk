"""
tools/scrape_bus.py — the residential scrape worker bus (shared by server.py endpoints AND the
Amazon pipeline). The cloud coordinates jobs; a worker on the user's PC/phone (residential IP)
fetches the page and posts the HTML back. In-memory, single-process. Never raises to callers.
"""
from __future__ import annotations

import base64
import gzip
import os
import threading
import time
import uuid

_JOBS: dict = {}                    # job_id -> {url, kind, status, html, error, created}
_LOCK = threading.Lock()
_WORKER = {"seen": 0.0}             # last time any worker polled/returned


def token_ok(tok: str) -> bool:
    want = (os.getenv("SCRAPE_WORKER_TOKEN") or "").strip()
    return (not want) or (tok == want)


def worker_online() -> bool:
    return _WORKER["seen"] > 0 and (time.time() - _WORKER["seen"]) < 40


def fetch(url: str, kind: str, timeout: float = 55.0) -> dict:
    """Enqueue a fetch job and wait (blocking) for a residential worker to return the page HTML.
    Run this via asyncio.to_thread from async endpoints so the event loop stays free. Never raises."""
    jid = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[jid] = {"url": url, "kind": kind, "status": "pending", "html": "", "error": "", "created": time.time()}
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            time.sleep(0.6)
            with _LOCK:
                j = _JOBS.get(jid) or {}
                if j.get("status") == "done":
                    html = j.get("html", "")
                    _JOBS.pop(jid, None)
                    return {"ok": bool(html), "html": html}
                if j.get("status") == "error":
                    err = j.get("error", "worker error")
                    _JOBS.pop(jid, None)
                    return {"ok": False, "error": err}
    finally:
        with _LOCK:
            _JOBS.pop(jid, None)
    if not worker_online():
        return {"ok": False, "error": "No scrape worker connected — start the PC worker (scripts/scrape_worker.py)."}
    return {"ok": False, "error": "Scrape worker timed out — is the worker running and online?"}


def take_jobs(token: str, limit: int = 3) -> dict:
    if not token_ok(token):
        return {"ok": False, "error": "bad token", "code": 403}
    _WORKER["seen"] = time.time()
    out = []
    with _LOCK:
        for jid, j in _JOBS.items():
            if j["status"] == "pending":
                j["status"] = "taken"
                out.append({"job_id": jid, "url": j["url"], "kind": j["kind"]})
            if len(out) >= limit:
                break
    return {"ok": True, "jobs": out}


def submit_result(token: str, job_id: str, html: str = "", gz: bool = False, error: str = "") -> dict:
    if not token_ok(token):
        return {"ok": False, "error": "bad token", "code": 403}
    _WORKER["seen"] = time.time()
    if gz and html:
        try:
            html = gzip.decompress(base64.b64decode(html)).decode("utf-8", "ignore")
        except Exception as e:
            html = ""
            error = error or f"gunzip failed: {str(e)[:60]}"
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            if error and not html:
                j["status"] = "error"; j["error"] = error
            else:
                j["status"] = "done"; j["html"] = html
    return {"ok": True}


def status() -> dict:
    seen = _WORKER["seen"]
    with _LOCK:
        total = len(_JOBS)
        pending = sum(1 for j in _JOBS.values() if j["status"] == "pending")
    return {"ok": True, "online": worker_online(),
            "last_seen_secs": (round(time.time() - seen) if seen else None),
            "queue_total": total, "queue_pending": pending, "pid": os.getpid()}
