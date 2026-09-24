#!/usr/bin/env python3
"""
scrape_worker.py — Business-SK residential scrape worker.

Amazon/Flipkart block the cloud server's datacenter IP. This tiny worker runs on YOUR machine
(PC or phone via Termux) — a residential IP they don't block — polls the server for fetch jobs,
downloads the page, and sends the HTML back. The server parses it into products. It's our own
free "ScraperAPI": the cloud coordinates, your device is the proxy.

Run it:
    pip install requests
    python scrape_worker.py

Config (env vars, optional — sensible defaults built in):
    SK_WORKER_URL    base URL of the affiliate API (default the production server)
    SK_WORKER_TOKEN  shared secret (must match SCRAPE_WORKER_TOKEN in the server .env)

Only makes OUTBOUND calls, so it works behind home/carrier NAT — no port-forwarding, no tunnel.
Leave it running while you generate Amazon/Flipkart posts. Ctrl-C to stop.
"""
import base64
import gzip
import os
import time

import requests

BASE = os.getenv("SK_WORKER_URL", "https://140-238-247-18.nip.io/sk-api").rstrip("/")
TOKEN = os.getenv("SK_WORKER_TOKEN", "")
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
POLL = 2.0
FETCH_TIMEOUT = 45


def fetch(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=FETCH_TIMEOUT)
    return r.text


def main():
    print(f"[worker] Business-SK scrape worker → {BASE}")
    print("[worker] polling for Amazon/Flipkart jobs… (Ctrl-C to stop)\n")
    idle = 0
    while True:
        try:
            r = requests.get(f"{BASE}/api/scrape/jobs", params={"token": TOKEN}, timeout=30)
            jobs = (r.json() or {}).get("jobs", []) if r.ok else []
        except Exception as e:
            print(f"[worker] poll error: {str(e)[:80]}")
            time.sleep(5)
            continue
        if not jobs:
            idle += 1
            if idle % 30 == 0:
                print("[worker] connected, waiting for jobs…")
            time.sleep(POLL)
            continue
        idle = 0
        for j in jobs:
            jid, url, kind = j.get("job_id"), j.get("url", ""), j.get("kind", "")
            try:
                html = fetch(url)
                payload = base64.b64encode(gzip.compress(html.encode("utf-8", "ignore"))).decode("ascii")
                requests.post(f"{BASE}/api/scrape/result",
                              json={"token": TOKEN, "job_id": jid, "html": payload, "gz": True},
                              timeout=60)
                print(f"[worker] ✓ {kind:9s} {len(html):>8,d} bytes  {url[:70]}")
            except Exception as e:
                try:
                    requests.post(f"{BASE}/api/scrape/result",
                                  json={"token": TOKEN, "job_id": jid, "error": str(e)[:140]}, timeout=30)
                except Exception:
                    pass
                print(f"[worker] ✗ {kind:9s} {str(e)[:80]}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[worker] stopped.")
