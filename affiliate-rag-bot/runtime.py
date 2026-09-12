"""
runtime.py  —  Runtime-tunable settings overlay (Phase: editable Agents panel).

Lets each agent's constraints be changed from the studio WITHOUT a restart. A small
key/value table (agent_settings) overrides the env/config defaults; hot paths read a
value via `get(KEY, default, cast)` which checks the overlay first (cached ~5s) then
falls back to the config default. Set/clear from the Agents panel via the API.

Only a curated allowlist of knobs is editable (TUNABLE), so the UI can't set arbitrary keys.
"""
from __future__ import annotations

import time

from sqlalchemy import Column, String, Text
from sqlalchemy.orm import DeclarativeBase

from rag.store_base import ensure, session
from utils.logger import log


class Base(DeclarativeBase):
    pass


class AgentSetting(Base):
    __tablename__ = "agent_settings"
    key   = Column(String(60), primary_key=True)
    value = Column(Text, nullable=False, default="")


# Editable knobs: key → (type, min, max, agent, label). Bounds are enforced on write.
TUNABLE: dict[str, dict] = {
    "DISCOVERY_MAX_QUERIES": {"type": "int", "min": 1, "max": 12, "agent": "discovery-planner", "label": "Search intents per run"},
    "DISCOVERY_MAX_PAGES":   {"type": "int", "min": 1, "max": 5,  "agent": "discovery-planner", "label": "Pages per intent"},
    "DISCOVERY_TARGET_POOL": {"type": "int", "min": 10, "max": 200, "agent": "discovery-planner", "label": "Target unique pool"},
    "NOVELTY_WEIGHT":        {"type": "float", "min": 0, "max": 0.5, "agent": "novelty-analyst", "label": "Novelty weight"},
    "TREND_WEIGHT":          {"type": "float", "min": 0, "max": 0.5, "agent": "trend-analyst", "label": "Trend weight"},
    "TREND_DISCOVERY_TERMS": {"type": "int", "min": 0, "max": 8, "agent": "trend-analyst", "label": "Trend terms in discovery"},
    "WINNER_MIN_CONFIDENCE": {"type": "float", "min": 0.2, "max": 1.0, "agent": "winner-engine", "label": "Confidence floor"},
    "PERFORMANCE_PRIOR_WEIGHT": {"type": "float", "min": 0, "max": 0.5, "agent": "learning-agent", "label": "Performance prior weight"},
    "QUALITY_MIN_RATING":    {"type": "float", "min": 0, "max": 5, "agent": "product-scout", "label": "Min rating"},
    "QUALITY_MIN_REVIEWS":   {"type": "int", "min": 0, "max": 5000, "agent": "product-scout", "label": "Min reviews"},
    "QUALITY_PRICE_MAX":     {"type": "int", "min": 200, "max": 100000, "agent": "product-scout", "label": "Max price ₹"},
    "CONTENT_DEFAULT_STYLE": {"type": "str", "min": None, "max": None, "agent": "content-intelligence", "label": "Default caption style"},
}

_cache: dict = {}
_cache_at = 0.0
_TTL = 5.0


def _init():
    ensure(Base, "agent_settings")


def _load() -> dict:
    global _cache, _cache_at
    now = time.time()
    if now - _cache_at < _TTL:
        return _cache
    try:
        _init()
        with session() as s:
            _cache = {r.key: r.value for r in s.query(AgentSetting).all()}
    except Exception as e:
        log.warning(f"[runtime] load failed: {e}")
        _cache = {}
    _cache_at = now
    return _cache


def _cast(v: str, t: str):
    try:
        if t == "int":
            return int(float(v))
        if t == "float":
            return float(v)
        return str(v)
    except Exception:
        return None


def get(key: str, default, cast: str = "str"):
    """Overlay value for `key` (cached), else `default`. `cast`: int|float|str."""
    ov = _load().get(key)
    if ov is None or ov == "":
        return default
    val = _cast(ov, cast)
    return default if val is None else val


def set_value(key: str, value) -> dict:
    """Set an override (validated against TUNABLE bounds). Returns {ok,...}."""
    spec = TUNABLE.get(key)
    if not spec:
        return {"ok": False, "error": f"'{key}' is not editable"}
    v = _cast(str(value), spec["type"])
    if v is None:
        return {"ok": False, "error": "invalid value"}
    if spec["type"] in ("int", "float") and spec.get("min") is not None:
        if v < spec["min"] or v > spec["max"]:
            return {"ok": False, "error": f"out of range [{spec['min']}, {spec['max']}]"}
    if key == "CONTENT_DEFAULT_STYLE":
        from chains.compose import STYLES
        if str(v) not in (["auto"] + STYLES):
            return {"ok": False, "error": "unknown style"}
    _init()
    with session() as s:
        row = s.get(AgentSetting, key) or AgentSetting(key=key)
        row.value = str(v); s.merge(row); s.commit()
    global _cache_at
    _cache_at = 0.0                                   # invalidate cache
    return {"ok": True, "key": key, "value": v}


def clear(key: str) -> dict:
    _init()
    with session() as s:
        row = s.get(AgentSetting, key)
        if row:
            s.delete(row); s.commit()
    global _cache_at
    _cache_at = 0.0
    return {"ok": True, "cleared": key}


def all_overrides() -> dict:
    return dict(_load())
