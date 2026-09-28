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
_LEASE_SECS = 420                              # > one scene paint (~2-3 min)
_MAX_UPLOAD = 12 * 1024 * 1024                 # 12 MB per asset


# ── ids / auth ───────────────────────────────────────────────────────────────
def img_id(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:24]


def scene_key(s: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:48]
    return k or "scene_" + hashlib.sha1((s or "").encode()).hexdigest()[:10]


# ── device-aware rendering: work on the PHONE → Colab renders; on the LAPTOP → the laptop GPU ──
# The Studio sends X-SK-Device (phone | laptop) with every request; jobs queued while serving it are
# tagged with a target. (Public IPs can't tell phone and laptop apart on the same Wi-Fi.)
import contextvars  # noqa: E402

_DEVICE: contextvars.ContextVar = contextvars.ContextVar("sk_render_device", default="")
_LAST_DEVICE = {"device": "", "at": 0.0}
COLAB_GRACE = float(os.getenv("SK_COLAB_GRACE_SECS", "300"))   # a phone job waits this long for Colab


def set_render_device(d: str) -> None:
    d = (d or "").strip().lower()
    if d in ("phone", "laptop"):
        _DEVICE.set(d)
        _LAST_DEVICE.update(device=d, at=time.time())


def render_target() -> str:
    d = _DEVICE.get() or (_LAST_DEVICE["device"] if time.time() - _LAST_DEVICE["at"] < 600 else "")
    return {"phone": "colab", "laptop": "laptop"}.get(d, "")


def worker_token_ok(tok: Optional[str]) -> bool:
    """The laptop's permanent token, or a temporary Google Colab session token."""
    want = (os.getenv("GPU_WORKER_TOKEN") or "").strip()
    tok = str(tok or "").strip()
    if not tok:
        return False
    if want and hmac.compare_digest(want, tok):
        return True
    return tok.startswith("skg_") and colab_token_ok(tok)


# ── Google Colab sessions: you start the notebook (a human, interactive session), type a
# one-time code from the Studio, and the notebook swaps it for a temporary worker token. ────────
_SESS_FILE = ROOT / "gpu_sessions.json"
_CODE_TTL = 30 * 60                     # the code must be used within 30 min
_SESSION_TTL = 6 * 3600                 # a Colab token works for 6 h (a free session is shorter anyway)
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CLAIM_FAILS: List[float] = []


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sessions() -> List[Dict[str, Any]]:
    try:
        items = json.loads(_SESS_FILE.read_text("utf-8"))
    except Exception:
        items = []
    now = time.time()
    return [s for s in items if float(s.get("exp") or 0) > now]


def _save_sessions(items: List[Dict[str, Any]]) -> None:
    tmp = _SESS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items), "utf-8")
    tmp.replace(_SESS_FILE)


def new_colab_code() -> Dict[str, Any]:
    import secrets
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    with _LOCK:
        items = _sessions()
        items.append({"code": _sha(raw), "exp": time.time() + _CODE_TTL})
        _save_sessions(items)
    return {"code": f"{raw[:4]}-{raw[4:]}", "expires_in": _CODE_TTL}


def claim_colab_code(code: str) -> Optional[str]:
    """One-time code → temporary worker token (only its hash is stored). None = wrong/expired."""
    import secrets
    now = time.time()
    _CLAIM_FAILS[:] = [t for t in _CLAIM_FAILS if now - t < 1800]
    if len(_CLAIM_FAILS) >= 20:                       # guessing protection
        return None
    raw = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    with _LOCK:
        items = _sessions()
        hit = next((s for s in items if s.get("code") == _sha(raw)), None) if len(raw) == 8 else None
        if not hit:
            _CLAIM_FAILS.append(now)
            return None
        items.remove(hit)                             # single use
        token = "skg_" + secrets.token_urlsafe(32)
        items.append({"token": _sha(token), "exp": now + _SESSION_TTL})
        _save_sessions(items)
    return token


def colab_token_ok(tok: str) -> bool:
    h = _sha(tok)
    return any(hmac.compare_digest(s.get("token") or "", h) for s in _sessions())


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
            _write_job({"id": jid, "type": "cutout", "url": url, "queued": time.time(), "lease": 0, "tries": 0,
                        "target": render_target()})
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
                        "queued": time.time(), "lease": 0, "tries": 0, "target": render_target()})
    return jid


def cancel_scene(key: str) -> bool:
    """Drop a queued scene the post no longer needs (not yet picked up by the laptop) — saves a
    ~80 s full-GPU paint. A scene already being painted is left to finish."""
    path = _jpath("scene_" + scene_key(key))
    with _LOCK:
        job = _read_job(path) if path and path.exists() else None
        if not job or (job.get("lease") and time.time() - job["lease"] < _LEASE_SECS):
            return False
        path.unlink(missing_ok=True)
    return True


# ── per-post plans: ONE art-direction plan + ONE rendered look per post ──────────────────────
PLANS = ROOT / "plans"
PLANS.mkdir(parents=True, exist_ok=True)
_PID = re.compile(r"^[a-f0-9]{16,40}$")
_PLAN_LOCKS: Dict[str, threading.Lock] = {}


def plan_lock(pid: str) -> threading.Lock:
    """One lock per post: concurrent previews of the same post share ONE plan (no double paint)."""
    with _LOCK:
        return _PLAN_LOCKS.setdefault(pid, threading.Lock())


def plan_get(pid: str) -> Optional[Dict[str, Any]]:
    if not _PID.match(pid or ""):
        return None
    try:
        return json.loads((PLANS / f"{pid}.json").read_text("utf-8"))
    except Exception:
        return None


def plan_put(pid: str, data: Dict[str, Any]) -> None:
    if not _PID.match(pid or ""):
        return
    tmp = PLANS / f"{pid}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tmp.replace(PLANS / f"{pid}.json")


def plan_update(pid: str, **kv: Any) -> None:
    with _LOCK:
        cur = plan_get(pid)
        if cur is not None:
            cur.update(kv)
            plan_put(pid, cur)


_COLAB_POLL_FILE = ROOT / "colab_worker.json"


def _poll_file(kind: str) -> Path:
    return _COLAB_POLL_FILE if kind == "colab" else _POLL_FILE


def _last_poll(kind: str = "laptop") -> Dict[str, Any]:
    try:
        return json.loads(_poll_file(kind).read_text("utf-8"))
    except Exception:
        return {"t": 0, "info": {}}


def laptop_online(max_age: float = 90.0) -> bool:  # file-backed → works across processes
    return time.time() - float(_last_poll("laptop").get("t") or 0) < max_age


def colab_online(max_age: float = 90.0) -> bool:
    return time.time() - float(_last_poll("colab").get("t") or 0) < max_age


def worker_online(max_age: float = 90.0) -> bool:
    """Any render GPU (laptop or Colab) is connected."""
    return laptop_online(max_age) or colab_online(max_age)


def render_gpu_online() -> bool:
    """Is the GPU that serves THIS device's work connected? (phone → Colab; laptop → laptop, or Colab
    while the laptop is off). Decides whether a post waits for a fresh backdrop or uses a saved one."""
    tgt = render_target()
    if tgt == "colab":
        return colab_online()
    return worker_online()


def _eligible(j: Dict[str, Any], kind: str, now: float) -> bool:
    """Who may take a job: phone-started work is reserved for Colab (the laptop only helps once no
    Colab showed up within COLAB_GRACE); laptop-started work goes to Colab only when the laptop is off."""
    tgt = j.get("target") or ""
    if kind == "laptop" and tgt == "colab":
        return not colab_online() and now - float(j.get("queued") or now) > COLAB_GRACE
    if kind == "colab" and tgt == "laptop":
        return not laptop_online()
    return True


# ── phone cut-out workers (SK Helper, ML Kit subject segmentation on the phone) ───────────────
_DEV_POLL_FILE = ROOT / "device_worker.json"


def _last_device_poll() -> Dict[str, Any]:
    try:
        return json.loads(_DEV_POLL_FILE.read_text("utf-8"))
    except Exception:
        return {"t": 0, "info": {}}


def device_online(max_age: float = 90.0) -> bool:
    return time.time() - float(_last_device_poll().get("t") or 0) < max_age


def take_cutout_jobs(n: int = 2, info: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """A phone's poll: cut-outs ONLY, and only while the laptop GPU is offline (its BiRefNet cut-outs
    are better, so the laptop always goes first). Scenes are never given to a phone."""
    now = time.time()
    try:
        _DEV_POLL_FILE.write_text(json.dumps({"t": now, "info": {k: str(v)[:60] for k, v in (info or {}).items()}}), "utf-8")
    except Exception:
        pass
    if colab_online():
        return []
    laptop = laptop_online()
    out: List[Dict[str, Any]] = []
    with _LOCK:
        for j in sorted((j for j in _jobs() if j.get("type") == "cutout"), key=lambda j: j.get("queued", 0)):
            if laptop and j.get("target") != "colab":
                continue                             # the laptop does its own (better) cut-outs
            if len(out) >= max(1, min(n, 4)):
                break
            if j.get("lease") and now - j["lease"] < _LEASE_SECS:
                continue
            j["tries"] = int(j.get("tries") or 0) + 1
            if j["tries"] > 3:
                (_jpath(j["id"]) or Path("/nonexistent")).unlink(missing_ok=True)
                continue
            j["lease"] = now
            j["by"] = "phone"
            _write_job(j)
            out.append({k: v for k, v in j.items() if k not in ("queued", "lease", "tries", "target", "by")})
    return out


def take_jobs(n: int = 4, info: Optional[Dict[str, Any]] = None, heartbeat: bool = False) -> List[Dict[str, Any]]:
    """Worker poll: lease up to n jobs (cutouts first — they're fast and block renders) but AT MOST
    ONE scene per poll (a scene takes ~2 min; batching scenes kept the worker silent for 10 min).
    heartbeat=True only records that the (busy) worker is alive — no jobs are leased."""
    now = time.time()
    kind = "colab" if (info or {}).get("kind") == "colab" else "laptop"
    try:
        _poll_file(kind).write_text(json.dumps({"t": now, "info": {k: str(v)[:60] for k, v in (info or {}).items()}}), "utf-8")
    except Exception:
        pass
    out: List[Dict[str, Any]] = []
    if heartbeat:
        return out
    scenes_taken = 0
    with _LOCK:
        # cut-outs first (fast, oldest first); scenes NEWEST first — the latest art direction is the
        # one a preview is waiting on (older re-directs shouldn't block it)
        for j in sorted(_jobs(), key=lambda j: (j.get("type") != "cutout",
                                                j.get("queued", 0) if j.get("type") == "cutout" else -j.get("queued", 0))):
            if len(out) >= max(1, min(n, 8)):
                break
            if j.get("lease") and now - j["lease"] < _LEASE_SECS:
                continue
            if not _eligible(j, kind, now):
                continue
            if j.get("type") == "scene":
                if scenes_taken:
                    continue
                scenes_taken += 1
            j["tries"] = int(j.get("tries") or 0) + 1
            if j["tries"] > 3:                       # permanently failing (dead image link…) → drop
                (_jpath(j["id"]) or Path("/nonexistent")).unlink(missing_ok=True)
                continue
            j["lease"] = now
            j["by"] = kind
            _write_job(j)
            out.append({k: v for k, v in j.items() if k not in ("queued", "lease", "tries", "target", "by")})
    return out


def release_job(job_id: str, error: str = "") -> bool:
    """A worker couldn't do this job: free it at once (another GPU / a retry takes it) and keep why."""
    path = _jpath(str(job_id))
    with _LOCK:
        job = _read_job(path) if path and path.exists() else None
        if not job:
            return False
        job.update(lease=0, error=str(error)[:200], by="")
        _write_job(job)
    return True


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
            m = {k: meta[k] for k in ("subject", "touches_bottom", "aspect", "colors", "fill", "by") if meta and k in meta}
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
        if render_gpu_online():
            return True
        if urls and not keys and device_online():  # a phone does cut-outs (never scenes)
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
    lp = _last_poll("laptop")
    cp = _last_poll("colab")
    now = time.time()
    busy = any(j.get("lease") and now - j["lease"] < _LEASE_SECS for j in q)
    active = [j for j in q if j.get("lease") and now - j["lease"] < _LEASE_SECS]
    rendering = sorted({str(j.get("by") or "laptop") for j in active})
    waiting_colab = sum(1 for j in q if j.get("target") == "colab" and not j.get("lease") and not colab_online())
    return {"worker_online": worker_online() or busy, "worker_busy": busy and not worker_online(),
            "laptop_online": laptop_online(), "colab_online": colab_online(),
            "laptop_gpu": (lp.get("info") or {}).get("gpu") or "", "colab_gpu": (cp.get("info") or {}).get("gpu") or "",
            "worker_kind": "colab" if colab_online() and not laptop_online() else "laptop",
            "worker_gpu": ((cp if colab_online() and not laptop_online() else lp).get("info") or {}).get("gpu") or "",
            "rendering": rendering, "waiting_colab": waiting_colab, "device": render_target() and _LAST_DEVICE["device"],
            "phone_cutouts_online": device_online(), "phone": _last_device_poll().get("info") or {},
            "last_poll_secs": round(time.time() - float(lp.get("t") or 0), 1)
            if lp.get("t") else None, "worker": lp.get("info") or {},
            "queued": len(q), "queued_cutouts": sum(1 for j in q if j["type"] == "cutout"),
            "queued_scenes": sum(1 for j in q if j["type"] == "scene"),
            "library": len(lib), "library_ready": sum(1 for s in lib if s.get("ready")),
            "cutouts": len(list(CUT.glob("*.png")))}
