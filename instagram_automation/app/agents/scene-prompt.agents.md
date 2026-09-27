# Agent — Scene Prompt Builder (constraints for the local image model)

**Role:** Turn the Art Director's product analysis into a prompt that the LOCAL image model
(**Z-Image-Turbo**, 4-bit, on the laptop GPU) reliably paints as a beautiful, Instagram-worthy EMPTY
scene for the post. This file is the **live source of truth**: `art_director.py` reads the RULES block
and the PALETTE table below on every post, so editing this file changes the next post — no code change.

**Code anchors:** `instagram_automation/app/services/art_director.py` (`_prompt_rules`,
`_palette_rows`, `_assemble_prompt`), `scripts/gpu_worker.py` (fixed quality suffix + render settings).
Parent agent: [[post-art-director]] (affiliate-rag-bot/agents/post-art-director.agents.md).

---

## How a scene prompt is built
1. **Analyse** every product (type, real colours from the photo, material, style, vibe).
2. **Choose the palette/look** (the Studio "Look": AI-chosen or a fixed palette — table below).
3. The LLM fills a **structured scene** — never a free-form paragraph:
   `setting · wall · floor · light · props · colors · camera · mood`
4. The code **assembles** the fields in that fixed order (the order Z-Image responds to best:
   subject/setting first, then surfaces, then light, then palette, then camera/mood) and the worker
   appends the fixed quality/empty-scene suffix.

## Render settings (fixed, worker)
Z-Image-Turbo · 9 steps · guidance 0 (distilled — **no negative prompt**, so every constraint must be
phrased POSITIVELY: say "empty seamless wall", not "no clutter") · 1024×1280 (4:5, the IG portrait
slide) · seed from the prompt.

<!-- RULES:BEGIN -->
SCENE PROMPT CONSTRAINTS (for Z-Image-Turbo — follow every rule):
0. PLAIN STUDIO ONLY: the scene is a REAL, plain photo-studio backdrop — a seamless paper sweep or a smooth plaster/microcement wall curving into the floor, in ONE calm colour from the palette. Nothing invented: no rooms, windows, furniture, plants, shelves, rails, doors or architecture. Elegance comes from colour, light and a soft shadow — not from objects.
1. EMPTY SCENE ONLY: describe a backdrop, never a subject. No people, bodies, mannequins, hangers, clothes, products, packaging, text, signage, logos or screens.
2. COMPOSITION: straight-on, eye-level view of the backdrop meeting the floor in a soft seamless curve; the CENTRE and LOWER HALF are open, clean space where the product will stand.
3. SURFACES: ONE backdrop surface + a matching floor, with only subtle texture — e.g. seamless matte paper, smooth limewash plaster, microcement, honed stone floor. Name the colour of each.
4. LIGHT: always state the light — e.g. "soft large key light from the left", "warm spotlight glow behind centre", "even diffused studio light". Light makes the centre the brightest or most glowing area so the product pops; one soft natural floor shadow.
5. COLOUR: use exactly the palette's scene colours (table) — 2–3 named colours, often a gentle tonal gradient of one colour. The backdrop must CONTRAST with the products' main colours (dark products → warmer/lighter glow; light products → deeper tones). Never repeat the product's own colour as the backdrop.
6. PROPS: none by default. At most ONE simple low plinth or block, only if the post concept needs it.
7. STYLE WORDS that work: "clean studio product photography", "plain seamless backdrop", "premium catalogue", "medium format", "soft natural shadows", "high detail". Avoid vague words ("nice", "beautiful") and fantasy/CGI words.
8. LENGTH: 25–60 words across all fields. Each field one short phrase.
9. CAMERA: "eye-level, straight-on, 35mm, deep focus" (or 50mm for tighter sets). No tilt, no top-down (the product photos are front views).
10. MOOD: one phrase matching the post concept (e.g. "calm quiet luxury", "moody after-dark", "fresh bright morning").
<!-- RULES:END -->

## Palette / Look table (scene colours for each slide template palette)
The slide palette (panel + accent colours) and the scene must belong together. The Studio "Look" picks
one row (or lets the AI choose); the LLM must write the scene inside that row.

<!-- PALETTES:BEGIN -->
| key | look | scene colours | materials | light | mood |
|-----|------|---------------|-----------|-------|------|
| noir | Premium dark (Noir Gold) | charcoal, espresso brown, black with a hint of warm gold glow | charcoal seamless paper, dark honed stone floor | warm spotlight glow behind centre, soft rim light | moody quiet luxury |
| warm | Warm Sand | warm taupe, sand beige, soft oat | taupe seamless paper, smooth limewash plaster, pale stone floor | soft large key light from the left, gentle shadows | calm editorial warmth |
| clay | Terracotta | terracotta, peach, warm clay | terracotta seamless paper, clay plaster, warm matte floor | warm side light, long soft shadow | sunlit Mediterranean |
| mono | Mono Ink | light grey, off-white, graphite | light grey seamless paper, microcement floor | cool diffused studio light, crisp soft shadows | clean modern minimal |
| sky | Sky Blue | pale sky blue, white, soft grey | pale blue seamless paper, smooth plaster | airy bright even light from above-left | fresh and breezy |
| rose | Rose Blush | blush pink, dusty rose, cream | blush seamless paper, cream matte floor | soft diffused glow, gentle vignette | soft romantic |
| mint | Fresh Mint | sage green, mint, stone white | sage seamless paper, pale stone floor | soft fresh morning light | fresh natural calm |
| lilac | Lilac Pop | lilac, lavender, soft white | lilac seamless paper, white matte floor | soft pastel studio light | playful soft pastel |
<!-- PALETTES:END -->

## Style presets (slash commands)
Each `/preset` expands into proven prompt phrases that are appended to the assembled scene prompt.
The agent picks 1–2 that fit the analysed products; you can force them from the Studio
("Style commands", e.g. `/premium /cinematic`). Add a row to create a new command — no code change.
Phrases are POSITIVE (Z-Image has no negative prompt) and only change the LIGHT / TONE / SURFACE of the
plain studio backdrop — a preset never adds objects or invented settings.
`palettes` = the palette rows a preset is coherent with — forced presets restrict the palette to
these, and an agent-picked preset that clashes with the post's palette is dropped (no muddy mixes).

<!-- PRESETS:BEGIN -->
| preset | adds to the scene | best for | palettes |
|--------|-------------------|----------|----------|
| /premium | deep tonal backdrop, controlled warm key light, rich soft shadows, polished expensive finish | premium apparel, watches, fragrance, leather | noir, mono, warm |
| /vintage | warm faded film tones, soft grain, gentle vignette, 1970s catalogue warmth | denim, jackets, retro sneakers, leather bags | warm, clay, noir |
| /minimal | generous negative space, soft even light, one subtle shadow, calm restraint | basics, tees, tech, skincare | mono, warm, sky, mint, lilac, rose |
| /streetwear | cool grey backdrop, crisp hard key light, bold defined floor shadow | hoodies, sneakers, caps, oversized fits | mono, noir |
| /cinematic | single strong key light, dramatic falloff across the backdrop, subtle haze | statement pieces, gadgets, dark products | noir, mono, clay |
| /golden-hour | warm low side light raking across the backdrop, long soft shadow, honey glow | summer wear, linen, sunglasses | warm, clay, rose |
| /studio | professional photo studio, seamless paper sweep, softbox lighting, crisp controlled shadows | any product, catalogue clarity | mono, warm, sky, rose, mint, lilac, clay, noir |
| /cozy | warm soft light, gentle warm glow, soft tonal gradient on the backdrop | sweaters, loungewear, winter wear | warm, clay, noir |
| /coastal | airy bright light, pale sand-toned backdrop, breezy calm | resort wear, sandals, linen shirts | sky, warm, mono |
| /scandi | pale neutral backdrop, soft north light, simple calm | home, kitchen, minimal fashion | mono, warm, mint |
| /industrial | cool concrete-textured plain backdrop, cool diffused light | boots, jackets, tools, tech | mono, noir |
| /botanical | soft dappled leaf-shadow light falling across the plain backdrop | beauty, wellness, summer dresses | mint, warm, sky |
| /tech | sleek dark backdrop, cool rim light, subtle reflective floor sheen | earbuds, smartwatches, phones, gadgets | noir, mono, sky |
| /y2k | glossy pastel backdrop, soft pop lighting, gentle iridescent sheen | trend fashion, accessories, beauty | lilac, rose, sky, mint |
| /editorial | one simple low plinth, confident directional light, magazine finish | fashion hero pieces, curated edits | noir, warm, mono, rose, clay |
<!-- PRESETS:END -->

## Knobs
`ART_LOOK` (default look: `premium` = noir row) · `ART_ALLOW_NEW_SCENES` · `ART_SCENE_WAIT_SECS`.
Edit the RULES block or a PALETTE row above to tune what the image model paints.
