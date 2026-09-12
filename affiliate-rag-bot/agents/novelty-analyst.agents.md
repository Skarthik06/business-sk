# Agent — Novelty Analyst (semantic freshness)

**Role:** Measure how UNLIKE already-posted content each candidate product is, so the feed
does not keep posting the same *idea* under different ASINs (e.g. "cable clips" vs "cable
organizer" vs "cable management"). Phase 2 of the [Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md).

**Code anchors:** `rag/store.py` (`novelty_scores`), `graph/nodes.py` (`compose_pins` — novelty
computed here, before `store_results` embeds this run's pins), `chains/discovery.py`
(`product_intelligence` folds novelty in), `config.py` (`NoveltyConfig`). Legend: ✓ · ⏳.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `NOVELTY_ENABLED` | `1` | `0` disables novelty entirely (winner falls back to content_score) |
| `NOVELTY_WEIGHT` | `0.10` | novelty's share of the product-intelligence blend |

---

## NA1 — What it compares ✓
Embed `"{category} {title}"` and cosine-compare against the pgvector pin memory (posted pins,
`doc_type=pin`). `novelty = round((1 − top_relevance) × 100)`. Higher = less similar to
anything already posted.

## NA2 — Cold-start & fail-open (G7, G2) ✓
Empty store, or any embedding/query error ⇒ **novelty = 100** (treat as fully novel). Emptiness
is normal on early runs, never an error.

## NA3 — Timing (avoid self-comparison) ✓
Novelty is computed in `compose_pins`, BEFORE `store_results` embeds this run's own pins.
Computing it after storage would match each product against itself and report false zero novelty.

## NA4 — Internal only, never a claim (G13) ✓
Novelty is a ranking feature. It is NEVER shown to a buyer as a claim ("100% unique!") — only
surfaced internally / in the studio's "Why this product?" panel.

## NA5 — How it's used ✓
`product_intelligence = content_score × (1 − NOVELTY_WEIGHT) + novelty × NOVELTY_WEIGHT`, which
feeds the [[winner-engine]]. Raise `NOVELTY_WEIGHT` to push the feed harder toward fresh ideas.

## NA6 — Future ⏳
Add title-embedding + image perceptual-hash signals; per-account novelty when multiple IG
accounts exist (a product novel for account A may be stale for account B).
