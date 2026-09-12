# Agent — Trend Analyst (persistent trends + momentum + direction)

**Role:** Turn the per-run keyword fetch ([[trend-scout]] / Tavily) into **persistent trend
memory**, compute internal **momentum + direction** over time, and make discovery + content
**trend-aware** — without adding any LLM call. Phase 3 of the
[Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md).

**Code anchors:** `rag/trends.py` (`trend_observations` table, `record_observations`,
`category_trends`, `trending_terms`, momentum + `_direction`), `tools/search.py` (keyword
source), `graph/nodes.py` (`search_trends` persists + emits `trend_signals`; `scrape_amazon`
folds trending terms into discovery; `compose_pins` computes per-product trend alignment),
`chains/discovery.py` (`trend_alignment`, `product_intelligence`), `chains/compose.py`
(`_trends_block` feeds direction to the ONE LLM call), `server.py` (`/api/trends`),
`config.TrendConfig`. Legend: ✓ · ⏳.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `TREND_ENABLED` | `1` | `0` disables the whole trend layer |
| `TREND_MAX_KEYWORDS` | `12` | keywords stored/returned per run |
| `TREND_LOOKBACK_DAYS` | `14` | momentum/direction window |
| `TREND_WEIGHT` | `0.08` | trend's share of product-intelligence (small — discovery-first, G14) |
| `TREND_DISCOVERY_TERMS` | `3` | trending terms blended into discovery rotation per run |

---

## TA1 — Persistent observations ✓
Each run stores one `trend_observation` per keyword (`keyword, category, source,
trend_score?, observed_at`). Source is `tavily` when a key is set, else `fallback`.

## TA2 — Momentum (0-100, INTERNAL) ✓
Derived from keyword recurrence (log-scaled count, ≤70) + recency (recent-half emphasis,
≤30). It is a MODEL score for ranking — never presented to a buyer as a market fact (G13).

## TA3 — Direction ✓
`EXPLODING / RISING / STABLE / DECLINING / UNKNOWN`, from recent-vs-prior counts across the
lookback window. No provider data ⇒ `UNKNOWN` + `trend_score=None` (never invented, G13).

## TA4 — Trend-aware discovery ✓
`trending_terms(category)` (from persisted momentum) is folded into the [[discovery-planner]]
query candidates and prioritised, so mining leans toward what's rising. Cold start ⇒ no change.

## TA5 — Trend-aware content (efficient AI) ✓
`_trends_block` passes `keyword↑DIRECTION` into the SINGLE structured compose call so captions
can lead with exploding trends — **no extra LLM call** (G10). Direction guides wording; it is
not asserted as fact in the caption.

## TA6 — Trend alignment in the Winner Engine ✓
`trend_alignment(product, signals)` = momentum of the best trend keyword matching the product
title/category (substring), else a small baseline; `None` when no trend data (no penalty at
cold start). Folded into `product_intelligence` at `TREND_WEIGHT` — modest, never dominant.

## TA7 — Fail-open (G2) ✓
Any provider/DB/momentum failure degrades gracefully: keywords still reach the composer, and
scoring proceeds without the trend term. A trend problem never aborts a valid run.

## TA8 — JSON everywhere ✓
`GET /api/trends` and `GET /api/trends/{category}` return `{ok, category, count, trends:[…]}`;
`/api/generate` includes a `trends` array + each item's `trend_score`. All timestamps ISO-8601.
