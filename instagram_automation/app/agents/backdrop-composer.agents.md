# Backdrop Composer agent

Gives **every post its own background** — composed by our own code (no LLM, no tokens) and painted
by our own image model (Z-Image on the laptop GPU or Colab, free).

Code: `app/services/backdrop_composer.py` · used by `ai_stylist.pick_surface` (flat-lay surfaces) and
`art_director._direct` (studio scenes) · verification hook in `scene_store.submit` ·
history `sk_scenes/backdrop_history.json`.

## Why
Before: the AI Stylist reused 4 fixed surfaces forever (every dark post = the same stone + monstera),
the LLM wrote near-identical "plain studio" scene prompts, and the paint seed was `len(prompt)` —
similar prompts painted near-identical images.

## How it composes
| Mode | Ingredients (one of each) |
|---|---|
| `flatlay` (AI Stylist, top-down) | surface (12 dark / 14 light) · prop (13) · corner (4) · light (6) |
| `studio` (AI-scene posts, eye-level) | colour from the post's palette row · wall finish (10) · floor (7) · set piece (10) · light (7) |

* An option used in the last few posts is avoided (surface: 6, prop/set piece: 4, light: 2 …);
  when all were used lately, the one used longest ago wins.
* An exact combination never repeats within **120 posts**.
* Every composition gets its **own random seed**.
* A post keeps its composition (re-renders, previews and posting never repaint); a re-roll
  (fresh plan) gets a new one.
* Studio scenes keep the house rules: plain, elegant studio sets (no invented rooms), the palette
  row's colours, the style-preset phrases, empty centre for the products.

## How it verifies
Every finished backdrop is fingerprinted (64-bit perceptual hash + average colour) and compared
with the last 60. Closer than the threshold → it is **repainted once** with a new composition and
seed under the same key. `backdrop_composer.report()` lists recent compositions, whether each was
painted, and how far it is from its nearest neighbour.

## Knobs
`ART_SCENE_SOURCE` = `composer` (default) | `llm` (the old LLM-written scene prompt).
