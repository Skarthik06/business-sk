# Agent — Winner Prediction (graceful, data-driven when possible)

**Role:** Predict which products are most likely to perform, following the blueprint's ML
roadmap — deterministic first, historical averages next, calibrated model only when data
justifies it. Phase 10 of the [Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md). The system
stays useful even with NO trained model.

**Code anchors:** `prediction/model.py` (`predict_winners`), `chains/discovery.py`
(deterministic winner_score, `blend_prior`), `performance/learner.py` (priors), `server.py`
(`/api/intelligence/winners`).

---

## WP1 — Graceful fallback (blueprint §101) ✓
`predict_winners` ranks posted products by the deterministic `winner_score`, layering measured
category priors on top when they exist. `method` reports `deterministic` or `historical-average`;
`has_model:false` until a trained model is added. No result is ever fabricated.

## WP2 — ML roadmap (do not over-engineer) ✓/⏳
Phase 1 deterministic (✓) → Phase 2 historical averages (✓ via priors) → Phase 3 feature-based
probability (⏳) → Phase 4 calibrated prediction (⏳). Each step ships only when the prior step's
data supports it; a new model is never auto-deployed without validation (blueprint §88).

## WP3 — Observation vs prediction (G13) ✓
Outputs are model estimates, clearly labelled `method`/`has_model` — never presented to a buyer
as guaranteed outcomes. Target is P(high performance | product, content, account), not a promise.

## WP4 — JSON ✓
`GET /api/intelligence/winners` → `{method, has_model, count, winners:[…]}`.
