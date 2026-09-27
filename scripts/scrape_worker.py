#!/usr/bin/env python3
"""
scrape_worker.py — Business-SK remote scrape worker v2 (ZERO dependencies, stdlib only).

One of the Scraper API's routes. Amazon/Flipkart reject the cloud server's datacenter IP; this worker
runs on YOUR laptop or phone (Termux) — a home / mobile IP — and the server's Strategy Engine sends it
the jobs that need it. Laptop off? The phone takes over automatically, and vice versa.

    python scrape_worker.py                      # laptop
    SK_WORKER_ID=phone-01 SK_WORKER_KIND=phone python scrape_worker.py    # phone (Termux)

Config (env, all optional):
    SK_SCRAPER_URL   worker endpoint base   (default https://140-238-247-18.nip.io/scraper-worker)
    SK_WORKER_TOKEN  worker token           (default: the file ~/.sk_worker_token)
    SK_WORKER_ID     unique name, e.g. laptop-01 / phone-01   (default laptop-<hostname>)
    SK_WORKER_KIND   laptop | phone | remote                    (default laptop)

Protocol: heartbeat every 20 s · long-poll /jobs/lease · POST the page back to /jobs/<id>/result.
Only OUTBOUND HTTPS, so it works behind home / carrier NAT — no port-forwarding, no tunnel.
"""
import base64
import gzip
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request

VERSION = "2.0"


def _load_token() -> str:
    t = os.getenv("SK_WORKER_TOKEN", "").strip()
    if t:
        return t
    try:                                   # a local token file (kept out of git)
        with open(os.path.join(os.path.expanduser("~"), ".sk_worker_token"), "r") as f:
            return f.read().strip()
    except Exception:
        return ""


BASE = os.getenv("SK_SCRAPER_URL", "https://140-238-247-18.nip.io/scraper-worker").rstrip("/")
TOKEN = _load_token()
KIND = os.getenv("SK_WORKER_KIND", "laptop").strip() or "laptop"
WORKER_ID = re.sub(r"[^A-Za-z0-9_.-]", "-", os.getenv("SK_WORKER_ID", "").strip()
                   or f"{KIND}-{socket.gethostname()}")[:40]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# Full browser-like headers (NOT Accept-Encoding — we want plain HTML, not gzip to decode).
_BROWSER = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_ACTIVE = {"n": 0}


def log(msg: str) -> None:
    try:
        print(f"[worker {WORKER_ID}] {time.strftime('%H:%M:%S')} {msg}", flush=True)
    except Exception:
        pass


def _api(path: str, obj: dict, timeout: float = 40) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Worker-Token": TOKEN,
                                          "User-Agent": f"sk-scrape-worker/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _info() -> dict:
    return {"worker_id": WORKER_ID, "status": "healthy", "capabilities": ["http"], "kind": KIND,
            "active_jobs": _ACTIVE["n"], "version": VERSION}


def _fetch(url: str, timeout: int) -> dict:
    """One page like a real browser. A tiny first page is usually a momentary throttle → one retry."""
    t0 = time.time()
    status, body, final, ctype, err = None, b"", url, "", None
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers=_BROWSER)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, body, final = r.status, r.read(), r.geturl()
                ctype = r.headers.get("Content-Type", "")
            err = None
        except urllib.error.HTTPError as e:          # 4xx/5xx still carry a page the server classifies
            status, final, ctype = e.code, url, e.headers.get("Content-Type", "") if e.headers else ""
            try:
                body = e.read()
            except Exception:
                body = b""
            err = None
        except Exception as e:                       # timeout / DNS / connection
            status, body, err = None, b"", f"{type(e).__name__}: {str(e)[:120]}"
        if status == 200 and len(body) < 20_000 and attempt == 1:
            time.sleep(4)
            continue
        break
    return {"status_code": status, "body": body, "final_url": final, "content_type": ctype, "error": err,
            "duration_ms": int((time.time() - t0) * 1000)}


def _heartbeat_loop() -> None:
    while True:
        try:
            _api("/heartbeat", _info(), 20)
        except Exception:
            pass
        time.sleep(20)


def main() -> None:
    if not TOKEN:
        log("no token: set SK_WORKER_TOKEN or create ~/.sk_worker_token")
        time.sleep(60)
        return
    log(f"v{VERSION} online as {KIND} -> {BASE}")
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    fails = 0
    while True:
        try:
            jobs = _api("/jobs/lease", {**_info(), "max": 1, "wait": 20}, timeout=40).get("jobs", [])
            fails = 0
        except urllib.error.HTTPError as e:
            log(f"lease refused: HTTP {e.code}" + (" (check the token)" if e.code == 401 else ""))
            time.sleep(30 if e.code == 401 else 10)
            continue
        except Exception as e:
            fails += 1
            if fails in (1, 10) or fails % 60 == 0:
                log(f"server unreachable ({str(e)[:80]}) — retrying")
            time.sleep(min(30, 3 * fails))
            continue
        for j in jobs:
            _ACTIVE["n"] += 1
            try:
                r = _fetch(j["url"], int(j.get("timeout") or 30))
                body = r.pop("body")
                payload = {"worker_id": WORKER_ID, **r,
                           "html": base64.b64encode(gzip.compress(body)).decode("ascii") if body else "", "gz": True}
                _api(f"/jobs/{j['attempt_id']}/result", payload, timeout=90)
                log(f"{'OK ' if r['status_code'] == 200 else 'ERR'} {r['status_code']} {len(body):>9,d} B "
                    f"{r['duration_ms']:>6d} ms  {j['url'][:70]}")
            except Exception as e:
                log(f"job failed: {str(e)[:100]}")
            finally:
                _ACTIVE["n"] -= 1


if __name__ == "__main__":
    # Self-healing: never die for good (runs from the Startup folder / Termux boot).
    while True:
        try:
            main()
        except KeyboardInterrupt:
            log("stopped.")
            break
        except Exception as e:
            log(f"crashed: {str(e)[:120]} — restarting in 10s")
            time.sleep(10)
