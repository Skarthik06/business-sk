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


# ── ONLINE path: the server fetches through a residential proxy (no laptop needed) ────────────
# Same env the browser scraper uses: SCRAPER_PROXY_SERVER=http://host:port, SCRAPER_PROXY_USER,
# SCRAPER_PROXY_PASS. Set them on the server and scraping works 24/7 from anywhere (phone too);
# the laptop worker stays as a free fallback. Credentials are never logged or returned.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_BROWSER = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1",
}
_PROXY_STATS = {"ok": 0, "fail": 0, "last_error": ""}


def _proxy_url() -> str:
    server = (os.getenv("SCRAPER_PROXY_SERVER") or "").strip()
    if not server:
        return ""
    if "://" not in server:
        server = "http://" + server
    user = (os.getenv("SCRAPER_PROXY_USER") or "").strip()
    pw = (os.getenv("SCRAPER_PROXY_PASS") or "").strip()
    if not user:
        return server
    from urllib.parse import quote
    scheme, rest = server.split("://", 1)
    return f"{scheme}://{quote(user, safe='')}:{quote(pw, safe='')}@{rest}"


def proxy_enabled() -> bool:
    return bool(_proxy_url())


def online() -> bool:
    """Can we fetch Amazon/Flipkart right now? (server proxy OR the laptop worker)."""
    return proxy_enabled() or worker_online()


def _fetch_via_proxy(url: str, timeout: float = 45.0) -> dict:
    import ssl
    import urllib.request
    purl = _proxy_url()
    ctx = ssl.create_default_context()
    if (os.getenv("SCRAPER_PROXY_INSECURE") or "").strip() in ("1", "true", "yes"):
        ctx.check_hostname = False                    # some scraping-API proxies re-sign TLS
        ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": purl, "https": purl}),
                                         urllib.request.HTTPSHandler(context=ctx))
    last, err = b"", ""
    for attempt in range(1, 4):                       # each retry usually gets a fresh proxy IP
        try:
            with opener.open(urllib.request.Request(url, headers=_BROWSER), timeout=timeout) as r:
                last = r.read()
            if len(last) > 80_000:                     # a real results page; a few KB = bot-check page
                _PROXY_STATS["ok"] += 1
                return {"ok": True, "html": last.decode("utf-8", "ignore"), "via": "proxy"}
            err = f"blocked/short page ({len(last)} bytes)"
        except Exception as e:                        # noqa: BLE001 — never raise to callers
            err = type(e).__name__ + ": " + str(e)[:120].replace(purl, "<proxy>")
        time.sleep(2 * attempt)
    _PROXY_STATS["fail"] += 1
    _PROXY_STATS["last_error"] = err
    html = last.decode("utf-8", "ignore") if last else ""
    return {"ok": bool(html), "html": html, "via": "proxy", "error": err}


def fetch(url: str, kind: str, timeout: float = 55.0) -> dict:
    """Fetch a shopping page from a residential IP: the server's proxy when configured (online,
    no laptop needed), else the laptop/phone worker. Run via asyncio.to_thread. Never raises."""
    if proxy_enabled():
        res = _fetch_via_proxy(url)
        if (res.get("ok") and len(res.get("html") or "") > 80_000) or not worker_online():
            return res
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
    return {"ok": True, "online": online(), "worker_online": worker_online(),
            "mode": "proxy (online)" if proxy_enabled() else ("laptop worker" if worker_online() else "offline"),
            "proxy": {"enabled": proxy_enabled(), "ok": _PROXY_STATS["ok"], "fail": _PROXY_STATS["fail"],
                      "last_error": _PROXY_STATS["last_error"]},
            "last_seen_secs": (round(time.time() - seen) if seen else None),
            "queue_total": total, "queue_pending": pending, "pid": os.getpid()}
