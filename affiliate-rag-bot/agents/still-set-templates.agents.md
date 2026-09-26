# Agent — Still Set Templates (Business-SK designed slides)

**Role:** Enforce the "Still Set" creative system when rendering affiliate carousels, so every
post looks hand-curated, premium and unmistakably ours — never a generic AI affiliate grid.
This agent governs the **look** of the slides; [[carousel-publisher]] governs posting them.

**Playbook (visual source of truth):** the "Still Set" creative-system artifact
(`creative-system.html` → https://claude.ai/code/artifact/698c2bcc-12f7-4141-aad4-27c0db47ff18).
**Code anchors:** `instagram_automation/app/services/sk_render.py` (renderer + `plan_slides`,
`_prep_image`, `_content_bbox`), `app/api.py` (`/api/sk/carousel` design=true, `/api/sk/render-preview`).
Legend: ✓ enforced · ⏳ pending.

---

## ST1 — The product is the hero: BIG, clear, cleanly cut ✓ enforced
A product must dominate its stage — never sit small inside its own margins. `_prep_image`:
trims the product out of ANY uniform background (`_content_bbox`: alpha bbox if isolated, else
a corner-background colour-distance trim that works on white / off-white / grey / solid colour),
then **scales it to fill the stage** (up or down, aspect preserved, ~2% padding), sharpening any
upscaled source. Stages fill 94–97% (`.stage img`, `.stage.big`). Feature/hero/value/deal slides
give the product 700–850px of the 1350px canvas. If the source is a busy lifestyle shot (corners
disagree) the frame is kept whole rather than risk cropping the product. **Rule: a viewer must be
able to see the product clearly at a thumbnail glance.**

## ST2 — Product true to source; only the environment is designed ✓ enforced (G3)
The renderer NEVER recolours, reshapes, relabels, or "beautifies" the product — it only builds the
ENVIRONMENT around it (tinted stage, one soft shadow, type, frame). No generative fill that TOUCHES
the product (it bleeds onto products and breaks trust). **Update 2026-09-26:** AI backdrops are allowed
only as a separately generated EMPTY scene with the real cut-out composited on top — see
[[post-art-director]] (AD1). Product slides now use that AI-scene format; the classic templates below
remain only for coupon/deal posts and as the no-library fallback.

**Background removal = rembg (AI segmentation), PRIMARY.** rembg (u2net, MIT) is installed and is the
default isolation path (`_prep_image(isolate=True)`, which `render_carousel`/`sk_carousel` use). It
segments the product/subject semantically, so white product parts (white shorts, shoes, a white sole)
are preserved even against a white background — the failure the old colour-based knockout had. The
zero-dependency corner white-knockout (`_knockout_white_bg`) remains ONLY as a fallback when rembg is
unavailable, and it self-limits (skips busy/coloured backgrounds) to avoid eroding the product.
**Permanently baked into the image**: `Dockerfile.backend` installs `rembg==2.0.59` +
`onnxruntime==1.19.2` in a dedicated LATE layer (keeps the heavy torch/docling layers cache-valid)
and pre-fetches the u2net model at BUILD time (`new_session('u2net')` + `test -f
/root/.u2net/u2net.onnx`, which fails the build if the model is missing). So background removal works
offline/instantly and survives any container recreate — no runtime install, no first-run download.
Optional future upgrade: `birefnet-general` for even finer edges.

## ST3 — The index-frame signature on every slide ✓ enforced
Every slide carries the ownable frame: hairline gallery border + ember corner ticks and a mono
**placard kick** (top-left, e.g. FASHION / BEST VALUE / PRICE DROP). The top-right **edition code**
and the footer **slide counter** are intentionally REMOVED (`.code{display:none}`, no page number)
— the brand chose a cleaner, uncluttered frame. This constant frame is the recognizer; tint varies,
frame does not.

## ST12 — Truthful trust-fact overlay ✓ enforced
Feature/value/deal slides overlay the REAL retail facts that build trust, each drawn only when its
data exists (`_info_block`): a **rating chip** (★ rating · N ratings, from `rating`+`reviews`), a
**demand chip** (`bought_past_month` → "N bought recently"), the price lockup (₹ · struck MRP · %
off), and the **real savings** ("You save ₹X" = MRP − price). Never fabricated, never zero-filled.
The `badge` field is NOT rendered while the scrape truncates it (e.g. "Amazon's") — a partial/at-risk
fact is dropped rather than shown misleadingly (G3). More real fields (verified badge, key specs)
can join the overlay once the scrape captures them cleanly.

## ST4 — Category tint system: one frame, many moods ✓ enforced
The stage gradient + accent inherit a per-category tint (`_TINTS`): fashion `#B04A32`, tech
`#3E5568`, home `#5C6A4B`, beauty `#A0566A`, deals `#9A6A2E`, default fashion. Type, layout and
frame stay identical, so the feed reads as one author while each collection feels made for it.

## ST5 — Type system (fixed) ✓ enforced
Display **Instrument Serif** (product/collection names, cover thesis), UI **Hanken Grotesk**
(prices with tabular figures), utility **Space Mono** (placard kick, edition code, %/OFF, page no.).
All OFL via Google Fonts; Indic (Kannada/Devanagari) via the container's Noto fonts so the language
module renders per-glyph. Never substitute Inter/Space Grotesk/other "safe AI" faces.

## ST6 — Layout by product count; never shrink to fit ✓ enforced
`plan_slides` picks the layout FAMILY by count — the structure changes, products never shrink:
`1` → hero, or deal (≥50% off) / value (has rating) · `2` → duo (face-off) · `3` → grid-3 ·
`4` → grid-4 · `5–7+` → **carousel**: cover → per-product feature slides (value if rating, else
hero) → closer (ranking arc inserts a ranked overview). Instagram's 10-slide cap is honoured.

## ST7 — No fabrication in the design ✓ enforced (G3, inherits [[product-scout]] S4)
MRP, discount % and rating render ONLY when actually present in the product data — a missing field
is simply not drawn, never invented or zero-filled. Discount derives truthfully from price vs MRP.
Prices use ₹ with Indian digit grouping + tabular numerals. Bundle/"all under ₹X" claims use real
summed/max prices ([[collection-builder]] CB2).

## ST8 — Quiet commerce, editorial restraint ✓ enforced
Price = a calm lockup (serif name, tabular price, struck MRP, one mono %-mark). Discount gets size
+ colour ONLY on the deal template. CTA is an outlined pill; the solid-ink fill appears once, on the
closer. No starbursts, no emoji on-slide, no ALL-CAPS shouting, generous negative space. Restraint
is the premium signal.

## ST9 — Preview before posting ✓ enforced
`/api/sk/render-preview` renders the exact slides (served from `/cdn`) WITHOUT posting, so the
Post-to-IG panel can show what will actually publish. `/api/sk/carousel` design=true renders, pushes
the PNGs to GitHub raw (IG-fetchable, CDN-verified) and posts those; it falls back to raw product
images only if rendering is unavailable — a post never fails over design.

## ST10 — Captions/hashtags: one call, the curator's voice ✓ enforced
Slide TEXT is deterministic (product data). The CAPTION + hashtags are ONE structured LLM call on
the user's own model (JSON, token-lean) following the playbook's 10 caption angles, brand voice
(prefer/avoid lists) and per-post hashtag engine (12–18, rotated, relevant). No local LLM; no
per-product captions.

## ST11 — Enhancement roadmap (all free, self-hosted)
✓ **rembg installed** as the primary background-remover (ST2) — perfect cutouts on model/white-bg
shots. ⏳ Remaining: **Real-ESRGAN** (BSD) to upscale low-res sources before staging; wire the
Post-to-IG preview to `/api/sk/render-preview` so users see the designed slides before publishing;
optional `birefnet-general` model swap for the finest edges.

## ST13 — Fact hygiene (credible, grouped) ✓ enforced
Counts are formatted for trust: review counts grouped (39800 → "39,800"), demand suffixes preserved
("1K+", "5K+"). A demand chip renders ONLY when credible — a bare "1"/"2" (no +/K/M and < 50) is
suppressed, because a weak number reads worse than none. All still truthful, drawn only when present.
