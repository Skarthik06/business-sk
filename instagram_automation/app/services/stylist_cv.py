"""Deterministic image analysis for the AI Stylist — no AI tokens.

  analyse(raw)                → garment colour, item colour name, emblem/logo found? (box + pixel mask)
  check_and_fix(styled, a)    → is the styled garment still the same colour? is the logo the REAL one?
                                if the image model drew a different emblem it is erased and the real
                                logo (cut from the product photo) is put back, lit like the fabric.

Emblem detector (the whole medium-vs-low decision):
  1. local contrast — every pixel vs a large median blur of the photo; small marks pop out, fabric,
     folds and shadows don't (they're bigger than the blur or have the same hue);
  2. connected blobs, filtered by rules a logo obeys and the rest don't:
       · compact (zippers, drawstrings, seams, sleeve stripes are long and thin → rejected)
       · a clear colour step from the fabric around it: a different hue, or a big lightness jump
         (folds / shadows / a metal zip pull are the same hue → rejected)
       · sitting on smooth fabric, inside the product (the silhouette edge, hair, faces, sunglasses,
         hands are rejected: their ring is skin, textured, or outside the product)
  3. emblem found → "medium" (the model needs the detail), otherwise "low" — stripes, colour blocks
     and plain garments come out right at low (measured).
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

SHARP_MIN = 0.47        # outline sharpness a dark same-hue mark needs — calibrated on 74 real cut-outs:
                        # logos/labels 0.48-0.75, fold shadows / arm gaps / pendants 0.24-0.46
WORK_PX = 1600          # analysis size (long side) — product photos are ≤1500 px, so the logo keeps every pixel


# ── basics ──────────────────────────────────────────────────────────────────────────────────────
def _decode(raw: bytes) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """bytes → (BGR uint8, alpha mask or None), long side ≤ WORK_PX."""
    arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        from PIL import Image                                     # webp/avif etc. via Pillow
        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        arr = np.array(im)
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA if arr.shape[2] == 4 else cv2.COLOR_RGB2BGR)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    s = WORK_PX / max(arr.shape[:2])
    if s < 1:
        arr = cv2.resize(arr, (round(arr.shape[1] * s), round(arr.shape[0] * s)), interpolation=cv2.INTER_AREA)
    if arr.shape[2] == 4:
        a = arr[:, :, 3]
        return np.ascontiguousarray(arr[:, :, :3]), (a > 128) if (a < 250).any() else None
    return arr, None


def _lab(bgr: np.ndarray) -> np.ndarray:
    """Real CIE Lab (L 0-100, a/b ±128) as float32."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] *= 100 / 255
    lab[..., 1:] -= 128
    return lab


def _skin(bgr: np.ndarray) -> np.ndarray:
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = ycc[..., 0], ycc[..., 1], ycc[..., 2]
    return (y > 50) & (cr >= 138) & (cr <= 173) & (cb >= 80) & (cb <= 125)


def _skin_lab(lab: np.ndarray) -> np.ndarray:
    """Wider skin rule (warm, lit hands that YCrCb misses) — only used where the pixel is NOT the
    garment's own colour, so tan/brown/rust clothes are never mistaken for skin."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    return (L > 20) & (L < 92) & (a > 7) & (a < 42) & (b > 11) & (b < 62) & (b > 0.7 * a)


def _foreground(bgr: np.ndarray, alpha: Optional[np.ndarray]) -> np.ndarray:
    """The product (+ wearer): cut-out alpha if we have it, else 'differs from the border colour'."""
    if alpha is not None:
        return alpha
    lab = _lab(bgr)
    border = np.concatenate([lab[:4].reshape(-1, 3), lab[-4:].reshape(-1, 3), lab[:, :4].reshape(-1, 3), lab[:, -4:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    fg = (np.linalg.norm(lab - bg, axis=2) > 10).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab_i, st, _ = cv2.connectedComponentsWithStats(fg)
    keep = np.zeros_like(fg, bool)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] > 0.02 * fg.size:
            keep |= lab_i == i
    return _fill(keep)


def _fill(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8) * 255
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, cnts, -1, 255, -1)
    return out > 0


def _ring(shape, x, y, w, h, pad) -> np.ndarray:
    r = np.zeros(shape[:2], bool)
    y0, y1, x0, x1 = max(0, y - pad), min(shape[0], y + h + pad), max(0, x - pad), min(shape[1], x + w + pad)
    r[y0:y1, x0:x1] = True
    r[y:y + h, x:x + w] = False
    return r


# ── colour names (for the prompt + item) ────────────────────────────────────────────────────────
_NAMES = {  # name: sRGB
    "black": (20, 20, 22), "charcoal": (55, 57, 60), "grey": (128, 128, 128), "light grey": (196, 196, 196),
    "white": (245, 245, 242), "off-white": (236, 230, 214), "beige": (214, 196, 160), "khaki": (176, 160, 112),
    "brown": (110, 70, 40), "tan": (178, 132, 86), "rust": (160, 72, 36), "maroon": (110, 24, 36),
    "red": (196, 30, 40), "pink": (232, 150, 170), "orange": (232, 120, 30), "mustard": (206, 160, 40),
    "yellow": (240, 210, 50), "olive": (110, 110, 50), "dark green": (24, 74, 56), "green": (50, 140, 70),
    "mint": (160, 220, 190), "teal": (20, 120, 120), "navy": (24, 34, 72), "blue": (40, 90, 180),
    "sky blue": (130, 180, 230), "purple": (100, 50, 130), "lavender": (190, 170, 220),
}
_NAME_LAB = {k: _lab(np.uint8([[v[::-1]]]))[0, 0] for k, v in _NAMES.items()}


_GREYS = {"black", "charcoal", "grey", "light grey", "white", "off-white"}


def colour_name(lab: np.ndarray) -> str:
    """Nearest name; a clearly coloured garment never gets a grey name (dark green ≠ charcoal)."""
    chroma = float(np.hypot(lab[1], lab[2]))
    names = [k for k in _NAME_LAB if chroma < 8 or k not in _GREYS]
    return min(names, key=lambda k: float(np.linalg.norm(_NAME_LAB[k] - lab)))


def _dominant(lab: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return _colours(lab, mask)[0][0]


def _colours(lab: np.ndarray, mask: np.ndarray) -> List[Tuple[np.ndarray, float]]:
    """The fabric colours of the garment, biggest first: [(Lab, share)]."""
    px = lab[mask]
    if len(px) < 50:
        return [(np.median(lab.reshape(-1, 3), axis=0), 1.0)]
    if len(px) > 30000:
        px = px[np.random.default_rng(0).choice(len(px), 30000, replace=False)]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    k = 3
    _, lbl, ctr = cv2.kmeans(px.astype(np.float32), k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    cnt = np.bincount(lbl.ravel(), minlength=k) / len(px)
    return [(ctr[i], float(cnt[i])) for i in np.argsort(-cnt)]


# ── 1 · emblem detector ─────────────────────────────────────────────────────────────────────────
def find_marks(bgr: np.ndarray, region: np.ndarray, skin: Optional[np.ndarray] = None,
               cloth: Optional[np.ndarray] = None) -> List[Dict[str, Any]]:
    """Compact, contrasting marks on smooth fabric inside `region` (bool mask). Best first.
    cloth = pixels of the garment's own colours: a logo's surroundings must be mostly cloth
    (a watch on a wrist, a hand in a pocket, the T-shirt under an open jacket are not)."""
    H, W = bgr.shape[:2]
    lab = _lab(bgr)
    k = max(15, (max(H, W) // 22) | 1)                                    # ≈ 4.5 % of the image
    bgm = _lab(cv2.medianBlur(bgr, k))
    d_ab = np.linalg.norm(lab[..., 1:] - bgm[..., 1:], axis=2)
    d_l = np.abs(lab[..., 0] - bgm[..., 0])
    hit = ((d_ab > 14) | (d_l > 28)) & region
    grad = cv2.magnitude(cv2.Sobel(lab[..., 0], cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(lab[..., 0], cv2.CV_32F, 0, 1, ksize=3)) / 8
    skin = _skin(bgr) if skin is None else skin
    inner = cv2.erode(region.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    hit &= inner & ~skin
    hit = cv2.morphologyEx(hit.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))   # join logo strokes
    n, lbl, st, _ = cv2.connectedComponentsWithStats(hit)
    area_ref = max(1, int(region.sum()))
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if a < max(25, area_ref * 4e-5) or a > area_ref * 0.06:
            continue
        if max(w, h) / max(1, min(w, h)) > 3.2 or a / (w * h) < 0.2:      # long/thin/scribbly: zip, cord, seam, stitching
            continue
        comp = lbl == i
        ring = _ring(bgr.shape, x, y, w, h, max(6, max(w, h) // 2)) & region & ~(cv2.dilate(hit, np.ones((3, 3), np.uint8)) > 0)
        if ring.sum() < 30 or (skin & _ring(bgr.shape, x, y, w, h, max(6, max(w, h)))).sum() > 0.08 * ring.sum():
            continue                                                      # on/next to skin: face, sunglasses, hands
        if cloth is not None and (cloth & ring).sum() < 0.6 * ring.sum():
            continue                                                      # not sitting on the garment's fabric
        rl = lab[ring]
        if float(np.std(rl[:, 0])) > 9 or float(np.std(rl[:, 1:], axis=0).max()) > 7:
            continue                                                      # textured surroundings: hair, prints of the scene
        fab, mk = np.median(rl, axis=0), np.median(lab[comp], axis=0)
        dab, dl = float(np.linalg.norm(mk[1:] - fab[1:])), abs(float(mk[0] - fab[0]))
        if dab < 18 and dl < 32:                                          # same hue, mild lightness: fold, shadow, zip pull
            continue
        # edge sharpness: a printed/embroidered mark has a crisp outline, a fold shadow or the gap
        # under an arm fades in softly (gradient at the outline per unit of contrast)
        edge = (cv2.dilate(comp.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & ~(cv2.erode(comp.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
        sharp = float(np.percentile(grad[edge], 75)) / max(8.0, dl) if edge.any() else 0.0
        if dab < 12 and mk[0] < fab[0] and sharp < SHARP_MIN:            # darker, same hue, soft → shadow / gap
            continue
        out.append({"box": (x, y, w, h), "area": a, "score": round(dab + 0.6 * dl, 1), "sharp": round(sharp, 3), "mark_lab": mk.tolist(),
                    "fabric_lab": fab.tolist(), "label": i})
    out = _drop_buttons(out)
    out.sort(key=lambda m: -m["score"] * np.sqrt(m["area"]))
    for m in out:
        m["mask"] = lbl == m.pop("label")
    return out


def _drop_buttons(marks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """3+ look-alike marks stacked in one vertical line = shirt buttons / snaps, not an emblem."""
    drop = set()
    for i, m in enumerate(marks):
        x, y, w, h = m["box"]
        row = [j for j, o in enumerate(marks)
               if abs((o["box"][0] + o["box"][2] / 2) - (x + w / 2)) < max(w, o["box"][2]) * 0.8
               and 0.5 < o["area"] / m["area"] < 2.0 and o["box"][1] != y]
        if len(row) >= 2:
            drop.add(i)
            drop.update(row)
    return [m for i, m in enumerate(marks) if i not in drop]


def analyse(raw: bytes, alpha_raw: Optional[bytes] = None) -> Dict[str, Any]:
    """Free, deterministic product read: colour, emblem → quality. alpha_raw = our cut-out PNG (optional)."""
    bgr, alpha = _decode(raw)
    if alpha is None and alpha_raw:
        cb, alpha = _decode(alpha_raw)
        if alpha is not None and cb.shape[:2] != bgr.shape[:2]:
            bgr = cb                                                       # the cut-out carries the original pixels
    H, W = bgr.shape[:2]
    fg = _foreground(bgr, alpha)
    skin = _skin(bgr)
    lab = _lab(bgr)
    ys, xs = np.nonzero(fg)
    if len(xs) == 0:
        fg = np.ones((H, W), bool)
        ys, xs = np.nonzero(fg)
    gx0, gx1, gy0, gy1 = xs.min(), xs.max(), ys.min(), ys.max()
    torso = np.zeros_like(fg)                                              # middle of the product: the garment
    torso[gy0 + (gy1 - gy0) * 30 // 100: gy0 + (gy1 - gy0) * 70 // 100, gx0 + (gx1 - gx0) * 30 // 100: gx0 + (gx1 - gx0) * 70 // 100] = True
    cols = _colours(lab, torso & fg & ~skin)
    garment_lab = cols[0][0]
    cloth = np.zeros((H, W), bool)
    for c, share in cols:
        if share >= 0.22 or c is garment_lab:
            cloth |= _colour_match(lab, c)
    skin = skin | (_skin_lab(lab) & ~cloth)
    marks = find_marks(bgr, fg, skin, cloth)
    worn = bool((skin & fg).sum() > 0.015 * fg.sum())
    res: Dict[str, Any] = {"colour": colour_name(garment_lab), "garment_lab": [round(float(v), 1) for v in garment_lab],
                           "worn": worn, "quality": "low", "box": [], "marks": 0, "size": [W, H],
                           "fg_box": [int(gx0), int(gy0), int(gx1), int(gy1)]}
    if marks:
        m = marks[0]
        x, y, w, h = m["box"]
        res.update(quality="medium", marks=len(marks), box=[round(x / W, 4), round(y / H, 4), round((x + w) / W, 4), round((y + h) / H, 4)],
                   mark_lab=[round(float(v), 1) for v in m["mark_lab"]], fabric_lab=[round(float(v), 1) for v in m["fabric_lab"]],
                   side="image-right" if x + w / 2 > (gx0 + gx1) / 2 else "image-left")
        res["_mask"], res["_bgr"] = m["mask"], bgr
    return res


def logo_png(a: Dict[str, Any], px: int = 256) -> Optional[bytes]:
    """The real logo, tight crop with a little fabric, upscaled — shown to the image model as image 3."""
    if not a.get("box") or "_bgr" not in a:
        return None
    bgr = a["_bgr"]
    H, W = bgr.shape[:2]
    x0, y0, x1, y1 = a["box"]
    pad = 0.6 * max(x1 - x0, (y1 - y0) * H / W)
    b = (int(max(0, x0 - pad) * W), int(max(0, y0 - pad * W / H) * H), int(min(1, x1 + pad) * W), int(min(1, y1 + pad * W / H) * H))
    c = bgr[b[1]:b[3], b[0]:b[2]]
    if c.size == 0:
        return None
    s = px / max(c.shape[:2])
    c = cv2.resize(c, (max(8, round(c.shape[1] * s)), max(8, round(c.shape[0] * s))), interpolation=cv2.INTER_CUBIC)
    ok, enc = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return enc.tobytes() if ok else None


# ── 2 · check the styled image + put the real logo back ─────────────────────────────────────────
def _norm_mask(m: np.ndarray, n: int = 40) -> np.ndarray:
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return np.zeros((n, n), bool)
    c = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
    s = n / max(c.shape)
    c = cv2.resize(c, (max(1, round(c.shape[1] * s)), max(1, round(c.shape[0] * s))), interpolation=cv2.INTER_AREA)
    out = np.zeros((n, n), np.uint8)
    out[(n - c.shape[0]) // 2:(n - c.shape[0]) // 2 + c.shape[0], (n - c.shape[1]) // 2:(n - c.shape[1]) // 2 + c.shape[1]] = c
    return out > 100


def _shape_match(a: np.ndarray, b: np.ndarray) -> float:
    """IoU of two marks, scale-normalised, best over small rotations/flips (folds tilt a logo)."""
    na = _norm_mask(a)
    best = 0.0
    for flip in (False, True):
        nb0 = _norm_mask(b[:, ::-1] if flip else b).astype(np.uint8) * 255
        for ang in (-12, -6, 0, 6, 12):
            M = cv2.getRotationMatrix2D((20, 20), ang, 1.0)
            nb = cv2.warpAffine(nb0, M, (40, 40)) > 100
            inter, uni = (na & nb).sum(), (na | nb).sum()
            best = max(best, inter / uni if uni else 0.0)
    return round(float(best), 3)


def _colour_match(lab: np.ndarray, garment_lab) -> np.ndarray:
    """Pixels of this fabric colour, lit or shaded (same hue, any fold/shadow lightness)."""
    g = np.asarray(garment_lab, np.float32)
    chroma = float(np.hypot(g[1], g[2]))
    dl = np.abs(lab[..., 0] - g[0])
    if chroma > 7:                         # coloured: same hue (±28°), real colour (not a grey surface)
        c = np.hypot(lab[..., 1], lab[..., 2])
        hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) - np.degrees(np.arctan2(g[2], g[1]))
        dh = np.abs((hue + 180) % 360 - 180)
        m = (dh < 28) & (c > 0.45 * chroma) & (c < 2.6 * chroma + 6) & (dl < 32)
    else:                                  # black / grey / white: lightness + neutrality
        m = (np.linalg.norm(lab[..., 1:] - g[1:], axis=2) < 6) & (dl < 12)
    return m


def _garment_mask(lab: np.ndarray, garment_lab: np.ndarray) -> np.ndarray:
    m = _colour_match(lab, garment_lab).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    k = max(9, (max(m.shape) // 70) | 1)                   # bridge a zipper / placket splitting the garment
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return np.zeros(m.shape, bool)
    areas = st[1:, cv2.CC_STAT_AREA]
    keep = np.isin(lbl, 1 + np.nonzero(areas >= 0.2 * areas.max())[0])   # both halves of a zip garment
    return _fill(keep)


def check_and_fix(styled_jpeg: bytes, a: Dict[str, Any]) -> Dict[str, Any]:
    """Free fidelity check of a paid image. Returns {"ok", "why", "logo": kept|restored|placed|-, "image": bytes}."""
    bgr = cv2.imdecode(np.frombuffer(styled_jpeg, np.uint8), cv2.IMREAD_COLOR)
    H, W = bgr.shape[:2]
    lab = _lab(bgr)
    g = np.asarray(a["garment_lab"], np.float32)
    gm = _garment_mask(lab, g)
    share = float(gm.mean())
    chroma = float(np.hypot(g[1], g[2]))
    if chroma > 10 and share < 0.06:                       # the garment's own colour is (almost) gone
        return {"ok": False, "why": f"garment colour changed ({a['colour']} covers {share:.0%})", "logo": "-"}
    res = {"ok": True, "why": "", "logo": "-", "image": styled_jpeg, "garment_share": round(share, 3)}
    if not a.get("box") or "_mask" not in a:
        return res
    # where should the logo be? map its spot from the product photo onto the styled garment
    src = a["_bgr"]
    sH, sW = src.shape[:2]
    sm = a["_mask"]
    ys, xs = np.nonzero(sm)
    lx0, lx1, ly0, ly1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    sgm = _garment_mask(_lab(src), g)
    if share < 0.02 or sgm.sum() < 100:
        return res
    sy, sx = np.nonzero(sgm)
    ty, tx = np.nonzero(gm)
    scale = (tx.max() - tx.min()) / max(1, sx.max() - sx.min())
    exp_cx = tx.min() + ((lx0 + lx1) / 2 - sx.min()) * scale
    exp_cy = ty.min() + ((ly0 + ly1) / 2 - sy.min()) * (ty.max() - ty.min()) / max(1, sy.max() - sy.min())
    exp_sz = max(lx1 - lx0, ly1 - ly0) * scale
    region = _fill(gm)
    # a flat-lay re-proportions the garment (hood laid up, sleeves out), so the mapped spot is only a
    # guide: take marks of a logo's size on the same side of the garment, nearest first
    gcx = (tx.min() + tx.max()) / 2
    same_side = (lambda x: x >= gcx) if a.get("side") == "image-right" else (lambda x: x < gcx)
    cands = [m for m in find_marks(bgr, region, np.zeros((H, W), bool))
             if same_side(m["box"][0] + m["box"][2] / 2)
             and np.hypot(m["box"][0] + m["box"][2] / 2 - exp_cx, m["box"][1] + m["box"][3] / 2 - exp_cy) < 0.3 * max(W, H)
             and 0.3 * exp_sz < max(m["box"][2], m["box"][3]) < 3.5 * exp_sz]
    real_lab = np.asarray(a["mark_lab"], np.float32)
    if cands:
        c = min(cands, key=lambda m: np.hypot(m["box"][0] + m["box"][2] / 2 - exp_cx, m["box"][1] + m["box"][3] / 2 - exp_cy))
        iou = _shape_match(sm[ly0:ly1, lx0:lx1], c["mask"])
        dcol = float(np.linalg.norm(np.asarray(c["mark_lab"])[1:] - real_lab[1:]))
        res.update(logo_iou=iou, logo_dcol=round(dcol, 1))
        if iou >= 0.62 and dcol < 22:
            res["logo"] = "kept"                            # the model drew the real one
            return res
        x, y, w, h = c["box"]
        cx, cy, size = x + w / 2, y + h / 2, max(w, h)
        bgr = _erase(bgr, c["mask"], region)
        res["logo"] = "restored"
    else:                                                   # the model left the logo out: a flat-lay
        res["logo"] = "missing"                             # re-proportions the garment, so guessing a
        return res                                          # spot could put it somewhere wrong — report it
    bgr = _paste_logo(bgr, src, sm, (lx0, ly0, lx1, ly1), (cx, cy), size, np.asarray(a["fabric_lab"], np.float32))
    ok, enc = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    res["image"] = enc.tobytes()
    return res


def _erase(bgr: np.ndarray, mask: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Remove a wrong emblem like a clone stamp: copy the nearest clean patch of the same fabric over
    it (keeps the knit texture and shading — plain inpainting leaves a smudge), feathered."""
    H, W = bgr.shape[:2]
    m = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    ys, xs = np.nonzero(m)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    w, h = x1 - x0, y1 - y0
    tgt = _lab(bgr[max(0, y0 - h // 2):y1 + h // 2, max(0, x0 - w // 2):x1 + w // 2])
    ref_l = float(np.median(tgt[..., 0]))
    best, best_cost = None, 1e9
    for dy, dx in [(0, 1.6), (0, -1.6), (1.6, 0), (-1.6, 0), (1.6, 1.6), (1.6, -1.6), (-1.6, 1.6), (-1.6, -1.6), (0, 2.6), (0, -2.6), (2.6, 0)]:
        sx, sy = int(x0 + dx * w), int(y0 + dy * h)
        if sx < 0 or sy < 0 or sx + w > W or sy + h > H or not region[sy:sy + h, sx:sx + w].all():
            continue
        pl = _lab(bgr[sy:sy + h, sx:sx + w])
        cost = float(np.std(pl[..., 0])) * 2 + abs(float(np.median(pl[..., 0])) - ref_l) + float(np.std(pl[..., 1:]))
        if cost < best_cost:
            best, best_cost = (sx, sy), cost
    if best is None:                                                       # nowhere clean to copy from
        return cv2.inpaint(bgr, m * 255, 5, cv2.INPAINT_TELEA)
    sx, sy = best
    patch = bgr[sy:sy + h, sx:sx + w].astype(np.float32)
    al = cv2.GaussianBlur(m[y0:y1, x0:x1].astype(np.float32), (0, 0), 2.0)[..., None]
    al = np.clip(al * 1.6, 0, 1)
    out = bgr.copy()
    out[y0:y1, x0:x1] = np.clip(patch * al + bgr[y0:y1, x0:x1].astype(np.float32) * (1 - al), 0, 255).astype(np.uint8)
    return out


def _paste_logo(dst, src, mask, box, centre, size, fabric_lab):
    """Real logo → dst at centre, scaled to `size`, relit to the local fabric. The edge is a colour
    matte (how far each pixel is from the fabric towards the logo colour) → crisp, anti-aliased."""
    lx0, ly0, lx1, ly1 = box
    pad = 3
    y0, y1, x0, x1 = max(0, ly0 - pad), ly1 + pad, max(0, lx0 - pad), lx1 + pad
    patch = src[y0:y1, x0:x1]
    near = cv2.dilate(mask[y0:y1, x0:x1].astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    pl = _lab(patch)
    fab = np.asarray(fabric_lab, np.float32)
    mark = np.median(pl[mask[y0:y1, x0:x1]], axis=0)
    span = max(8.0, float(np.linalg.norm(mark - fab)))
    alpha = np.clip(np.linalg.norm(pl - fab, axis=2) / span * 1.25, 0, 1) * near
    # un-mix the photo's fabric from the edge pixels so no green/grey fringe travels with the logo
    fab_bgr = cv2.cvtColor(np.uint8([[[fab[0] * 2.55, fab[1] + 128, fab[2] + 128]]]), cv2.COLOR_LAB2BGR)[0, 0].astype(np.float32)
    a3 = np.maximum(alpha, 1e-3)[..., None]
    fg = np.clip((patch.astype(np.float32) - (1 - a3) * fab_bgr) / a3, 0, 255)
    s = size / max(lx1 - lx0, ly1 - ly0)
    nw, nh = max(3, round(fg.shape[1] * s)), max(3, round(fg.shape[0] * s))
    interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA
    fg = cv2.resize(fg, (nw, nh), interpolation=interp)
    al = np.clip(cv2.resize(alpha.astype(np.float32), (nw, nh), interpolation=interp), 0, 1)
    H, W = dst.shape[:2]
    X, Y = int(round(centre[0] - nw / 2)), int(round(centre[1] - nh / 2))
    if X < 0 or Y < 0 or X + nw > W or Y + nh > H:
        return dst
    roi = dst[Y:Y + nh, X:X + nw].astype(np.float32)
    # relight: the fabric under the new spot vs the fabric around the logo in the photo
    bg_l = float(np.median(_lab(dst[Y:Y + nh, X:X + nw])[..., 0]))
    k = float(np.clip((bg_l + 10) / max(10.0, float(fab[0]) + 10), 0.55, 1.3))
    fg = np.clip(fg * k, 0, 255)
    # a hint of the cloth's shading through the logo, like embroidery
    shade = cv2.GaussianBlur(cv2.cvtColor(roi.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 3)
    fg *= (shade / max(1.0, float(shade.mean())))[..., None] ** 0.3
    a = al[..., None]
    out = dst.copy()
    out[Y:Y + nh, X:X + nw] = np.clip(fg * a + roi * (1 - a), 0, 255).astype(np.uint8)
    return out
