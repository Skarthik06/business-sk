# Agent — Discovery Planner (multi-query mining + adaptive rotation)

**Role:** Decide WHICH Amazon search intents to mine each run and how deep to go, so every
run surfaces the maximum number of **fresh, unique** quality products — instead of one fixed
category term. This is Phase 1 of the [Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md).

**Code anchors:** `tools/amazon.py` (`scrape_products_multi`, `_scrape_page`, `_finalize_pool`),
`chains/discovery.py` (`category_queries`, `SUBCATEGORIES`), `rag/discovery_stats.py`
(query-yield ledger + `pick_queries`/`record_yields`), `graph/nodes.py` (`scrape_amazon`),
`config.py` (`DiscoveryConfig`). Legend: ✓ enforced · ⏳ pending.

**Editable constraints (change without touching code):**
| Env var | Default | Meaning |
|---|---|---|
| `DISCOVERY_ENABLED` | `1` | `0` reverts to the original single-query scrape |
| `DISCOVERY_MAX_QUERIES` | `5` | subcategory intents mined per run |
| `DISCOVERY_MAX_PAGES` | `2` | search pages paginated per intent |
| `DISCOVERY_TARGET_POOL` | `60` | stop early once this many unique quality candidates are pooled |

---

## DP1 — Multi-query mining ✓
In **category mode**, expand the category to its subcategory search intents
(`discovery.category_queries` → `SUBCATEGORIES`) and mine several per run, pooling results
before quality/dedup. **Keyword mode** (`q=`) still runs a single targeted query. Discovery
OFF ⇒ original single category-term scrape.

## DP2 — Pagination ✓
Mine up to `DISCOVERY_MAX_PAGES` pages per intent (`&page=N`). Stop a query's pagination as
soon as a page returns nothing.

## DP3 — Adaptive stopping ✓
Stop mining more intents once the pooled **unique quality** candidates ≥ `DISCOVERY_TARGET_POOL`.
Keeps runtime bounded — never blindly scrape every intent/page.

## DP4 — Adaptive rotation ✓
`pick_queries` orders candidate intents by learned **priority** (`fresh_total / usage_count`,
highest first), then least-recently-used, with **unseen intents explored first**. A query that
keeps returning duplicates sinks; a query that yields fresh winners rises. Yields are recorded
each run (`record_yields`).

## DP5 — Uniqueness is preserved (inherits [[dedup-guard]], G17) ✓
Pooling across intents/pages goes through the SAME within-run dedup (`_dedup_products`: ASIN +
base-image-id + normalized-title) and the SAME cross-run `seen_products` filter downstream.
More mining ⇒ more *candidates*, never repeated products.

## DP6 — No fabrication, bounded load (G13, G4) ✓
Only real scraped fields are kept; missing fields stay missing. Respect human-like delays
between page loads; never hammer Amazon in a tight loop.

## DP7 — Fail-open ✓
Any discovery/stats failure falls back to the static subcategory order or the single-query
scrape — a discovery problem must never abort an otherwise-valid run (G2).

## DP8 — Observability ⏳
Per-query yields are queryable at `GET /api/discovery/queries` (Intelligence panel). Future:
surface duplicate-rate and winner-yield per query for tuning.
