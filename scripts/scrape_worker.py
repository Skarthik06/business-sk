#!/usr/bin/env python3
"""
scrape_worker.py — Business-SK residential scrape worker (ZERO dependencies, stdlib only).

Amazon/Flipkart block the cloud server's datacenter IP. This tiny worker runs on YOUR machine
(PC or phone via Termux) — a residential IP they don't block — polls the server for fetch jobs,
downloads the page, and sends the HTML back. The server parses it into products. Our own free
"ScraperAPI": the cloud coordinates, your device is the proxy.

Run it (needs only Python 3, nothing to pip install):
    python scrape_worker.py

Config (env vars, optional — sensible defaults built in):
    SK_WORKER_URL    base URL of the affiliate API (default the production server)
    SK_WORKER_TOKEN  shared secret (must match SCRAPE_WORKER_TOKEN in the server .env)

Only makes OUTBOUND calls, so it works behind home/carrier NAT — no port-forwarding, no tunnel.
Leave it running while you generate Amazon/Flipkart posts. Ctrl-C to stop.
"""
import base64
import gzip
import json
import os
import time
import urllib.parse
import urllib.request

def _load_token() -> str:
    t = os.getenv("SK_WORKER_TOKEN", "").strip()
    if t:
        return t
    try:                                   # fallback: a local token file (kept out of git)
        with open(os.path.join(os.path.expanduser("~"), ".sk_worker_token"), "r") as f:
            return f.read().strip()
    except Exception:
        return ""


BASE = os.getenv("SK_WORKER_URL", "https://140-238-247-18.nip.io/sk-api").rstrip("/")
TOKEN = _load_token()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
POLL = 2.0
FETCH_TIMEOUT = 45
# Full browser-like headers so Amazon/Flipkart treat us like a real visitor (NOT Accept-Encoding —
# we want plain HTML, not gzip we'd have to decode).
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


def _get(url: str, timeout: int = 30, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_page(url: str) -> bytes:
    """Fetch a shopping page like a real browser, with retry/backoff. Retries when the site returns
    a tiny 'block' page (Amazon/Flipkart anti-bot) so a transient throttle doesn't kill the job."""
    last = b""
    for attempt in range(1, 4):
        try:
            last = _get(url, FETCH_TIMEOUT, headers=_BROWSER)
            # a real search page is large; a few-KB page is a bot-check/robot page → back off & retry
            if len(last) > 80_000:
                return last
        except Exception as e:
            if attempt == 3:
                raise
        time.sleep(3 * attempt + 1)          # 4s, 7s — let the throttle cool down
    return last                               # return whatever we got (server will judge)


def _post_json(url: str, obj: dict, timeout: int = 60) -> None:
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def main():
    print(f"[worker] Business-SK scrape worker -> {BASE}")
    print("[worker] polling for Amazon/Flipkart jobs... (Ctrl-C to stop)\n")
    idle = 0
    jobs_url = f"{BASE}/api/scrape/jobs?" + urllib.parse.urlencode({"token": TOKEN})
    result_url = f"{BASE}/api/scrape/result"
    while True:
        try:
            jobs = (json.loads(_get(jobs_url, 30)) or {}).get("jobs", [])
        except Exception as e:
            print(f"[worker] poll error: {str(e)[:90]}")
            time.sleep(5)
            continue
        if not jobs:
            idle += 1
            if idle % 30 == 0:
                print("[worker] connected, waiting for jobs...")
            time.sleep(POLL)
            continue
        idle = 0
        for j in jobs:
            jid, url, kind = j.get("job_id"), j.get("url", ""), j.get("kind", "")
            try:
                html = _fetch_page(url)
                payload = base64.b64encode(gzip.compress(html)).decode("ascii")
                _post_json(result_url, {"token": TOKEN, "job_id": jid, "html": payload, "gz": True})
                print(f"[worker] OK  {kind:9s} {len(html):>8,d} bytes  {url[:70]}")
            except Exception as e:
                try:
                    _post_json(result_url, {"token": TOKEN, "job_id": jid, "error": str(e)[:140]}, 30)
                except Exception:
                    pass
                print(f"[worker] ERR {kind:9s} {str(e)[:80]}")


if __name__ == "__main__":
    # Self-healing: if main() ever crashes unexpectedly, wait and restart (so an always-on
    # scheduled task keeps the worker alive indefinitely without manual intervention).
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print("\n[worker] stopped.")
            break
        except Exception as e:
            print(f"[worker] crashed: {str(e)[:120]} — restarting in 10s")
            time.sleep(10)
