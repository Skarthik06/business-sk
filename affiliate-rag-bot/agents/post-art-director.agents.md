# Agent — Post Art Director (AI-scene post design)

**Role:** Design every Instagram product post. The agent looks at the scraped products — their
PHOTOS, their JSON facts and the measured cut-out facts — and decides the post's concept, the scene
(backdrop), every slide's layout, the palette and the headline chip. Since 2026-09-26 this AI-scene
format is **the** format for product posts; the nine classic Still-Set templates are retired for
product slides (coupon/deal posts keep their deal cards — they have no product photos).

**Code anchors:** `instagram_automation/app/services/art_director.py` (the agent),
`app/services/scene_store.py` (backdrop library + cut-out cache + GPU job queue),
`app/services/sk_render.py` (`_scene_hero/_float/_split/_flatlay/_closer`, `_apply_art`, `_scene_ctx`),
`app/api.py` (`/api/sk/art-direct`, `/api/sk/scenes`, `/api/gpu/worker/{jobs,result}`, `_ensure_art`),
`scripts/gpu_worker.py` (laptop GPU worker). Studio: Content Studio → Post tab ("✨ AI Art Director").
Legend: ✓ enforced · ⏳ pending.

---

## AD1 — The product is NEVER generated or altered ✓ enforced (G3, ST2)
The image model only paints an **empty scene** (no people, no products, no text). The product is the
retailer's real photo, cut out by BiRefNet: RGB pixels are untouched, only an alpha mask is computed.
CSS only scales it and adds a shadow. Verified in testing: 0.000% of opaque product pixels changed.
Image-editing models that redraw the product (Qwen-Image-Edit, FLUX Kontext, virtual try-on) are
not used for products.

## AD2 — The LLM directs; it sees the photos ✓ enforced
`OPENAI_MODEL` (gpt-5-nano, vision) receives up to `ART_MAX_IMAGES` product photos + the scraped
facts (title, brand, store, price, MRP, %, rating, reviews) + the measured photo facts
(`subject` person|object, `cropped_at_bottom`, aspect, main colours) + the ready backdrop library.
It returns strict JSON: concept, scene.use (library key), optional scene.new (a new EMPTY-scene
prompt), palette, chip, and one layout per slide.

## AD3 — Every answer is validated; a plan ALWAYS exists ✓ enforced
Unknown scene keys, layouts or palettes are dropped and filled by the deterministic director
(`_fallback_plan`): least-used niche-matching scene, `scene_hero` for measured person photos, else
`scene_float`, a `scene_split` every third slide. No LLM key / LLM error → the rules plan. If the
Studio sends no plan, the server art-directs itself (`_ensure_art`).

## AD4 — Layouts (the only product-slide layouts) ✓ enforced
- **scene_hero** — a model wearing the item, photo cropped at the waist/legs: the person stands in the
  scene; the info panel overlaps the cropped edge.
- **scene_float** — a whole object (shoes, bag, watch, bottle, a flat garment): centred, fully visible,
  soft shadow.
- **scene_split** — product left, editorial details card right (max 1–2 per post, for variety).
- **cover = collage (display only)** — up to 6 real cut-outs in frosted tiles of MIXED aspect ratios
  (tall / wide / square, asymmetric mood-board). **No names and no prices on the cover** (user rule,
  2026-09-26): the hook is the look; details live on the product slides. Tall tiles ← model shots /
  tall items, wide tiles ← wide items (measured aspect). Each tile carries its product's number (01…N),
  the same number opens that product's slide eyebrow, and a bold "Swipe → N picks inside" bar closes
  the cover. Headline = AI cover title with a price/% guard; subtitle = the post concept.
- **closer = scene_closer** — follow → comment LINK → link in bio, on the same scene.
Measured facts refine the LLM: **model shot = a face is detected** (YuNet, OpenCV model zoo, on the
laptop; silhouette rule as fallback). A Hero needs a photo cropped at the bottom; a cropped model is
always a Hero (never floats cut off mid-air). A manual per-slide pick in the Studio always wins.

## AD5 — One scene per post; the library grows ✓ enforced
One backdrop for the whole carousel (cohesive feed). The agent picks from the library; when nothing
suits, it may propose ONE new empty-scene prompt (`ART_ALLOW_NEW_SCENES`) — queued to the laptop,
added to the library for future posts. Scenes rotate by `uses` (least-used first) to avoid a samey
grid. Seed library (2026-09-26): warm_taupe_studio, peach_seamless, brown_spotlight, noir_marble,
mono_concrete, sage_plaster.

## AD6 — Truthful info panel ✓ enforced (G3)
Name, price, struck MRP (only when > price), % OFF (only when > 0), ★ rating + count (only when > 0),
store label, real brand (only when the scrape gave one), "Follow + comment LINK → DM". Nothing is
zero-filled or invented. Fashion posts say "for this look", other niches "for this find".

## AD7 — Never blocks a post ✓ enforced
The laptop GPU is optional: backdrops live on the server; missing laptop cut-outs are replaced by the
server's own background removal (rembg). Waits are bounded (`ART_META_WAIT_SECS`, `ART_WAIT_SECS`).
The job queue is file-backed (survives restarts). Failed jobs retry ≤ 3 times, then drop.

## AD8 — GPU worker safety ✓ enforced
Worker endpoints bypass the admin gate but require `GPU_WORKER_TOKEN` (constant-time compare). Every
upload is decoded + verified as a real image (size-bounded) and saved under a hash/slug name — the
worker cannot write arbitrary files. Scene assets are git-ignored (`images/sk_scenes/`).

## AD9 — Looks + prompt constraints ✓ enforced
The Studio **Look** (knob `ART_LOOK`, default `premium`) sets the post's palette and the scene's colour
direction: `premium` (= Noir Gold: dark panels + gold accents, dark luxurious scene), any slide palette
(`warm`, `clay`, `mono`, `sky`, `rose`, `mint`, `lilac`) or `ai` (the agent picks the row). Every post
gets its OWN scene: the agent analyses the products, then fills STRUCTURED scene fields under the rules
of [[scene-prompt]] (`instagram_automation/app/agents/scene-prompt.agents.md`, read live); the code
assembles them in a fixed order into the Z-Image prompt. Library scenes are only the fallback while the
laptop paints (the render waits ≤ `ART_SCENE_WAIT_SECS`, a busy worker counts as online).

## AD10 — Token economy + cost transparency ✓ enforced
Static instructions first, per-post data last (OpenAI prompt cache bills the ~1.3k fixed tokens at
10%); compact JSON; library trimmed to key/mood/palette; photos sent as small cut-out thumbnails
(`ART_IMG_PX` 320) instead of full images; minimal reasoning. Measured per 4-product post: Art
Director ~2.9k in (1.3k cached) + ~0.7k out ≈ $0.0004; with the caption writer ≈ 5.7k tokens,
**$0.00055 (₹0.05) per post** (was ~8k+ input for the Art Director alone). Every preview returns the
priced breakdown (input / cached / output / reasoning per step) and the Studio post card shows it.
Knobs: `LLM_PRICE_IN_PER_M` 0.05 · `LLM_PRICE_CACHED_PER_M` 0.005 · `LLM_PRICE_OUT_PER_M` 0.40 ·
`USD_INR` 88 · `ART_REASONING_EFFORT` minimal · `ART_IMG_PX` 320. Default look is `ai` (the agent decides
per post; a palette used by the last 2 posts is excluded for variety; `recent_looks.json`).

---

## Tunable knobs (env, IG backend)
| Knob | Default | Effect |
|------|---------|--------|
| `ART_DIRECTOR_ENABLED` | `1` | `0` → deterministic director only (no LLM call) |
| `ART_MAX_IMAGES` | `4` | product photos sent to the vision LLM |
| `ART_ALLOW_NEW_SCENES` | `1` | let the agent commission new backdrops |
| `ART_MAX_TOKENS` / `ART_REASONING_EFFORT` | `3000` / `low` | LLM budget |
| `ART_META_WAIT_SECS` | `15` | wait for laptop cut-out facts before the LLM plans |
| `ART_WAIT_SECS` | `20` | wait for cut-outs/backdrop before rendering |
| `GPU_WORKER_TOKEN` | — | laptop worker auth |
| `GPU_IDLE_UNLOAD_SECS` (worker) | `600` | free Z-Image VRAM after idle |

## Models (laptop, `%USERPROFILE%\sk-ai\venv`, torch cu128)
BiRefNet (MIT) — cut-outs ~0.5 s · Z-Image-Turbo 4-bit (unsloth bnb, Apache-2.0) — backdrops ~60–75 s
at 1024×1280, 9 steps · (planned) Real-ESRGAN 2× for small photos · Wan 2.2 TI2V-5B for Reels.
