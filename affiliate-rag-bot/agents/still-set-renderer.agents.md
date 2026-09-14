# Agent: still-set-renderer

**Role:** Designs the Business-SK carousel slides ("The Still Set") and controls the
**product cutout** quality — the step that lifts the product off its original photo and
stages it on the branded tint. Runs in the IG backend (`app/services/sk_render.py`); its
knobs are edited from the studio **Agents panel** and read live via `GET /api/render-config`.

## Why it exists
Amazon catalog photos come on white or busy backgrounds. To place a product on our tinted
stage we remove its background. A hard binary mask leaves jagged / "brushed-out" edges;
**alpha matting** feathers the boundary for clean, natural edges, and a zero erode keeps the
full product outline instead of shaving pixels off it.

## How it works
1. Fetch the product image.
2. **Cutout** — `rembg` (model `RENDER_ISOLATE_MODEL`, default `u2net`) with **alpha matting**
   (`RENDER_ALPHA_MATTING`) using foreground/background thresholds and `RENDER_ALPHA_ERODE`.
   If `rembg` is unavailable, a zero-dependency white-background flood-fill fallback runs at
   `RENDER_KNOCKOUT_THRESH` strength (lower = safer, won't eat light product edges).
3. Trim the product's bounding box, scale to fill the stage, light sharpen — **product pixels
   are never recoloured or reshaped**, only the environment (stage, shadow, type) is built.

## Editable constraints (Agents panel → still-set-renderer)
| Key | Default | Meaning |
|---|---|---|
| `RENDER_ISOLATE` | 1 | Cut out the product (0 = keep the original photo background) |
| `RENDER_ALPHA_MATTING` | 1 | Feather edges for a clean cutout (0 = hard mask, faster) |
| `RENDER_ALPHA_ERODE` | 0 | Shave N px off the edge (0 = keep the full product edge) |
| `RENDER_KNOCKOUT_THRESH` | 30 | White-bg fallback cutout strength (lower = safer) |
| `RENDER_ISOLATE_MODEL` | u2net | Cutout model (`u2net` / `isnet-general-use`) |

Truthful-design guarantee holds: fields (MRP, discount, rating, badge) render only when the
real data exists. Related: [[retailer-adapter]] · [[content-intelligence]].
