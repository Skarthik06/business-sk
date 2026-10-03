# AI Stylist agent

Turns a scraped product photo — often a model wearing the item — into a **styled top-down flat-lay
product photo** (premium fashion-Instagram look), keeps the template's product details in one compact
band, and spends as little as possible. **One paid call per product (the image); every decision and
check around it is our own deterministic code — no AI tokens.**

Code: `app/services/ai_stylist.py` (pipeline + money) · `app/services/stylist_cv.py` (image analysis)
· endpoints `/api/sk/stylist/{budget,estimate,style}` · renderer `sk_render._styled_slide` /
`_styled_cover` · Studio button **✨ Style with AI** on each post card (Content Studio).

## Pipeline (per post, only on click)

1. **Surface — free.** One of four flat-lay surfaces (charcoal stone + monstera, light oak, beige
   linen, white marble), painted once by our own Z-Image model (laptop GPU / Colab) and reused by
   every post. Dark looks (noir, mono) → stone; others → a light surface (stable per post).
2. **Read the product — free, deterministic, cached per photo** (`stylist_cv.analyse`):
   - garment colour = biggest k-means colour of the garment's middle (skin excluded) → a colour name;
   - item = that colour + the garment type matched in the listing title ("dark green zip hoodie");
   - **emblem detector**: local contrast vs a large median blur → blobs, kept only if
     compact (not zip / cord / seam / stripe / stitching), clearly different in hue or lightness from
     the fabric, sitting on the garment's own smooth fabric (not skin, hair, a watch, a hand, the tee
     under a jacket), with a crisp outline (fold shadows and arm gaps fade softly), and not a
     vertical row of look-alikes (buttons);
   - a print/graphic named in the title ("Printed", "Graphic", "Marvel" …) also counts.
3. **Quality.** Emblem or symbol → `medium` (the model needs the detail) and the REAL logo, cut
   from the photo, goes in as image 3. Everything else → `low` — plain garments, stripes and colour
   blocks come out right at low (measured).
4. **Style — paid.** `SK_STYLIST_MODEL` gets the product + surface (downscaled to
   `SK_STYLIST_INPUT_PX`) and a short prompt. Output 1024×1536, cropped to the 4:5 slide.
5. **Fidelity — free** (`stylist_cv.check_and_fix`): the garment's colour must still cover the
   image (else rejected → that slide keeps the free design); the logo the model drew is found and
   compared with the real one (shape IoU over small rotations/flips + colour). A different emblem
   is erased (clone-stamped with nearby fabric) and the **real logo is put back**, relit to the
   fabric with a colour matte — no second paid image.
6. **Slide.** Styled image full-bleed + one compact details band; slide 1 = collage of the styled images.

Calibrated on 74 real cut-outs (2026-10-03): logos/labels/prints → medium, plain/striped → low;
logo outlines score 0.48–0.75 sharpness, shadows/gaps 0.24–0.46 (`SHARP_MIN = 0.47`). Known
limits: tone-on-tone tiny logos and same-hue back prints without "print" in the title go `low`.

## Money rules (non-negotiable)

| # | Rule |
|---|---|
| M1 | Nothing runs automatically — only the button; the estimate (medium/low split) + today's spend are shown and confirmed first. |
| M2 | Every paid image is saved per (surface, product) and reused — previews / re-renders / posting never pay twice. |
| M3 | The product read (step 2) is free and cached per photo. |
| M4 | `SK_STYLIST_DAILY_CAP_USD` is checked before EVERY paid image; reaching it stops the post. |
| M5 | `SK_STYLIST_CREDIT_USD` − spend since `SK_STYLIST_CREDIT_SINCE` must stay above `SK_STYLIST_RESERVE_USD`. |
| M6 | Every cent is written to `stylist_ledger.json` from the API's own usage numbers (OpenAI has no balance API). |
| M7 | Inputs are downscaled and prompts short — input tokens were ~70 % of the measured cost. |
| M8 | A refused/failed call is never retried automatically; a wrong logo is fixed in code, not re-bought. |

## Measured (2026-10-03, gpt-image-1-mini, 1024×1536, 640 px inputs)

| | cost per product |
|---|---|
| low | ≈ $0.0043 |
| medium (+ real-logo image) | ≈ $0.0139 |
| analysis + fidelity + logo restore | $0 (own code, < 1 s) |
