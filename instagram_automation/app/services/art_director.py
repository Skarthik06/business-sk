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
    return (look or _knob("ART_LOOK", "premium")).strip().lower() or "premium"


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


def _user_prompt(products, category, lib, metas, allow_new: bool, style_rule: str = "") -> str:
    scenes = [{"key": s["key"], "mood": s.get("mood"), "palette": s.get("palette"), "niches": s.get("niches"),
               "tags": s.get("tags"), "uses": s.get("uses", 0)} for s in lib if s.get("ready")]
    new_rule = ("STEP 2 — WRITE THE SCENE PROMPT (required): from YOUR analysis in step 1, write ONE new "
                "scene for THIS post in scene.new — it is painted by the image model and used for this post. "
                "Also give the closest library scene in scene.use as a fallback." if allow_new else
                "STEP 2 — pick the best library scene in scene.use; set scene.new to null.")
    return (
        f"Design ONE Instagram carousel for category '{category or 'mixed'}' with {len(products)} products.\n\n"
        f"PRODUCTS (scraped facts + measured photo facts; the photos are attached in the same order):\n"
        f"{json.dumps(_facts(products, metas), ensure_ascii=False)}\n\n"
        f"BACKDROP LIBRARY (fallback scenes):\n{json.dumps(scenes, ensure_ascii=False)}\n\n"
        f"SLIDE LAYOUTS (choose one per product — these are the ONLY layouts):\n{json.dumps(_LAYOUT_GUIDE)}\n\n"
        "STEP 1 — ANALYSE EVERY PRODUCT first, from its photo AND its facts: product type, its real colours "
        "(read them from the photo), material/texture, style, and the vibe/occasion it suits. Be precise — "
        "this analysis drives everything else.\n"
        f"{new_rule}\n"
        "  The scene must flatter THESE products: a setting where they'd naturally be styled for their vibe, "
        "with a colour palette that CONTRASTS with the products' main colours so they pop (dark products → a "
        "light/warm scene; light products → a deeper scene; never the same colour as the product). "
        "Describe an EMPTY photographic scene only — no people, no products, no text, no logos — with open "
        "space in the centre and lower half, real materials, surfaces and light direction (e.g. 'warm taupe "
        "limewash wall, pale oak floor, soft window light from the left, a linen-covered bench at the far "
        "edge'). 30-70 words.\n"
        + (f"{style_rule}\n" if style_rule else "") +
        "STEP 3 — LAYOUTS: a model wearing it with the photo cropped at the bottom → scene_hero; a whole "
        "object → scene_float; vary layouts so the post isn't monotonous.\n"
        "RULES:\n"
        f"- palette: one of {list(PALETTES)} matching the scene (text/panel colours).\n"
        "- The cover is a collage of the products (no names/prices) on the same scene.\n"
        "- chip: a short top headline, ≤ 34 chars, e.g. 'Comment “LINK” for this look' (fashion) or "
        "'Comment “LINK” for this find'.\n"
        "- concept: ≤ 6 words naming the post's mood.\n\n"
        'Return JSON: {"analysis": [{"i": int, "type": str, "colors": [str], "material": str, "style": str, '
        '"vibe": str}], "concept": str, "scene": {"use": "<library key>", "new": null | {"key": "snake_case", '
        '"prompt": str, "palette": str, "mood": str, "niches": [str], "tags": [str]}}, "palette": str, '
        '"cover": {"layout": str}, "chip": str, "slides": [{"i": int, "layout": str, "why": "≤ 10 words"}]}'
    )


def _call_llm(products, category, lib, metas, allow_new, style_rule: str = "") -> Dict[str, Any]:
    from app.services.llm import _get_client, _is_reasoning_model
    client = _get_client()
    content: List[Dict[str, Any]] = [{"type": "text", "text": _user_prompt(products, category, lib, metas, allow_new, style_rule)}]
    for p in products[: int(_knob("ART_MAX_IMAGES", "4"))]:
        u = _img_url(p)
        if u.startswith("http"):
            content.append({"type": "image_url", "image_url": {"url": u, "detail": "low"}})
    kw: Dict[str, Any] = {"model": settings.OPENAI_MODEL, "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": content}]}
    if _is_reasoning_model(settings.OPENAI_MODEL):
        kw["max_completion_tokens"] = int(_knob("ART_MAX_TOKENS", "3000"))
        kw["reasoning_effort"] = _knob("ART_REASONING_EFFORT", "low")
    else:
        kw["max_tokens"] = 1200
        kw["temperature"] = 0.4
    resp = client.chat.completions.create(**kw)
    return json.loads(resp.choices[0].message.content or "{}")


def _validate(raw: Dict[str, Any], base: Dict[str, Any], products, lib, allow_new) -> Dict[str, Any]:
    plan = json.loads(json.dumps(base))
    plan["source"] = "llm"
    keys = {s["key"] for s in lib if s.get("ready")}
    sc = raw.get("scene") or {}
    if sc.get("use") in keys:
        plan["scene"]["use"] = sc["use"]
    new = sc.get("new")
    if allow_new and isinstance(new, dict) and len(str(new.get("prompt") or "")) >= 40:
        import hashlib as _h
        base_key = scene_store.scene_key(str(new.get("key") or new["prompt"][:40]))[:40]
        plan["scene"]["new"] = {"key": f"{base_key}_{_h.sha1(str(new['prompt']).encode()).hexdigest()[:6]}",
                                "prompt": str(new["prompt"])[:900],
                                "palette": new.get("palette") if new.get("palette") in PALETTES else plan["palette"],
                                "mood": str(new.get("mood") or "")[:120],
                                "niches": [str(x) for x in (new.get("niches") or [])][:6],
                                "tags": [str(x) for x in (new.get("tags") or [])][:8]}
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


def direct(products: List[Dict[str, Any]], category: str = "", look: str = "") -> Dict[str, Any]:
    """Return the art-direction plan for these products and QUEUE the GPU work it needs
    (cut-outs for every photo, plus any new scene). Never raises."""
    products = [p for p in (products or []) if isinstance(p, dict)][:8]
    for p in products:                                    # the laptop cuts every photo out
        scene_store.enqueue_cutout(_img_url(p))
    # let the measured photo facts (person vs object, colours) reach the LLM when the GPU is up
    scene_store.wait_for([_img_url(p) for p in products], [], float(_knob("ART_META_WAIT_SECS", "15")))
    lk = look_of(look)
    lib = scene_store.library()
    fixed = scene_store.scene(lk) if lk not in ("premium", "ai") else None
    style_rule = ""
    if fixed and fixed.get("ready"):
        lib = [fixed]                                     # a chosen scene: always that backdrop
    elif lk == "premium":
        dark = [s for s in lib if s.get("palette") == "noir"]
        lib = dark or lib                                 # premium fallbacks = the dark scenes
        style_rule = PREMIUM_RULE
    metas = {u: scene_store.cutout_meta(u) for u in (_img_url(p) for p in products) if u}
    plan = _fallback_plan(products, category, lib, metas)
    allow_new = (_knob("ART_ALLOW_NEW_SCENES", "1").lower() not in ("0", "false", "off")) and not fixed
    err = ""
    if enabled() and settings.OPENAI_API_KEY and products:
        try:
            plan = _validate(_call_llm(products, category, lib, metas, allow_new, style_rule), plan, products, lib, allow_new)
        except Exception as e:                            # noqa: BLE001 — rules plan stands
            err = str(e)[:160]
    if lk == "premium":                                   # the brand look: noir panels + gold accents
        plan["palette"] = "noir"
        if plan["scene"].get("new"):
            plan["scene"]["new"]["palette"] = "noir"
    elif fixed:
        plan["palette"] = fixed.get("palette") or plan["palette"]
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
    return plan
