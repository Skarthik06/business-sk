"""
prediction/model.py  —  Winner prediction (Phase 10).

Follows the blueprint's ML roadmap (§89): start with deterministic scoring + historical
averages, escalate only when data justifies it. The system stays useful even with NO trained
model — `predict_winners` falls back to the deterministic winner_score, layering measured
category priors on top when they exist. No fabrication; every number is grounded.

Roadmap:
  Phase 1 deterministic scoring        ← live (chains/discovery)
  Phase 2 historical averages          ← live (performance.learner priors, folded here)
  Phase 3 feature-based probability     ← ⏳ (interface ready; falls back until data)
  Phase 4 calibrated winner prediction  ← ⏳
"""
from __future__ import annotations

from config import cfg
from chains import discovery as _d
from utils.logger import log


def predict_winners(limit: int = 12) -> dict:
    """Predicted-winner shortlist over already-posted products. Uses measured category
    priors when available (historical-average model), else pure deterministic winner_score.
    Returns {method, has_model, count, winners:[…]} — all JSON."""
    try:
        from rag.posts import post_store
        raw = post_store.all_products()
    except Exception as e:
        log.warning(f"[prediction] posts unavailable: {e}")
        return {"method": "unavailable", "has_model": False, "count": 0, "winners": []}

    cats: dict = {}
    if cfg.performance.enabled:
        try:
            from performance.learner import learner
            cats = learner.category_priors()
        except Exception:
            cats = {}

    scored = []
    for p in raw:
        wb = _d.winner_bundle(p, None, None)                 # no novelty/trend context off-run
        prior = cats.get(p.get("category", ""))
        prior_v = prior["prior"] if prior else None
        intel = _d.blend_prior(wb["intelligence_score"], prior_v, cfg.performance.prior_weight)
        ws = _d.winner_score(intel, wb["confidence"])
        scored.append({**p, **_d.score_product(p),
                       "intelligence_score": intel, "confidence": wb["confidence"],
                       "winner_score": ws, "winner_tier": _d.tier(ws),
                       "performance_prior": prior_v, "evidence": wb["evidence"]})
    scored.sort(key=lambda x: x["winner_score"], reverse=True)
    method = "historical-average" if cats else "deterministic"
    return {"method": method, "has_model": False, "count": len(scored),
            "winners": scored[:limit]}
