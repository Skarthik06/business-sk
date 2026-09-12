"""
performance/learner.py  —  Learning agent (Phase 7).

Turns measured results (Phase 6) into PRIORS that reweight future selection:
  - which CATEGORIES historically perform best
  - which CONTENT STYLES historically perform best
  - which DISCOVERY QUERIES yield winners (delegated to rag.discovery_stats)

Guardrails: priors need a minimum sample (PERFORMANCE_MIN_SAMPLE) before they count —
no overfitting to one post (blueprint §87). Distinguishes observation from prediction.
Everything is JSON-serializable; empty until performance data exists (G7).
"""
from __future__ import annotations

from performance.store import performance_store, outcome_score
from config import cfg
from utils.logger import log


def _joined() -> list[dict]:
    """Join latest performance snapshots with their post's category + content_style."""
    try:
        from rag.posts import post_store
        posts = {str(p["id"]): p for p in post_store.list(limit=500)}
    except Exception as e:
        log.warning(f"[learner] posts unavailable: {e}")
        return []
    rows = []
    try:
        for snap in performance_store.by_posts(limit=500):
            p = posts.get(str(snap["post_id"]))
            if not p or snap["outcome_score"] is None:
                continue
            rows.append({"category": p.get("category", ""),
                         "content_style": p.get("content_style", "") or "UNKNOWN",
                         "outcome_score": snap["outcome_score"]})
    except Exception as e:
        log.warning(f"[learner] join failed: {e}")
    return rows


def _avg_by(rows: list[dict], key: str) -> dict:
    agg: dict = {}
    for r in rows:
        agg.setdefault(r[key], []).append(r["outcome_score"])
    out = {}
    for k, vals in agg.items():
        if len(vals) >= cfg.performance.min_sample:      # min-sample gate (no overfit)
            out[k] = {"prior": round(sum(vals) / len(vals), 1), "samples": len(vals)}
    return out


class Learner:
    def category_priors(self) -> dict:
        """{category: {prior 0-100, samples}} — categories with enough measured posts."""
        return _avg_by(_joined(), "category")

    def style_priors(self) -> dict:
        """{content_style: {prior 0-100, samples}}."""
        return _avg_by(_joined(), "content_style")

    def prior_for_category(self, category: str) -> float | None:
        p = self.category_priors().get((category or "").lower()) or self.category_priors().get(category)
        return p["prior"] if p else None

    def recommendations(self) -> dict:
        """Top categories + styles by measured outcome (JSON). Empty until data exists."""
        cats = self.category_priors(); styles = self.style_priors()
        top_cat = sorted(cats.items(), key=lambda kv: kv[1]["prior"], reverse=True)
        top_sty = sorted(styles.items(), key=lambda kv: kv[1]["prior"], reverse=True)
        return {
            "categories": [{"category": k, **v} for k, v in top_cat],
            "content_styles": [{"style": k, **v} for k, v in top_sty],
            "has_data": bool(cats or styles),
            "note": ("recommendations are based on measured performance"
                     if (cats or styles) else "no performance data yet (connect a source)"),
        }


learner = Learner()
