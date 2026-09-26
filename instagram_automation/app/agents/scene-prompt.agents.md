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
1. EMPTY SCENE ONLY: describe a place, never a subject. No people, bodies, mannequins, hangers, clothes, products, packaging, text, signage, logos or screens.
2. COMPOSITION: straight-on, eye-level view of a wall meeting a floor/surface; the CENTRE and LOWER HALF are open, uncluttered space where the product will stand. Any prop sits only at the far left/right edge and is small.
3. SURFACES: name real materials with texture — e.g. limewash plaster, microcement, travertine, black marble with fine veins, raw oak, walnut, linen drape, seamless paper, terrazzo. One wall material + one floor material.
4. LIGHT: always state the light source, direction and quality — e.g. "soft window light from the left", "warm spotlight glow behind centre", "golden-hour side light with long soft shadows". Light must make the centre the brightest or most glowing area so the product pops.
5. COLOUR: use exactly the palette's scene colours (table) — 2–3 named colours. The scene must CONTRAST with the products' main colours (dark products → warm glow / lighter wall; light products → deeper tones). Never repeat the product's own colour as the main wall colour.
6. PROPS (optional, max 1–2): quiet, niche-appropriate, at the edge — fashion: a lounge chair, a plinth, a linen drape; tech: a stone block, a shelf edge; beauty: a travertine tray, dried stems; home: a ceramic vase.
7. STYLE WORDS that work: "editorial fashion photography", "minimal luxury studio", "medium format", "soft natural shadows", "high detail". Avoid vague words ("nice", "beautiful") and avoid fantasy/CGI words.
8. LENGTH: 30–70 words across all fields. Each field one short phrase.
9. CAMERA: "eye-level, straight-on, 35mm, deep focus" (or 50mm for tighter sets). No tilt, no top-down (the product photos are front views).
10. MOOD: one phrase matching the post concept (e.g. "calm quiet luxury", "moody after-dark streetwear", "fresh summer morning").
<!-- RULES:END -->

## Palette / Look table (scene colours for each slide template palette)
The slide palette (panel + accent colours) and the scene must belong together. The Studio "Look" picks
one row (or lets the AI choose); the LLM must write the scene inside that row.

<!-- PALETTES:BEGIN -->
| key | look | scene colours | materials | light | mood |
|-----|------|---------------|-----------|-------|------|
| noir | Premium dark (Noir Gold) | charcoal, espresso brown, black with a hint of brass/gold | black marble with fine veins, espresso limewash plaster, dark walnut | warm spotlight glow behind centre, soft rim light | moody quiet luxury |
| warm | Warm Sand | warm taupe, sand beige, soft oat | limewash plaster, pale oak floor, travertine | soft window light from the left, gentle shadows | calm editorial warmth |
| clay | Terracotta | terracotta, peach, warm clay | clay plaster, terracotta tiles, seamless peach paper | golden-hour side light, long soft shadows | sunlit Mediterranean |
| mono | Mono Ink | light grey, off-white, graphite | microcement, smooth concrete, white seamless | cool diffused daylight, crisp soft shadows | clean modern minimal |
| sky | Sky Blue | pale sky blue, white, soft grey | blue-tinted plaster, white oak, linen | airy morning daylight from above-left | fresh and breezy |
| rose | Rose Blush | blush pink, dusty rose, cream | rose plaster, cream terrazzo, silk drape | soft diffused glow, gentle vignette | soft romantic |
| mint | Fresh Mint | sage green, mint, stone white | sage limewash, pale stone floor, leafy shadow | morning sun with soft leaf shadows | fresh natural calm |
| lilac | Lilac Pop | lilac, lavender, soft white | lilac plaster, white terrazzo, frosted glass block | soft pastel studio light | playful soft pastel |
<!-- PALETTES:END -->

## Style presets (slash commands)
Each `/preset` expands into proven prompt phrases that are appended to the assembled scene prompt.
The agent picks 1–2 that fit the analysed products; you can force them from the Studio
("Style commands", e.g. `/premium /cinematic`). Add a row to create a new command — no code change.
Phrases are POSITIVE (Z-Image has no negative prompt) and describe only the empty scene.

<!-- PRESETS:BEGIN -->
| preset | adds to the scene | best for |
|--------|-------------------|----------|
| /premium | quiet luxury, polished stone and brushed brass details, controlled warm spotlight, rich deep shadows, expensive editorial finish | premium apparel, watches, fragrance, leather |
| /vintage | film photography look, warm faded tones, aged plaster and worn wood, soft grain, 1970s editorial warmth | denim, jackets, retro sneakers, leather bags |
| /minimal | clean seamless backdrop, generous negative space, soft even light, one subtle shadow, calm restraint | basics, tees, tech, skincare |
| /streetwear | urban concrete and raw brick, cool daylight with hard shadows, gritty texture, street editorial energy | hoodies, sneakers, caps, oversized fits |
| /cinematic | cinematic lighting with a single strong key light, dramatic falloff, subtle haze, anamorphic depth | statement pieces, gadgets, dark products |
| /golden-hour | low warm sun raking across the wall, long soft shadows, honey-gold glow | summer wear, linen, sunglasses, outdoor |
| /studio | professional photo studio, seamless paper sweep, softbox lighting, crisp controlled shadows | any product, catalogue clarity |
| /cozy | soft knit throw and warm wood, lamp-lit ambience, gentle warm glow, homely calm | sweaters, loungewear, winter wear, home |
| /coastal | whitewashed plaster, pale sand and driftwood tones, airy sea-light, breezy calm | resort wear, sandals, linen shirts |
| /scandi | pale birch wood, white walls, soft north light, simple functional calm | home, kitchen, minimal fashion |
| /industrial | raw concrete, black steel beams, exposed brick, cool diffused skylight | boots, jackets, tools, tech |
| /botanical | lush green leaves at the edge, dappled natural light through foliage, fresh organic calm | beauty, wellness, summer dresses |
| /tech | sleek dark surfaces, cool rim light, subtle reflective sheen, precise modern minimalism | earbuds, smartwatches, phones, gadgets |
| /y2k | glossy pastel surfaces, iridescent sheen, playful soft pop lighting | trend fashion, accessories, beauty |
| /editorial | high-fashion magazine set, sculptural plinth, confident directional light, art-directed composition | fashion hero pieces, curated edits |
<!-- PRESETS:END -->

## Knobs
`ART_LOOK` (default look: `premium` = noir row) · `ART_ALLOW_NEW_SCENES` · `ART_SCENE_WAIT_SECS`.
Edit the RULES block or a PALETTE row above to tune what the image model paints.
