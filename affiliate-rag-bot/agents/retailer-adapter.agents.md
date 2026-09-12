# Agent — Retailer Adapter (multi-retailer abstraction)

**Role:** Abstract product search + normalization + affiliate-link building behind one
interface so new retailers can be added without touching the pipeline. Phase 8 of the
[Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md).

**Code anchors:** `tools/retailers/base.py` (`RetailerAdapter`), `tools/retailers/amazon.py`
(`AmazonAdapter`), `tools/retailers/__init__.py` (registry + `health`), `tools/affiliate.py`
(`AffiliateLinkService`), `server.py` (`/api/retailers`), `config.RetailerConfig`.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `RETAILERS_ENABLED` | `amazon` | CSV of enabled retailers |

---

## RA1 — One interface ✓
`RetailerAdapter`: `search`, `normalize_product`, `build_affiliate_link`, `health_check`. The
common product contract (`retailer`, `marketplace`, real ids only) flows through unchanged.

## RA2 — Amazon is live; others gated ✓
`AmazonAdapter` wraps the proven scraper + deep-link builder. Planned retailers
(flipkart/myntra/meesho/…) are advertised honestly as `implemented:false` by `/api/retailers`
and are NOT enabled until their data + affiliate method exist (blueprint §47). No fabrication.

## RA3 — Affiliate Link Service ✓
`AffiliateLinkService.create_link(retailer, product)` routes to the adapter, builds from the
real product id, VALIDATES the URL, records the source retailer, and fails closed rather than
returning an untracked/fabricated link (G13, blueprint §49).

## RA4 — Non-disruptive ✓
The live content pipeline still calls `tools/amazon` directly; the adapter layer is additive,
ready for multi-retailer merging (dedup across retailers by canonical key) in a later step (⏳).
