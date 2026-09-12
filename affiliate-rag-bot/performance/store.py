"""
performance/store.py  —  Post performance storage + funnel metrics (Phase 6).

Stores observable results for each published post and derives funnel/efficiency metrics.
CRITICAL (G13): metrics come ONLY from a connected source (IG insights, affiliate tracking).
Nothing is fabricated — an unavailable metric stays None and its derived metrics stay None
("not connected"). This is the data foundation the Learning agent (Phase 7) builds on.

Table: post_performance (one row per snapshot; latest per post wins)
  id, post_id, account_id, captured_at, source,
  reach, impressions, likes, comments, shares, saves,
  profile_visits, link_clicks, orders, commission
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import Column, String, Integer, Float, DateTime, func
from sqlalchemy.orm import DeclarativeBase

from rag.store_base import ensure, session
from config import cfg
from utils.logger import log

_METRICS = ["reach", "impressions", "likes", "comments", "shares", "saves",
            "profile_visits", "link_clicks", "orders", "commission"]


class Base(DeclarativeBase):
    pass


class PostPerformance(Base):
    __tablename__ = "post_performance"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    post_id        = Column(String(64), nullable=False, index=True)
    account_id     = Column(String(80), nullable=False, default="default")
    source         = Column(String(40), nullable=False, default="manual")
    captured_at    = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    reach          = Column(Integer, nullable=True)
    impressions    = Column(Integer, nullable=True)
    likes          = Column(Integer, nullable=True)
    comments       = Column(Integer, nullable=True)
    shares         = Column(Integer, nullable=True)
    saves          = Column(Integer, nullable=True)
    profile_visits = Column(Integer, nullable=True)
    link_clicks    = Column(Integer, nullable=True)
    orders         = Column(Integer, nullable=True)
    commission     = Column(Float, nullable=True)


def _init():
    ensure(Base, "performance")


def _row_to_metrics(r: PostPerformance) -> dict:
    return {m: getattr(r, m) for m in _METRICS}


def outcome_score(metrics: dict) -> float | None:
    """Internal 0-100 outcome score from AVAILABLE metrics only (weighted, log-scaled).
    None when no usable metric exists — never invents a result (G13)."""
    import math
    weights = {"commission": 0.35, "orders": 0.20, "link_clicks": 0.20,
               "saves": 0.15, "comments": 0.05, "likes": 0.05}
    caps = {"commission": 5000, "orders": 50, "link_clicks": 1000,
            "saves": 2000, "comments": 500, "likes": 5000}
    num = 0.0; wsum = 0.0
    for k, w in weights.items():
        v = metrics.get(k)
        if v is None:
            continue
        norm = min(math.log10(float(v) + 1) / math.log10(caps[k] + 1), 1.0) * 100
        num += norm * w; wsum += w
    if wsum == 0:
        return None
    return round(num / wsum, 1)


class PerformanceStore:
    def ingest(self, post_id: str, metrics: dict, account_id: str = "default",
               source: str = "manual") -> dict:
        """Store one performance snapshot. Only known metric keys are kept; unknown/None
        stay None. Returns the stored snapshot + its outcome score."""
        _init()
        clean = {m: metrics.get(m) for m in _METRICS if metrics.get(m) is not None}
        with session() as s:
            row = PostPerformance(post_id=str(post_id), account_id=account_id,
                                  source=source, **clean)
            s.add(row); s.commit()
            snap = _row_to_metrics(row)
        return {"post_id": post_id, "metrics": snap, "outcome_score": outcome_score(snap),
                "source": source}

    def latest_for(self, post_id: str) -> dict | None:
        _init()
        with session() as s:
            r = (s.query(PostPerformance).filter(PostPerformance.post_id == str(post_id))
                 .order_by(PostPerformance.captured_at.desc()).first())
            if not r:
                return None
            m = _row_to_metrics(r)
            return {"post_id": post_id, "captured_at": r.captured_at.isoformat(),
                    "source": r.source, "metrics": m, "outcome_score": outcome_score(m)}

    def _latest_rows(self, days: int) -> list[PostPerformance]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with session() as s:
            # one latest snapshot per post within the window
            sub = (s.query(PostPerformance.post_id,
                           func.max(PostPerformance.captured_at).label("mx"))
                   .filter(PostPerformance.captured_at >= since)
                   .group_by(PostPerformance.post_id).subquery())
            rows = (s.query(PostPerformance)
                    .join(sub, (PostPerformance.post_id == sub.c.post_id) &
                          (PostPerformance.captured_at == sub.c.mx)).all())
            return rows

    def overview(self) -> dict:
        """Aggregate funnel + efficiency across posts with data. 'connected' is False when
        no source has reported anything (everything then reads 'not connected')."""
        _init()
        try:
            rows = self._latest_rows(cfg.performance.lookback_days)
        except Exception as e:
            log.warning(f"[performance] overview failed: {e}")
            rows = []
        if not rows:
            return {"connected": False, "posts_with_data": 0,
                    "totals": {m: None for m in _METRICS}, "derived": {}}
        totals = {}
        for m in _METRICS:
            vals = [getattr(r, m) for r in rows if getattr(r, m) is not None]
            totals[m] = (round(sum(vals), 2) if m == "commission" else sum(vals)) if vals else None

        def ratio(a, b):
            return round(totals[a] / totals[b], 4) if totals.get(a) is not None and totals.get(b) else None

        derived = {
            "profile_visit_rate": ratio("profile_visits", "reach"),
            "product_ctr":        ratio("link_clicks", "reach"),
            "conversion_rate":    ratio("orders", "link_clicks"),
            "commission_per_click": ratio("commission", "link_clicks"),
            "commission_per_post":  round(totals["commission"] / len(rows), 2) if totals.get("commission") is not None else None,
            "commission_per_1000_reach": (round(totals["commission"] / totals["reach"] * 1000, 2)
                                          if totals.get("commission") is not None and totals.get("reach") else None),
        }
        return {"connected": True, "posts_with_data": len(rows),
                "totals": totals, "derived": derived}

    def by_posts(self, limit: int = 50) -> list[dict]:
        _init()
        rows = self._latest_rows(cfg.performance.lookback_days)
        out = [{"post_id": r.post_id, "metrics": _row_to_metrics(r),
                "outcome_score": outcome_score(_row_to_metrics(r)),
                "captured_at": r.captured_at.isoformat()} for r in rows]
        out.sort(key=lambda x: (x["outcome_score"] or 0), reverse=True)
        return out[:limit]


performance_store = PerformanceStore()
