"""
tools/retailers  —  Multi-retailer adapters (Phase 8).

Abstracts product search + normalization + affiliate-link building behind one interface so
new retailers can be added without touching the pipeline. Only retailers whose data AND
affiliate-link method are actually available are enabled (blueprint §47) — Amazon is live;
others stay unavailable (health_check False) until implemented. No fabrication.
"""
from __future__ import annotations

from config import cfg
from .base import RetailerAdapter
from .amazon import AmazonAdapter

# Registry of implemented adapters. Add new ones here as they become real.
_ADAPTERS: dict[str, RetailerAdapter] = {
    "amazon": AmazonAdapter(),
}


def enabled_retailers() -> list[str]:
    return [r.strip().lower() for r in (cfg.retailers.enabled or "amazon").split(",") if r.strip()]


def get_adapter(name: str) -> RetailerAdapter | None:
    name = (name or "").lower()
    if name not in enabled_retailers():
        return None
    return _ADAPTERS.get(name)


def health() -> list[dict]:
    """JSON status of every known adapter (live / disabled / not-implemented)."""
    out = []
    en = enabled_retailers()
    for key, adapter in _ADAPTERS.items():
        out.append({"retailer": key, "enabled": key in en,
                    "implemented": True, "healthy": adapter.health_check()})
    # advertise planned adapters honestly as not-implemented
    for planned in ("flipkart", "myntra", "meesho", "croma", "ajio"):
        if planned not in _ADAPTERS:
            out.append({"retailer": planned, "enabled": planned in en,
                        "implemented": False, "healthy": False})
    return out
