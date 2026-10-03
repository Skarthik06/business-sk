# AI Stylist agent

Turns a scraped product photo — often a model wearing the item — into a **styled top-down flat-lay
product photo** (premium fashion-Instagram look), keeps the template's product details in one compact
band, and spends as little as possible.

Code: `app/services/ai_stylist.py` · endpoints `/api/sk/stylist/{budget,estimate,style}` · renderer
`sk_render._styled_slide` · Studio button **✨ Style with AI** on each post card (Content Studio).

## Pipeline (per post, only on click)

1. **Surface — free.** One of four flat-lay surfaces (charcoal stone + monstera, light oak, beige
   linen, white marble), painted once by our own Z-Image model (laptop GPU / Colab) and reused by
   every post. Dark looks (noir, mono) → stone; others → a light surface (stable per post).
2. **Look — cheap, cached forever.** `SK_STYLIST_VISION_MODEL` reads the product photo once:
   item (3–6 words), worn by a person?, logo/print/text note. Logo found → `medium`, else `low`.
3. **Style — paid.** `SK_STYLIST_MODEL` gets the product photo + the surface (both downscaled to
   `SK_STYLIST_INPUT_PX`) and a short prompt: only the item, no person, laid flat, exact colours,
   "keep exactly: <logo note>". Output 1024×1536, cropped to the 4:5 slide.
4. **Fidelity — cheap.** The vision model compares original vs styled (both 512 px, low detail);
   a changed product is rejected and that slide keeps the free design.
5. **Slide.** Styled image full-bleed + one compact details band (store · brand · number, name,
   rating, price / MRP / % off) just above the footer.

## Money rules (non-negotiable)

| # | Rule |
|---|---|
| M1 | Nothing runs automatically — only the button; the estimate + today's spend are shown and confirmed first. |
| M2 | Every paid image is saved per (surface, product) and reused — previews / re-renders / posting never pay twice. |
| M3 | The product look (step 2) is cached per photo — never asked twice. |
| M4 | `SK_STYLIST_DAILY_CAP_USD` is checked before EVERY paid image; reaching it stops the post. |
| M5 | `SK_STYLIST_CREDIT_USD` − spend since `SK_STYLIST_CREDIT_SINCE` must stay above `SK_STYLIST_RESERVE_USD`. |
| M6 | Every cent is written to `stylist_ledger.json` from the API's own usage numbers (OpenAI has no balance API). |
| M7 | Inputs are downscaled and prompts short — input tokens were ~70 % of the measured cost. |
| M8 | A refused/failed call is never retried automatically. |

## Measured (2026-10-03, gpt-image-1-mini, 1024×1536)

| | tokens (text / image in / out) | cost |
|---|---|---|
| low, full-size inputs | 139 / 3040 / 408 | $0.0111 |
| medium + logo note | 122 / 3040 / 1584 | $0.0205 — logo kept |
| product look (gpt-5-nano) | — | $0.0007 |
