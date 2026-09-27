"""Scraper API settings — all from env (see .env.example). Nothing secret is ever logged."""
from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("SCRAPER_DATABASE_URL", "postgresql://instagram:instagram@db:5432/scraper_api")
REDIS_URL = os.getenv("SCRAPER_REDIS_URL", "redis://redis:6379/3")
DATA_DIR = Path(os.getenv("SCRAPER_DATA_DIR", "/data"))
CACHE_DIR = DATA_DIR / "cache"
STRATEGIES_FILE = Path(os.getenv("SCRAPER_STRATEGIES", str(ROOT / "strategies" / "domains.yaml")))

API_KEYS = [k.strip() for k in (os.getenv("SCRAPER_API_KEYS") or "").split(",") if k.strip()]
SECRET_KEY = (os.getenv("SCRAPER_SECRET_KEY") or "").strip()   # Fernet key for proxy credentials

MAX_BYTES = _int("SCRAPER_MAX_BYTES", 8_000_000)                # per response
MAX_URL_LEN = _int("SCRAPER_MAX_URL_LEN", 2048)
MAX_REDIRECTS = _int("SCRAPER_MAX_REDIRECTS", 5)
GLOBAL_CONCURRENCY = _int("SCRAPER_CONCURRENCY", 8)
BROWSER_CONCURRENCY = _int("SCRAPER_BROWSER_CONCURRENCY", 2)
BROWSER_IDLE_CLOSE_SECS = _int("SCRAPER_BROWSER_IDLE_CLOSE_SECS", 300)
RATE_LIMIT_PER_MIN = _int("SCRAPER_RATE_LIMIT_PER_MIN", 120)
JOB_WORKERS = _int("SCRAPER_JOB_WORKERS", 2)
JOB_RESULT_TTL = _int("SCRAPER_JOB_RESULT_TTL", 86400)
CACHE_MAX_AGE = _int("SCRAPER_CACHE_MAX_AGE", 86400)            # disk files older than this are removed
ALLOW_PRIVATE = _flag("SCRAPER_ALLOW_PRIVATE")                  # SSRF guard off (tests / trusted LAN only)

PROXY_HEALTH_URL = os.getenv("SCRAPER_PROXY_HEALTH_URL", "https://www.gstatic.com/generate_204")
PROXY_HEALTH_SECS = _int("SCRAPER_PROXY_HEALTH_SECS", 600)
PROXY_MAX_LEASES = _int("SCRAPER_PROXY_MAX_LEASES", 4)          # concurrent requests per proxy
WORKER_ID = os.getenv("HOSTNAME", "scraper")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "application/json", "application/ld+json",
                         "text/plain", "application/xml", "text/xml")
