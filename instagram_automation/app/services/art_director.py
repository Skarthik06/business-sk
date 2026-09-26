"""art_director — agent `post-art-director` (spec: affiliate-rag-bot/agents/post-art-director.agents.md).

Looks at the scraped products (their PHOTOS + JSON + the measured cut-out facts) and decides how the
Instagram post should look: the concept, the scene (pick one from the backdrop library, or write a
new Z-Image prompt for an EMPTY scene), the layout of every slide and the headline wording.

The LLM (OPENAI_MODEL, gpt-5-nano — vision capable) only DIRECTS; it never edits a product. The
product photo is always the real one, cut out and placed on top of the scene. Every field of the
LLM's answer is validated against the allowed values; anything missing or invalid is filled by the
deterministic director below, so a plan is ALWAYS returned (never blocks a post).

Tunable knobs (env): ART_DIRECTOR_ENABLED, ART_MAX_IMAGES, ART_ALLOW_NEW_SCENES, ART_MAX_TOKENS.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import settings
from app.services import scene_store

SCENE_LAYOUTS = ("scene_hero", "scene_float", "scene_split")
CLASSIC_LAYOUTS = ("spotlight", "savings", "proof", "feature", "editorial", "lookbook", "bold", "stat", "minimal")
COVER_LAYOUTS = ("scene_flatlay", "classic")
PALETTES = ("warm", "sky", "noir", "rose", "mint", "lilac", "clay", "mono")
# scene layout → classic template used when the scene assets aren't ready (never blocks a post)
FALLBACK = {"scene_hero": "lookbook", "scene_float": "spotlight", "scene_split": "feature"}

_LAYOUT_GUIDE = {
    "scene_hero": "a MODEL wearing the item, photo cropped at the waist/legs (subject=person). The person "
                  "stands in the scene; the info panel covers the cropped edge. Best for apparel on models.",
    "scene_float": "a WHOLE object (shoes, bag, watch, bottle, earbuds, a folded or flat-lay garment). "
                   "Centred, fully visible, soft shadow beneath. Best for products without a model.",
    "scene_split": "premium or feature-rich items: product in the scene on the left, editorial details "
                   "(name, price, proof) in a column on the right. Use for variety, max 1-2 per post.",
}


def _knob(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


# House LOOK (knob ART_LOOK, overridable per post from the Studio):
#   premium — THE brand look: dark noir panels + gold accents on a dark, luxurious scene the agent
#             writes for the post (fallback: the dark library scenes)
#   ai      — the agent picks any style/palette
#   <key>   — a fixed library scene (its palette)
PREMIUM_RULE = ("HOUSE STYLE = PREMIUM DARK: the scene must be a deep, moody, luxurious setting — dark "
                "stone or black marble, dark walnut, espresso/charcoal plaster, a soft warm spotlight or rim "
                "light, a hint of brass/gold — editorial, expensive, never bright or plain. Keep contrast: dark "
                "products get a warm glow/spotlight behind them so they still pop.")


def look_of(look: str = "") -> str:
    lk = (look or _knob("ART_LOOK", "ai")).strip().lower() or "ai"
    return "noir" if lk == "premium" else lk             # premium = the Noir Gold palette row


_AGENT_MD = Path(__file__).resolve().parent.parent / "agents" / "scene-prompt.agents.md"
_FALLBACK_RULES = ("EMPTY scene only (no people/products/text); centre and lower half open; name real wall "
                   "and floor materials; state light source + direction; 2-3 palette colours contrasting the "
                   "products; eye-level straight-on 35mm; 30-70 words.")


def _agent_md() -> str:
    try:
        return _AGENT_MD.read_text("utf-8")
    except Exception:
        return ""


def _prompt_rules() -> str:
    """The RULES block of agents/scene-prompt.agents.md — the live constraints for scene prompts."""
    m = re.search(r"<!-- RULES:BEGIN -->(.*?)<!-- RULES:END -->", _agent_md(), re.S)
    return (m.group(1).strip() if m else "") or _FALLBACK_RULES


def _palette_rows() -> Dict[str, Dict[str, str]]:
    """The PALETTE table of agents/scene-prompt.agents.md → {palette key: scene direction}."""
    m = re.search(r"<!-- PALETTES:BEGIN -->(.*?)<!-- PALETTES:END -->", _agent_md(), re.S)
    rows: Dict[str, Dict[str, str]] = {}
    for line in (m.group(1).splitlines() if m else []):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and cells[0] in PALETTES:
            rows[cells[0]] = {"look": cells[1], "colors": cells[2], "materials": cells[3],
                              "light": cells[4], "mood": cells[5]}
    return rows


def presets() -> Dict[str, Dict[str, str]]:
    """The STYLE PRESETS table of agents/scene-prompt.agents.md → {"/vintage": {"adds", "best_for"}}."""
    m = re.search(r"<!-- PRESETS:BEGIN -->(.*?)<!-- PRESETS:END -->", _agent_md(), re.S)
    out: Dict[str, Dict[str, str]] = {}
    for line in (m.group(1).splitlines() if m else []):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"/[a-z0-9][a-z0-9-]{1,24}", cells[0]):
            pals = [x.strip() for x in (cells[3] if len(cells) > 3 else "").split(",") if x.strip() in PALETTES]
            out[cells[0]] = {"adds": cells[1], "best_for": cells[2], "palettes": pals or list(PALETTES)}
    return out


def parse_styles(styles: Any) -> List[str]:
    """'/premium /cinematic' or ['premium', '/vintage'] → the valid presets, in order, max 3."""
    raw = styles if isinstance(styles, list) else re.split(r"[\s,]+", str(styles or ""))
    known = presets()
    out: List[str] = []
    for t in raw:
        t = str(t).strip().lower()
        t = t if t.startswith("/") else "/" + t
        if t in known and t not in out:
            out.append(t)
    return out[:3]


def _fill_presets(chosen: List[str], palette: str, analysis: List[Dict[str, Any]], products: List[Dict[str, Any]],
                  want: int = 3) -> List[str]:
    """Top up the agent's presets to `want` with presets that are coherent with the palette, ranked by
    how well their 'best for' matches the analysed products, then by least recent use."""
    ps = presets()
    words = " ".join([str(a.get(k, "")) for a in (analysis or []) for k in ("type", "style", "vibe", "material")] +
                     [str(p.get("product_title") or p.get("title") or "") for p in products]).lower()
    used = [p for r in _recent_looks(12) for p in (r.get("presets") or [])]

    def score(key: str) -> float:
        best = ps[key]["best_for"].lower()
        hits = sum(1 for w in re.findall(r"[a-z]{4,}", best) if w in words)
        return hits * 3 - used.count(key)

    out = [p for p in chosen if p in ps][:want]
    pool = sorted((k for k, v in ps.items() if palette in v["palettes"] and k not in out), key=score, reverse=True)
    for k in pool:
        if len(out) >= want:
            break
        if k != "/studio" or not out:                   # /studio is the generic filler, only if nothing else
            out.append(k)
    return out


def _row_rule(key: str, row: Dict[str, str]) -> str:
    return (f"LOOK = {row['look']} (palette '{key}'): scene colours {row['colors']}; materials {row['materials']}; "
            f"light {row['light']}; mood {row['mood']}. Write the scene INSIDE this look.")


_LOOK_LOG = scene_store.ROOT / "recent_looks.json"


def _recent_looks(n: int = 6) -> List[Dict[str, str]]:
    try:
        return json.loads(_LOOK_LOG.read_text("utf-8"))[-n:]
    except Exception:
        return []


def _remember_look(palette: str, concept: str, scene: str, used_presets: Optional[List[str]] = None) -> None:
    try:
        hist = _recent_looks(30) + [{"palette": palette, "concept": concept[:60], "scene": scene[:60],
                                     "presets": list(used_presets or [])}]
        _LOOK_LOG.write_text(json.dumps(hist[-30:]), "utf-8")
    except Exception:
        pass


def price_usd(inp: int, cached: int, out: int) -> float:
    """OpenAI list price for OPENAI_MODEL (knobs, USD per 1M tokens; gpt-5-nano defaults):
    uncached input · cached input (prompt cache) · output (includes billed reasoning tokens)."""
    p_in = float(_knob("LLM_PRICE_IN_PER_M", "0.05"))
    p_cached = float(_knob("LLM_PRICE_CACHED_PER_M", "0.005"))
    p_out = float(_knob("LLM_PRICE_OUT_PER_M", "0.40"))
    return ((max(0, inp - cached) * p_in) + (cached * p_cached) + (out * p_out)) / 1_000_000


def usage_of(resp) -> Dict[str, Any]:
    u = getattr(resp, "usage", None)
    if not u:
        return {}
    inp = int(getattr(u, "prompt_tokens", 0) or 0)
    out = int(getattr(u, "completion_tokens", 0) or 0)
    cached = int(getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
    reasoning = int(getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0)
    usd = price_usd(inp, cached, out)
    return {"model": settings.OPENAI_MODEL, "input": inp, "cached": cached, "output": out,
            "reasoning": reasoning, "total": inp + out, "usd": round(usd, 6),
            "inr": round(usd * float(_knob("USD_INR", "88")), 4)}


_SCENE_FIELDS = ("setting", "wall", "floor", "light", "props", "camera", "mood")


def _assemble_prompt(f: Dict[str, Any], style_presets: Optional[List[str]] = None) -> str:
    """Fixed field order Z-Image responds to best: setting → surfaces → light → props → palette → camera → mood."""
    parts = [str(f.get("setting") or "").strip(), str(f.get("wall") or "").strip(),
             str(f.get("floor") or "").strip(), str(f.get("light") or "").strip()]
    props = re.sub(r"\s*(at|on|near)\s+(the\s+)?(far\s+)?(left\s+|right\s+)?(edge|side)(\s+of\s+the\s+frame)?\.?$", "",
                   str(f.get("props") or "").strip(), flags=re.I)
    if props and props.lower() not in ("none", "no props", "-"):
        parts.append(f"{props} at the far edge of the frame")
    cols = [str(c).strip() for c in (f.get("colors") or []) if str(c).strip()][:3]
    if cols:
        parts.append("colour palette of " + ", ".join(cols))
    parts += [str(f.get("camera") or "eye-level, straight-on, 35mm, deep focus").strip(),
              (str(f.get("mood") or "").strip() + " mood") if f.get("mood") else "",
              *[presets().get(p, {}).get("adds", "") for p in (style_presets or [])],
              "editorial fashion photography, minimal luxury studio, open empty space in the centre and lower half"]
    return ", ".join(p for p in parts if p)[:900]


def enabled() -> bool:
    return _knob("ART_DIRECTOR_ENABLED", "1").lower() not in ("0", "false", "off", "no")


def _img_url(p: Dict[str, Any]) -> str:
    return (p.get("_art_src") or p.get("image_url") or p.get("image") or "").strip()


def _num(v: Any) -> Optional[float]:
    try:
        return float(re.sub(r"[^\d.]", "", str(v))) if v not in (None, "") else None
    except Exception:
        return None


def _is_fashion(category: str, products: List[Dict[str, Any]]) -> bool:
    words = (category + " " + " ".join((p.get("product_title") or p.get("title") or "") for p in products)).lower()
    return any(w in words for w in ("fashion", "shirt", "hoodie", "jacket", "dress", "kurta", "jean", "trouser",
                                    "tee", "t-shirt", "sweat", "saree", "top", "shoe", "sneaker", "apparel"))


# ── deterministic director (fallback + gap filler) ───────────────────────────
def _pick_scene(category: str, products: List[Dict[str, Any]], lib: List[Dict[str, Any]]) -> Optional[str]:
    ready = [s for s in lib if s.get("ready")]
    if not ready:
        return None
    cat = (category or "").lower()
    fashion = _is_fashion(category, products)
    def score(s):
        niches = [n.lower() for n in s.get("niches") or []]
        sc = 0.0
        if any(n in cat for n in niches):
            sc += 3
        if fashion and "fashion" in niches:
            sc += 2
        sc -= 0.15 * int(s.get("uses") or 0)          # rotate: less-used scenes first
        return sc
    return max(ready, key=score)["key"]


def _layout_for(meta: Optional[Dict[str, Any]], i: int, used_split: int) -> str:
    if meta and meta.get("subject") == "person" and meta.get("touches_bottom"):
        return "scene_hero"
    if i % 3 == 2 and used_split < 2:
        return "scene_split"
    return "scene_float"


def _fallback_plan(products, category, lib, metas) -> Dict[str, Any]:
    key = _pick_scene(category, products, lib)
    pal = next((s.get("palette") for s in lib if s.get("key") == key), "warm") or "warm"
    slides, splits = [], 0
    for i, p in enumerate(products):
        lay = _layout_for(metas.get(_img_url(p)), i, splits)
        splits += lay == "scene_split"
        slides.append({"layout": lay, "why": "measured photo type"})
    fashion = _is_fashion(category, products)
    return {"concept": "", "scene": {"use": key, "new": None}, "palette": pal,
            "cover": {"layout": "scene_flatlay" if len(products) >= 2 else "classic"},
            "chip": "Comment “LINK” for this look" if fashion else "Comment “LINK” for this find",
            "slides": slides, "source": "rules"}


# ── LLM director ─────────────────────────────────────────────────────────────
_SYSTEM = (
    "You are the ART DIRECTOR of a premium Indian Instagram affiliate page (fashion-first, but it also "
    "posts gadgets, beauty, home and accessories). You design scroll-stopping, aesthetic posts in the "
    "style of top fashion curator accounts: clean studio or lifestyle backdrops, real product photos, "
    "elegant typography. You NEVER alter a product — you only choose the SCENE around it and the LAYOUT. "
    "Reply with strict JSON only."
)


def _facts(products, metas) -> List[Dict[str, Any]]:
    out = []
    for i, p in enumerate(products):
        m = metas.get(_img_url(p)) or {}
        out.append({
            "i": i, "title": (p.get("product_title") or p.get("title") or "")[:90],
            "brand": p.get("brand") or "", "store": p.get("source") or "amazon",
            "price": p.get("price"), "mrp": p.get("orig_price") or p.get("mrp"),
            "discount_pct": p.get("discount_pct"), "rating": p.get("rating"), "reviews": p.get("reviews"),
            "photo": {"subject": m.get("subject", "unknown"), "cropped_at_bottom": m.get("touches_bottom"),
                      "aspect_w_over_h": m.get("aspect"), "main_colors": m.get("colors")},
        })
    return out


def _user_prompt(products, category, lib, metas, allow_new: bool, style_rule: str = "", forced: str = "") -> str:
    scenes = [{"key": s["key"], "mood": s.get("mood"), "palette": s.get("palette")} for s in lib if s.get("ready")]
    J = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731 — compact
    new_rule = ("STEP 2 — DESIGN THE SCENE for THIS post (required): from YOUR analysis in step 1, fill "
                "scene.new as STRUCTURED fields (setting, wall, floor, light, props, colors, camera, mood). It is "
                "painted by the local image model and used for this post. Also give the closest library scene "
                "in scene.use as a fallback." if allow_new else
                "STEP 2 — pick the best library scene in scene.use; set scene.new to null.")
    return (
        "You design ONE Instagram carousel for the products given at the END under === THIS POST ===.\n\n"
        "STYLE PRESETS (slash commands; each adds proven scene phrases): "
        f"{J({k: v['best_for'] + ' | palettes: ' + ','.join(v['palettes']) for k, v in presets().items()})}\n"
        "A preset may ONLY be combined with one of its listed palettes — the palette, the presets and the "
        "scene fields must describe ONE coherent scene.\n"
        "Pick the 2-3 presets that best fit YOUR product analysis in `presets` (unless the post gives "
        "USER STYLE COMMANDS — then use exactly those). The presets LEAD the scene: build the scene fields "
        "(setting, wall, floor, light, props) from the presets' materials and light; the palette row only "
        "sets the colour family. Prefer presets the recent posts did NOT use, so the feed stays fresh.\n\n"
        f"SLIDE LAYOUTS (choose one per product — these are the ONLY layouts):\n{J(_LAYOUT_GUIDE)}\n\n"
        "STEP 1 — ANALYSE EVERY PRODUCT first, from its photo AND its facts: product type, its real colours "
        "(read them from the photo), material/texture, style, and the vibe/occasion it suits. Be precise — "
        "this analysis drives everything else.\n"
        f"{new_rule}\n"
        f"{_prompt_rules()}\n"
        "STEP 3 — LAYOUTS: a model wearing it with the photo cropped at the bottom → scene_hero; a whole "
        "object → scene_float; vary layouts so the post isn't monotonous.\n"
        "RULES:\n"
        f"- palette: one of {list(PALETTES)} matching the scene (text/panel colours).\n"
        "- The cover is a collage of the products (no names/prices) on the same scene.\n"
        "- chip: a short top headline, ≤ 34 chars, e.g. 'Comment “LINK” for this look' (fashion) or "
        "'Comment “LINK” for this find'.\n"
        "- concept: ≤ 6 words naming the post's mood.\n\n"
        'Return JSON: {"analysis": [{"i": int, "type": str, "colors": [str], "material": str, "style": str, '
        '"vibe": str}], "presets": [str], "concept": str, "scene": {"use": "<library key>", "new": null | {"key": "snake_case", '
        '"palette": str, "setting": str, "wall": str, "floor": str, "light": str, "props": str, "colors": [str], '
        '"camera": str, "mood": str, "niches": [str], "tags": [str]}}, "palette": str, '
        '"cover": {"layout": str}, "chip": str, "slides": [{"i": int, "layout": str, "why": "≤ 10 words"}]}\n\n'
        # ── per-post data LAST (everything above is identical across posts → prompt-cached) ──
        f"=== THIS POST ===\nCategory: {category or 'mixed'} · {len(products)} products.\n"
        + (f"{style_rule}\n" if style_rule else "")
        + (f"USER STYLE COMMANDS: {forced} — use exactly these presets.\n" if forced else "") +
        f"PRODUCTS (scraped + measured photo facts; photos attached in this order):\n{J(_facts(products, metas))}\n"
        f"BACKDROP LIBRARY (fallback scenes):\n{J(scenes)}\n"
    )


def _thumb_uri(url: str) -> str:
    """A small JPEG data-URI of the product for the vision LLM (ART_IMG_PX, default 320 px).
    Uses the laptop cut-out (just the product, on white) when ready, else the original photo.
    Image tokens scale with pixel area, so this is the biggest token saving (~70% of input)."""
    import base64 as _b64
    import io as _io
    try:
        from PIL import Image
        px = int(_knob("ART_IMG_PX", "320"))
        cp = scene_store.cutout_path(url)
        if cp:
            im = Image.open(cp).convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            import requests as _rq
            r = _rq.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            im = Image.open(_io.BytesIO(r.content)).convert("RGB")
        im.thumbnail((px, px))
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + _b64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return url if url.startswith("http") else ""


def _call_llm(products, category, lib, metas, allow_new, style_rule: str = "", forced: str = "") -> Dict[str, Any]:
    from app.services.llm import _get_client, _is_reasoning_model
    client = _get_client()
    content: List[Dict[str, Any]] = [{"type": "text", "text": _user_prompt(products, category, lib, metas, allow_new, style_rule, forced)}]
    for p in products[: int(_knob("ART_MAX_IMAGES", "4"))]:
        u = _thumb_uri(_img_url(p))
        if u:
            content.append({"type": "image_url", "image_url": {"url": u, "detail": "low"}})
    kw: Dict[str, Any] = {"model": settings.OPENAI_MODEL, "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": content}]}
    if _is_reasoning_model(settings.OPENAI_MODEL):
        kw["max_completion_tokens"] = int(_knob("ART_MAX_TOKENS", "3000"))
        kw["reasoning_effort"] = _knob("ART_REASONING_EFFORT", "minimal")
    else:
        kw["max_tokens"] = 1200
        kw["temperature"] = 0.4
    resp = client.chat.completions.create(**kw)
    return json.loads(resp.choices[0].message.content or "{}"), usage_of(resp)


def _validate(raw: Dict[str, Any], base: Dict[str, Any], products, lib, allow_new) -> Dict[str, Any]:
    plan = json.loads(json.dumps(base))
    plan["source"] = "llm"
    keys = {s["key"] for s in lib if s.get("ready")}
    sc = raw.get("scene") or {}
    if sc.get("use") in keys:
        plan["scene"]["use"] = sc["use"]
    new = sc.get("new")
    if isinstance(new, dict) and any(new.get(k) for k in ("setting", "wall", "floor", "light")):
        new["prompt"] = _assemble_prompt(new)            # structured fields → the fixed-order prompt
    if allow_new and isinstance(new, dict) and len(str(new.get("prompt") or "")) >= 40:
        import hashlib as _h
        _k = str(new.get("key") or "").strip()
        if not _k or _k.lower() in ("snake_case", "key", "scene", "new"):
            _k = " ".join(str(new.get(f) or "") for f in ("mood", "setting"))[:40] or new["prompt"][:40]
        base_key = scene_store.scene_key(_k)[:40]
        plan["scene"]["new"] = {"key": f"{base_key}_{_h.sha1(str(new['prompt']).encode()).hexdigest()[:6]}",
                                "prompt": str(new["prompt"])[:900],
                                "palette": new.get("palette") if new.get("palette") in PALETTES else plan["palette"],
                                "mood": str(new.get("mood") or "")[:120],
                                "niches": [str(x) for x in (new.get("niches") or [])][:6],
                                "tags": [str(x) for x in (new.get("tags") or [])][:8],
                                "fields": {**{k: str(new.get(k) or "")[:160] for k in _SCENE_FIELDS},
                                           "colors": [str(c)[:24] for c in (new.get("colors") or [])][:3]}}
    plan["presets"] = parse_styles(raw.get("presets") or [])
    if raw.get("palette") in PALETTES:
        plan["palette"] = raw["palette"]
    elif plan["scene"]["use"]:
        plan["palette"] = next((s.get("palette") for s in lib if s["key"] == plan["scene"]["use"]), plan["palette"])
    if (raw.get("cover") or {}).get("layout") in COVER_LAYOUTS:
        plan["cover"]["layout"] = raw["cover"]["layout"]
    chip = str(raw.get("chip") or "").strip()
    if 6 <= len(chip) <= 40:
        plan["chip"] = chip
    if _is_fashion("", products) and "look" not in plan["chip"].lower():
        plan["chip"] = re.sub(r"(?i)\bfind\b", "look", plan["chip"])   # fashion posts sell a LOOK
    plan["concept"] = str(raw.get("concept") or "")[:60]
    plan["analysis"] = []
    for a in (raw.get("analysis") or [])[:8]:
        if isinstance(a, dict):
            plan["analysis"].append({"i": a.get("i"), "type": str(a.get("type") or "")[:40],
                                     "colors": [str(c)[:20] for c in (a.get("colors") or [])][:4],
                                     "material": str(a.get("material") or "")[:40],
                                     "style": str(a.get("style") or "")[:40], "vibe": str(a.get("vibe") or "")[:60]})
    for s in raw.get("slides") or []:
        try:
            i = int(s.get("i"))
        except Exception:
            continue
        lay = s.get("layout")
        if 0 <= i < len(plan["slides"]) and lay in SCENE_LAYOUTS:
            plan["slides"][i] = {"layout": lay, "why": str(s.get("why") or "")[:80]}
    return plan


def direct(products: List[Dict[str, Any]], category: str = "", look: str = "", styles: Any = None) -> Dict[str, Any]:
    """Return the art-direction plan for these products and QUEUE the GPU work it needs
    (cut-outs for every photo, plus any new scene). Never raises."""
    products = [p for p in (products or []) if isinstance(p, dict)][:8]
    for p in products:                                    # the laptop cuts every photo out
        scene_store.enqueue_cutout(_img_url(p))
    # let the measured photo facts (person vs object, colours) reach the LLM when the GPU is up
    scene_store.wait_for([_img_url(p) for p in products], [], float(_knob("ART_META_WAIT_SECS", "15")))
    lk = look_of(look)
    lib = scene_store.library()
    rows = _palette_rows()
    fixed = scene_store.scene(lk) if (lk != "ai" and lk not in rows) else None
    style_rule = ""
    if fixed and fixed.get("ready"):
        lib = [fixed]                                     # a chosen scene: always that backdrop
    elif lk in rows:                                      # a palette Look (premium = noir)
        same = [x for x in lib if x.get("palette") == lk]
        lib = same or lib                                 # fallbacks in the same palette
        style_rule = _row_rule(lk, rows[lk]) + (" " + PREMIUM_RULE if lk == "noir" else "")
    else:                                                 # 'ai': the agent picks the look
        recent = _recent_looks()
        _used = [p for r in recent for p in (r.get("presets") or [])]
        variety = ("RECENT POSTS used these looks (newest last): " + json.dumps(recent, ensure_ascii=False) +
                   ". Do your best, most creative work and keep the feed VARIED: choose a different "
                   "palette/scene idea than the last posts unless these products clearly demand the same "
                   "style.\n" + (f"Presets used recently (prefer others): {' '.join(_used[-8:])}.\n" if _used else "")
                   ) if recent else ""
        _forced = parse_styles(styles)
        if _forced:
            _sets = [set(presets()[p]["palettes"]) for p in _forced]
            _ok = set.intersection(*_sets) or set.union(*_sets)
            rows = {k: r for k, r in rows.items() if k in _ok} or rows
        last2 = [r.get("palette") for r in recent[-2:]]
        banned = last2[0] if (len(last2) == 2 and last2[0] and last2[0] == last2[1]) else ""
        if banned and len(rows) > 1:
            rows = {k: r for k, r in rows.items() if k != banned}
            variety += f"The last 2 posts both used '{banned}', so it is NOT available this time.\n"
        style_rule = (variety + "LOOK = AI CHOICE: pick the palette row that best flatters these products, set "
                      "`palette` to its key and write the scene inside it:\n" +
                      "\n".join(f"- {k}: {_row_rule(k, r)}" + (" " + PREMIUM_RULE if k == "noir" else "")
                                for k, r in rows.items()))
    metas = {u: scene_store.cutout_meta(u) for u in (_img_url(p) for p in products) if u}
    plan = _fallback_plan(products, category, lib, metas)
    allow_new = (_knob("ART_ALLOW_NEW_SCENES", "1").lower() not in ("0", "false", "off")) and not fixed
    err = ""
    if enabled() and settings.OPENAI_API_KEY and products:
        try:
            _raw, _usage = _call_llm(products, category, lib, metas, allow_new, style_rule,
                                     " ".join(parse_styles(styles)))
            plan = _validate(_raw, plan, products, lib, allow_new)
            plan["usage"] = _usage
        except Exception as e:                            # noqa: BLE001 — rules plan stands
            err = str(e)[:160]
    if lk in rows:                                        # the chosen Look fixes the slide palette
        plan["palette"] = lk
        if plan["scene"].get("new"):
            plan["scene"]["new"]["palette"] = lk
    elif fixed:
        plan["palette"] = fixed.get("palette") or plan["palette"]
    if lk == "ai" and rows and plan.get("palette") not in rows:
        plan["palette"] = next(iter(rows))               # a banned/unknown pick → first allowed row
        if plan["scene"].get("new"):
            plan["scene"]["new"]["palette"] = plan["palette"]
    forced = parse_styles(styles)
    _pal = plan.get("palette")
    plan["presets"] = forced or _fill_presets(
        [p for p in (plan.get("presets") or []) if _pal in presets().get(p, {}).get("palettes", [])],
        _pal, plan.get("analysis") or [], products, want=int(_knob("ART_PRESETS_PER_POST", "3")))
    _new = plan["scene"].get("new")
    if _new and _new.get("fields") and any(_new["fields"].get(k) for k in ("setting", "wall", "floor", "light")):
        _new["prompt"] = _assemble_prompt(_new["fields"], plan["presets"])
    plan["look"] = lk
    new = plan["scene"].get("new")
    if new:
        scene_store.upsert_scene(new["key"], prompt=new["prompt"], palette=new["palette"], mood=new["mood"],
                                 tags=new["tags"], niches=new["niches"])
        scene_store.enqueue_scene(new["key"], new["prompt"], seed=len(new["prompt"]))
        plan["scene"]["fallback"] = plan["scene"]["use"]  # library scene while the new one is painted
        if scene_store.worker_online() or not plan["scene"]["use"]:
            plan["scene"]["use"] = new["key"]             # THIS post gets its own AI-built scene
            plan["palette"] = new["palette"]
    plan["error"] = err
    plan["worker_online"] = scene_store.worker_online()
    _remember_look(plan.get("palette", ""), plan.get("concept", ""), str(plan["scene"].get("use") or ""),
                   plan.get("presets"))
    return plan
