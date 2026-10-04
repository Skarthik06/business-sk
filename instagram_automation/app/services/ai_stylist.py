"""AI Stylist (agent spec: app/agents/ai-stylist.agents.md).

Turns each scraped product photo (often a model wearing it) into a styled top-down flat-lay product
photo, like premium fashion Instagram posts:

  FREE  — the flat-lay SURFACE (stone / oak / linen / marble) is painted by our own Z-Image model on
          the laptop GPU or Colab, once, and reused by every post.
  PAID  — OpenAI's image model (SK_STYLIST_MODEL, default gpt-image-1-mini) builds the product into
          that surface — the ONLY paid step.
  FREE  — everything else is our own deterministic image analysis (app/services/stylist_cv.py):
          · item = colour (measured from the photo) + garment type (from the product title)
          · emblem/logo detector → "medium" ONLY when the product carries an emblem or symbol, else
            "low" (stripes, colour blocks and plain garments come out right at low — measured)
          · fidelity: the garment colour must survive, and the logo must be the REAL one — if the
            model drew a different emblem it is erased and the real logo is put back (no re-buy)

Money guards (nothing runs by itself — only when you press "Style with AI"):
  · inputs are downscaled (input image tokens were ~70 % of the cost), prompts are short
  · every styled image is saved per (surface, product) and reused — never paid twice
  · daily cap + reserve floor on an exact local spend ledger (OpenAI has no balance API)
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import settings
from app.services import scene_store, stylist_cv


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _fenv(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


MODEL = _env("SK_STYLIST_MODEL", "gpt-image-1-mini")
DAILY_CAP = _fenv("SK_STYLIST_DAILY_CAP_USD", 0.30)
RESERVE = _fenv("SK_STYLIST_RESERVE_USD", 0.50)
CREDIT = _fenv("SK_STYLIST_CREDIT_USD", 0.0)            # balance when set up (0 = reserve check off)
CREDIT_SINCE = _env("SK_STYLIST_CREDIT_SINCE", "")      # YYYY-MM-DD the balance above was read
INPUT_PX = int(_fenv("SK_STYLIST_INPUT_PX", 640))       # long side of images sent to OpenAI
SIZE = _env("SK_STYLIST_SIZE", "1024x1536")
CHECK = _env("SK_STYLIST_FIDELITY_CHECK", "1") != "0"     # free deterministic check + logo restore
# list prices per 1M tokens (override in .env if OpenAI changes them)
P_TEXT = _fenv("SK_STYLIST_PRICE_TEXT_IN", 2.0)
P_IMG_IN = _fenv("SK_STYLIST_PRICE_IMAGE_IN", 2.5)
P_IMG_OUT = _fenv("SK_STYLIST_PRICE_IMAGE_OUT", 8.0)
EST = {"low": 0.006, "medium": 0.016}                   # measured 0.0043 / 0.0139 with downscaled inputs (+ margin)

ROOT = scene_store.ROOT
OUT = ROOT / "styled"
DESC = ROOT / "stylist_desc"
LEDGER = ROOT / "stylist_ledger.json"
for _d in (OUT, DESC):
    _d.mkdir(parents=True, exist_ok=True)
_LOCK = threading.Lock()

# Free flat-lay surfaces (painted once by our own model, reused by every post).
SURFACES = {
    "flatlay_charcoal_stone": "top-down flat lay photography surface, dark charcoal textured stone floor tiles, a single "
                              "green monstera leaf entering from the top right corner, soft natural daylight from the "
                              "top left, completely empty center, no objects, no clothes, no text, photorealistic",
    "flatlay_light_oak": "top-down flat lay photography surface, light natural oak wood planks, a small dried eucalyptus "
                         "sprig in the top left corner, soft natural daylight, completely empty center, no objects, "
                         "no clothes, no text, photorealistic",
    "flatlay_beige_linen": "top-down flat lay photography surface, crumpled warm beige linen fabric, soft natural "
                           "daylight from the top left, completely empty center, no objects, no clothes, no text, "
                           "photorealistic",
    "flatlay_white_marble": "top-down flat lay photography surface, white marble with soft grey veins, a small green "
                            "olive branch in the bottom right corner, soft daylight, completely empty center, no "
                            "objects, no clothes, no text, photorealistic",
}
DARK_SURFACES = {"flatlay_charcoal_stone"}


class Refused(Exception):
    """A money guard said no (daily cap / reserve / no key) — nothing was spent."""


# ── ledger: every cent, exactly ────────────────────────────────────────────────────────────────
def _ledger() -> List[Dict[str, Any]]:
    try:
        return json.loads(LEDGER.read_text("utf-8"))
    except Exception:
        return []


def _charge(kind: str, usd: float, detail: Dict[str, Any]) -> None:
    with _LOCK:
        rows = _ledger()
        rows.append({"t": time.time(), "day": time.strftime("%Y-%m-%d"), "kind": kind, "usd": round(usd, 6), **detail})
        tmp = LEDGER.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-5000:]), "utf-8")
        tmp.replace(LEDGER)


def budget() -> Dict[str, Any]:
    rows = _ledger()
    today = time.strftime("%Y-%m-%d")
    spent_today = sum(r["usd"] for r in rows if r.get("day") == today)
    since = sum(r["usd"] for r in rows if not CREDIT_SINCE or r.get("day", "") >= CREDIT_SINCE)
    left = round(CREDIT - since, 4) if CREDIT else None
    return {"model": MODEL, "today_usd": round(spent_today, 4), "daily_cap_usd": DAILY_CAP,
            "today_left_usd": round(max(0.0, DAILY_CAP - spent_today), 4), "total_usd": round(sum(r["usd"] for r in rows), 4),
            "credit_left_usd": left, "reserve_usd": RESERVE, "images": sum(1 for r in rows if r.get("kind") == "image"),
            "enabled": bool(settings.OPENAI_API_KEY)}


def _guard(need: float) -> None:
    if not settings.OPENAI_API_KEY:
        raise Refused("OPENAI_API_KEY is not set")
    b = budget()
    if b["today_usd"] + need > DAILY_CAP + 1e-9:
        raise Refused(f"daily cap reached (${b['today_usd']:.3f} of ${DAILY_CAP:.2f} today)")
    if b["credit_left_usd"] is not None and b["credit_left_usd"] - need < RESERVE:
        raise Refused(f"keeping the ${RESERVE:.2f} reserve (≈${b['credit_left_usd']:.2f} credit left)")


# ── helpers ────────────────────────────────────────────────────────────────────────────────────
def _client():
    from app.services.llm import _get_client  # same key + client as the rest of the Studio
    return _get_client()


def _small_jpeg(raw: bytes, px: int = INPUT_PX, q: int = 85) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((px, px), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q, optimize=True)
    return b.getvalue()


def _download(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]


# ── 1 · read the product (free, deterministic, cached per photo) ──────────────────────────────
_TYPES = [  # (title pattern, item name) — most specific first
    # clothing first: "Sunscreen Jacket" is a jacket, "Kurta with Dupatta" a kurta
    (r"kurti|kurta", "kurta"), (r"lehenga", "lehenga"), (r"track ?suit", "tracksuit"),
    (r"pyjama|pajama|night ?suit|nightwear", "nightwear"),
    (r"(half|quarter)[- ]?zip.*hood|hood\w*.*(half|quarter)[- ]?zip", "half-zip hoodie"),
    (r"(half|quarter)[- ]?zip", "half-zip sweatshirt"), (r"zip\w*[- ]?(up )?hood|hood(ie|y|ed sweat)\w*.*\bzip", "zip hoodie"),
    (r"hoodie|hoody|huddy|hooded (sweat ?shirt|pullover|top)", "hoodie"), (r"jacket|bomber|windcheater", "jacket"),
    (r"hooded", "hoodie"), (r"sweat ?shirt", "sweatshirt"), (r"polo", "polo t-shirt"), (r"t[- ]?shirt|\btee\b", "t-shirt"),
    (r"overshirt|shacket", "overshirt"), (r"\bshirt", "shirt"), (r"blazer", "blazer"),
    (r"cardigan", "cardigan"), (r"sweater|pullover|jumper", "sweater"), (r"kurta", "kurta"), (r"dress", "dress"),
    (r"co[- ]?ord", "co-ord set"), (r"jogger|track ?pant", "joggers"), (r"jeans|denim", "jeans"), (r"chino", "chinos"),
    (r"cargo", "cargo pants"), (r"trouser|pant", "trousers"), (r"shorts", "shorts"), (r"skirt", "skirt"), (r"saree|\bsari\b", "saree"),
    (r"\btop\b", "top"), (r"sneaker|shoe|trainer", "sneakers"), (r"sandal", "sandals"), (r"smart ?watch|fitness band", "smartwatch"), (r"watch", "watch"),
    (r"backpack", "backpack"), (r"\bbag\b|tote|sling", "bag"), (r"wallet", "wallet"), (r"\bcap\b|\bhat\b", "cap"),
    (r"sunglass", "sunglasses"), (r"\bbelt\b", "belt"),
    (r"round neck|half sleeve|drop shoulder|oversized", "t-shirt"), (r"dupatta|\bstole\b", "dupatta"),
    # then everything else
    (r"smart ?watch|fitness band", "smartwatch"), (r"ear ?buds|\btws\b|earphone|neckband|airdopes", "wireless earbuds"),
    (r"headphone|headset", "headphones"), (r"speaker|soundbar", "speaker"), (r"power ?bank", "power bank"),
    (r"charger|charging cable|usb cable", "charger"), (r"keyboard", "keyboard"), (r"\bmouse\b", "mouse"),
    (r"phone (case|cover)|back cover", "phone case"), (r"perfume|eau de|fragrance|body mist|deodorant|\battar\b", "perfume"),
    (r"lipstick|lip ?gloss|kajal|eyeliner|foundation|makeup", "makeup product"),
    (r"serum|face ?wash|moisturi[sz]er|sunscreen|lotion|shampoo|conditioner|hair oil|face cream", "skincare product"),
    (r"photo ?frame|picture frame", "photo frame set"), (r"wall (decor|hanging|art)|painting|poster", "wall decor"),
    (r"fairy lights|string lights|led lights|curtain lights", "string lights"), (r"\blamp\b", "lamp"),
    (r"\bclock\b", "clock"), (r"mirror", "mirror"), (r"candle", "candle"), (r"planter|plant pot|vase", "planter"),
    (r"cushion|pillow", "cushion"), (r"bed ?sheet|bedspread|comforter|blanket|dohar", "bedsheet"),
    (r"\bcurtains?\b", "curtains"), (r"\bmug\b|coffee cup", "mug"), (r"water bottle|\bflask\b", "bottle"),
    (r"earring|jhumka", "earrings"), (r"necklace|pendant", "necklace"), (r"bracelet|bangle", "bracelet"),
    (r"heels|stiletto", "heels"), (r"\bboots?\b", "boots"), (r"loafer", "loafers"), (r"slipper|flip[- ]?flop|slider", "slippers"),
]
_PRINT_WORDS = re.compile(r"\b(print(ed)?|graphic|typograph\w*|slogan|logo|embroider\w*|badge|patch|marvel|disney|"
                          r"spider[- ]?man|batman|superman|avengers|anime|naruto|mickey|cartoon|character|artwork)\b", re.I)
_PLAIN_WORDS = re.compile(r"\b(all[- ]?over|stripe[ds]?|check(ed|s)?|plaid|floral|camo\w*|polka)\b", re.I)   # pattern ≠ emblem → low is fine


# clothing lies flat with folds; everything else (watch, bottle, frame…) is placed at its real proportions
APPAREL = {"half-zip hoodie", "half-zip sweatshirt", "zip hoodie", "hoodie", "sweatshirt", "polo t-shirt", "t-shirt",
           "overshirt", "shirt", "blazer", "jacket", "cardigan", "sweater", "kurta", "dress", "co-ord set", "joggers",
           "jeans", "chinos", "cargo pants", "trousers", "shorts", "skirt", "saree", "top", "lehenga", "dupatta",
           "nightwear", "tracksuit"}


def _title(p: Dict[str, Any]) -> str:
    return (p.get("product_title") or p.get("title") or p.get("name") or "").strip()


def item_type(title: str) -> str:
    """The product type from the listing title ('' when unknown)."""
    t = (title or "").lower()
    for pat, name in _TYPES:
        if re.search(pat, t):
            return name
    return ""


def item_name(title: str, colour: str) -> str:
    """'<measured colour> <type from the title>' — e.g. 'dark green zip hoodie'. Unknown type →
    'black product' (never random title words: this text goes into the image model's prompt)."""
    return f"{colour} {item_type(title) or 'product'}".strip()


def _photo(src: str):
    """(photo bytes, our cut-out PNG or None) — the cut-out (original pixels + mask) saves a download."""
    cp = scene_store.cutout_path(src)
    cut = cp.read_bytes() if cp else None
    return (cut or _download(src)), cut


def describe(src: str, title: str = "", raw: Optional[bytes] = None, cut: Optional[bytes] = None) -> Dict[str, Any]:
    """item / colour / quality (medium ONLY for an emblem or symbol) — no AI call, cached per photo."""
    path = DESC / f"{_key(src)}.cv.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        pass
    if raw is None:
        raw, cut = _photo(src)
    try:
        a = stylist_cv.analyse(raw, cut)
        d = {k: v for k, v in a.items() if not k.startswith("_")}
    except Exception as e:  # noqa: BLE001 — unreadable photo: be safe, keep details
        d = {"colour": "", "quality": "medium", "box": [], "error": str(e)[:80]}
    d["item"] = item_name(title, d.get("colour") or "")
    d["apparel"] = item_type(title) in APPAREL
    # a chest-wide graphic has no plain fabric around it, so the pixel detector can't call it a
    # mark — but the listing title says so ("Printed", "Graphic", "Marvel" …) → medium keeps it
    title_print = bool(_PRINT_WORDS.search(title or "")) and not _PLAIN_WORDS.search(title or "")
    if d.get("quality") == "low" and title_print:
        d["quality"] = "medium"
    d["why"] = ("emblem/logo found in the photo" if d.get("box") else "print/graphic in the title" if title_print
                else "unreadable photo — kept details" if d.get("error") else "plain — no emblem")
    path.write_text(json.dumps(d), "utf-8")
    return d


# ── 2 · the free surface (our own model) ──────────────────────────────────────────────────────
def pick_surface(post_id: str, dark: bool) -> str:
    keys = [k for k in SURFACES if (k in DARK_SURFACES) == dark] or list(SURFACES)
    return keys[int(_key(post_id or "x"), 16) % len(keys)]


def surface_path(key: str, wait_secs: float = 0) -> Optional[Path]:
    """The painted surface; queues a FREE paint on the laptop GPU / Colab if it doesn't exist yet."""
    p = scene_store.backdrop_path(key)
    if p:
        return p
    scene_store.upsert_scene(key, prompt=SURFACES[key], palette="", mood="flat lay surface", tags=["flatlay"])
    scene_store.enqueue_scene(key, SURFACES[key], seed=11)
    if wait_secs:
        scene_store.wait_for([], [key], wait_secs)
    return scene_store.backdrop_path(key)


# ── 3 · style one product (paid) ──────────────────────────────────────────────────────────────
def styled_path(surface: str, src: str) -> Path:
    return OUT / f"{_key(surface, src)}.jpg"


def estimate(products: List[Dict[str, Any]], surface: str) -> Dict[str, Any]:
    todo, cost, qs = [], 0.0, []
    for p in products:
        src = _src(p)
        if not src or styled_path(surface, src).exists():
            continue
        try:
            q = describe(src, _title(p)).get("quality") or "medium"    # free → the estimate is exact per product
        except Exception:  # noqa: BLE001
            q = "medium"                                               # unreadable now → assume the dearer one
        cost += EST[q]
        todo.append(src)
        qs.append(q)
    return {"to_style": len(todo), "already_styled": len(products) - len(todo), "estimate_usd": round(cost, 4),
            "medium": qs.count("medium"), "low": qs.count("low"), **budget()}


def _src(p: Dict[str, Any]) -> str:
    return (p.get("_art_src") or p.get("image_url") or p.get("image") or "").strip()


def style_product(p: Dict[str, Any], surface: str) -> Dict[str, Any]:
    src = _src(p)
    out = styled_path(surface, src)
    if out.exists():
        return {"src": src, "status": "cached", "usd": 0.0}
    sp = surface_path(surface)
    if not sp:
        return {"src": src, "status": "no_surface"}
    raw, cut = _photo(src)
    d = describe(src, _title(p), raw, cut)
    q = d["quality"]
    _guard(EST[q])
    a = stylist_cv.analyse(raw, cut) if (q == "medium" or CHECK) else None   # pixels + logo mask (free, <1 s)
    logo = stylist_cv.logo_png(a) if a and q == "medium" else None
    side = {"image-right": "on the right side of the chest as seen in image 1",
            "image-left": "on the left side of the chest as seen in image 1"}.get((a or {}).get("side"), "at the same spot as in image 1")
    prompt = (f"Image 1: product photo. Image 2: empty surface. Create a top-down flat-lay product photo of ONLY the "
              f"{d.get('item') or 'product'} from image 1 (no person, body, face, hands or mannequin), "
              + ("laid on the surface from image 2 with natural folds and soft realistic shadows. "
                 if d.get("apparel", True) else
                 "placed on the surface from image 2 at its real proportions, arranged naturally with soft realistic shadows. ")
              + "Keep its exact colours, shape, panels, stripes and details."
              f"{' Light the dark fabric so its folds, seams and texture stay clearly visible (not flat pure black).' if (a or d).get('garment_lab', [50])[0] < 25 else ''}"
              f"{f' Image 3 is a close-up of its small logo: reproduce exactly this design, {side}.' if logo else ''}"
              f" Keep the surface decor. No added text, price tags, labels or watermarks (the post adds its own details). Photorealistic.")
    images = [("product.jpg", _small_jpeg(raw), "image/jpeg"),
              ("surface.jpg", _small_jpeg(sp.read_bytes(), INPUT_PX), "image/jpeg")]
    if logo:
        images.append(("logo.jpg", logo, "image/jpeg"))
    res = _client().images.edit(model=MODEL, prompt=prompt, quality=q, size=SIZE, n=1, image=images)
    u = getattr(res, "usage", None)
    det = getattr(u, "input_tokens_details", None)
    tin, iin, iout = (getattr(det, "text_tokens", 0) or 0), (getattr(det, "image_tokens", 0) or 0), (getattr(u, "output_tokens", 0) or 0)
    usd = (tin * P_TEXT + iin * P_IMG_IN + iout * P_IMG_OUT) / 1e6
    _charge("image", usd, {"what": (d.get("item") or "")[:40], "quality": q, "tokens": [tin, iin, iout]})
    img = base64.b64decode(res.data[0].b64_json)
    note: Dict[str, Any] = {}
    if CHECK and a is not None:                                     # free: colour survived? the REAL logo?
        chk = stylist_cv.check_and_fix(img, a)
        if not chk["ok"]:
            return {"src": src, "status": "rejected", "why": chk["why"], "usd": round(usd, 5)}
        img = chk.get("image") or img
        note = {"logo": chk.get("logo", "-")}
        lift = stylist_cv.lift_dark_garment(img, a["garment_lab"])         # black cloth: bring the folds back
        if lift.get("lifted"):
            img = lift["image"]
            note["lifted"] = True
    from PIL import Image
    Image.open(io.BytesIO(img)).convert("RGB").save(out, "JPEG", quality=90, optimize=True)
    return {"src": src, "status": "styled", "quality": q, "usd": round(usd, 5), **note}


def style_post(products: List[Dict[str, Any]], post_id: str, dark: bool) -> Dict[str, Any]:
    """Style every product of a post (sequential: each one re-checks the money guards)."""
    surface = pick_surface(post_id, dark)
    if not surface_path(surface, wait_secs=240):
        return {"ok": False, "error": "The free background is still being painted — start the laptop GPU or Colab, "
                                      "then press Style with AI again (nothing was charged).", **budget()}
    results = []
    for p in products:
        if not _src(p):
            continue
        try:
            results.append(style_product(p, surface))
        except Refused as e:
            results.append({"src": _src(p), "status": "refused", "why": str(e)})
            break                                                   # the guard said stop → stop
        except Exception as e:                                      # noqa: BLE001 — one product never sinks the post
            results.append({"src": _src(p), "status": "failed", "why": f"{type(e).__name__}: {str(e)[:120]}"})
    styled = {r["src"]: str(styled_path(surface, r["src"])) for r in results if r["status"] in ("styled", "cached")}
    if post_id:
        cur = (scene_store.plan_get(post_id) or {}).get("styled") or {}
        scene_store.plan_update(post_id, styled={**cur, **styled}, styled_surface=surface)
    return {"ok": True, "surface": surface, "results": results, "styled": len(styled),
            "spent_usd": round(sum(r.get("usd", 0) for r in results), 5), **budget()}


def styled_for(post_id: str) -> Dict[str, str]:
    """{original photo URL: styled jpg path} for a post (only files that still exist)."""
    m = (scene_store.plan_get(post_id) or {}).get("styled") or {}
    return {k: v for k, v in m.items() if v and Path(v).exists()}
