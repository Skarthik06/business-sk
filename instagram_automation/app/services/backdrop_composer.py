"""Backdrop Composer agent (spec: app/agents/backdrop-composer.agents.md).

A UNIQUE background for every post — composed by our own code (no LLM, no tokens) and painted by
our own image model (Z-Image on the laptop GPU / Colab, free).

  · compose  — picks one option from each ingredient list (surface / wall, floor, colour, prop or set
               piece, corner, light). Options used in the last few posts are penalised and an exact
               combination used in the last 120 posts is never repeated.
  · seed     — every paint gets its own random seed (the old code used len(prompt) → similar
               prompts painted near-identical images).
  · verify   — every finished backdrop is fingerprinted (64-bit perceptual hash + average colour);
               one that looks like a recent backdrop is repainted with a new composition, once.

Two modes:
  flatlay — top-down surfaces for the AI Stylist (dark or light).
  studio  — eye-level studio sets for AI-scene posts, coloured by the post's palette row.
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services import scene_store

HIST = scene_store.ROOT / "backdrop_history.json"
_LOCK = threading.Lock()
KEEP = 400                      # remembered compositions
NO_REPEAT = 120                 # an exact combination never repeats within this many posts

# ── flat-lay ingredients (top-down) ─────────────────────────────────────────────────────────────
FLAT_DARK = [
    "dark charcoal slate tiles", "black terrazzo with fine grey chips", "dark walnut wood planks",
    "smoked oak boards", "raw dark concrete with fine hairline cracks", "black marble with soft white veins",
    "deep navy washed linen", "dark olive green linen", "rough basalt stone slab", "graphite brushed plaster",
    "espresso brown leather-textured surface", "dark burnt timber boards",
]
FLAT_LIGHT = [
    "light natural oak planks", "crumpled warm beige linen", "white marble with soft grey veins",
    "cream travertine stone", "pale terrazzo with soft pastel chips", "sand-coloured limewash plaster",
    "light washed concrete", "ivory cotton canvas", "pale birch plywood", "blush pink limewash plaster",
    "sage green linen", "terracotta clay tiles", "light grey felt", "warm sandstone slab",
]
FLAT_PROPS = [
    "a single monstera leaf entering from the {c} corner", "a small eucalyptus sprig in the {c} corner",
    "a few stems of dried pampas grass in the {c} corner", "an olive branch in the {c} corner",
    "the soft shadow of a palm frond falling across the {c} corner", "a small handmade ceramic dish in the {c} corner",
    "two smooth river stones in the {c} corner", "a neatly folded linen napkin in the {c} corner",
    "a sprig of dried lavender in the {c} corner", "a few scattered dried autumn leaves in the {c} corner",
    "a small succulent in a clay pot in the {c} corner", "a rolled straw hat brim just entering the {c} corner",
    "no props, a clean minimal surface",
]
FLAT_LIGHT_SRC = [
    "soft natural daylight from the top left", "warm golden-hour light with long soft shadows from the right",
    "dappled window light with soft leaf shadows", "cool overcast diffuse light, even and calm",
    "crisp afternoon sun with a defined window-pane shadow", "soft studio light, even and nearly shadowless",
]
CORNERS = ["top right", "top left", "bottom right", "bottom left"]

# ── studio ingredients (eye-level, plain and elegant — no invented rooms) ──────────────────────
STUDIO_WALL = [
    "{col} seamless paper sweep", "{col} limewash plaster wall", "{col} microcement wall",
    "{col} fluted plaster panel wall", "{col} soft gradient backdrop", "{col} brushed concrete wall",
    "{col} linen-textured backdrop", "{col} plaster wall with a shallow arched niche",
    "{col} ribbed travertine wall", "{col} matte wall curving softly into the floor",
]
STUDIO_FLOOR = [
    "matching seamless floor", "pale honed stone floor", "honed travertine floor", "polished microcement floor",
    "light oak floor", "warm terrazzo floor", "soft {col} matte floor",
]
STUDIO_PIECE = [
    "a low round plinth", "two stacked stone blocks", "a single dried pampas stem in a slim vase",
    "a sculptural ceramic vase", "a softly draped linen fabric fold", "a small potted olive tree",
    "the soft shadow of a window frame", "the soft shadow of a palm leaf", "a low travertine bench",
    "no set piece",
]
STUDIO_LIGHT = [   # described as light FALLING on the set — never the lamp itself (Z-Image paints fixtures)
    "soft light falling from the left", "warm light from the side casting a long soft shadow",
    "soft light from above with a gentle vignette", "dappled window light across the wall",
    "low raking golden light across the wall", "cool diffused light with crisp soft shadows",
    "a soft glow on the wall behind the centre",
]


# ── history ─────────────────────────────────────────────────────────────────────────────────────
def _load() -> Dict[str, Any]:
    try:
        return json.loads(HIST.read_text("utf-8"))
    except Exception:
        return {"items": [], "by_post": {}}


def _save(d: Dict[str, Any]) -> None:
    d["items"] = d.get("items", [])[-KEEP:]
    if len(d.get("by_post", {})) > 2000:
        d["by_post"] = dict(list(d["by_post"].items())[-1500:])
    tmp = HIST.with_suffix(".tmp")
    tmp.write_text(json.dumps(d), "utf-8")
    tmp.replace(HIST)


def _pick(rng: random.Random, options: List[str], recent: List[str], window: int) -> str:
    """Prefer options not used in the last `window` posts (least-recently-used, random tie-break)."""
    last = recent[-window:]
    fresh = [o for o in options if o not in last]
    pool = fresh or options
    if not fresh:                                           # all used lately → the one used longest ago
        order = {o: max((i for i, r in enumerate(recent) if r == o), default=-1) for o in options}
        oldest = min(order.values())
        pool = [o for o in options if order[o] == oldest]
    return rng.choice(pool)


# ── compose ─────────────────────────────────────────────────────────────────────────────────────
def compose(mode: str, post_id: str, *, dark: bool = False, palette: str = "", colors: Optional[List[str]] = None,
            fresh: bool = False) -> Dict[str, Any]:
    """A new composition for this post (or the one it already has, unless `fresh`)."""
    with _LOCK:
        d = _load()
        bp = d.setdefault("by_post", {})
        pk = f"{mode}:{post_id}"
        if post_id and not fresh and pk in bp:
            got = next((x for x in d["items"] if x["key"] == bp[pk]), None)
            if got:
                return got
        items = [x for x in d["items"] if x.get("mode") == mode]
        rng = random.Random(f"{post_id}|{time.time_ns()}")
        used = {x["combo"] for x in items[-NO_REPEAT:]}
        for _ in range(40):
            if mode == "flatlay":
                surf = _pick(rng, FLAT_DARK if dark else FLAT_LIGHT, [x["parts"][0] for x in items], 6)
                prop = _pick(rng, FLAT_PROPS, [x["parts"][1] for x in items], 4)
                corner = _pick(rng, CORNERS, [x["parts"][2] for x in items], 2)
                light = _pick(rng, FLAT_LIGHT_SRC, [x["parts"][3] for x in items], 2)
                parts = [surf, prop, corner, light]
                prompt = (f"top-down flat lay photography surface, {surf}, {prop.format(c=corner)}, {light}, "
                          f"completely empty centre, no objects in the middle, no clothes, no text, photorealistic, "
                          f"high detail texture")
            else:
                cols = [c.strip() for c in (colors or []) if c.strip()] or ["warm neutral"]
                col = _pick(rng, cols, [x["parts"][0] for x in items], 1)
                wall = _pick(rng, STUDIO_WALL, [x["parts"][1] for x in items], 4)
                floor = _pick(rng, STUDIO_FLOOR, [x["parts"][2] for x in items], 3)
                piece = _pick(rng, STUDIO_PIECE, [x["parts"][3] for x in items], 4)
                light = _pick(rng, STUDIO_LIGHT, [x["parts"][4] for x in items], 2)
                col2 = rng.choice([c for c in cols if c != col] or cols)
                parts = [col, wall, floor, piece, light]
                prompt = None                              # assembled by the art director (presets added)
                fields = {"setting": "elegant minimal photo studio set, no visible lamps, softboxes or photo equipment",
                          "wall": wall.format(col=col),
                          "floor": floor.format(col=col2), "light": light,
                          "props": "" if piece == "no set piece" else piece,
                          "camera": "eye-level, straight-on, 35mm, deep focus", "colors": cols[:3]}
            combo = "|".join(parts)
            if combo not in used:
                break
        key = f"bd_{mode[:4]}_{hashlib.sha1(f'{combo}|{post_id}|{time.time_ns()}'.encode()).hexdigest()[:10]}"
        item = {"key": key, "mode": mode, "post": post_id, "parts": parts, "combo": combo, "dark": bool(dark),
                "palette": palette, "seed": rng.randrange(1, 99999), "t": int(time.time()), "tries": 0}
        if mode == "flatlay":
            item["prompt"] = prompt
        else:
            item["fields"] = fields
        d["items"].append(item)
        if post_id:
            bp[pk] = key
        _save(d)
        return item


def get(key: str) -> Optional[Dict[str, Any]]:
    return next((x for x in _load().get("items", []) if x["key"] == key), None)


def set_prompt(key: str, prompt: str) -> None:
    with _LOCK:
        d = _load()
        for x in d["items"]:
            if x["key"] == key:
                x["prompt"] = prompt[:900]
        _save(d)


def paint(item: Dict[str, Any], palette: str = "", mood: str = "") -> None:
    """Queue the FREE paint on our own model with this composition's own seed."""
    scene_store.upsert_scene(item["key"], prompt=item["prompt"], palette=palette or item.get("palette") or "",
                             mood=mood or ("flat lay surface" if item["mode"] == "flatlay" else "studio set"),
                             tags=["composer", item["mode"]])
    scene_store.enqueue_scene(item["key"], item["prompt"], seed=item["seed"])


# ── verify: never two look-alike backdrops ─────────────────────────────────────────────────────
def _fingerprint(path) -> Tuple[str, List[float]]:
    from PIL import Image
    im = Image.open(path).convert("L").resize((9, 8))
    px = list(im.getdata())
    bits = "".join("1" if px[r * 9 + c] > px[r * 9 + c + 1] else "0" for r in range(8) for c in range(8))
    col = Image.open(path).convert("RGB").resize((1, 1)).getpixel((0, 0))
    return f"{int(bits, 2):016x}", [float(v) for v in col]


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def verify(key: str) -> Dict[str, Any]:
    """Called when a backdrop arrives. Near-duplicate of a recent one → repaint once, new composition.
    Returns {"unique": bool, "closest": hamming distance, "repainted": bool}."""
    path = scene_store.backdrop_path(key)
    if not path:
        return {"unique": False, "error": "no image"}
    fp, col = _fingerprint(path)
    with _LOCK:
        d = _load()
        me = next((x for x in d["items"] if x["key"] == key), None)
        others = [x for x in d["items"] if x.get("fp") and x["key"] != key][-60:]
        closest = min((_hamming(fp, x["fp"]) + sum(abs(a - b) for a, b in zip(col, x["col"])) / 30
                       for x in others), default=99)
        if me is not None:
            me.update({"fp": fp, "col": col, "closest": round(closest, 1)})
        _save(d)
    unique = closest > 10
    repainted = False
    if not unique and me is not None and me.get("tries", 0) < 1 and me.get("prompt"):
        # look-alike → a different composition for the same post, painted under the same key
        new = compose(me["mode"], f"{me.get('post', '')}#retry", dark=me.get("dark", False),
                      palette=me.get("palette", ""))
        prompt = new.get("prompt") or me["prompt"]
        with _LOCK:
            d = _load()
            for x in d["items"]:
                if x["key"] == key:
                    x.update({"tries": x.get("tries", 0) + 1, "parts": new["parts"], "combo": new["combo"],
                              "prompt": prompt, "seed": new["seed"]})
            d["items"] = [x for x in d["items"] if x["key"] != new["key"]]
            _save(d)
        path.unlink(missing_ok=True)
        scene_store.enqueue_scene(key, prompt, seed=new["seed"])
        repainted = True
    return {"unique": unique, "closest": round(closest, 1), "repainted": repainted}


def report(n: int = 30) -> Dict[str, Any]:
    """The last n compositions: what was composed, painted, and how far each is from the others."""
    items = _load().get("items", [])[-n:]
    return {"count": len(items), "distinct_combos": len({x["combo"] for x in items}),
            "items": [{"key": x["key"], "mode": x["mode"], "parts": x["parts"], "seed": x["seed"],
                       "painted": bool(scene_store.backdrop_path(x["key"])), "closest": x.get("closest")}
                      for x in items]}
