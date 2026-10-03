"""AI Stylist (agent spec: app/agents/ai-stylist.agents.md).

Turns each scraped product photo (often a model wearing it) into a styled top-down flat-lay product
photo, like premium fashion Instagram posts:

  FREE  — the flat-lay SURFACE (stone / oak / linen / marble) is painted by our own Z-Image model on
          the laptop GPU or Colab, once, and reused by every post.
  PAID  — OpenAI's image model (SK_STYLIST_MODEL, default gpt-image-1-mini) builds the product into
          that surface. Quality is chosen per product: medium only when a logo/print/text must be
          kept, otherwise low.

Money guards (nothing runs by itself — only when you press "Style with AI"):
  · one cheap vision look per product (cached forever) decides the quality and writes the logo note
  · inputs are downscaled (input image tokens were ~70 % of the cost), prompts are short
  · every styled image is saved per (surface, product) and reused — never paid twice
  · daily cap + reserve floor on an exact local spend ledger (OpenAI has no balance API)
  · a fidelity check rejects an image whose product changed → that slide uses the free design
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import settings
from app.services import scene_store


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _fenv(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


MODEL = _env("SK_STYLIST_MODEL", "gpt-image-1-mini")
VISION_MODEL = _env("SK_STYLIST_VISION_MODEL", "gpt-5-nano")
DAILY_CAP = _fenv("SK_STYLIST_DAILY_CAP_USD", 0.30)
RESERVE = _fenv("SK_STYLIST_RESERVE_USD", 0.50)
CREDIT = _fenv("SK_STYLIST_CREDIT_USD", 0.0)            # balance when set up (0 = reserve check off)
CREDIT_SINCE = _env("SK_STYLIST_CREDIT_SINCE", "")      # YYYY-MM-DD the balance above was read
INPUT_PX = int(_fenv("SK_STYLIST_INPUT_PX", 640))       # long side of images sent to OpenAI
SIZE = _env("SK_STYLIST_SIZE", "1024x1536")
CHECK = _env("SK_STYLIST_FIDELITY_CHECK", "1") != "0"
# list prices per 1M tokens (override in .env if OpenAI changes them)
P_TEXT = _fenv("SK_STYLIST_PRICE_TEXT_IN", 2.0)
P_IMG_IN = _fenv("SK_STYLIST_PRICE_IMAGE_IN", 2.5)
P_IMG_OUT = _fenv("SK_STYLIST_PRICE_IMAGE_OUT", 8.0)
P_V_IN = _fenv("SK_STYLIST_PRICE_VISION_IN", 0.05)
P_V_OUT = _fenv("SK_STYLIST_PRICE_VISION_OUT", 0.40)
EST = {"low": 0.009, "medium": 0.019}                   # measured, with downscaled inputs (upper-ish)

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


def _vision(content: List[Dict[str, Any]], label: str, effort: str = "minimal") -> Dict[str, Any]:
    """One structured vision call (cheap model, little reasoning). Charged to the ledger."""
    resp = _client().chat.completions.create(
        model=VISION_MODEL, response_format={"type": "json_object"}, reasoning_effort=effort,
        messages=[{"role": "user", "content": content}])
    u = getattr(resp, "usage", None)
    usd = ((getattr(u, "prompt_tokens", 0) or 0) * P_V_IN + (getattr(u, "completion_tokens", 0) or 0) * P_V_OUT) / 1e6
    _charge("vision", usd, {"what": label})
    try:
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        return {}


def _img_part(jpeg: bytes, detail: str = "auto") -> Dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(), "detail": detail}}


# ── 1 · describe the product once (cached forever per photo) ─────────────────────────────────
def describe(src: str, raw: Optional[bytes] = None) -> Dict[str, Any]:
    path = DESC / f"{_key(src)}.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        pass
    raw = raw or _download(src)
    # Small embroidered logos are easy to miss: a sharper image + a little reasoning (≈$0.0003 more,
    # once per product) — a missed logo means low quality and a lost logo (measured).
    d = _vision([{"type": "text", "text": (
        "Product photo. Look closely at the chest, sleeves, hood, pockets and hem for ANY logo, emblem, "
        "embroidery, print or text, even tiny ones. JSON only: {\"item\": the main product in 3-6 words "
        "(e.g. \"dark green zip hoodie\"), \"worn\": true if a person/mannequin wears or holds it, \"marks\": exact "
        "description of every logo/emblem/print/text incl. shape, colour and position, or \"\" if truly none, "
        "\"box\": [x0, y0, x1, y1] fractions 0-1 of the image around the MAIN logo/print, or [] if none}")},
        _img_part(_small_jpeg(raw, 1024), "high")], "describe", effort="low")
    box = d.get("box") if isinstance(d.get("box"), list) and len(d.get("box")) == 4 else []
    d = {"item": str(d.get("item") or "product")[:60], "worn": bool(d.get("worn")), "marks": str(d.get("marks") or "")[:160],
         "box": [max(0.0, min(1.0, float(v))) for v in box] if box else []}
    d["quality"] = "medium" if d["marks"] else "low"
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
    todo, cost = [], 0.0
    for p in products:
        src = _src(p)
        if not src or styled_path(surface, src).exists():
            continue
        d = None
        try:
            d = json.loads((DESC / f"{_key(src)}.json").read_text("utf-8"))
        except Exception:
            pass
        q = (d or {}).get("quality") or "medium"                       # unknown → assume the dearer one
        cost += EST[q] + (0 if d else 0.0008) + (0.0006 if CHECK else 0)
        todo.append(src)
    return {"to_style": len(todo), "already_styled": len(products) - len(todo), "estimate_usd": round(cost, 4),
            **budget()}


def _logo_crop(raw: bytes, box: List[float]) -> Optional[bytes]:
    """The real logo, cut from the original photo (padded, upscaled) — shown to the image model."""
    if not box:
        return None
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    W, H = im.size
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return None
    pad = 0.35 * max(x1 - x0, y1 - y0)                           # a little cloth around it for context
    b = (int(max(0, x0 - pad) * W), int(max(0, y0 - pad) * H), int(min(1, x1 + pad) * W), int(min(1, y1 + pad) * H))
    if b[2] - b[0] < 12 or b[3] - b[1] < 12:
        return None
    c = im.crop(b)
    c = c.resize((256, max(32, int(256 * c.height / max(1, c.width)))), Image.LANCZOS)
    out = io.BytesIO()
    c.save(out, "JPEG", quality=90)
    return out.getvalue()


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
    raw = _download(src)
    d = describe(src, raw)
    q = d["quality"]
    _guard(EST[q])
    item = d["item"]
    logo = _logo_crop(raw, d.get("box") or [])
    prompt = (f"Image 1: product photo. Image 2: empty surface. Create a top-down flat-lay product photo of ONLY the "
              f"{item} from image 1{' (no person, body, face, hands or mannequin)' if d['worn'] else ''}, laid on the "
              f"surface from image 2 with natural folds and soft realistic shadows. Keep its exact colours and details"
              f"{'; ' + d['marks'] if d['marks'] else ''}."
              f"{' Image 3 is a close-up of its logo: reproduce exactly this design, same shape, colour and position (never another emblem).' if logo else ''}"
              f" Keep the surface decor. Photorealistic.")
    images = [("product.jpg", _small_jpeg(raw), "image/jpeg"),
              ("surface.jpg", _small_jpeg(sp.read_bytes(), INPUT_PX), "image/jpeg")]
    if logo:
        images.append(("logo.jpg", logo, "image/jpeg"))
    res = _client().images.edit(model=MODEL, prompt=prompt, quality=q, size=SIZE, n=1, image=images)
    u = getattr(res, "usage", None)
    det = getattr(u, "input_tokens_details", None)
    tin, iin, iout = (getattr(det, "text_tokens", 0) or 0), (getattr(det, "image_tokens", 0) or 0), (getattr(u, "output_tokens", 0) or 0)
    usd = (tin * P_TEXT + iin * P_IMG_IN + iout * P_IMG_OUT) / 1e6
    _charge("image", usd, {"what": item[:40], "quality": q, "tokens": [tin, iin, iout]})
    img = base64.b64decode(res.data[0].b64_json)
    from PIL import Image
    im = Image.open(io.BytesIO(img)).convert("RGB")
    if CHECK:                                                       # same product? (colour / logo / shape)
        parts = [{"type": "text", "text": (
            "Image A is the original product photo, image B the styled photo of the " + item +
            (", image C a close-up of the original logo" if logo else "") + ". JSON only: {\"same\": true only if B "
            "shows the same product: same colour, same shape" + (", and the SAME logo design as C (a different emblem, "
            "e.g. a wreath instead of antlers, means false)" if logo else "") + ", \"why\": short}")},
            _img_part(_small_jpeg(raw, 512), "low"), _img_part(_small_jpeg(img, 768 if logo else 512), "auto" if logo else "low")]
        if logo:
            parts.append(_img_part(logo, "low"))
        v = _vision(parts, "fidelity")
        if v.get("same") is False:
            return {"src": src, "status": "rejected", "why": str(v.get("why") or "")[:120], "usd": round(usd, 5)}
    im.save(out, "JPEG", quality=90, optimize=True)
    return {"src": src, "status": "styled", "quality": q, "usd": round(usd, 5)}


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
