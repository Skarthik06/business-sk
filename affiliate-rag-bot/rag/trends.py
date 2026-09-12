"""
rag/trends.py  —  Persistent Trend Intelligence (Phase 3, Trend Analyst).

Turns the per-run Tavily keyword fetch (tools/search.py) into PERSISTENT trend memory
so the system can compute momentum + direction over time and make discovery + content
trend-aware. Lives in the SAME PostgreSQL DB as the other stores (one DATABASE_URL).

All I/O is plain JSON-serializable dicts. No fabrication (G13): if a provider gives no
numeric score, trend_score stays None and direction is UNKNOWN — momentum is an INTERNAL
model score derived only from observed keyword recurrence, never presented as a market fact.

Table: trend_observations
  id           SERIAL PK
  keyword      VARCHAR
  category     VARCHAR
  source       VARCHAR      — e.g. 'tavily', 'fallback'
  trend_score  FLOAT NULL   — provider score if any (else None)
  observed_at  TIMESTAMP

Constraint tuning lives in config.TrendConfig / agents/trend-analyst.agents.md.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import cfg
from utils.logger import log


class Base(DeclarativeBase):
    pass


class TrendObservation(Base):
    __tablename__ = "trend_observations"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    keyword     = Column(String(120), nullable=False, index=True)
    category    = Column(String(60), nullable=False, index=True)
    source      = Column(String(40), nullable=False, default="tavily")
    trend_score = Column(Float, nullable=True)
    observed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


_engine = None
_SessionLocal = None


def _get_session() -> Session:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(cfg.storage.sqlalchemy_url, pool_pre_ping=True, echo=False)
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        log.success("[trends] trend_observations table ready ✓")
    return _SessionLocal()


def _norm(kw: str) -> str:
    return re.sub(r"\s+", " ", (kw or "").strip().lower())


def _direction(recent: int, prior: int) -> str:
    """Classify momentum from recent-vs-prior observation counts (window halves)."""
    if recent == 0 and prior == 0:
        return "UNKNOWN"
    if prior == 0:
        return "EXPLODING" if recent >= 3 else "RISING"
    ratio = recent / prior
    if ratio >= 2.0:
        return "EXPLODING"
    if ratio >= 1.2:
        return "RISING"
    if ratio <= 0.5:
        return "DECLINING"
    return "STABLE"


class TrendStore:
    """Persist trend observations and compute internal momentum + direction."""

    def record_observations(self, category: str, keywords: list[str],
                            source: str = "tavily", scores: dict | None = None) -> int:
        """Persist one observation per keyword for this run. Returns count stored."""
        if not cfg.trends.enabled or not keywords:
            return 0
        scores = scores or {}
        now = datetime.now(timezone.utc)
        stored = 0
        try:
            with _get_session() as s:
                for kw in keywords[: cfg.trends.max_keywords]:
                    k = _norm(kw)
                    if not k:
                        continue
                    s.add(TrendObservation(keyword=k, category=category, source=source,
                                           trend_score=scores.get(kw), observed_at=now))
                    stored += 1
                s.commit()
        except Exception as e:
            log.warning(f"[trends] record_observations skipped: {e}")
            return 0
        return stored

    def category_trends(self, category: str, limit: int = 10) -> list[dict]:
        """Top trending keywords for a category with internal momentum + direction.
        JSON-shaped: [{keyword, category, momentum, direction, observations,
        last_observed, avg_provider_score}]. Empty on cold start (G7)."""
        lookback = max(1, cfg.trends.lookback_days)
        since = datetime.now(timezone.utc) - timedelta(days=lookback)
        half = datetime.now(timezone.utc) - timedelta(days=lookback / 2)
        try:
            with _get_session() as s:
                rows = (s.query(
                            TrendObservation.keyword,
                            func.count(TrendObservation.id),
                            func.max(TrendObservation.observed_at),
                            func.avg(TrendObservation.trend_score),
                        )
                        .filter(TrendObservation.category == category,
                                TrendObservation.observed_at >= since)
                        .group_by(TrendObservation.keyword)
                        .all())
                out = []
                for kw, total, last, avg_score in rows:
                    recent = (s.query(func.count(TrendObservation.id))
                              .filter(TrendObservation.category == category,
                                      TrendObservation.keyword == kw,
                                      TrendObservation.observed_at >= half).scalar() or 0)
                    prior = int(total) - int(recent)
                    # Internal momentum 0-100: recurrence (log-scaled) + recency emphasis.
                    import math
                    base = min(math.log10(int(total) + 1) / math.log10(20), 1.0) * 70
                    recency = min(recent / 5, 1.0) * 30
                    momentum = int(round(base + recency))
                    out.append({
                        "keyword": kw, "category": category, "momentum": momentum,
                        "direction": _direction(int(recent), int(prior)),
                        "observations": int(total),
                        "last_observed": last.isoformat() if last else "",
                        "avg_provider_score": round(float(avg_score), 3) if avg_score is not None else None,
                    })
                out.sort(key=lambda x: x["momentum"], reverse=True)
                return out[:limit]
        except Exception as e:
            log.warning(f"[trends] category_trends failed: {e}")
            return []

    def trending_terms(self, category: str, n: int = 5) -> list[str]:
        """Just the top-N trending keyword strings (for trend-aware discovery)."""
        return [t["keyword"] for t in self.category_trends(category, limit=n)]

    def all_trends(self, limit: int = 50) -> list[dict]:
        """Top trends across all categories (dashboard)."""
        cats: list[str] = []
        try:
            with _get_session() as s:
                cats = [r[0] for r in s.query(TrendObservation.category).distinct().all()]
        except Exception:
            return []
        out: list[dict] = []
        for c in cats:
            out.extend(self.category_trends(c, limit=limit))
        out.sort(key=lambda x: x["momentum"], reverse=True)
        return out[:limit]


trend_store = TrendStore()
