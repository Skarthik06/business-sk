"""Business-SK creative renderer — "The Still Set".

Turns product data + real product images into designed Instagram slides (1080×1350,
2× retina) using the exact visual identity from the creative-system playbook: the
"index frame", per-category tint, editorial serif + mono placard, quiet price lockup.

Design principles enforced here (see creative-system.html + carousel-publisher.agents.md):
  • The PRODUCT stays true to source — we never recolour/reshape/relabel it. Only the
    design ENVIRONMENT (stage, shadow, type) is created around it.
  • NO fabrication — a field (MRP, discount, rating) renders only when it is actually
    present in the product data. Missing → the element simply isn't drawn.
  • Multi-product LOGIC — the layout family is chosen by product count; products are
    never shrunk to fit, the structure changes instead (1→hero … 7+→carousel).

Stack (all free, self-hosted): Playwright/Chromium (render), Pillow (image prep),
Google Fonts CDN (Instrument Serif · Hanken Grotesk · Space Mono, all OFL) + the
system Noto fonts (Kannada/Devanagari) for the language module. rembg is used for
product isolation IF installed — otherwise the source image is staged as-is.
"""
from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ── Cutout / render knobs — env defaults, overlaid LIVE from the Agents panel ──────
# The "still-set-renderer" agent (affiliate backend /api/render-config) can tune these
# from the studio without a restart. Fetched with a short cache; falls back to env/defaults
# so rendering never depends on that call succeeding.
_RENDER_DEFAULTS = {
    "isolate": os.getenv("SK_ISOLATE", "1") not in ("0", "false", ""),
    "alpha_matting": os.getenv("SK_ALPHA_MATTING", "1") not in ("0", "false", ""),
    "fg_threshold": int(os.getenv("SK_ALPHA_FG", "240") or 240),   # keep this-and-brighter as product
    "bg_threshold": int(os.getenv("SK_ALPHA_BG", "12") or 12),     # treat this-and-darker as background
    "erode": int(os.getenv("SK_ALPHA_ERODE", "0") or 0),           # 0 = don't eat the product edge
    "model": os.getenv("SK_ISOLATE_MODEL", "u2net"),
    "knockout_thresh": int(os.getenv("SK_KNOCKOUT_THRESH", "30") or 30),
    "brand_logos": os.getenv("SK_BRAND_LOGOS", "1") not in ("0", "false", ""),  # show the cover 'Featuring' brand marks
    "brand_max": int(os.getenv("SK_BRAND_MAX", "4") or 4),          # how many brand marks on the cover
}
_RCFG_CACHE: Dict[str, Any] = {"at": 0.0, "val": None}


def _render_cfg() -> Dict[str, Any]:
    """Current cutout knobs (env defaults + live overlay from the affiliate Agents panel).
    Cached ~60s so we don't fetch per slide; any failure → defaults (rendering never breaks)."""
    now = time.time()
    if _RCFG_CACHE["val"] is not None and (now - _RCFG_CACHE["at"]) < 60:
        return _RCFG_CACHE["val"]
    cfg = dict(_RENDER_DEFAULTS)
    try:
        r = requests.get("http://affiliate_backend:8100/api/render-config", timeout=4)
        if r.ok:
            for k, v in (r.json() or {}).items():
                if k in cfg and v is not None:
                    cfg[k] = type(cfg[k])(v) if not isinstance(cfg[k], bool) else bool(v)
    except Exception:
        pass
    _RCFG_CACHE.update({"at": now, "val": cfg})
    return cfg

W, H = 1080, 1350

# ── category tint system: one frame, many moods ───────────────────────────────
_TINTS = {
    "fashion": "#B04A32", "tech": "#3E5568", "home": "#5C6A4B",
    "beauty": "#A0566A", "deal": "#9A6A2E", "deals": "#9A6A2E",
    "default": "#B04A32",
}
# Latin display/UI/utility via Google Fonts; Indic via the container's Noto fonts.
_INDIC = "'Noto Sans Kannada','Noto Sans Devanagari','Noto Sans'"
_SERIF = f"'Instrument Serif',{_INDIC},Georgia,serif"
_SANS = f"'Hanken Grotesk',{_INDIC},-apple-system,'Segoe UI',sans-serif"
_MONO = "'Space Mono',ui-monospace,monospace"


def _tint(category: str) -> str:
    key = (category or "").strip().lower()
    for k, v in _TINTS.items():
        if k in key:
            return v
    return _TINTS["default"]


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def _multiline(s: Any) -> str:
    """Escape text, then turn line breaks into <br>. Handles BOTH a real newline and a LITERAL
    backslash-n (LLMs often emit the two characters '\\n' instead of a newline in a JSON string)."""
    raw = "" if s is None else str(s)
    raw = raw.replace("\\n", "\n")                     # literal backslash-n → real newline
    parts = [p.strip() for p in raw.split("\n") if p.strip()]
    return "<br>".join(_esc(p) for p in parts)


def _money(v: Any) -> str:
    """Render a price/MRP as a clean ₹ figure with Indian grouping. Passes through
    a value that already has a currency symbol; drops nothing, invents nothing."""
    if v is None or v == "":
        return ""
    s = str(v).strip()
    if not s:
        return ""
    if s[0] in "₹$€£":                       # already formatted upstream — keep as-is
        return s
    m = re.search(r"\d[\d,]*\.?\d*", s)
    if not m:
        return s
    num = m.group(0).replace(",", "")
    try:
        n = int(round(float(num)))
    except ValueError:
        return f"₹{s}"
    grp = _indian_group(n)
    return f"₹{grp}"


def _indian_group(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"{head},{tail}"


def _discount_pct(p: Dict[str, Any]) -> Optional[int]:
    """Real discount only: use the given pct, else derive from price vs MRP. None if
    we can't compute it truthfully."""
    d = p.get("discount_pct")
    if d not in (None, "", 0, "0"):
        try:
            return int(round(float(str(d).replace("%", ""))))
        except ValueError:
            pass
    price = _num(p.get("price"))
    mrp = _num(p.get("orig_price") or p.get("mrp"))
    if price and mrp and mrp > price:
        return int(round((mrp - price) / mrp * 100))
    return None


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    m = re.search(r"\d[\d,]*\.?\d*", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _name(p: Dict[str, Any]) -> str:
    return (p.get("product_title") or p.get("title") or p.get("name") or "Product").strip()


def _clean_title(p: Dict[str, Any], limit: int = 88) -> str:
    """A READABLE product name for the slide. Prefers the AI-written `display_title` (a short,
    elegant, human name from the composer); falls back to the FULL Amazon name (brand + product +
    key attributes), only tidying the keyword-stuffing punctuation and capping at a word boundary
    so nothing is chopped mid-word."""
    ai = " ".join((p.get("display_title") or "").split())
    if ai:                                  # trust the composer's clean name; just cap length
        return ai if len(ai) <= limit else ai[:limit].rsplit(" ", 1)[0].rstrip(" ,-·") + "…"
    t = " ".join(_name(p).split())
    t = t.replace(" | ", " · ").replace("|", " · ").replace(" - ", " · ")   # de-clutter separators, keep content
    t = re.sub(r"\s*·\s*·\s*", " · ", t).strip(" ·-,")
    if len(t) > limit:                      # cap at a word boundary, no mid-word chop
        cut = t[:limit].rsplit(" ", 1)[0].rstrip(" ,-·")
        t = (cut or t[:limit]) + "…"
    return t


def _brand(p: Dict[str, Any]) -> str:
    b = (p.get("brand") or "").strip()
    if b:
        return b
    # first token of the REAL Amazon title as a fallback brand cue (never the AI display name,
    # which is intentionally brand-free) — kept short, never fabricated.
    raw = (p.get("product_title") or p.get("title") or p.get("name") or "").strip()
    return (raw.split()[0][:22] if raw else "")


# ── brand marks: real logos for known brands (Clearbit), elegant wordmark otherwise ──────
# Fills the cover's empty space with the brands featured — recognisable names pull the click.
# Curated domains only, so Clearbit reliably has the logo; everything else shows a clean wordmark.
_BRAND_DOMAINS = {
    "nike": "nike.com", "adidas": "adidas.com", "puma": "puma.com", "reebok": "reebok.com",
    "boat": "boat-lifestyle.com", "boult": "boultaudio.com", "noise": "gonoise.com",
    "realme": "realme.com", "mi": "mi.com", "xiaomi": "mi.com", "samsung": "samsung.com",
    "oneplus": "oneplus.in", "sony": "sony.co.in", "jbl": "jbl.com", "philips": "philips.co.in",
    "levis": "levi.com", "allensolly": "allensolly.com", "peterengland": "peterengland.com",
    "hrx": "hrx.co.in", "campus": "campusshoes.com", "wildcraft": "wildcraft.com",
    "fastrack": "fastrack.in", "titan": "titan.co.in", "fossil": "fossil.com",
    "prestige": "ttkprestige.com", "pigeon": "stovekraft.com", "bajaj": "bajajelectricals.com",
    "usha": "usha.com", "havells": "havells.com", "milton": "miltonindia.com",
    "cello": "celloworld.com", "borosil": "borosil.com", "wow": "buywow.in",
    "mamaearth": "mamaearth.in", "lakme": "lakmeindia.com", "nivea": "nivea.in",
    "boldfit": "boldfit.in", "wakefit": "wakefit.co", "sparx": "sparxfootwear.com",
    "redtape": "redtape.com", "bata": "bata.in", "wrogn": "wrogn.com", "roadster": "myntra.com",
    # Cuelinks marketplaces / stores (so DEAL cards show the merchant logo)
    "flipkart": "flipkart.com", "myntra": "myntra.com", "ajio": "ajio.com", "nykaa": "nykaa.com",
    "nykaabeauty": "nykaa.com", "meesho": "meesho.com", "tatacliq": "tatacliq.com",
    "tatacliqluxury": "tatacliq.com", "pepperfry": "pepperfry.com", "firstcry": "firstcry.com",
    "lenskart": "lenskart.com", "croma": "croma.com", "reliancedigital": "reliancedigital.in",
    "pharmeasy": "pharmeasy.in", "netmeds": "netmeds.com", "decathlon": "decathlon.in",
    "bigbasket": "bigbasket.com", "snapdeal": "snapdeal.com", "makemytrip": "makemytrip.com",
    "urbanic": "urbanic.com", "swiggy": "swiggy.com", "swiggyinstamart": "swiggy.com",
    "muscleblaze": "muscleblaze.com", "ekart": "flipkart.com",
}


def _brand_key(b: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (b or "").lower())


def _brand_logo_url(brand: str) -> str:
    d = _BRAND_DOMAINS.get(_brand_key(brand))
    return f"https://logo.clearbit.com/{d}?size=160&format=png" if d else ""


def _uniq_brands(products: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    out, seen = [], set()
    for p in products or []:
        b = _brand(p).strip()
        k = _brand_key(b)
        # skip empty, pure-number, generic or over-long tokens (not a real brand cue)
        if b and k and k not in seen and len(b) <= 18 and not b.isdigit() and k not in ("generic", "the", "mens", "women"):
            seen.add(k); out.append(b)
        if len(out) >= limit:
            break
    return out


def _brand_marks(products: List[Dict[str, Any]], P: Dict[str, str], *, limit: int = 4) -> str:
    """A 'Featuring' row of brand marks for the cover's empty space. Known brands render a real
    logo (Clearbit); the rest render a tasteful serif wordmark. Never fabricates a brand.
    Gated + sized live by the still-set-renderer agent (RENDER_BRAND_LOGOS / RENDER_BRAND_MAX)."""
    cfg = _render_cfg()
    if not cfg.get("brand_logos", True):
        return ""
    limit = max(0, int(cfg.get("brand_max", limit) or 0))
    if limit == 0:
        return ""
    brands = _uniq_brands(products, limit)
    if not brands:
        return ""
    cells = []
    for b in brands:
        url = _brand_logo_url(b)
        # The wordmark ALWAYS renders; a real logo fades in ONLY once it successfully loads
        # (opacity:0 → 1 on load) and removes itself on error — so a failed/slow logo never
        # shows a broken-image icon, it just falls back to the clean wordmark.
        logo = (f'<img src="{url}" loading="eager" style="opacity:0" '
                f'onload="this.style.opacity=1" onerror="this.remove()">') if url else ""
        cells.append(f'<div class="brandmark"><span class="wm">{_esc(b)}</span>{logo}</div>')
    return (f'<div style="display:flex;flex-direction:column;gap:14px">'
            f'<span class="brandeyebrow">Featuring</span>'
            f'<div class="brandrow">{"".join(cells)}</div></div>')


# ── product image prep: stage the environment, keep the product true ──────────
_REMBG_SESSION = None


_REMBG_MODEL = None


def _rembg_cut(img_bytes: bytes) -> Optional[bytes]:
    """Isolate the product (transparent PNG) IF rembg is installed. Returns None when
    unavailable so the caller stages the source image as-is.

    Edge quality: ALPHA MATTING is enabled by default — instead of a hard binary mask (which
    leaves jagged / brushed-out edges), it feathers the boundary using foreground/background
    thresholds, and erode=0 means we don't shave pixels off the product. All tunable live from
    the Agents panel. Never alters the product's own pixels — only the cutout mask."""
    global _REMBG_SESSION, _REMBG_MODEL
    try:
        from rembg import remove, new_session  # type: ignore
        rc = _render_cfg()
        model = rc.get("model") or "u2net"
        if _REMBG_SESSION is None or _REMBG_MODEL != model:
            try:
                _REMBG_SESSION = new_session(model)
            except Exception:                       # unknown/undownloadable model → safe default
                _REMBG_SESSION = new_session("u2net"); model = "u2net"
            _REMBG_MODEL = model
        if rc.get("alpha_matting"):
            return remove(
                img_bytes, session=_REMBG_SESSION,
                alpha_matting=True,
                alpha_matting_foreground_threshold=int(rc.get("fg_threshold", 240)),
                alpha_matting_background_threshold=int(rc.get("bg_threshold", 12)),
                alpha_matting_erode_size=int(rc.get("erode", 0)),
            )
        return remove(img_bytes, session=_REMBG_SESSION)
    except Exception:
        return None


def _knockout_white_bg(im):
    """Zero-dependency fallback background removal for the very common case of a product
    shot on a plain white studio background (most Amazon catalog images). Flood-fills the
    connected near-white region from the four corners → transparent, so the product sits on
    our tinted stage. Interior whites (a white logo, a white sole) are preserved because they
    aren't connected to a corner. If the corners aren't white (lifestyle/coloured bg), the
    image is returned untouched — never risk cutting into the product."""
    from PIL import Image, ImageDraw
    w, h = im.size
    rgb = im.convert("RGB")
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    if not all(min(rgb.getpixel(c)) > 232 for c in corners):
        return im                                   # not a white-bg catalog shot → leave alone
    sentinel = (255, 0, 255)
    _kt = int(_render_cfg().get("knockout_thresh", 30))   # lower = safer (won't eat light product edges)
    for c in corners:
        try:
            ImageDraw.floodfill(rgb, c, sentinel, thresh=_kt)
        except Exception:
            return im
    try:
        import numpy as np
        arr = np.asarray(rgb)
        mask = (arr[:, :, 0] == 255) & (arr[:, :, 1] == 0) & (arr[:, :, 2] == 255)
        alpha = np.asarray(im.split()[3]).copy()
        alpha[mask] = 0
        im.putalpha(Image.fromarray(alpha, "L"))
    except Exception:
        return im
    return im


def _prep_image(src: str, *, isolate: bool = True, box: int = 1000) -> Optional[str]:
    """Download/load a product image and return a data: URI ready for the stage.
    Steps: fetch → (optional rembg isolate) → fit into a square, padded, RGBA canvas
    (contain, never crop the product) → light sharpen. The product is untouched;
    only padding/transparency (the environment) is added for cross-source consistency."""
    from PIL import Image, ImageOps, ImageFilter
    raw: Optional[bytes] = None
    try:
        if src.startswith("http"):
            r = requests.get(src, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            raw = r.content
        else:
            p = Path(src)
            if p.exists():
                raw = p.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    isolated = False
    if isolate and _render_cfg().get("isolate", True):   # panel can disable cutout entirely
        cut = _rembg_cut(raw)
        if cut:
            raw = cut
            isolated = True
    try:
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im).convert("RGBA")
        if not isolated:
            im = _knockout_white_bg(im)             # free fallback: drop a plain white catalog bg
        # Trim the product out of ANY uniform border (white / off-white / grey / solid colour),
        # not just a transparent one — this is what makes the product BIG on the stage instead
        # of sitting tiny inside its original margins. Uniform border only; never crops the product.
        bbox = _content_bbox(im)
        if bbox:
            im = im.crop(bbox)
        # Scale the product to FILL the stage (up OR down, aspect preserved) with only a hair of
        # padding, so it reads large and clear. Upscaled shots get a sharpen so they stay crisp.
        pad = int(box * 0.02)
        inner = box - 2 * pad
        scale = min(inner / max(1, im.width), inner / max(1, im.height))
        neww, newh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
        im = im.resize((neww, newh), Image.LANCZOS)
        if scale > 1.05:                            # we enlarged a small source → recover edges
            im = im.filter(ImageFilter.UnsharpMask(radius=1.5, percent=115, threshold=2))
        canvas = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        canvas.paste(im, ((box - im.width) // 2, (box - im.height) // 2), im)
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _content_bbox(im) -> Optional[Tuple[int, int, int, int]]:
    """Bounding box of the actual product. If the image already has transparency (rembg or the
    white-knockout), use the alpha bbox. Otherwise estimate the background colour from the four
    corners and return the box of everything that differs from it — trimming a uniform border of
    ANY colour. Returns None (keep full frame) when the image is edge-to-edge content."""
    try:
        import numpy as np
        w, h = im.size
        alpha = np.asarray(im.split()[3])
        if int(alpha.min()) < 245:                  # real transparency present → trust it
            return im.split()[3].getbbox()
        rgb = np.asarray(im.convert("RGB")).astype(np.int16)
        corners = np.array([rgb[0, 0], rgb[0, w - 1], rgb[h - 1, 0], rgb[h - 1, w - 1]])
        bg = np.median(corners, axis=0)
        # if corners disagree wildly it's a busy/lifestyle bg — don't risk trimming into product
        if np.abs(corners - bg).sum(axis=1).max() > 60:
            return None
        dist = np.abs(rgb - bg).sum(axis=2)
        mask = dist > 42
        ys, xs = np.where(mask)
        if xs.size == 0:
            return None
        m = max(4, int(min(w, h) * 0.01))
        x0, y0 = max(0, int(xs.min()) - m), max(0, int(ys.min()) - m)
        x1, y1 = min(w, int(xs.max()) + m), min(h, int(ys.max()) + m)
        # ignore a trim that barely does anything or one that ate almost everything (safety)
        if (x1 - x0) < w * 0.2 or (y1 - y0) < h * 0.2:
            return None
        return (x0, y0, x1, y1)
    except Exception:
        return im.getbbox()


# ── shared CSS (the identity) ─────────────────────────────────────────────────
def _base_css(tint: str) -> str:
    return f"""
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px}}
body{{font-family:{_SANS};background:#EDE7DD;color:#221E18;overflow:hidden;-webkit-font-smoothing:antialiased}}
.slide{{width:{W}px;height:{H}px;position:relative;background:
   radial-gradient(140% 100% at 50% -6%, #FBF8F2 0%, #EFE9E1 46%, #E7DFD2 100%);padding:60px}}
.frame{{position:absolute;inset:34px;border:1.5px solid #CFC5B2;border-radius:6px;pointer-events:none}}
.corner{{position:absolute;width:26px;height:26px;border:1.5px solid {tint};opacity:.75}}
.c1{{top:34px;left:34px;border-right:none;border-bottom:none}}
.c2{{top:34px;right:34px;border-left:none;border-bottom:none}}
.c3{{bottom:34px;left:34px;border-right:none;border-top:none}}
.c4{{bottom:34px;right:34px;border-left:none;border-top:none}}
.placard{{position:relative;z-index:2;display:flex;justify-content:space-between;align-items:flex-start}}
.kick{{font-family:{_MONO};font-size:22px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:{tint}}}
.code{{font-family:{_MONO};font-size:19px;letter-spacing:.14em;color:#8B8171}}
.stage{{position:relative;border-radius:8px;overflow:hidden;background:
   radial-gradient(120% 92% at 50% 16%, {tint}1F 0%, {tint}0D 55%, #E4DBCC 100%);
   display:flex;align-items:center;justify-content:center}}
.stage img{{width:94%;height:94%;object-fit:contain;
   filter:drop-shadow(0 34px 40px {tint}59) drop-shadow(0 10px 14px rgba(34,30,24,.18))}}
.stage.big img{{width:97%;height:97%}}
.serif{{font-family:{_SERIF}}}
.pname{{font-family:{_SERIF};line-height:1.02;color:#221E18;letter-spacing:-.01em}}
.plock{{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}}
.price{{font-family:{_SANS};font-weight:800;font-variant-numeric:tabular-nums;color:#221E18}}
.mrp{{font-size:.5em;color:#8B8171;text-decoration:line-through;font-variant-numeric:tabular-nums;font-weight:600}}
.off{{font-family:{_MONO};font-weight:700;color:#fff;background:{tint};padding:6px 14px;border-radius:6px;letter-spacing:.03em}}
.rating{{font-family:{_MONO};font-size:24px;color:#5A5245;letter-spacing:.04em}}
.chip{{display:inline-flex;align-items:center;gap:14px;border:1.5px solid #CFC5B2;background:#F6F2EB;
   border-radius:100px;padding:12px 24px}}
.chips{{display:flex;flex-wrap:wrap;gap:12px}}
.factchip{{display:inline-flex;align-items:center;gap:9px;border:1.5px solid #CFC5B2;background:#F6F2EB;
   border-radius:100px;padding:10px 20px;font-family:{_MONO};font-size:22px;color:#5A5245;letter-spacing:.02em}}
.factchip b{{color:#221E18;font-weight:700}}
.save{{font-family:{_MONO};font-size:24px;font-weight:700;color:{tint};letter-spacing:.02em}}
.code{{display:none}}
.cta{{display:inline-flex;align-items:center;gap:12px;font-family:{_MONO};font-weight:700;letter-spacing:.16em;
   text-transform:uppercase;border:1.5px solid #221E18;border-radius:100px;padding:16px 30px;color:#221E18;font-size:22px}}
.cta.solid{{background:#221E18;color:#EDE7DD}}
.rank{{font-family:{_SERIF};color:{tint};line-height:.8}}
.foot{{position:absolute;left:60px;right:60px;bottom:56px;z-index:2;display:flex;justify-content:space-between;
   align-items:center;font-family:{_MONO};font-size:19px;letter-spacing:.14em;text-transform:uppercase;color:#8B8171}}
.progress{{position:absolute;left:34px;right:34px;bottom:34px;height:4px;background:#00000014;z-index:3;border-radius:2px;overflow:hidden}}
.progress i{{display:block;height:100%;background:{tint}}}
"""


def _page(tint: str, inner: str, *, idx: int = 1, total: int = 1,
          foot_left: str = "@lostinframes0605.exe", foot_right: str = "") -> str:
    # No edition code (top-right) and no slide counter (footer) — per brand: clean, uncluttered.
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Hanken+Grotesk:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>{_base_css(tint)}</style></head>
<body><div class="slide">
  <div class="frame"></div><span class="corner c1"></span><span class="corner c2"></span><span class="corner c3"></span><span class="corner c4"></span>
  {inner}
  <div class="foot"><span>{_esc(foot_left)}</span></div>
</div></body></html>"""


# ── individual templates ──────────────────────────────────────────────────────
def _clean_count(v: Any) -> str:
    """Tidy a count-ish field ('52', '50+', '1,240', '1K+', '500+ bought') → a compact token."""
    s = str(v or "").strip()
    m = re.search(r"[\d,]+\s*[KkMm]?\+?", s)
    return re.sub(r"\s+", "", m.group(0)) if m else ""


def _fmt_count(v: Any) -> str:
    """Human count with grouping, preserving +/K/M suffixes: 39800→'39,800', '1K+'→'1K+'."""
    s = _clean_count(v)
    m = re.match(r"^([\d,]+)([KkMm]?\+?)$", s)
    if not m:
        return s
    try:
        return _indian_group(int(m.group(1).replace(",", ""))) + m.group(2)
    except ValueError:
        return s


_GOOD_BADGES = {
    "amazon's choice": "Amazon's Choice", "amazons choice": "Amazon's Choice",
    "best seller": "Best Seller", "#1 best seller": "#1 Best Seller",
    "limited time deal": "Limited Time Deal",
}


def _badge_text(p: Dict[str, Any]) -> str:
    """Return a CLEAN, verified badge label or "". Guards against the truncated scrape
    ("Amazon's") and anything not on the known-good list — never show a partial badge."""
    raw = (p.get("badge") or "").strip()
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw.replace("’", "'")).strip().lower()
    if key in _GOOD_BADGES:
        return _GOOD_BADGES[key]
    if key in ("amazon's", "amazons", "amazon"):     # the old truncated form → repair
        return "Amazon's Choice"
    for k, v in _GOOD_BADGES.items():
        if k in key:
            return v
    return ""                                         # unknown/partial → drop (no fabrication)


def _badge_pill(p: Dict[str, Any], tint: str) -> str:
    b = _badge_text(p)
    if not b:
        return ""
    return (f'<span style="display:inline-flex;align-items:center;gap:8px;font-family:{_MONO};'
            f'font-size:20px;font-weight:700;letter-spacing:.04em;color:#fff;background:{tint};'
            f'padding:9px 18px;border-radius:100px">✓ {_esc(b)}</span>')


def _rating_chip(p: Dict[str, Any]) -> str:
    rating = str(p.get("rating") or "").strip()
    if not rating:
        return ""
    reviews = _fmt_count(p.get("reviews") or p.get("ratings_count"))
    txt = f"★ {rating}" + (f" · <b>{reviews}</b> ratings" if reviews else "")
    return f'<span class="factchip">{txt}</span>'


def _demand_chip(p: Dict[str, Any]) -> str:
    """Show demand ONLY when it's a credible number — a bare '1'/'2' reads worse than nothing.
    Anything with a +, K or M suffix, or ≥ 50, qualifies as a real social-proof signal."""
    raw = _clean_count(p.get("bought_past_month"))
    if not raw:
        return ""
    credible = any(c in raw for c in "+KkMm")
    if not credible:
        try:
            credible = int(raw.replace(",", "")) >= 50
        except ValueError:
            credible = False
    if not credible:
        return ""
    return f'<span class="factchip"><b>{_fmt_count(raw)}</b> bought recently</span>'


def _savings_line(p: Dict[str, Any]) -> str:
    price = _num(p.get("price"))
    mrp = _num(p.get("orig_price") or p.get("mrp"))
    if price and mrp and mrp > price:
        return f'<div class="save">You save ₹{_indian_group(int(round(mrp - price)))}</div>'
    return ""


def _info_block(p: Dict[str, Any], tint: str = "#B04A32", *, name_size: int = 56,
                price_size: int = 62) -> str:
    """The rich, TRUTHFUL product overlay: a verified badge (Amazon's Choice / Best Seller),
    real trust chips (rating · reviews, demand), the serif name, the price lockup (₹ · struck
    MRP · % off) and the real savings. Every element is drawn only when its data exists."""
    chips = "".join(c for c in (_badge_pill(p, tint), _rating_chip(p), _demand_chip(p)) if c)
    chips_row = f'<div class="chips">{chips}</div>' if chips else ""
    save = _savings_line(p)
    return f"""{chips_row}
    {_lockup(p, name_size=name_size, price_size=price_size)}
    {save}"""


def _lockup(p: Dict[str, Any], *, name_size: int = 52, price_size: int = 58, show_mrp: bool = True) -> str:
    name = _esc(_name(p))[:60]
    price = _money(p.get("price"))
    mrp = _money(p.get("orig_price") or p.get("mrp")) if show_mrp else ""
    off = _discount_pct(p)
    pieces = []
    if price:
        pieces.append(f'<span class="price" style="font-size:{price_size}px">{price}</span>')
    if mrp and mrp != price:
        pieces.append(f'<span class="mrp">{mrp}</span>')
    if off:
        pieces.append(f'<span class="off" style="font-size:{max(20,int(price_size*0.36))}px">{off}% OFF</span>')
    lock = f'<div class="plock">{"".join(pieces)}</div>' if pieces else ""
    return f'<div class="pname" style="font-size:{name_size}px">{name}</div>{lock}'


def _cover_html(title: str, subtitle: str, tint: str, kick: str, imgs: List[str],
                idx: int, total: int) -> str:
    hero = next((u for u in imgs if u), "")
    hero_stage = (f'<div class="stage big" style="position:absolute;left:60px;right:60px;bottom:150px;'
                  f'height:720px;z-index:1"><img src="{hero}"></div>') if hero else ""
    inner = f"""
  <div class="placard"><span class="kick">{_esc(kick)}</span><span class="code">SK · THE EDIT</span></div>
  <div style="position:relative;z-index:2;margin-top:100px">
    <div class="serif" style="font-size:120px;line-height:.9;letter-spacing:-.02em;max-width:920px">{_esc(title)}</div>
    <div class="serif" style="font-size:50px;font-style:italic;color:{tint};margin-top:18px">{_esc(subtitle)}</div>
  </div>
  {hero_stage}
"""
    return _page(tint, inner, idx=idx, total=total)


def _hero_html(p: Dict[str, Any], img: str, tint: str, kick: str, code: str,
               idx: int, total: int) -> str:
    inner = f"""
  <div class="placard"><span class="kick">{_esc(kick)}</span><span class="code">{_esc(code)}</span></div>
  <div class="stage big" style="position:absolute;left:56px;right:56px;top:130px;height:800px;z-index:1">
    <img src="{img}">
  </div>
  <div style="position:absolute;left:60px;right:60px;bottom:140px;z-index:2;display:flex;flex-direction:column;align-items:flex-start;gap:18px">{_info_block(p, tint, name_size=60, price_size=64)}</div>
"""
    return _page(tint, inner, idx=idx, total=total)


def _deal_html(p: Dict[str, Any], img: str, tint: str, idx: int, total: int) -> str:
    off = _discount_pct(p)
    price = _money(p.get("price"))
    mrp = _money(p.get("orig_price") or p.get("mrp"))
    tag = f'<div class="off" style="font-size:34px;padding:10px 22px">↓ {off}% OFF</div>' if off else ""
    inner = f"""
  <div class="placard"><span class="kick">Price Drop</span></div>
  <div class="stage big" style="position:absolute;left:56px;right:56px;top:126px;height:660px;z-index:1"><img src="{img}"></div>
  <div style="position:absolute;left:60px;right:60px;bottom:140px;z-index:2;display:flex;flex-direction:column;align-items:flex-start;gap:18px">
    {f'<div class="chips">{_badge_pill(p, tint)}{_rating_chip(p)}{_demand_chip(p)}</div>' if (_badge_pill(p, tint) or _rating_chip(p) or _demand_chip(p)) else ''}
    <div class="pname" style="font-size:40px;max-width:940px">{_esc(_clean_title(p))}</div>
    <div class="plock"><span class="price" style="font-size:92px">{price}</span>{f'<span class="mrp" style="font-size:42px">{mrp}</span>' if mrp and mrp!=price else ''}</div>
    <div style="display:flex;align-items:center;gap:18px">{tag}{_savings_line(p)}</div>
  </div>
"""
    return _page(tint, inner, idx=idx, total=total)


def _value_html(p: Dict[str, Any], img: str, tint: str, idx: int, total: int) -> str:
    inner = f"""
  <div class="placard"><span class="kick">Best Value</span></div>
  <div class="stage big" style="position:absolute;left:56px;right:56px;top:130px;height:740px;z-index:1"><img src="{img}"></div>
  <div style="position:absolute;left:60px;right:60px;bottom:140px;z-index:2;display:flex;flex-direction:column;align-items:flex-start;gap:18px">
    {_info_block(p, tint, name_size=56, price_size=60)}
  </div>
"""
    return _page(tint, inner, idx=idx, total=total)


def _duo_html(ps: List[Dict[str, Any]], imgs: List[str], tint: str, idx: int, total: int) -> str:
    cols = ""
    for i, (p, u) in enumerate(zip(ps[:2], imgs[:2])):
        cols += f"""
      <div style="flex:1;display:flex;flex-direction:column;gap:22px">
        <div class="stage" style="flex:1"><img src="{u}"></div>
        <div>{_lockup(p, name_size=38, price_size=42)}</div>
      </div>"""
    inner = f"""
  <div class="placard"><span class="kick">Face-off / 02</span><span class="code">1 or 2?</span></div>
  <div style="position:absolute;left:60px;right:60px;top:150px;bottom:150px;z-index:2;display:flex;gap:26px">{cols}</div>
"""
    return _page(tint, inner, idx=idx, total=total)


def _rank_html(ps: List[Dict[str, Any]], imgs: List[str], tint: str, idx: int, total: int) -> str:
    rows = ""
    for i, (p, u) in enumerate(zip(ps[:5], imgs[:5]), 1):
        op = 1 - (i - 1) * 0.16
        price = _money(p.get("price"))
        off = _discount_pct(p)
        offtxt = f' <span class="off" style="font-size:20px;padding:3px 9px">{off}%</span>' if off else ""
        rows += f"""
      <div style="display:flex;align-items:center;gap:28px">
        <span class="rank" style="font-size:96px;opacity:{op:.2f}">{i:02d}</span>
        <div class="stage" style="width:150px;height:150px;flex:none"><img src="{u}"></div>
        <div style="flex:1">
          <div class="pname" style="font-size:38px">{_esc(_name(p))[:40]}</div>
          <div class="plock" style="margin-top:6px"><span class="price" style="font-size:38px">{price}</span>{offtxt}</div>
        </div>
      </div>"""
    inner = f"""
  <div class="placard"><span class="kick">Ranked / Top {min(5,len(ps))}</span><span class="code">SK · VERDICT</span></div>
  <div style="position:absolute;left:60px;right:60px;top:170px;bottom:150px;z-index:2;display:flex;flex-direction:column;justify-content:center;gap:30px">{rows}</div>
"""
    return _page(tint, inner, idx=idx, total=total)


def _grid_html(ps: List[Dict[str, Any]], imgs: List[str], tint: str, cols: int, kick: str,
               idx: int, total: int, theme_line: str = "") -> str:
    cells = ""
    for p, u in zip(ps, imgs):
        price = _money(p.get("price"))
        cells += f"""
      <div style="display:flex;flex-direction:column;gap:10px">
        <div class="stage" style="flex:1"><img src="{u}"></div>
        <div class="pname" style="font-size:27px">{_esc(_name(p))[:26]}</div>
        {f'<div class="price" style="font-size:30px">{price}</div>' if price else ''}
      </div>"""
    line = f'<div class="serif" style="position:absolute;left:60px;bottom:150px;z-index:2;font-size:44px">{_esc(theme_line)}</div>' if theme_line else ""
    inner = f"""
  <div class="placard"><span class="kick">{_esc(kick)}</span><span class="code">SK · THE EDIT</span></div>
  <div style="position:absolute;left:60px;right:60px;top:150px;bottom:{'230' if theme_line else '150'}px;z-index:2;
       display:grid;grid-template-columns:repeat({cols},1fr);gap:24px">{cells}</div>
  {line}
"""
    return _page(tint, inner, idx=idx, total=total)


def _lead_rail_html(ps: List[Dict[str, Any]], imgs: List[str], tint: str, idx: int, total: int) -> str:
    lead, lu = ps[0], imgs[0]
    rail = ""
    for p, u in zip(ps[1:5], imgs[1:5]):
        rail += f'<div class="stage"><img src="{u}"></div>'
    inner = f"""
  <div class="placard"><span class="kick">Top pick + {min(4,len(ps)-1)}</span><span class="code">SK · EDITOR</span></div>
  <div style="position:absolute;left:60px;right:60px;top:150px;bottom:150px;z-index:2;display:flex;gap:26px">
    <div style="flex:1.35;display:flex;flex-direction:column;gap:18px">
      <div class="stage" style="flex:1"><img src="{lu}"></div>
      <div>{_lockup(lead, name_size=40, price_size=44)}</div>
    </div>
    <div style="flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:18px">{rail}</div>
  </div>
"""
    return _page(tint, inner, idx=idx, total=total)


def _closer_html(tint: str, handle: str, idx: int, total: int) -> str:
    inner = f"""
  <div class="placard"><span class="kick">Shop the set</span><span class="code">SK · LINK</span></div>
  <div style="position:absolute;inset:150px 60px;z-index:2;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:44px;text-align:center">
    <div class="serif" style="font-size:100px;line-height:1.02">Everything here,<br>one link.</div>
    <span class="cta solid" style="font-size:26px">Link in bio →</span>
    <span class="rating" style="font-size:26px">{_esc(handle)} · new picks weekly</span>
  </div>
"""
    return _page(tint, inner, idx=idx, total=total)


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE SYSTEM v2 — carousel-first, palette-driven, bold prices, teaser cover.
# One product per slide (no cramped grids); every slide shows a big, unmissable
# price lockup; per-product template auto-chosen by the data; selectable palette.
# ══════════════════════════════════════════════════════════════════════════════
# Instagram-worthy slide palettes. `card` is the raised-surface colour (price card, stat tiles,
# CTA rows) — it MUST track the palette, otherwise a dark theme renders dark text on a white chip.
# `tint` is the accent; "warm" leaves it None so it keeps the per-category tint (fashion/tech/home…).
_PALETTES = {
    "warm":  {"label": "Warm Sand",  "tint": None,      "g1": "#FBF8F2", "g2": "#EFE9E1", "g3": "#E7DFD2",
              "text": "#221E18", "muted": "#8B8171", "border": "#CFC5B2", "chip": "#F6F2EB",
              "stage": "#E4DBCC", "card": "#FFFFFFF0"},
    "sky":   {"label": "Sky Blue",   "tint": "#2E7DC4", "g1": "#F4F9FD", "g2": "#E7F0F8", "g3": "#DCE8F3",
              "text": "#16273A", "muted": "#6E8296", "border": "#C4D6E6", "chip": "#EEF5FB",
              "stage": "#DCE8F3", "card": "#FFFFFFF0"},
    "noir":  {"label": "Noir Gold",  "tint": "#D8B45A", "g1": "#262A30", "g2": "#1B1E23", "g3": "#121417",
              "text": "#F4F2ED", "muted": "#9BA3AE", "border": "#3C424B", "chip": "#2A2F36",
              "stage": "#2A2F36", "card": "#2A2F36F2"},
    "rose":  {"label": "Rose Blush", "tint": "#D95C77", "g1": "#FFF7F8", "g2": "#FCEBEE", "g3": "#F7DDE3",
              "text": "#3A1F26", "muted": "#9E7B84", "border": "#EFCBD4", "chip": "#FFF1F4",
              "stage": "#F6DDE3", "card": "#FFFFFFF0"},
    "mint":  {"label": "Fresh Mint", "tint": "#2E9E6B", "g1": "#F4FBF7", "g2": "#E6F5EC", "g3": "#D8ECE1",
              "text": "#12291F", "muted": "#6E8C7C", "border": "#C0DFCD", "chip": "#EFF9F3",
              "stage": "#DCEFE4", "card": "#FFFFFFF0"},
    "lilac": {"label": "Lilac Pop",  "tint": "#7C5CD6", "g1": "#F9F6FE", "g2": "#F0EAFB", "g3": "#E4DBF6",
              "text": "#241B36", "muted": "#7E7295", "border": "#D5C8EE", "chip": "#F5F0FD",
              "stage": "#E7DFF7", "card": "#FFFFFFF0"},
    "clay":  {"label": "Terracotta", "tint": "#C0743A", "g1": "#FBF6EF", "g2": "#F2E8DA", "g3": "#E8DBC6",
              "text": "#2C2118", "muted": "#907F68", "border": "#DCC9AC", "chip": "#F7F0E5",
              "stage": "#ECDFCB", "card": "#FFFFFFF0"},
    "mono":  {"label": "Mono Ink",   "tint": "#141416", "g1": "#FFFFFF", "g2": "#F4F4F5", "g3": "#E7E7EA",
              "text": "#111113", "muted": "#77777E", "border": "#D6D6DA", "chip": "#FAFAFB",
              "stage": "#EDEDEF", "card": "#FFFFFFF2"},
}

# Public list for the UI palette picker: [{id, label, tint, swatch}]
def palette_options() -> List[Dict[str, str]]:
    out = []
    for k, v in _PALETTES.items():
        out.append({"id": k, "label": v.get("label", k.title()),
                    "tint": v.get("tint") or "#B0763C", "swatch": v["g2"],
                    "dark": k == "noir"})
    return out


def _palette(name: str, category: str) -> Dict[str, str]:
    """Resolve a palette. 'warm' keeps the per-category tint (fashion/tech/home…);
    'sky' is the cool blue identity. Anything else → warm."""
    key = (name or "warm").strip().lower()
    src = _PALETTES.get(key)
    if src is None:
        key, src = "warm", _PALETTES["warm"]
    P = dict(src)
    # "warm" has no fixed accent — it keeps the per-category tint (fashion/tech/home…).
    P["tint"] = src.get("tint") or _tint(category)
    P["name"] = key
    return P


def _css2(P: Dict[str, str]) -> str:
    t = P["tint"]
    return f"""
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px}}
body{{font-family:{_SANS};background:{P['g2']};color:{P['text']};overflow:hidden;-webkit-font-smoothing:antialiased}}
.slide{{width:{W}px;height:{H}px;position:relative;padding:60px;background:
   radial-gradient(140% 100% at 50% -6%, {P['g1']} 0%, {P['g2']} 46%, {P['g3']} 100%)}}
.frame{{position:absolute;inset:34px;border:1.5px solid {P['border']};border-radius:6px;pointer-events:none}}
.corner{{position:absolute;width:26px;height:26px;border:1.5px solid {t};opacity:.75}}
.c1{{top:34px;left:34px;border-right:none;border-bottom:none}}.c2{{top:34px;right:34px;border-left:none;border-bottom:none}}
.c3{{bottom:34px;left:34px;border-right:none;border-top:none}}.c4{{bottom:34px;right:34px;border-left:none;border-top:none}}
.kick{{font-family:{_MONO};font-size:22px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:{t}}}
.code{{font-family:{_MONO};font-size:19px;letter-spacing:.14em;color:{P['muted']}}}
.placard{{position:relative;z-index:2;display:flex;justify-content:space-between;align-items:flex-start}}
.serif{{font-family:{_SERIF}}}
.pname{{font-family:{_SERIF};line-height:1.02;color:{P['text']};letter-spacing:-.01em}}
.stage{{position:relative;border-radius:12px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:
   radial-gradient(120% 92% at 50% 16%, {t}22 0%, {t}0D 55%, {P['stage']} 100%)}}
.stage img{{width:94%;height:94%;object-fit:contain;filter:drop-shadow(0 30px 36px {t}4D) drop-shadow(0 10px 14px rgba(20,30,45,.16))}}
.foot{{position:absolute;left:60px;right:60px;bottom:56px;z-index:2;display:flex;justify-content:space-between;align-items:center;
   font-family:{_MONO};font-size:19px;letter-spacing:.14em;text-transform:uppercase;color:{P['muted']}}}
.badge{{display:inline-flex;align-items:center;gap:8px;font-family:{_MONO};font-size:21px;font-weight:700;color:#fff;background:{t};
   padding:9px 18px;border-radius:100px;letter-spacing:.02em}}
.chip{{display:inline-flex;align-items:center;gap:9px;border:1.5px solid {P['border']};background:{P['chip']};border-radius:100px;
   padding:10px 20px;font-family:{_MONO};font-size:21px;color:{P['muted']}}}
.chip b{{color:{P['text']};font-weight:700}}
.pricecard{{display:inline-flex;flex-direction:column;gap:8px;background:{P['card']};border:1.5px solid {P['border']};
   border-radius:16px;padding:18px 24px;box-shadow:0 18px 40px rgba(20,30,45,.14)}}
.prow{{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}}
.big-price{{font-family:{_SANS};font-weight:800;font-size:88px;letter-spacing:-.02em;color:{P['text']};font-variant-numeric:tabular-nums;line-height:.9}}
.big-mrp{{font-family:{_SANS};font-size:38px;color:{P['muted']};text-decoration:line-through;font-weight:600}}
.off-pill{{font-family:{_MONO};font-weight:700;font-size:28px;color:#fff;background:{t};padding:8px 16px;border-radius:10px}}
.save-line{{font-family:{_MONO};font-weight:700;font-size:24px;color:{t}}}
.spark{{position:absolute;color:{t};font-size:40px;opacity:.5;z-index:1}}
.saletag{{position:absolute;transform:rotate(-8deg);font-family:{_MONO};font-weight:700;letter-spacing:.06em;color:#fff;background:{t};
   padding:10px 20px;border-radius:8px;font-size:26px;box-shadow:0 10px 24px {t}59;z-index:3}}
.megaoff{{font-family:{_SERIF};color:{t};line-height:.82;letter-spacing:-.02em}}
.swipe{{display:inline-flex;align-items:center;gap:10px;font-family:{_MONO};font-weight:700;font-size:24px;color:{t};letter-spacing:.08em;text-transform:uppercase}}
.thumbs{{display:flex;gap:18px}}.thumb{{flex:1;aspect-ratio:1;border-radius:14px;border:1.5px solid {P['border']}}}
.brandeyebrow{{font-family:{_MONO};font-size:20px;letter-spacing:.22em;text-transform:uppercase;color:{P['muted']}}}
.brandrow{{display:flex;gap:16px;flex-wrap:wrap;align-items:center}}
.brandmark{{position:relative;display:inline-flex;align-items:center;justify-content:center;height:76px;min-width:132px;
   padding:0 24px;background:{P['card']};border:1.5px solid {P['border']};border-radius:16px;box-shadow:0 12px 28px rgba(20,30,45,.10)}}
.brandmark .wm{{font-family:{_SERIF};font-size:34px;font-weight:600;color:{P['text']};letter-spacing:.01em;white-space:nowrap;line-height:1}}
.brandmark img{{position:absolute;left:14px;top:12px;width:calc(100% - 28px);height:calc(100% - 24px);object-fit:contain;background:#fff;border-radius:8px}}
.collage{{display:grid;gap:14px;height:100%}}
.ccell{{position:relative;border-radius:16px;border:1.5px solid {P['border']};overflow:hidden;
   background:radial-gradient(120% 100% at 50% 18%, {t}26 0%, {t}0F 55%, {P['stage']} 100%)}}
.ccell>img{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;padding:8%;
   filter:drop-shadow(0 16px 20px {t}45) drop-shadow(0 6px 8px rgba(20,30,45,.12))}}
.ctag{{position:absolute;left:9px;right:9px;bottom:9px;display:flex;justify-content:space-between;align-items:center;gap:8px;
   background:{P['card']};border:1px solid {P['border']};border-radius:11px;padding:8px 13px;box-shadow:0 8px 18px rgba(20,30,45,.10)}}
.ctag span{{font-family:{_MONO};font-size:19px;font-weight:700;color:{P['text']};white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ctag b{{font-family:{_SANS};font-size:21px;font-weight:800;color:{t};white-space:nowrap}}
.coff{{position:absolute;top:10px;right:10px;font-family:{_MONO};font-weight:700;font-size:17px;color:#fff;background:{t};padding:5px 10px;border-radius:8px}}
.ctile{{display:flex;align-items:center;justify-content:center;background:{t};color:#fff;border-color:{t}}}
.ctile div{{text-align:center;font-family:{_MONO};font-weight:700;font-size:30px;letter-spacing:.08em;line-height:1.25}}
.ctile small{{display:block;font-size:19px;opacity:.85;letter-spacing:.16em;margin-top:4px}}
.selchip{{display:inline-flex;align-items:center;font-family:{_MONO};font-size:20px;font-weight:700;letter-spacing:.02em;
   color:{P['text']};background:{P['chip']};border:1.5px solid {t};border-radius:100px;padding:8px 18px}}
.cta{{display:inline-flex;align-items:center;gap:12px;font-family:{_MONO};font-weight:700;letter-spacing:.16em;text-transform:uppercase;
   border-radius:100px;padding:18px 34px;font-size:26px;background:{t};color:#fff}}
"""


def _page2(P: Dict[str, str], inner: str, *, foot_right: str = "SWIPE →", handle: str = "@lostinframes0605.exe") -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Hanken+Grotesk:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>{_css2(P)}</style></head><body><div class="slide">
  <div class="frame"></div><span class="corner c1"></span><span class="corner c2"></span><span class="corner c3"></span><span class="corner c4"></span>
  {inner}
  <div class="foot"><span>{_esc(handle)}</span><span>{_esc(foot_right)}</span></div>
</div></body></html>"""


def _pricecard(p: Dict[str, Any]) -> str:
    price = _money(p.get("price")); mrp = _money(p.get("orig_price") or p.get("mrp")); off = _discount_pct(p)
    pr = _num(p.get("price")); mr = _num(p.get("orig_price") or p.get("mrp"))
    save = f'<div class="save-line">You save ₹{_indian_group(int(round(mr - pr)))}</div>' if (pr and mr and mr > pr) else ""
    row = f'<span class="big-price">{price}</span>' if price else ""
    if mrp and mrp != price:
        row += f'<span class="big-mrp">{mrp}</span>'
    if off:
        row += f'<span class="off-pill">↓ {off}% OFF</span>'
    return f'<div class="pricecard"><div class="prow">{row}</div>{save}</div>' if row else ""


def _chips2(p: Dict[str, Any]) -> str:
    out = ""
    b = _badge_text(p)
    if b:
        out += f'<span class="badge">✓ {_esc(b)}</span>'
    rating = str(p.get("rating") or "").strip()
    if rating:
        revs = _fmt_count(p.get("reviews"))
        out += f'<span class="chip">★ {rating}{f" · <b>{revs}</b> ratings" if revs else ""}</span>'
    dem = _clean_count(p.get("bought_past_month"))
    if dem and (any(c in dem for c in "+KkMm") or (dem.replace(",", "").isdigit() and int(dem.replace(",", "")) >= 50)):
        out += f'<span class="chip"><b>{_fmt_count(dem)}</b> bought recently</span>'
    return out


def _store_name(p: Dict[str, Any]) -> str:
    """The store a product is live on, from its `source` — so a Flipkart post never says Amazon."""
    return "Flipkart" if str((p or {}).get("source", "")).lower() == "flipkart" else "Amazon.in"


def _badges_strip(products: List[Dict[str, Any]]) -> str:
    """Truthful store badges present across the picks + a 'Live on <store>' pill.
    Uses the real badge TEXT only (never a store logo) — trademark-safe."""
    seen, pills = set(), []
    for p in products:
        b = _badge_text(p)
        if b and b not in seen:
            seen.add(b); pills.append(f'<span class="badge">✓ {_esc(b)}</span>')
    store = _store_name(products[0]) if products else "Amazon.in"
    pills.append(f'<span class="chip">🛒 Live on <b>{store}</b></span>')
    return '<div style="display:flex;gap:12px;flex-wrap:wrap">' + "".join(pills[:3]) + "</div>"


def _collage(products: List[Dict[str, Any]], imgs: List[str], P: Dict[str, str]) -> str:
    """A COLLAGE of every product on the cover — a tidy grid of product cutouts, each with a
    NAME tag (prices are intentionally NOT shown on the cover — the tease is the look, the price
    reveals inside). A '% off' flag stays as a hook when the discount is steep. Columns scale with
    the product count so the grid always fills the cover's lower half."""
    n = len(products)
    if n == 0:
        return ""
    cols = 2 if n <= 2 else 3 if n == 3 else 2 if n == 4 else 3 if n <= 6 else 4
    cells: List[str] = []
    for p, img in zip(products, imgs):
        nm = _esc(_clean_title(p, limit=26))
        off = _discount_pct(p) or 0
        im = f'<img src="{img}">' if img else '<div style="position:absolute;inset:0"></div>'
        offflag = f'<div class="coff">-{off}%</div>' if off >= 40 else ""   # discount hook, not a price
        tag = f'<div class="ctag"><span>{nm}</span></div>'                  # NAME only — no cost on the cover
        cells.append(f'<div class="ccell">{im}{offflag}{tag}</div>')
    rows = (n + cols - 1) // cols
    if cols * rows > n:                      # fill an odd trailing slot with a CTA tile
        cells.append('<div class="ccell ctile"><div>MORE<small>SWIPE →</small></div></div>')
    return (f'<div class="collage" style="grid-template-columns:repeat({cols},1fr);grid-auto-rows:1fr">'
            f'{"".join(cells)}</div>')


def _sel_chips(cover_tags: Optional[List[str]], P: Dict[str, str]) -> str:
    """A row of the selections used for THIS post (audience · style · deals · price · rating),
    so every cover reflects its own filters and reads as unique. No prices — labels only."""
    tags = [str(t).strip() for t in (cover_tags or []) if str(t).strip()][:6]
    if not tags:
        return ""
    chips = "".join(f'<span class="selchip">{_esc(t)}</span>' for t in tags)
    return f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px">{chips}</div>'


def _cover2(products, imgs, P, *, title, subtitle, handle, cover_tags=None):
    n = len(products)
    maxoff = max((_discount_pct(p) or 0) for p in products) if products else 0
    off_pill = f'<span class="badge">↓ UP TO {maxoff}% OFF</span>' if maxoff else ""
    # Top third: AI headline + subtitle + deal badges + the selection tags for this post.
    # Lower two-thirds: a COLLAGE of every product (image + name, no price).
    inner = f"""
  <div class="placard"><span class="kick">The Drop</span><span class="code">SK · EDIT</span></div>
  <span class="spark" style="top:150px;left:90px;font-size:26px">✧</span>
  <div style="position:absolute;left:60px;right:60px;top:150px;z-index:2">
    <div class="serif" style="font-size:88px;line-height:.92;letter-spacing:-.02em;max-width:960px">{_multiline(title)}</div>
    <div class="serif" style="font-size:42px;font-style:italic;color:{P['tint']};margin-top:10px">{_esc(subtitle)}</div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:20px">{off_pill}{_badges_strip(products)}</div>
    {_sel_chips(cover_tags, P)}
  </div>
  <div style="position:absolute;left:60px;right:60px;top:560px;bottom:104px;z-index:2">{_collage(products, imgs, P)}</div>
"""
    # "Swipe" lives in the small footer (bottom of the slide) rather than as a large line above it.
    return _page2(P, inner, foot_right=f"SWIPE → {n} INSIDE", handle=handle)


def _deal_word(p: Dict[str, Any]) -> str:
    """The catchy 1-2 word price-sticker label: the AI `deal_tag` when present, else a truthful
    deterministic hype word chosen by discount depth (never the flat 'OFF TODAY')."""
    t = re.sub(r"[^A-Za-z' ]", "", (p.get("deal_tag") or "")).strip().upper()
    t = " ".join(t.split()[:2])
    if t and "OFF" not in t.split():
        return t
    off = _discount_pct(p) or 0
    return "STEAL" if off >= 70 else "BIG DROP" if off >= 50 else "HOT PRICE" if off >= 30 else "TODAY ONLY"


def _side_chips(p: Dict[str, Any], P: Dict[str, str]) -> str:
    """A column of truthful proof chips to sit BESIDE the price (fills the empty space next to
    the ₹ lockup). Falls back to a single 'Live on <store>' chip so the space never looks bare."""
    chips = _chips2(p) or f'<span class="chip">🛒 Live on <b>{_store_name(p)}</b></span>'
    return f'<div style="display:flex;flex-direction:column;gap:12px;padding-bottom:6px">{chips}</div>'


def _spotlight2(p, img, P, handle):
    off = _discount_pct(p) or 0
    word = _multiline(_deal_word(p))          # AI 1-2 word hype label, stacked if two words
    inner = f"""
  <div class="placard"><span class="kick">Price Drop</span><span class="code">SK · DEAL</span></div>
  <span class="spark" style="top:150px;right:110px">✦</span>
  <div style="position:absolute;inset:120px 60px 130px 60px;z-index:2;display:flex;flex-direction:column;gap:18px">
    <div class="stage" style="flex:1 1 auto;min-height:0"><img src="{img}"></div>
    <div style="display:flex;align-items:flex-end;gap:22px">
      <div class="megaoff" style="font-size:150px">{off}%</div><div class="megaoff" style="font-size:50px;padding-bottom:20px">{word}</div>
    </div>
    <div class="pname" style="font-size:40px;max-width:940px">{_esc(_clean_title(p))}</div>
    <div style="display:flex;align-items:flex-end;gap:28px;flex-wrap:wrap">{_pricecard(p)}{_side_chips(p, P)}</div>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _editorial2(p, img, P, handle):
    chips = _chips2(p)
    inner = f"""
  <div class="placard"><span class="kick">Editor's Pick</span><span class="code">SK · HERO</span></div>
  <span class="spark" style="top:150px;right:110px">✦</span>
  <div style="position:absolute;inset:120px 60px 130px 60px;z-index:2;display:flex;flex-direction:column;gap:16px">
    <div class="stage" style="flex:1 1 auto;min-height:0"><img src="{img}"></div>
    {f'<div style="display:flex;gap:12px;flex-wrap:wrap">{chips}</div>' if chips else ''}
    <div class="pname" style="font-size:42px;max-width:940px">{_esc(_clean_title(p))}</div>
    {_pricecard(p)}
  </div>
"""
    return _page2(P, inner, handle=handle)


def _proof2(p, img, P, handle):
    chips = _chips2(p)
    inner = f"""
  <div class="placard"><span class="kick">Loved by shoppers</span><span class="code">SK · PROOF</span></div>
  <div style="position:absolute;left:60px;right:60px;top:150px;bottom:150px;z-index:2;display:flex;flex-direction:column;gap:24px">
    <div class="stage" style="flex:1 1 auto;min-height:0"><img src="{img}"></div>
    <div style="display:flex;flex-direction:column;gap:15px">
      <div style="display:flex;gap:12px;flex-wrap:wrap">{chips or '<span class="chip">Verified pick</span>'}</div>
      <div class="pname" style="font-size:40px;max-width:940px">{_esc(_clean_title(p))}</div>
      {_pricecard(p)}
    </div>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _lookbook2(p, img, P, handle):
    """Full-bleed LOOKBOOK — the product fills the frame under a soft scrim, with the name + price
    overlaid at the foot. Editorial, lifestyle, scroll-stopping (great for fashion/home/beauty)."""
    inner = f"""
  <div class="placard" style="position:absolute;left:60px;right:60px;top:96px;z-index:4"><span class="kick" style="color:#fff">The Look</span><span class="code" style="color:#FFFFFFCC">SK · LOOKBOOK</span></div>
  <div class="stage" style="position:absolute;left:40px;right:40px;top:88px;bottom:88px;z-index:1;border-radius:30px"><img src="{img}" style="width:90%;height:90%"></div>
  <div style="position:absolute;left:40px;right:40px;bottom:88px;height:440px;z-index:2;border-radius:0 0 30px 30px;background:linear-gradient(to top,rgba(24,22,18,.86) 8%,rgba(24,22,18,.45) 48%,rgba(24,22,18,0) 100%)"></div>
  <div style="position:absolute;left:80px;right:80px;bottom:150px;z-index:3;display:flex;flex-direction:column;gap:20px">
    <div class="serif" style="font-size:62px;line-height:1.0;color:#fff;max-width:900px">{_multiline(_clean_title(p, limit=62))}</div>
    <div style="display:flex;align-items:flex-end;gap:22px;flex-wrap:wrap">{_pricecard(p)}</div>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _feature_points(p: Dict[str, Any]) -> List[str]:
    """3-4 short, TRUTHFUL selling points derived from the product's own data (never invented)."""
    pts: List[str] = []
    off = _discount_pct(p) or 0
    if off >= 15:
        pts.append(f"{off}% off right now")
    r = _num(p.get("rating"))
    if r:
        pts.append(f"Rated {r}★ by shoppers")
    rv = _num(p.get("reviews"))
    if rv and rv >= 50:
        pts.append(f"{_fmt_count(rv)} verified ratings")
    b = _badge_text(p)
    if b:
        pts.append(_esc(b))
    dem = _clean_count(p.get("bought_past_month"))
    if dem and any(c in dem for c in "+KkMm0123456789"):
        pts.append(f"{_fmt_count(dem)} bought recently")
    if not pts:
        pts.append("Hand-picked by the editor")
    pts.append(f"Live on {_store_name(p)}")
    return pts[:4]


def _feature2(p, img, P, handle):
    """WHY-WE-LOVE-IT — product on the left, a numbered list of real reasons on the right. Reads
    like a mini review; strong for well-rated / spec-y products."""
    t = P["tint"]
    pts = _feature_points(p)
    items = "".join(
        f'<div style="display:flex;align-items:flex-start;gap:18px">'
        f'<div style="flex:none;width:52px;height:52px;border-radius:14px;background:{t};color:#fff;'
        f'font-family:{_MONO};font-weight:700;font-size:26px;display:flex;align-items:center;justify-content:center">{i+1}</div>'
        f'<div style="font-family:{_SANS};font-weight:600;font-size:30px;color:{P["text"]};line-height:1.2;padding-top:6px">{f}</div></div>'
        for i, f in enumerate(pts))
    inner = f"""
  <div class="placard"><span class="kick">Why we love it</span><span class="code">SK · PICK</span></div>
  <div class="stage" style="position:absolute;left:48px;top:150px;bottom:150px;width:470px;z-index:1"><img src="{img}"></div>
  <div style="position:absolute;right:56px;top:150px;bottom:150px;width:470px;z-index:2;display:flex;
       flex-direction:column;gap:26px;justify-content:center">
    <div class="pname" style="font-size:40px">{_esc(_clean_title(p, limit=52))}</div>
    <div style="display:flex;flex-direction:column;gap:18px">{items}</div>
    <div style="display:flex">{_pricecard(p)}</div>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _savings2(p, img, P, handle):
    """SAVINGS hero — leads with the rupee amount saved (or % when there's no MRP). Product to the
    right; the big number does the selling. Distinct from Spotlight (which leads with % off)."""
    pr = _num(p.get("price")); mr = _num(p.get("orig_price") or p.get("mrp"))
    saved = int(round(mr - pr)) if (pr and mr and mr > pr) else 0
    big = f"₹{_indian_group(saved)}" if saved >= 300 else f"{_discount_pct(p) or 0}%"
    inner = f"""
  <div class="placard"><span class="kick">You Save</span><span class="code">SK · SAVINGS</span></div>
  <span class="spark" style="top:150px;left:120px;font-size:30px">✧</span>
  <div class="stage" style="position:absolute;right:48px;top:170px;width:500px;height:560px;z-index:1"><img src="{img}"></div>
  <div style="position:absolute;left:60px;top:230px;z-index:2;max-width:560px;display:flex;flex-direction:column;gap:6px">
    <div class="serif" style="font-size:46px;font-style:italic;color:{P['muted']}">you save</div>
    <div class="megaoff" style="font-size:158px">{big}</div>
    <div class="pname" style="font-size:36px;max-width:540px;margin-top:12px">{_esc(_clean_title(p, limit=58))}</div>
  </div>
  <div style="position:absolute;left:60px;bottom:140px;z-index:2;display:flex;align-items:flex-end;gap:24px;flex-wrap:wrap">{_pricecard(p)}{_side_chips(p, P)}</div>
"""
    return _page2(P, inner, handle=handle)


def _bold2(p, img, P, handle):
    """STATEMENT — the product name set BIG as the hero (editorial typography), the product framed
    below, price at the foot. A clean typographic change of pace; works for any product."""
    inner = f"""
  <div class="placard"><span class="kick">The Statement</span><span class="code">SK · EDIT</span></div>
  <span class="spark" style="top:150px;right:110px">✦</span>
  <div style="position:absolute;inset:150px 60px 120px 60px;z-index:2;display:flex;flex-direction:column;gap:18px">
    <div class="serif" style="font-size:84px;line-height:.92;letter-spacing:-.02em;max-width:960px">{_multiline(_clean_title(p, limit=52))}</div>
    <div class="stage" style="flex:1 1 auto;min-height:0;width:86%;align-self:center"><img src="{img}"></div>
    <div style="display:flex;align-items:flex-end;gap:24px;flex-wrap:wrap">{_pricecard(p)}{_side_chips(p, P)}</div>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _stat_tiles(p, P) -> str:
    """A row of REAL stat tiles from the product's own data — never invented."""
    def tile(big, sub):
        return (f'<div style="flex:1 1 0;min-width:150px;background:{P["card"]};border:1.5px solid {P["border"]};'
                f'border-radius:18px;padding:20px 20px;display:flex;flex-direction:column;gap:2px">'
                f'<div style="font-family:{_SANS};font-weight:800;font-size:42px;color:{P["text"]};line-height:1">{big}</div>'
                f'<div style="font-family:{_MONO};font-size:19px;color:{P["muted"]}">{sub}</div></div>')
    off = _discount_pct(p) or 0
    r = _num(p.get("rating")); rv = _num(p.get("reviews"))
    pr = _num(p.get("price")); mr = _num(p.get("orig_price") or p.get("mrp"))
    saved = (mr - pr) if (mr and pr and mr > pr) else 0
    dem = _clean_count(p.get("bought_past_month"))
    tiles = []
    if off >= 10:
        tiles.append(tile(f"{off}%", "off today"))
    if r:
        tiles.append(tile(f"{r}★", "shopper rating"))
    if rv and rv >= 50:
        tiles.append(tile(_fmt_count(rv), "ratings"))
    if saved >= 100:
        tiles.append(tile(f"₹{int(saved):,}", "you save"))
    if dem and any(c in dem for c in "0123456789"):
        tiles.append(tile(_fmt_count(dem), "bought recently"))
    if not tiles:
        tiles.append(tile(_esc(_store_name(p)), "in stock now"))
    return "".join(tiles[:4])


def _stat2(p, img, P, handle):
    """BY THE NUMBERS — product image over a strip of REAL stat tiles (rating, ratings, % off, ₹ saved,
    demand). All grounded in the product's own data; strong for well-reviewed / discounted picks."""
    inner = f"""
  <div class="placard"><span class="kick">By the numbers</span><span class="code">SK · STATS</span></div>
  <div style="position:absolute;inset:130px 60px 110px 60px;z-index:2;display:flex;flex-direction:column;gap:20px">
    <div class="stage" style="flex:1 1 auto;min-height:0"><img src="{img}"></div>
    <div class="pname" style="font-size:38px;max-width:940px">{_esc(_clean_title(p, limit=56))}</div>
    <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:stretch">{_stat_tiles(p, P)}</div>
    <div style="display:flex">{_pricecard(p)}</div>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _minimal2(p, img, P, handle):
    """MINIMAL — a calm, centered hero: product floating on the palette, name + price centered below.
    Lots of whitespace; premium and scroll-stopping for well-shot products."""
    inner = f"""
  <div class="placard"><span class="kick">Simply put</span><span class="code">SK · MINIMAL</span></div>
  <span class="spark" style="top:160px;left:96px;font-size:26px">✧</span>
  <div style="position:absolute;inset:170px 80px 130px 80px;z-index:2;display:flex;flex-direction:column;align-items:center;gap:20px;text-align:center">
    <div class="stage" style="flex:1 1 auto;min-height:0;width:74%"><img src="{img}"></div>
    <div class="serif" style="font-size:52px;line-height:1.02;max-width:840px">{_multiline(_clean_title(p, limit=46))}</div>
    <div style="display:flex;justify-content:center">{_pricecard(p)}</div>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _deal_logo(brand: str, P: Dict[str, str], *, big: bool = False) -> str:
    """A single merchant logo block (Clearbit) with a serif wordmark fallback — for deal slides."""
    url = _brand_logo_url(brand)
    logo = (f'<img src="{url}" loading="eager" style="opacity:0" onload="this.style.opacity=1" onerror="this.remove()">') if url else ""
    h, mw, fs = (118, 220, 46) if big else (76, 132, 34)
    return (f'<div class="brandmark" style="height:{h}px;min-width:{mw}px">'
            f'<span class="wm" style="font-size:{fs}px">{_esc(brand)}</span>{logo}</div>')


def _deal_card2(p, P, handle):
    """A single Cuelinks DEAL slide — merchant logo + discount + offer hook. The coupon CODE is
    NEVER printed on the post (it lives in the store, revealed there); the card only teases that a
    coupon exists, points to the store link in bio, and nudges follow + comment for the link."""
    brand = _brand(p) or "Store"
    off = _discount_pct(p) or 0
    has_code = bool(str(p.get("coupon_code") or "").strip())
    hook = _esc((p.get("hook") or _clean_title(p, limit=64)))
    big_off = (f'<div class="megaoff" style="font-size:150px">{off}%<span style="font-size:52px"> OFF</span></div>'
               if off else '<div class="megaoff" style="font-size:100px">Deal Drop</div>')
    coupon_teaser = (f'<div style="display:inline-flex;align-items:center;gap:14px;background:{P["card"]};'
                     f'border:2px dashed {P["tint"]};border-radius:14px;padding:14px 26px">'
                     f'<span style="font-size:34px">🎟️</span>'
                     f'<span style="font-family:{_MONO};font-weight:700;font-size:26px;color:{P["text"]}">Coupon available in the store</span></div>') if has_code else ""
    inner = f"""
  <div class="placard"><span class="kick">Deal Drop</span><span class="code">SK · CUELINKS</span></div>
  <span class="spark" style="top:150px;right:110px">✦</span>
  <div style="position:absolute;left:60px;right:60px;top:180px;z-index:2;display:flex;flex-direction:column;gap:26px;align-items:flex-start">
    {_deal_logo(brand, P, big=True)}
    {big_off}
    <div class="pname" style="font-size:40px;max-width:920px">{hook}</div>
    {coupon_teaser}
  </div>
  <div style="position:absolute;left:60px;right:60px;bottom:120px;z-index:2;display:flex;flex-direction:column;gap:12px">
    <div style="font-family:{_MONO};font-weight:700;font-size:24px;color:{P['tint']}">👉 Follow + comment “LINK” to get it</div>
    <span class="cta">🔗 Store link in bio →</span>
  </div>
"""
    return _page2(P, inner, handle=handle)


def _deal_cover2(deals, P, *, title, subtitle, handle):
    """Cover for a DEALS post — headline + a grid of the merchant logos featured."""
    n = len(deals)
    inner = f"""
  <div class="placard"><span class="kick">The Deals Edit</span><span class="code">SK · CUELINKS</span></div>
  <span class="spark" style="top:150px;left:90px;font-size:26px">✧</span>
  <div style="position:absolute;left:60px;right:60px;top:150px;z-index:2">
    <div class="serif" style="font-size:88px;line-height:.92;letter-spacing:-.02em;max-width:960px">{_multiline(title or "Today's Best Deals")}</div>
    <div class="serif" style="font-size:42px;font-style:italic;color:{P['tint']};margin-top:10px">{_esc(subtitle or f"{n} live offers inside")}</div>
  </div>
  <div style="position:absolute;left:60px;right:60px;top:540px;bottom:150px;z-index:2">{_brand_marks(deals, P, limit=6)}</div>
"""
    return _page2(P, inner, foot_right=f"SWIPE → {n} DEALS", handle=handle)


def _closer2(P, handle):
    """Elegant final CTA: comment→auto-DM (any comment triggers the DM link), link in
    bio, and a follow nudge for the account."""
    def row(icon, big, sub):
        return (f'<div style="display:flex;align-items:center;gap:22px;background:{P["card"]};'
                f'border:1.5px solid {P["border"]};border-radius:20px;padding:22px 28px;'
                f'box-shadow:0 14px 34px rgba(20,30,45,.10)">'
                f'<div style="font-size:46px;line-height:1">{icon}</div>'
                f'<div style="display:flex;flex-direction:column;gap:3px">'
                f'<div style="font-family:{_SANS};font-weight:800;font-size:34px;color:{P["text"]}">{big}</div>'
                f'<div style="font-family:{_MONO};font-size:21px;color:{P["muted"]}">{sub}</div></div></div>')
    inner = f"""
  <div class="placard"><span class="kick">Get the links</span><span class="code">SK · SHOP</span></div>
  <span class="spark" style="top:150px;right:120px">✦</span><span class="spark" style="bottom:170px;left:110px;font-size:30px">✧</span>
  <div style="position:absolute;inset:140px 60px;z-index:2;display:flex;flex-direction:column;justify-content:center;gap:24px">
    <div class="serif" style="font-size:92px;line-height:.96;text-align:center;margin-bottom:6px">Want these deals?</div>
    {row('➕', f'Follow {_esc(handle)}', 'follow first — DMs go to followers only')}
    {row('💬', 'Comment “LINK”', "we’ll DM you every product + store link")}
    {row('🔗', 'Tap the link in bio', 'shop all picks + grab coupons in the store')}
  </div>
"""
    return _page2(P, inner, foot_right="FOLLOW + COMMENT → DM", handle=handle)


# ── AI-SCENE layouts (agent: post-art-director) ─────────────────────────────────────────────
# The backdrop is an EMPTY scene painted by Z-Image-Turbo on the laptop GPU; the product is the
# REAL photo cut out by BiRefNet (original pixels, alpha only) and placed on top. CSS only scales
# it and adds a shadow — the product itself is never altered (ST2).
_SCENE_LAYOUTS = ("scene_hero", "scene_float", "scene_split")
_STORE_LABELS = {"amazon": "Amazon", "flipkart": "Flipkart", "shopsy": "Shopsy", "myntra": "Myntra",
                 "ajio": "AJIO", "boat": "boAt", "noise": "Noise", "snitch": "Snitch", "cuelinks": "Online"}


def _store_label(p: Dict[str, Any]) -> str:
    s = str(p.get("store") or p.get("source") or "amazon").strip().lower()
    return _STORE_LABELS.get(s, s.replace("_", " ").title()[:18] or "Amazon")


def _scene_css(P: Dict[str, str], dark: bool) -> str:
    t = P["tint"]
    ink = "#FFFFFF" if dark else P["text"]
    return f"""<style>
.frame,.corner{{z-index:6}} .foot{{z-index:6;color:{'#FFFFFFD9' if dark else P['muted']}}}
.scn{{position:absolute;inset:0;z-index:0;background-size:cover;background-position:center}}
.scol{{position:absolute;inset:66px 56px 104px 56px;z-index:2;display:flex;flex-direction:column}}
.schip{{align-self:center;font-family:{_MONO};font-weight:700;font-size:23px;letter-spacing:.12em;text-transform:uppercase;
   color:{P['text']};background:{P['card']};border-radius:100px;padding:12px 28px;box-shadow:0 10px 26px rgba(0,0,0,.14)}}
.sstage{{flex:1 1 auto;min-height:0;position:relative;display:flex;justify-content:center}}
.sstage img{{width:86%;height:100%;object-fit:contain}}
.shero img{{object-position:center bottom;filter:drop-shadow(16px 22px 26px rgba(18,12,6,.34))}}
.sfloat img{{width:80%;height:90%;filter:drop-shadow(0 34px 26px rgba(18,12,6,.30)) drop-shadow(0 8px 10px rgba(18,12,6,.18))}}
.spanel{{position:relative;z-index:3;background:{P['card']};border-radius:30px;padding:30px 40px 28px;
   box-shadow:0 22px 50px rgba(0,0,0,.20);display:flex;flex-direction:column;gap:10px}}
.seye{{font-family:{_MONO};font-size:20px;letter-spacing:.2em;text-transform:uppercase;color:{P['muted']}}}
.sname{{font-family:{_SERIF};font-size:46px;line-height:1.04;color:{P['text']};display:-webkit-box;-webkit-line-clamp:2;
   -webkit-box-orient:vertical;overflow:hidden}}
.sprow{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:4px}}
.sprice{{font-family:{_SANS};font-weight:800;font-size:72px;letter-spacing:-.02em;line-height:.95;color:{P['text']}}}
.smrp{{font-family:{_SANS};font-size:34px;font-weight:600;color:{P['muted']};text-decoration:line-through}}
.soff{{font-family:{_MONO};font-weight:700;font-size:26px;color:#fff;background:{t};padding:8px 16px;border-radius:12px}}
.sfoot{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}
.srate{{font-family:{_SANS};font-size:26px;color:{P['muted']}}} .srate b{{color:{t}}}
.scta{{font-family:{_SANS};font-weight:800;font-size:25px;color:{t}}}
.stag{{position:absolute;display:flex;flex-direction:column;align-items:flex-start;background:{P['card']};border-radius:14px;
   padding:9px 16px;box-shadow:0 10px 24px rgba(0,0,0,.16);z-index:4}}
.stag span{{font-family:{_MONO};font-size:17px;letter-spacing:.08em;text-transform:uppercase;color:{P['muted']};white-space:nowrap}}
.stag b{{font-family:{_SANS};font-weight:800;font-size:30px;color:{P['text']}}}
.stitle{{font-family:{_SERIF};font-size:74px;line-height:.95;letter-spacing:-.02em;color:{ink};
   text-shadow:{'0 2px 18px rgba(0,0,0,.35)' if dark else 'none'}}}
</style>"""


def _scene_price_row(p: Dict[str, Any]) -> str:
    price = _money(p.get("price")); mrp = _money(p.get("orig_price") or p.get("mrp")); off = _discount_pct(p) or 0
    row = f'<span class="sprice">{price}</span>' if price else ""
    if mrp and mrp != price:
        row += f'<span class="smrp">{mrp}</span>'
    if off > 0:
        row += f'<span class="soff">{off}% OFF</span>'
    return f'<div class="sprow">{row}</div>' if row else ""


def _scene_rating(p: Dict[str, Any]) -> str:
    r = _num(p.get("rating"))
    if not r or r <= 0:
        return ""
    revs = _fmt_count(p.get("reviews"))
    revs = revs if revs and revs not in ("0",) else ""
    return f'<span class="srate"><b>★</b> {r:g}{f" · {revs} ratings" if revs else ""}</span>'


def _scene_eyebrow(p: Dict[str, Any], kick: str, num: int = 0) -> str:
    brand = str(p.get("brand") or "").strip()[:24]
    bits = ([f"{num:02d}"] if num else []) + [_store_label(p)] + ([brand] if brand and brand.lower() not in _store_label(p).lower() else []) + \
           ([kick] if kick and kick.lower() not in ("the edit", "picks") else [])
    return " · ".join(_esc(b) for b in bits[:4 if num else 3])


def _scene_panel(p: Dict[str, Any], P: Dict[str, str], kick: str, num: int = 0) -> str:
    return f"""<div class="spanel">
      <div class="seye">{_scene_eyebrow(p, kick, num)}</div>
      <div class="sname">{_esc(_clean_title(p, limit=80))}</div>
      {_scene_price_row(p)}
      <div class="sfoot">{_scene_rating(p)}<span class="scta">Follow + comment LINK → DM</span></div>
    </div>"""


def _scene_page(P, bg: str, inner: str, handle: str, dark: bool) -> str:
    body = f'<div class="scn" style="background-image:url({bg})"></div>{_scene_css(P, dark)}{inner}'
    return _page2(P, body, foot_right="SWIPE →", handle=handle)


def _scene_hero(p, cut, bg, P, handle, chip, kick, dark=False, num=0):
    """A MODEL wearing the item stands in the scene; the panel overlaps the photo's cropped edge."""
    inner = f"""<div class="scol">
      <div class="schip">{_esc(chip)}</div>
      <div class="sstage shero" style="align-items:flex-end;margin-bottom:-120px;margin-top:10px"><img src="{cut}"></div>
      {_scene_panel(p, P, kick, num)}
    </div>"""
    return _scene_page(P, bg, inner, handle, dark)


def _scene_float(p, cut, bg, P, handle, chip, kick, dark=False, num=0):
    """A WHOLE object floats, fully visible, centred in the scene with a soft shadow beneath."""
    inner = f"""<div class="scol">
      <div class="schip">{_esc(chip)}</div>
      <div class="sstage sfloat" style="align-items:center;margin:18px 0 26px"><img src="{cut}"></div>
      {_scene_panel(p, P, kick, num)}
    </div>"""
    return _scene_page(P, bg, inner, handle, dark)


def _scene_split(p, cut, bg, P, handle, chip, kick, dark=False, num=0):
    """Editorial split: the product in the scene on the left, the details in a tall card on the right."""
    inner = f"""<div class="scol">
      <div class="schip">{_esc(chip)}</div>
      <div style="flex:1 1 auto;min-height:0;display:flex;gap:26px;margin-top:22px">
        <div class="sstage sfloat" style="flex:0 0 55%;align-items:center"><img src="{cut}" style="width:100%;height:96%"></div>
        <div class="spanel" style="flex:1;justify-content:center;gap:18px;padding:34px 30px">
          <div class="seye">{_scene_eyebrow(p, kick, num)}</div>
          <div class="sname" style="font-size:44px;-webkit-line-clamp:4">{_esc(_clean_title(p, limit=80))}</div>
          <div style="display:flex;flex-direction:column;gap:10px">{_scene_price_row(p).replace('class="sprow"', 'class="sprow" style="flex-direction:column;align-items:flex-start;gap:10px"')}</div>
          {_scene_rating(p)}
          <span class="scta" style="font-size:23px">Follow + comment LINK → DM</span>
        </div>
      </div>
    </div>"""
    return _scene_page(P, bg, inner, handle, dark)


# Collage tiles (x, y, w, h in % of the collage area) — deliberately MIXED aspect ratios
# (tall / wide / square), asymmetric like a magazine mood board. Area ≈ 968 × 760 px.
_COLLAGE = {
    1: [(8, 0, 84, 100)],
    2: [(0, 0, 57, 100), (59, 14, 41, 72)],
    3: [(0, 0, 56, 100), (58, 0, 42, 55), (58, 57, 42, 43)],
    4: [(0, 0, 54, 63), (0, 65, 54, 35), (56, 0, 44, 41), (56, 43, 44, 57)],
    5: [(0, 0, 47, 60), (0, 62, 47, 38), (49, 0, 51, 35), (49, 37, 25, 63), (76, 37, 24, 63)],
    6: [(0, 0, 39, 52), (0, 54, 39, 46), (41, 0, 59, 36), (41, 38, 28, 62), (71, 38, 29, 30), (71, 70, 29, 30)],
}


def _scene_flatlay(products, cuts, bg, P, handle, *, title, subtitle, chip, dark=False, nums=None):
    """COVER — a COLLAGE of the real product cut-outs in frosted tiles of MIXED aspect ratios.
    No names and no prices (the hook is the look; details live on the product slides). Tall tiles
    get model shots / tall items, wide tiles get wide items (measured cut-out aspect). Each tile is
    numbered like its product slide, and a bold swipe bar closes the cover — built to make people swipe."""
    n = max(1, min(len(products), 6))
    tiles = _COLLAGE[n]
    aw, ah = 968.0, 760.0

    def p_aspect(p):
        m = _cut_meta(p) or {}
        try:
            return float(m.get("aspect") or 0.8)
        except (TypeError, ValueError):
            return 0.8

    order_t = sorted(range(n), key=lambda k: (tiles[k][2] * aw) / (tiles[k][3] * ah))
    order_p = sorted(range(n), key=lambda k: p_aspect(products[k]))
    pair = dict(zip(order_t, order_p))                      # narrowest tile ← narrowest product
    glass = "rgba(20,22,26,.30)" if dark else "rgba(255,255,255,.30)"
    edge = "rgba(255,255,255,.18)" if dark else "rgba(255,255,255,.55)"
    items = []
    for k, (x, y, w, h) in enumerate(tiles):
        j = pair[k]
        p, c = products[j], cuts[j]
        person = (_cut_meta(p) or {}).get("subject") == "person"
        num = (nums[j] if nums and j < len(nums) else j + 1)
        img_css = ("position:absolute;left:5%;right:5%;top:7%;bottom:0;width:90%;height:93%;object-fit:contain;"
                   "object-position:center bottom" if person else
                   "position:absolute;left:7%;top:7%;width:86%;height:86%;object-fit:contain")
        items.append(
            f'<div style="position:absolute;left:calc({x}% + 0px);top:{y}%;width:{w}%;height:{h}%;border-radius:24px;'
            f'overflow:hidden;background:{glass};border:1.5px solid {edge};backdrop-filter:blur(12px) saturate(1.15);'
            f'-webkit-backdrop-filter:blur(12px);box-shadow:0 20px 44px rgba(0,0,0,.22)">'
            f'<img src="{c}" style="{img_css};filter:drop-shadow(0 18px 18px rgba(18,12,6,.30))">'
            f'<span style="position:absolute;left:14px;top:12px;font-family:{_MONO};font-weight:700;font-size:21px;'
            f'color:#fff;background:{P["tint"]};padding:4px 11px;border-radius:9px;letter-spacing:.04em">{num:02d}</span>'
            f'</div>')
    ink = "#FFFFFFE6" if dark else P["tint"]
    inner = f"""<div class="scol">
      <div class="schip">{_esc(chip)}</div>
      <div style="text-align:center;margin-top:18px">
        <div class="stitle" style="font-size:80px">{_multiline(title)}</div>
        {f'<div class="serif" style="font-size:38px;font-style:italic;margin-top:6px;color:{ink}">{_esc(subtitle)}</div>' if subtitle else ''}
      </div>
      <div style="position:relative;flex:1 1 auto;min-height:0;margin-top:22px">{''.join(items)}</div>
      <div style="align-self:center;margin-top:22px;display:inline-flex;align-items:center;gap:14px;font-family:{_MONO};
        font-weight:700;font-size:26px;letter-spacing:.14em;text-transform:uppercase;color:#fff;background:{P['tint']};
        padding:16px 34px;border-radius:100px;box-shadow:0 14px 30px rgba(0,0,0,.22)">Swipe → {n} picks inside</div>
    </div>"""
    return _scene_page(P, bg, inner, handle, dark)


def _scene_closer(P, bg, handle, dark=False):
    """CLOSER on the post's scene: how to get the links (follow → comment LINK → bio)."""
    steps = [("➕", f"Follow {_esc(handle)}", "DMs go to followers only"),
             ("💬", "Comment “LINK”", "we’ll DM you every product link"),
             ("🔗", "Tap the link in bio", "shop all picks in the store")]
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:22px;padding:20px 26px;background:{P["chip"]};'
        f'border-radius:20px"><span style="font-size:40px">{ic}</span><div><div style="font-family:{_SANS};'
        f'font-weight:800;font-size:34px;color:{P["text"]}">{t}</div><div style="font-family:{_MONO};font-size:20px;'
        f'color:{P["muted"]};margin-top:4px">{s}</div></div></div>' for ic, t, s in steps)
    inner = f"""<div class="scol" style="justify-content:center">
      <div class="spanel" style="gap:18px;padding:46px 44px">
        <div class="seye">Get the links</div>
        <div class="sname" style="font-size:78px;-webkit-line-clamp:2">Want these?</div>
        {rows}
      </div>
    </div>"""
    return _scene_page(P, bg, inner, handle, dark)


# The Instagram-worthy per-product templates the renderer agent chooses between.
_PROD_TEMPLATES = ("spotlight", "savings", "proof", "feature", "editorial", "lookbook", "bold", "stat", "minimal")


# Human-facing details for the template picker. `best_for` explains WHEN the agent favours it,
# so a manual override is an informed choice rather than a guess.
_TMPL_INFO = {
    "spotlight": {"label": "Price-Drop Spotlight", "best_for": "Deep % discounts — leads with a huge % OFF"},
    "savings":   {"label": "Savings Hero",         "best_for": "Big ₹ saved — leads with the rupee amount"},
    "proof":     {"label": "Social-Proof",         "best_for": "Lots of ratings/reviews — trust first"},
    "feature":   {"label": "Why-We-Love-It",       "best_for": "Well-rated / badged — numbered reasons"},
    "editorial": {"label": "Editorial Hero",       "best_for": "Any product — clean magazine hero"},
    "lookbook":  {"label": "Lookbook",             "best_for": "Fashion / home — full-bleed lifestyle"},
    "bold":      {"label": "Statement",            "best_for": "Any product — big typographic name"},
    "stat":      {"label": "By-the-Numbers",       "best_for": "Strong real numbers — rating/reviews/₹ tiles"},
    "minimal":   {"label": "Minimal Hero",         "best_for": "Well-shot products — calm, premium, centered"},
}


_SCENE_INFO = {
    "scene_hero": {"label": "AI Scene · Hero", "best_for": "Model shots — the person stands in the scene"},
    "scene_float": {"label": "AI Scene · Float", "best_for": "Whole objects — shoes, bags, watches, flat garments"},
    "scene_split": {"label": "AI Scene · Split", "best_for": "Premium items — product left, editorial details right"},
}


def template_options() -> List[Dict[str, str]]:
    """The per-product layouts a user may pick manually — the AI-scene layouts (the classic
    templates are retired for product posts; cover/closer are structural)."""
    return [{"id": k, "label": v["label"], "best_for": v["best_for"]} for k, v in _SCENE_INFO.items()]


def _template_scores(p: Dict[str, Any]) -> Dict[str, float]:
    """Fit each template to the product's REAL signals (discount depth, savings ₹, rating,
    reviews, badge). BOUNDED + comparable (~16–62 each) so the variety penalties in the planner
    actually shape the mix. Higher = better suited. The renderer agent ranks these."""
    import math
    off = _discount_pct(p) or 0
    reviews = _num(p.get("reviews")) or 0
    rating = _num(p.get("rating")) or 0
    pr = _num(p.get("price")); mr = _num(p.get("orig_price") or p.get("mrp"))
    saved = (mr - pr) if (mr and pr and mr > pr) else 0
    badge = 6 if _badge_text(p) else 0
    rev_s = math.log10(reviews + 1) * 8                      # 0..~40, bounded (no runaway)
    return {
        "spotlight": 22 + min(off, 80) * 0.5,                # rises with % off
        "savings":   22 + min(saved / 200.0, 32),            # rises with ₹ saved
        "proof":     16 + rev_s * 0.55 + rating * 3,         # social proof: reviews + rating
        "feature":   26 + rating * 3 + badge,                # well-rated / badged
        "editorial": 30.0,                                   # clean neutral hero
        "lookbook":  30.0,                                   # lifestyle neutral, high visual appeal
        "bold":      28.0,                                   # typographic statement, neutral
        "stat":      18 + rev_s * 0.45 + min(off, 60) * 0.35 + rating * 2 + (10 if saved >= 200 else 0),
        "minimal":   29.0,                                   # calm centered hero, neutral
    }


def _pick_tmpl(p: Dict[str, Any]) -> str:
    """Single best-fit template for one product (used for the 1-product carousel)."""
    return max(_template_scores(p).items(), key=lambda kv: kv[1])[0]


def _plan_templates(products: List[Dict[str, Any]]) -> List[str]:
    """AGENTIC template planning (the still-set-renderer agent): score each of the six templates
    against every product, then assign the best while ENFORCING VARIETY — no template twice in a
    row and the six spread across the set — so a carousel never looks repetitive. Deterministic
    (same products → same plan), grounded in the product's own data."""
    out: List[str] = []
    counts: Dict[str, int] = {}
    for p in products:
        sc = _template_scores(p)
        prev = out[-1] if out else ""
        prev2 = out[-2] if len(out) >= 2 else ""
        def _adj(name: str, base: float) -> float:
            pen = 0.0
            if name == prev:
                pen += 100                                   # never repeat back-to-back
            if name == prev2:
                pen += 30
            pen += counts.get(name, 0) * 14                  # spread usage across the six
            return base - pen
        choice = max(sc.items(), key=lambda kv: _adj(kv[0], kv[1]))[0]
        out.append(choice)
        counts[choice] = counts.get(choice, 0) + 1
    return out


# ── the planner: product count + arc → slide specs ────────────────────────────
def plan_slides(products: List[Dict[str, Any]], *, category: str = "", arc: str = "auto",
                handle: str = "@lostinframes0605.exe", theme: str = "", cover_tags: Optional[List[str]] = None,
                templates: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Carousel-FIRST plan: every product gets its OWN full slide (no cramped grids).
    2–8 products → teaser Cover → one auto-chosen template per product → CTA closer.
    The per-product template is picked from the product's own data (deep discount →
    Spotlight, strong reviews/badge → Social-Proof, else Editorial Hero)."""
    n = len(products)
    kick = (category or "The Edit").strip().title()
    specs: List[Dict[str, Any]] = []
    if n == 0:
        return specs
    # DEALS carousel (Cuelinks offers — no product photos): brand-logo deal cards, not product slides.
    if all(p.get("deal") for p in products):
        ai_ct = next((c for c in ((p.get("cover_title") or "").strip() for p in products) if c), "")
        ai_cs = next((c for c in ((p.get("cover_subtitle") or "").strip() for p in products) if c), "")
        specs.append({"tmpl": "deal_cover", "products": products[:6], "all": products[:8],
                      "title": theme or ai_ct or "", "subtitle": ai_cs})
        for p in products[:8]:
            specs.append({"tmpl": "deal", "products": [p], "kick": kick})
        specs.append({"tmpl": "closer", "products": [], "kick": kick, "handle": handle})
        return specs[:10]
    if n == 1:                                      # single product → just its own hero
        p = products[0]
        one = (templates[0] if templates and templates[0] in _PROD_TEMPLATES else _pick_tmpl(p))
        specs.append({"tmpl": one, "products": [p], "kick": kick})
        return specs
    _tmpls = _plan_templates(products[:8])          # agentic: fit-scored + variety-enforced
    # Manual override: templates[i] replaces the agent's pick for slide i ("" / unknown = keep AI).
    if templates:
        _tmpls = [((templates[i] if i < len(templates) and templates[i] in _PROD_TEMPLATES else a))
                  for i, a in enumerate(_tmpls)]
    # teaser cover → one slide per product → closer (Instagram hard-caps at 10 slides)
    # Cover copy priority: explicit theme (user override) → AI-written cover_title from the
    # composer → deterministic fallback. Same for the subtitle.
    ai_ct = next((c for c in ((p.get("cover_title") or "").strip() for p in products) if c), "")
    ai_cs = next((c for c in ((p.get("cover_subtitle") or "").strip() for p in products) if c), "")
    cover_title = theme or ai_ct or _cover_title("", category, n, products)
    cover_sub   = ai_cs or _cover_sub(products)
    specs.append({"tmpl": "cover", "products": products[:3], "kick": kick,
                  "title": cover_title, "subtitle": cover_sub,
                  "all": products[:10], "cover_tags": cover_tags or []})
    for p, tm in zip(products[:8], _tmpls):
        specs.append({"tmpl": tm, "products": [p], "kick": kick})
    specs.append({"tmpl": "closer", "products": [], "kick": kick, "handle": handle})
    return specs[:10]


def _cover_seed(products: List[Dict[str, Any]], n: int) -> int:
    """Deterministic seed from the actual products, so the cover copy is STABLE for a given
    set of picks but VARIES across different runs (no more identical 'Fashion Edit' every time)."""
    s = "".join((p.get("asin") or p.get("product_title") or "")[:8] for p in (products or []))
    return (sum(ord(c) for c in s) + n) if s else n


def _cover_variants(category: str, n: int, products: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """The deterministic cover-headline pool (fallback when the composer sends no AI title,
    and the rotation source that guarantees a NON-REPEATING front slide)."""
    products = products or []
    cat = (category or "Picks").strip().title()
    maxoff = max((_discount_pct(p) or 0) for p in products) if products else 0
    variants = [
        f"The {cat}\nEdit", f"{n} {cat}\nPicks", f"Best {cat}\nThis Week",
        f"{cat} Worth\nBuying", f"Top {cat}\nFinds", f"{cat} We're\nLoving",
        f"The {cat}\nShortlist", f"{cat}, Sorted", f"{cat}\nWorth It",
        f"This Week's\n{cat}", f"{cat} on\nRepeat", f"Editor's {cat}\nEdit",
    ]
    if maxoff >= 50:                       # lead with the deal when there's a strong one
        variants = [f"Up to {maxoff}%\nOff {cat}", f"{cat} Deals\nWorth Grabbing"] + variants
    return variants


def _cover_title(theme: str, category: str, n: int, products: Optional[List[Dict[str, Any]]] = None) -> str:
    if theme:
        return theme
    variants = _cover_variants(category, n, products)
    return variants[_cover_seed(products or [], n) % len(variants)]


# ── front-slide uniqueness: remember recent cover headlines so the cover NEVER repeats ──────
def _cover_history_path(out_dir: Path) -> Path:
    return Path(out_dir).parent / "sk_cover_history.json"


def _norm_cov(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace("\n", " ").strip().lower())


def _load_cover_history(out_dir: Path) -> List[str]:
    try:
        data = json.loads(_cover_history_path(out_dir).read_text("utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


def _remember_cover(out_dir: Path, title: str) -> None:
    try:
        hist = _load_cover_history(out_dir)
        key = _norm_cov(title)
        if key:
            hist = [h for h in hist if h != key] + [key]
            _cover_history_path(out_dir).write_text(json.dumps(hist[-80:]), "utf-8")
    except Exception:
        pass


def _unique_cover_title(title: str, out_dir: Path, category: str, n: int,
                        products: Optional[List[Dict[str, Any]]], *, explicit: bool) -> str:
    """Return a cover headline guaranteed NOT to match a recently-used one. An explicit user
    theme is respected as-is; otherwise, on a collision we rotate through the deterministic
    variant pool until we find an unused headline (best-effort; never fails the render)."""
    if explicit or not title:
        return title
    hist = set(_load_cover_history(out_dir))
    if _norm_cov(title) not in hist:
        return title
    for cand in _cover_variants(category, n, products):     # rotate to an unused variant
        if _norm_cov(cand) not in hist:
            return cand
    return title                                            # pool exhausted → keep it (rare)


def _cover_sub(products: List[Dict[str, Any]]) -> str:
    n = len(products)
    prices = [p for p in (_num(x.get("price")) for x in products) if p]
    maxoff = max((_discount_pct(p) or 0) for p in products) if products else 0
    unit = "piece" if n == 1 else "pieces"
    subs = []
    if prices:
        subs.append(f"{n} {unit} · all under ₹{_indian_group(_round_up(int(max(prices))))}")
    if maxoff:
        subs.append(f"{n} hand-picked · up to {maxoff}% off")
    subs.append(f"{n} {unit} worth a look")
    return subs[_cover_seed(products, n) % len(subs)]


def _round_up(n: int) -> int:
    for step in (500, 1000, 2000, 5000, 10000):
        if n <= step:
            return step
    return ((n // 1000) + 1) * 1000


# ── render ────────────────────────────────────────────────────────────────────
def _render_htmls(htmls: List[str], out_dir: Path, cdn_prefix: str, slug: str) -> Dict[str, Any]:
    dest = out_dir / f"sk_{slug}"
    dest.mkdir(parents=True, exist_ok=True)
    images_local: List[str] = []
    images_cdn: List[str] = []
    rendered, error = False, None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
            page.set_default_timeout(20000)
            for i, doc in enumerate(htmls, 1):
                page.set_content(doc, wait_until="load")
                page.wait_for_timeout(450)          # let webfonts settle
                fname = f"slide_{i:02d}.png"
                fp = dest / fname
                page.screenshot(path=str(fp), clip={"x": 0, "y": 0, "width": W, "height": H})
                images_local.append(str(fp))
                images_cdn.append(f"{cdn_prefix}/sk_{slug}/{fname}")
            browser.close()
        rendered = True
    except Exception as exc:                        # noqa: BLE001
        error = str(exc)
    return {"rendered": rendered, "images": images_cdn, "local": images_local,
            "count": len(images_cdn), "dir": str(dest), "error": error}


_TMPL_LABEL = {"cover": "Teaser cover", "spotlight": "Price-Drop Spotlight",
               "savings": "Savings Hero", "editorial": "Editorial Hero", "proof": "Social-Proof",
               "feature": "Why-We-Love-It", "lookbook": "Lookbook", "closer": "Shop-the-set CTA",
               "bold": "Statement", "stat": "By-the-Numbers", "minimal": "Minimal Hero",
               "deal_cover": "Deals cover", "deal": "Deal card",
               "scene_hero": "AI Scene · Hero", "scene_float": "AI Scene · Float",
               "scene_split": "AI Scene · Split", "scene_flatlay": "AI Scene · Collage cover",
               "scene_closer": "AI Scene · Get the links"}


def render_carousel(products: List[Dict[str, Any]], *, category: str = "", out_dir: Path,
                    cdn_prefix: str, slug: str, arc: str = "auto", handle: str = "@lostinframes0605.exe",
                    theme: str = "", isolate: bool = True, palette: str = "warm",
                    track_cover: bool = True, cover_tags: Optional[List[str]] = None,
                    templates: Optional[List[str]] = None,
                    art: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full pipeline (Template System v2): plan a carousel-first sequence → prep each
    product image (staged, product-true) → render designed PNGs in the chosen palette
    (warm | sky). Returns cdn urls + local paths + the plan (with human labels).

    `track_cover` (posts only, not previews): dedupe the front-slide headline against recent
    posts and remember it, so the cover NEVER repeats across posts."""
    P = _palette(palette, category)
    specs = plan_slides(products, category=category, arc=arc, handle=handle, theme=theme,
                        cover_tags=cover_tags, templates=templates)
    if not specs:
        return {"rendered": False, "images": [], "local": [], "count": 0, "error": "no products"}

    # Front-slide uniqueness: ensure the cover headline hasn't been used recently, then record it.
    cover_spec = next((s for s in specs if s["tmpl"] == "cover"), None)
    if cover_spec is not None:
        uniq = _unique_cover_title(cover_spec.get("title", ""), out_dir, category,
                                   len(products), products, explicit=bool(theme))
        cover_spec["title"] = uniq
        if track_cover:
            _remember_cover(out_dir, uniq)

    # prep every unique product image once (data URIs), reused across slides
    img_cache: Dict[str, str] = {}
    scene: Optional[Dict[str, Any]] = None

    def prep(p: Dict[str, Any]) -> str:
        # the laptop's BiRefNet cut-out (cleaner edges, no server rembg pass) when it exists
        if scene and _src(p) in scene["cuts"]:
            return scene["cuts"][_src(p)]
        src = (p.get("image_url") or p.get("image") or "").strip()
        if not src:
            return ""
        if src not in img_cache:
            img_cache[src] = _prep_image(src, isolate=isolate) or ""
        return img_cache[src]

    # AI Art Director (agent post-art-director) — THE format for product posts: an AI scene
    # backdrop from the library + the real product cut-out. The laptop GPU's BiRefNet cut-outs are
    # used when ready; otherwise the server cuts the product out itself (never old templates).
    if art:
        scene = _scene_ctx(products, art)
        if scene:
            for p in products:
                if _src(p) and _src(p) not in scene["cuts"]:
                    cut = prep(p)
                    if cut:
                        scene["cuts"][_src(p)] = cut
        if art.get("palette") in _PALETTES:
            P = _palette(art["palette"], category)
        _apply_art(specs, products, art, scene, templates)
    chip = str((art or {}).get("chip") or "Comment “LINK” for this look")
    dark = bool(art and art.get("palette") == "noir")

    # product number = its slide order ("01", "02"…) — shown on the cover tile AND its slide
    _slide_no: Dict[int, int] = {}
    for sp in specs:
        if sp["tmpl"] in _SCENE_LAYOUTS or sp["tmpl"] in _PROD_TEMPLATES:
            _slide_no.setdefault(id(sp["products"][0]), len(_slide_no) + 1)

    htmls: List[str] = []
    for sp in specs:
        ps = sp["products"]
        t = sp["tmpl"]
        imgs = [] if t.startswith("scene_") else [prep(p) for p in ps]
        if t in _SCENE_LAYOUTS:
            fn = {"scene_hero": _scene_hero, "scene_float": _scene_float, "scene_split": _scene_split}[t]
            htmls.append(fn(ps[0], scene["cuts"][_src(ps[0])], scene["bg"], P, handle, chip, sp["kick"], dark,
                            num=_slide_no.get(id(ps[0]), 0)))
        elif t == "scene_flatlay":
            htmls.append(_scene_flatlay(ps, [scene["cuts"][_src(p)] for p in ps], scene["bg"], P, handle,
                                        title=_cover_hook(sp.get("title", ""), category, len(products)),
                                        subtitle=str((art or {}).get("concept") or "").strip(),
                                        chip=chip, dark=dark, nums=[_slide_no.get(id(p), 0) for p in ps]))
        elif t == "cover":
            cov_products = sp.get("all", ps)                 # ALL products for the collage
            cov_imgs = [prep(p) for p in cov_products]
            htmls.append(_cover2(cov_products, cov_imgs, P,
                                 title=sp.get("title", ""), subtitle=sp.get("subtitle", ""), handle=handle,
                                 cover_tags=sp.get("cover_tags", [])))
        elif t == "spotlight":
            htmls.append(_spotlight2(ps[0], imgs[0], P, handle))
        elif t == "savings":
            htmls.append(_savings2(ps[0], imgs[0], P, handle))
        elif t == "proof":
            htmls.append(_proof2(ps[0], imgs[0], P, handle))
        elif t == "feature":
            htmls.append(_feature2(ps[0], imgs[0], P, handle))
        elif t == "lookbook":
            htmls.append(_lookbook2(ps[0], imgs[0], P, handle))
        elif t == "bold":
            htmls.append(_bold2(ps[0], imgs[0], P, handle))
        elif t == "stat":
            htmls.append(_stat2(ps[0], imgs[0], P, handle))
        elif t == "minimal":
            htmls.append(_minimal2(ps[0], imgs[0], P, handle))
        elif t == "deal_cover":
            htmls.append(_deal_cover2(sp.get("products", []), P, title=sp.get("title", ""), subtitle=sp.get("subtitle", ""), handle=handle))
        elif t == "deal":
            htmls.append(_deal_card2(ps[0], P, handle))
        elif t == "scene_closer":
            htmls.append(_scene_closer(P, scene["bg"], sp.get("handle", handle), dark))
        elif t == "closer":
            htmls.append(_closer2(P, sp.get("handle", handle)))
        else:                                          # "editorial" + any fallback
            htmls.append(_editorial2(ps[0], imgs[0], P, handle))

    result = _render_htmls(htmls, out_dir, cdn_prefix, slug)
    # What the AGENT would pick per product slide (independent of any manual override) — the UI
    # badges "AI pick" on that option so a manual choice is always an informed one.
    _prod_slides = [s for s in specs if s["tmpl"] in _PROD_TEMPLATES]
    _ai = _plan_templates([s["products"][0] for s in _prod_slides]) if _prod_slides else []
    _ai_by_id = {}
    for s, a in zip(_prod_slides, _ai):
        _ai_by_id[id(s)] = a
    for s in specs:                                    # AI-scene slides: the art director's own pick
        if s.get("ai_layout"):
            _ai_by_id[id(s)] = s["ai_layout"]
    result["plan"] = [{"tmpl": s["tmpl"], "label": _TMPL_LABEL.get(s["tmpl"], s["tmpl"]),
                       "n": len(s["products"]),
                       "editable": s["tmpl"] in _PROD_TEMPLATES or s["tmpl"] in _SCENE_LAYOUTS,
                       "ai_tmpl": _ai_by_id.get(id(s)),
                       "ai_label": _TMPL_LABEL.get(_ai_by_id.get(id(s)), None),
                       "product": (s["products"][0].get("product_title") if s["products"] else None)}
                      for s in specs]
    result["palette"] = P["name"]
    result["isolated"] = isolate and _REMBG_SESSION is not None
    if art:
        result["art"] = {"concept": art.get("concept", ""), "scene": (scene or {}).get("key"),
                         "scene_ready": bool(scene), "cutouts_ready": len((scene or {}).get("cuts") or {}),
                         "source": art.get("source"), "chip": chip}
    return result


def _src(p: Dict[str, Any]) -> str:
    """The ORIGINAL photo URL (cut-outs are keyed by it; the carousel re-hosts image_url later)."""
    return (p.get("_art_src") or p.get("image_url") or p.get("image") or "").strip()


def _scene_ctx(products: List[Dict[str, Any]], art: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Load the art director's scene backdrop + the real product cut-outs (waiting a bounded
    ART_WAIT_SECS for the laptop GPU when it's online). None → classic fallbacks everywhere."""
    try:
        from app.services import scene_store
    except Exception:
        return None
    key = str(((art or {}).get("scene") or {}).get("use") or "")
    urls = [_src(p) for p in products if _src(p)]
    try:
        wait = float(os.getenv("ART_WAIT_SECS", "20"))
    except ValueError:
        wait = 20.0
    scene_store.wait_for(urls, [key] if key else [], wait)
    bgp = scene_store.backdrop_path(key) if key else None
    if not bgp:                                  # chosen scene not painted yet → least-used ready one
        ready = sorted((s for s in scene_store.library() if s.get("ready")), key=lambda s: int(s.get("uses") or 0))
        if not ready:
            return None
        key = ready[0]["key"]
        bgp = scene_store.backdrop_path(key)
    scene_store.note_scene_use(key)
    cuts = {u: scene_store.data_uri(scene_store.cutout_path(u)) for u in urls if scene_store.cutout_path(u)}
    return {"key": key, "bg": scene_store.data_uri(bgp, jpeg=True), "cuts": cuts}


def _cover_hook(title: str, category: str, n: int) -> str:
    """The collage cover's headline must never carry a price or % (the cover is price-free)."""
    t = (title or "").strip()
    if t and not re.search(r"[₹%]|\bRs\.?\b|\boff\b", t, re.I):
        return t
    cat = (category or "Picks").strip().title()
    pool = [f"The {cat}\nEdit", f"{cat} on\nRepeat", f"{cat} We're\nLoving", f"The {cat}\nShortlist"]
    return pool[n % len(pool)]


def _cut_meta(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from app.services import scene_store
        return scene_store.cutout_meta(_src(p))
    except Exception:
        return None


def _apply_art(specs: List[Dict[str, Any]], products: List[Dict[str, Any]], art: Dict[str, Any],
               scene: Optional[Dict[str, Any]], templates: Optional[List[str]]) -> None:
    """Swap the planned templates for the art director's layouts. A manual template choice always
    wins; a scene layout whose assets aren't ready falls back to its classic twin (never blocks)."""
    if not scene:                                      # no backdrop library at all → leave as planned
        return
    lays = [str((s or {}).get("layout") or "") for s in (art.get("slides") or [])]
    cuts = scene.get("cuts") or {}
    for sp in specs:
        t = sp["tmpl"]
        if t in _PROD_TEMPLATES and sp["products"]:
            p = sp["products"][0]
            i = next((k for k, q in enumerate(products) if q is p), -1)
            if _src(p) not in cuts:
                continue                                   # no image at all → nothing to place
            lay = lays[i] if 0 <= i < len(lays) else ""
            meta = _cut_meta(p) or {}
            cropped_model = meta.get("subject") == "person" and bool(meta.get("touches_bottom"))
            if lay not in _SCENE_LAYOUTS:
                lay = "scene_hero" if cropped_model else "scene_float"
            # Measured facts refine the LLM's pick: a Hero needs a photo cropped at the bottom (the
            # panel hides that edge); a model (face detected) cropped at the bottom must be a Hero,
            # never floating cut off in mid-air.
            if lay == "scene_hero" and meta and not meta.get("touches_bottom"):
                lay = "scene_float"
            elif lay != "scene_hero" and cropped_model:
                lay = "scene_hero"
            sp["ai_layout"] = lay
            if templates and 0 <= i < len(templates) and templates[i] in _SCENE_LAYOUTS:
                sp["tmpl"] = templates[i]                  # manual scene-layout pick wins
                continue
            sp["tmpl"] = lay
        elif t == "cover":
            ready = [p for p in sp.get("all", sp["products"]) if _src(p) in cuts][:6]
            if len(ready) >= 2:
                sp["tmpl"] = "scene_flatlay"
                sp["products"] = ready
        elif t == "closer":
            sp["tmpl"] = "scene_closer"
