"""scene_store — the AI-scene asset library + the GPU job queue (agent: post-art-director).

Assets live on the server under images/sk_scenes/ (git-ignored):
  cutouts/<id>.png + <id>.json   product photo cut out by BiRefNet (ORIGINAL pixels, alpha mask)
                                 + measured facts (is it a cropped person? aspect, colours)
  backdrops/<key>.jpg            EMPTY scene painted by Z-Image-Turbo (no product, no people)
  library.json                   the backdrop library the art director picks from

The heavy models run on the user's laptop GPU (scripts/gpu_worker.py). The worker polls
/api/gpu/worker/jobs and posts results to /api/gpu/worker/result. Every upload is verified as a
real image and every name is a sanitised hash/slug, so the worker can never write arbitrary files.
Nothing here ever blocks a render: missing assets → the renderer falls back to classic templates.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import settings

ROOT = settings.IMAGES_DIR / "sk_scenes"
CUT = ROOT / "cutouts"
BG = ROOT / "backdrops"
LIB = ROOT / "library.json"
for _d in (CUT, BG):
    _d.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_LEASE_SECS = 240
_MAX_UPLOAD = 12 * 1024 * 1024                 # 12 MB per asset


# ── ids / auth ───────────────────────────────────────────────────────────────
def img_id(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:24]


def scene_key(s: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:48]
    return k or "scene_" + hashlib.sha1((s or "").encode()).hexdigest()[:10]


def worker_token_ok(tok: Optional[str]) -> bool:
    want = (os.getenv("GPU_WORKER_TOKEN") or "").strip()
    return bool(want) and bool(tok) and hmac.compare_digest(want, str(tok).strip())


# ── library ──────────────────────────────────────────────────────────────────
def library() -> List[Dict[str, Any]]:
    try:
        data = json.loads(LIB.read_text("utf-8"))
        items = data if isinstance(data, list) else []
    except Exception:
        items = []
    for it in items:
        it["ready"] = (BG / f"{it.get('key')}.jpg").exists()
    return items


def _save_library(items: List[Dict[str, Any]]) -> None:
    clean = [{k: v for k, v in it.items() if k != "ready"} for it in items]
    tmp = LIB.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(LIB)


def scene(key: str) -> Optional[Dict[str, Any]]:
    return next((s for s in library() if s.get("key") == key), None)


def upsert_scene(key: str, *, prompt: str, palette: str, mood: str = "", tags: Optional[List[str]] = None,
                 niches: Optional[List[str]] = None, subject: str = "any") -> Dict[str, Any]:
    key = scene_key(key)
    with _LOCK:
        items = library()
        cur = next((s for s in items if s.get("key") == key), None)
        if cur is None:
            cur = {"key": key, "created": int(time.time()), "uses": 0}
            items.append(cur)
        cur.update({"prompt": prompt[:900], "palette": palette, "mood": mood[:120],
                    "tags": [str(t)[:24] for t in (tags or [])][:8],
                    "niches": [str(t)[:24] for t in (niches or [])][:8], "subject": subject})
        _save_library(items)
    return cur


def note_scene_use(key: str) -> None:
    with _LOCK:
        items = library()
        for s in items:
            if s.get("key") == key:
                s["uses"] = int(s.get("uses") or 0) + 1
                s["last_used"] = int(time.time())
        _save_library(items)


def backdrop_path(key: str) -> Optional[Path]:
    p = BG / f"{scene_key(key)}.jpg"
    return p if p.exists() else None


def thumb(key: str, width: int = 180) -> str:
    """Small JPEG data URI of a backdrop for the Studio panel (cached next to it)."""
    src = backdrop_path(key)
    if not src:
        return ""
    tp = BG / f"{scene_key(key)}.thumb.jpg"
    try:
        if not tp.exists() or tp.stat().st_mtime < src.stat().st_mtime:
            from PIL import Image
            im = Image.open(src).convert("RGB")
            im.thumbnail((width, width * 2))
            im.save(tp, quality=80)
        return data_uri(tp, jpeg=True)
    except Exception:
        return ""


# ── cutouts ──────────────────────────────────────────────────────────────────
def cutout_path(url: str) -> Optional[Path]:
    p = CUT / f"{img_id(url)}.png"
    return p if p.exists() else None


def cutout_meta(url: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((CUT / f"{img_id(url)}.json").read_text("utf-8"))
    except Exception:
        return None


def data_uri(path: Optional[Path], *, jpeg: bool = False) -> str:
    if not path:
        return ""
    mime = "image/jpeg" if jpeg else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


# ── queue (file-backed: survives backend restarts and is shared by every process) ─────────
QDIR = ROOT / "queue"
QDIR.mkdir(parents=True, exist_ok=True)
_POLL_FILE = ROOT / "worker.json"
_JID = re.compile(r"^(cut|scene)_[a-z0-9_]{1,60}$")


def _jpath(jid: str) -> Optional[Path]:
    return QDIR / f"{jid}.json" if _JID.match(jid or "") else None


def _read_job(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def _write_job(job: Dict[str, Any]) -> None:
    path = _jpath(job["id"])
    if path:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job), "utf-8")
        tmp.replace(path)


def _jobs() -> List[Dict[str, Any]]:
    return [j for j in (_read_job(f) for f in QDIR.glob("*.json")) if j]


def enqueue_cutout(url: str) -> Optional[str]:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")) or cutout_path(url):
        return None
    jid = "cut_" + img_id(url)
    with _LOCK:
        if not _jpath(jid).exists():
            _write_job({"id": jid, "type": "cutout", "url": url, "queued": time.time(), "lease": 0, "tries": 0})
    return jid


def enqueue_scene(key: str, prompt: str, seed: int = 0) -> Optional[str]:
    key = scene_key(key)
    if backdrop_path(key) or not prompt:
        return None
    jid = "scene_" + key
    with _LOCK:
        if _jpath(jid) and not _jpath(jid).exists():
            _write_job({"id": jid, "type": "scene", "key": key, "prompt": prompt[:900],
                        "seed": int(seed) % 100000, "w": 1024, "h": 1280,
                        "queued": time.time(), "lease": 0, "tries": 0})
    return jid


def _last_poll() -> Dict[str, Any]:
    try:
        return json.loads(_POLL_FILE.read_text("utf-8"))
    except Exception:
        return {"t": 0, "info": {}}


def worker_online(max_age: float = 90.0) -> bool:  # file-backed → works across processes
    return time.time() - float(_last_poll().get("t") or 0) < max_age


def take_jobs(n: int = 4, info: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Worker poll: lease up to n jobs (cutouts first — they're fast and block renders)."""
    now = time.time()
    try:
        _POLL_FILE.write_text(json.dumps({"t": now, "info": {k: str(v)[:60] for k, v in (info or {}).items()}}), "utf-8")
    except Exception:
        pass
    out: List[Dict[str, Any]] = []
    with _LOCK:
        # cut-outs first (fast, oldest first); scenes NEWEST first — the latest art direction is the
        # one a preview is waiting on (older re-directs shouldn't block it)
        for j in sorted(_jobs(), key=lambda j: (j.get("type") != "cutout",
                                                j.get("queued", 0) if j.get("type") == "cutout" else -j.get("queued", 0))):
            if len(out) >= max(1, min(n, 8)):
                break
            if j.get("lease") and now - j["lease"] < _LEASE_SECS:
                continue
            j["tries"] = int(j.get("tries") or 0) + 1
            if j["tries"] > 3:                       # permanently failing (dead image link…) → drop
                (_jpath(j["id"]) or Path("/nonexistent")).unlink(missing_ok=True)
                continue
            j["lease"] = now
            _write_job(j)
            out.append({k: v for k, v in j.items() if k not in ("queued", "lease", "tries")})
    return out


def _verify_image(raw: bytes, *, want_alpha: bool):
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.verify()                                   # structural check
    im = Image.open(io.BytesIO(raw))
    if im.width < 32 or im.height < 32 or im.width > 4096 or im.height > 4096:
        raise ValueError("bad image size")
    if want_alpha and im.mode != "RGBA":
        im = im.convert("RGBA")
    return im


def submit(job_id: str, b64: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    path = _jpath(str(job_id))
    job = _read_job(path) if path and path.exists() else None
    if not job:
        return {"ok": False, "error": "unknown job"}
    try:
        raw = base64.b64decode(b64 or "", validate=True)
    except Exception:
        return {"ok": False, "error": "bad base64"}
    if not raw or len(raw) > _MAX_UPLOAD:
        return {"ok": False, "error": "empty or too large"}
    try:
        if job["type"] == "cutout":
            im = _verify_image(raw, want_alpha=True)
            iid = img_id(job["url"])
            im.save(CUT / f"{iid}.png", optimize=True)
            m = {k: meta[k] for k in ("subject", "touches_bottom", "aspect", "colors", "fill") if meta and k in meta}
            m.update({"url": job["url"], "w": im.width, "h": im.height, "t": int(time.time())})
            (CUT / f"{iid}.json").write_text(json.dumps(m), "utf-8")
        else:
            im = _verify_image(raw, want_alpha=False).convert("RGB")
            im.save(BG / f"{job['key']}.jpg", quality=90, optimize=True)
    except Exception as e:
        return {"ok": False, "error": f"invalid image: {str(e)[:80]}"}
    path.unlink(missing_ok=True)
    return {"ok": True}


def wait_for(urls: List[str], keys: List[str], timeout: float) -> None:
    """Block (bounded) until these cutouts/backdrops exist or the worker looks offline."""
    end = time.time() + max(0.0, timeout)
    # "online" includes BUSY: while the laptop paints a scene (~70 s) it doesn't poll, so a job it
    # leased recently also counts — otherwise the wait gave up mid-paint and used the fallback.
    def _alive() -> bool:
        if worker_online():
            return True
        now = time.time()
        return any(j.get("lease") and now - j["lease"] < _LEASE_SECS for j in _jobs())
    while time.time() < end and _alive():
        if all(cutout_path(u) for u in urls if u) and all(backdrop_path(k) for k in keys if k):
            return
        time.sleep(0.5)


def status() -> Dict[str, Any]:
    q = _jobs()
    lib = library()
    lp = _last_poll()
    now = time.time()
    busy = any(j.get("lease") and now - j["lease"] < _LEASE_SECS for j in q)
    return {"worker_online": worker_online() or busy, "worker_busy": busy and not worker_online(),
            "last_poll_secs": round(time.time() - float(lp.get("t") or 0), 1)
            if lp.get("t") else None, "worker": lp.get("info") or {},
            "queued": len(q), "queued_cutouts": sum(1 for j in q if j["type"] == "cutout"),
            "queued_scenes": sum(1 for j in q if j["type"] == "scene"),
            "library": len(lib), "library_ready": sum(1 for s in lib if s.get("ready")),
            "cutouts": len(list(CUT.glob("*.png")))}
