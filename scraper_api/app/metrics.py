"""Prometheus metrics + the shared Redis client (set at startup)."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

registry = CollectorRegistry()
requests = Counter("scraper_attempts_total", "Fetch attempts", ["domain", "mode", "outcome"], registry=registry)
latency = Histogram("scraper_attempt_seconds", "Fetch attempt latency", ["mode"], registry=registry,
                    buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120))
cache_hits = Counter("scraper_cache_hits_total", "Cache hits", registry=registry)
bandwidth = Counter("scraper_response_bytes_total", "Response bytes received", registry=registry)
queue_depth = Gauge("scraper_queue_depth", "Queued async jobs", ["queue"], registry=registry)
proxies_healthy = Gauge("scraper_proxies_healthy", "Usable proxies", registry=registry)
browser_active = Gauge("scraper_browser_pages_active", "Open browser pages", registry=registry)

redis = None                                   # redis.asyncio client, set in main.startup

_DOMS: set = set()


def dom(domain: str) -> str:
    """Bounded label cardinality: at most 60 distinct domains, the rest are 'other'."""
    if domain in _DOMS or len(_DOMS) < 60:
        _DOMS.add(domain)
        return domain
    return "other"
