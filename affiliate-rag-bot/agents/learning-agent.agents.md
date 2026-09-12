# Agent — Learning Agent (priors from measured performance)

**Role:** Turn measured results (Phase 6) into PRIORS that reweight future selection — which
categories and content styles actually perform — without overfitting. Phase 7 of the
[Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md).

**Code anchors:** `performance/learner.py` (`category_priors`, `style_priors`,
`recommendations`), `chains/discovery.py` (`blend_prior`), `server.py`
(`/api/generate` prior fold + `/api/intelligence/*`), `config.PerformanceConfig`.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `PERFORMANCE_PRIOR_WEIGHT` | `0.10` | how much a category prior nudges product-intelligence |
| `PERFORMANCE_MIN_SAMPLE` | `3` | measured posts required before a prior counts (anti-overfit) |

---

## LN1 — Priors, gated by sample size ✓
`category_priors` / `style_priors` average `outcome_score` per group, and ONLY return a group
once it has ≥ `PERFORMANCE_MIN_SAMPLE` posts (blueprint §87 — never conclude "viral" from one
post). Empty until enough data (G7).

## LN2 — Feeds ranking ✓
`/api/generate` folds a category's prior into `intelligence_score` via `blend_prior` at
`PERFORMANCE_PRIOR_WEIGHT`, then recomputes `winner_score`/`winner_tier`. Bounded weight — a
prior nudges, never dominates ([[winner-engine]]).

## LN3 — Observation vs prediction ✓
Priors describe what was OBSERVED. Forward-looking probability is the [[winner-prediction]] agent
(Phase 10), which falls back to deterministic scoring when data is thin.

## LN4 — Query learning ✓ (delegated)
Which discovery queries yield winners is already learned by [[discovery-planner]]
(`discovery_queries` priority). This agent adds the category/style layer on top.

## LN5 — Recommendations (JSON) ✓
`/api/intelligence/insights` + `/recommendations` return ranked categories/styles with sample
counts and a `has_data` flag. Honest "no performance data yet" until a source is connected.

## LN6 — Future ⏳
Per-(category × price-band × style) priors; time-decay so recent performance weighs more; hook
learning. Escalate to a calibrated model only via [[winner-prediction]].
