# Agent — Winner Engine (intelligence × confidence → winner score)

**Role:** Turn the deterministic scores + novelty into a single, transparent **winner score**
used to rank the final picks, and produce a grounded "Why this product?" explanation. Phase 2
of the [Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md). Builds on [[product-scorer]].

**Code anchors:** `chains/discovery.py` (`confidence_score`, `product_intelligence`,
`winner_score`, `evidence`, `winner_bundle`), `server.py` (`_content_item` attaches the bundle;
`/api/generate` ranks by `winner_score`), `config.py` (`NoveltyConfig.min_confidence`).

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `WINNER_MIN_CONFIDENCE` | `0.55` | confidence floor — a couple of missing fields only gently penalise a product, never zero it |
| `NOVELTY_WEIGHT` | `0.10` | novelty's share of product-intelligence (see [[novelty-analyst]]) |

Master weights for the underlying content_score live in `chains/discovery.WEIGHTS` and are
env-tunable — keep commission's share small (G14).

---

## WE1 — Confidence (0.55–1.0) ✓
Reward complete evidence: real price (+0.30), valid image (+0.20), rating (+0.20), reviews
(+0.15), stable ASIN (+0.15); mapped onto `[WINNER_MIN_CONFIDENCE, 1.0]`. A product is never
*selected on incomplete evidence*, but strong products aren't discarded for one missing field.

## WE2 — Product Intelligence ✓
`content_score` (the [[product-scorer]] master blend) folded with novelty per
[[novelty-analyst]] NA5. This is the "how good is this pick" number.

## WE3 — Winner Score ✓
`winner_score = product_intelligence × confidence × freshness`. `freshness` defaults to `1.0`
until product first/last-seen data exists (Phase 6). `/api/generate` ranks items by this,
falling back to `content_score` when novelty is disabled.

## WE4 — Winner tier ✓
Same S/A/B/C/D thresholds as [[product-scorer]] SC6, applied to `winner_score`. Returned as
`winner_tier`, and aggregated in the response's `winner_tiers` histogram.

## WE5 — "Why this product?" evidence ✓
`evidence()` returns ONLY facts the real data supports — real price, rating, review count, real
discount, recent demand, badge, image, category. No claim is emitted unless the source field
exists (G13). This is what the studio's expandable explanation renders.

## WE6 — Honesty & no fabrication (G13) ✓
Every number is derived from real fields or explicit heuristics. Scores rank internally; they
are never printed to buyers as marketing ("96/100!").

## WE7 — Learning hook ⏳
When real performance data exists (Phase 6/7), multiply in a `performance_prior` and let
measured clicks/saves/orders reweight — persist learned weights in config/DB, never inline.
