# Agent — Content Intelligence (styles + hashtag bank + validation)

**Role:** Make the caption an experiment, not a one-off: pick/record a **content style** (for
A/B), guarantee a coherent **hashtag** spine, and **fact-check** every numeric claim against the
real product rows. Phase 4 of the [Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md). Extends
[[content-strategist]]. Still ONE structured LLM call (G10).

**Code anchors:** `chains/compose.py` (`PinBatch.style`, `_style_directive`, post-process),
`chains/hashtags.py` (`BANK`, `merge_hashtags`), `chains/validate.py` (`audit_caption`),
`rag/posts.py` (`content_style` column), `server.py` (`/api/generate?content=`, `/api/posts`),
`config.ContentConfig`.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `CONTENT_DEFAULT_STYLE` | `auto` | default caption style (auto = model chooses) |
| `CONTENT_VALIDATE` | `1` | run the numeric fact-check |
| `CONTENT_TAG_BANK` | `1` | blend the curated hashtag bank |

Styles: `DEAL_DROP, STORY, LISTICLE, PROBLEM_SOLUTION, QUESTION, TRANSFORMATION, GIFT_GUIDE,
BUDGET, PREMIUM, VIRAL_FIND` (+ `auto`).

---

## CI1 — Content styles + A/B ✓
The composer writes in a requested style (or auto-picks) and RETURNS the style it used
(`content_style`). It's recorded on the post (`rag/posts.py`), so performance can later be
compared per style ([[learning-agent]]). Request via `/api/generate?content=PROBLEM_SOLUTION`.

## CI2 — One LLM call preserved (G10) ✓
Style is an extra field on the SAME `PinBatch` structured output — no extra call. Direction/trend
context (Phase 3) rides the same call.

## CI3 — Hashtag bank ✓
`merge_hashtags` blends the LLM's product-aware tags (first) with a curated per-category spine
(broad + category + audience + branded reach), lowercased, deduped, capped at 25, `#ad` forced.

## CI4 — Caption validation (fact-check) ✓
`audit_caption` extracts every ₹price and "% off" from the caption and verifies it against the
real product rows (±2% tolerance for rounding). Unverified numbers are reported as
`content_warnings` (non-destructive — flags, never fabricates or silently edits). Inherits G13.

## CI5 — Disclosure (G3) ✓
`#ad` is always present; the verbose "As an Amazon Associate…" sentence stays out of the caption
(storefront carries it).

## CI6 — Future ⏳
Optional micro-captions per product; experiment engine (one variable at a time) once performance
data (Phase 6) exists.
