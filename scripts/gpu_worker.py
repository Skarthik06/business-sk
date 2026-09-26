"""Business-SK GPU worker — runs on the laptop (RTX GPU), serves the AI Art Director.

  cutout job : download the product photo → BiRefNet mask → RGBA PNG with the ORIGINAL pixels
               (only the alpha is computed) + measured facts (cropped person? aspect, colours)
  scene job  : Z-Image-Turbo (4-bit) paints an EMPTY backdrop from the art director's prompt

Polls the cloud (/api/gpu/worker/jobs) and posts results back (/api/gpu/worker/result).
Runs from the dedicated AI venv:  %USERPROFILE%\\sk-ai\\venv\\Scripts\\pythonw.exe scripts\\gpu_worker.py
Token: env GPU_WORKER_TOKEN or ~/.sk_worker_token (same file the scrape worker uses).
Knobs (env): SK_API (server), GPU_IDLE_UNLOAD_SECS (free the Z-Image VRAM after idle, default 1800).
"""
from __future__ import annotations

import base64
import gc
import io
import json
import os
import sys
import time
import traceback
import urllib.request

API = os.getenv("SK_API", "https://140-238-247-18.nip.io").rstrip("/")
VER = "1.1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
IDLE_UNLOAD = int(os.getenv("GPU_IDLE_UNLOAD_SECS", "1800"))
EMPTY = ("completely empty scene, no people, no person, no mannequin, no clothes, no products, no text, "
         "no logo, clear open space in the center and lower half, photorealistic, editorial photography, "
         "shot on medium format, soft natural shadows, high detail")
LOG = os.path.join(os.path.expanduser("~"), "sk-ai", "gpu_worker.log")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _token() -> str:
    t = os.getenv("GPU_WORKER_TOKEN", "").strip()
    if not t:
        try:
            with open(os.path.join(os.path.expanduser("~"), ".sk_worker_token"), encoding="utf-8") as f:
                t = f.read().strip()
        except Exception:
            t = ""
    return t


def _get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "sk-gpu-worker/" + VER})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url: str, obj: dict, timeout: int = 120):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                          "User-Agent": "sk-gpu-worker/" + VER})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


# ── models ───────────────────────────────────────────────────────────────────
class Models:
    def __init__(self):
        import torch
        self.torch = torch
        self.gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self._bir = None
        self._zimg = None
        self.last_scene = 0.0

    def birefnet(self):
        if self._bir is None:
            from transformers import AutoModelForImageSegmentation
            m = AutoModelForImageSegmentation.from_pretrained("ZhengPeng7/BiRefNet", trust_remote_code=True)
            self._bir = (m.to(self.dev).eval().half() if self.dev == "cuda" else m.float().eval())
            log(f"BiRefNet loaded on {self.dev}")
        return self._bir

    def zimage(self):
        if self._zimg is None:
            from diffusers import ZImagePipeline
            t = time.time()
            pipe = ZImagePipeline.from_pretrained("unsloth/Z-Image-Turbo-unsloth-bnb-4bit",
                                                  torch_dtype=self.torch.bfloat16)
            try:
                pipe.to("cuda")
            except Exception:
                pipe.enable_model_cpu_offload()
            self._zimg = pipe
            log(f"Z-Image-Turbo loaded in {time.time() - t:.0f}s")
        self.last_scene = time.time()
        return self._zimg

    def maybe_unload(self):
        if self._zimg is not None and time.time() - self.last_scene > IDLE_UNLOAD:
            self._zimg = None
            gc.collect()
            if self.dev == "cuda":
                self.torch.cuda.empty_cache()
            log("Z-Image unloaded (idle) — GPU memory freed")


# ── jobs ─────────────────────────────────────────────────────────────────────
_FACE_MODEL = os.path.join(os.path.expanduser("~"), "sk-ai", "models", "face_detection_yunet_2023mar.onnx")


def _has_face(img):
    """True/False if a face is visible (YuNet, OpenCV model zoo); None if the detector is unavailable."""
    try:
        import cv2
        import numpy as np
        if not os.path.exists(_FACE_MODEL):
            return None
        small = img.copy()
        small.thumbnail((640, 640))
        bgr = cv2.cvtColor(np.array(small.convert("RGB")), cv2.COLOR_RGB2BGR)
        det = cv2.FaceDetectorYN.create(_FACE_MODEL, "", (bgr.shape[1], bgr.shape[0]), 0.8)
        _, faces = det.detect(bgr)
        return faces is not None and len(faces) > 0
    except Exception:
        return None


def do_cutout(M: Models, job: dict) -> tuple[str, dict]:
    from PIL import Image, ImageFilter
    from torchvision import transforms
    torch = M.torch
    img = Image.open(io.BytesIO(_download(job["url"]))).convert("RGB")
    tf = transforms.Compose([transforms.Resize((1024, 1024)), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    x = tf(img).unsqueeze(0).to(M.dev)
    if M.dev == "cuda":
        x = x.half()
    with torch.no_grad():
        pred = M.birefnet()(x)[-1].sigmoid().float().cpu()[0, 0]
    mask = transforms.functional.to_pil_image(pred).resize(img.size, Image.BILINEAR)
    # edge clean-up: pull the soft edge in by ~1px so no white studio halo survives; RGB untouched
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    rgba = img.copy()
    rgba.putalpha(mask)
    # Crop TIGHT to the visible product: solid pixels only (alpha > 128), with tiny specks and
    # faint halo noise removed first (open = erode then dilate). A loose box made the product
    # render small and float above the panel.
    solid = mask.point(lambda v: 255 if v > 128 else 0).filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    bbox = solid.getbbox() or mask.point(lambda v: 255 if v > 10 else 0).getbbox() or (0, 0, img.width, img.height)
    pad = max(2, int(0.01 * max(img.size)))                    # keep the soft edge, not the noise
    bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(img.width, bbox[2] + pad), min(img.height, bbox[3] + pad))
    cut = rgba.crop(bbox)
    if max(cut.size) > 1600:
        cut.thumbnail((1600, 1600), Image.LANCZOS)
    # measured facts for the art director
    touches_bottom = bbox[3] >= img.height - max(3, img.height // 100)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    aspect = round(bw / max(1, bh), 2)
    alpha = cut.split()[-1]
    fill = round(sum(1 for v in alpha.getdata() if v > 128) / max(1, cut.width * cut.height), 2)
    small = cut.copy()
    small.thumbnail((96, 96))
    solid = Image.new("RGB", small.size, (255, 255, 255))
    solid.paste(small, mask=small.split()[-1])
    pal = solid.quantize(colors=4).convert("RGB").getcolors(96 * 96) or []
    colors = ["#%02X%02X%02X" % c for _, c in sorted(pal, reverse=True) if c != (255, 255, 255)][:3]
    # A MODEL shot = a face is visible (YuNet face detector). Fallback without the model file: a
    # silhouette rule (wide at the bottom edge, narrow at the top).
    face = _has_face(img)
    if face is None:
        def _row_width(frac: float) -> float:
            y = min(alpha.height - 1, max(0, int(alpha.height * frac)))
            return sum(1 for x in range(alpha.width) if alpha.getpixel((x, y)) > 128) / max(1, alpha.width)
        face = touches_bottom and aspect < 1.0 and _row_width(0.97) >= 0.45 and _row_width(0.08) <= 0.6
    subject = "person" if face else "object"
    buf = io.BytesIO()
    cut.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), {
        "subject": subject, "touches_bottom": touches_bottom, "aspect": aspect, "colors": colors, "fill": fill}


def do_scene(M: Models, job: dict) -> tuple[str, dict]:
    torch = M.torch
    pipe = M.zimage()
    prompt = f"{job['prompt']}, {EMPTY}"
    img = pipe(prompt=prompt, height=int(job.get("h", 1280)), width=int(job.get("w", 1024)),
               num_inference_steps=9, guidance_scale=0.0,
               generator=torch.Generator(M.dev).manual_seed(int(job.get("seed", 0)))).images[0]
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92)
    M.last_scene = time.time()
    return base64.b64encode(buf.getvalue()).decode("ascii"), {}


def main() -> None:
    tok = _token()
    if not tok:
        log("No token: set GPU_WORKER_TOKEN or create ~/.sk_worker_token")
        sys.exit(1)
    M = Models()
    M.birefnet()                                   # fast (~1 GB) — keep warm for cut-outs
    log(f"GPU worker {VER} online · {M.gpu} · {API}")
    # heartbeat while busy: a scene paint takes ~2 min; tell the server we're alive every 20 s
    import threading
    busy = {"on": False}

    def _beat():
        while True:
            time.sleep(20)
            if busy["on"]:
                try:
                    _get_json(f"{API}/api/gpu/worker/jobs?token={tok}&hb=1&gpu={urllib.request.quote(M.gpu)}&ver={VER}", timeout=15)
                except Exception:
                    pass
    threading.Thread(target=_beat, daemon=True).start()
    idle = 0
    while True:
        try:
            jobs = _get_json(f"{API}/api/gpu/worker/jobs?token={tok}&n=4&gpu={urllib.request.quote(M.gpu)}&ver={VER}"
                             ).get("jobs", [])
        except Exception as e:
            log(f"poll failed: {str(e)[:120]}")
            time.sleep(15)
            continue
        if not jobs:
            idle += 1
            M.maybe_unload()
            time.sleep(3 if idle < 20 else 8)
            continue
        idle = 0
        busy["on"] = True
        for job in jobs:
            t = time.time()
            try:
                b64, meta = (do_cutout if job["type"] == "cutout" else do_scene)(M, job)
                _post_json(f"{API}/api/gpu/worker/result",
                           {"token": tok, "job_id": job["id"], "b64": b64, "meta": meta})
                log(f"{job['type']} {job['id']} done in {time.time() - t:.1f}s {meta if meta else ''}")
            except Exception as e:
                log(f"{job['type']} {job['id']} FAILED: {str(e)[:200]}")
                if "CUDA" in str(e):
                    log(traceback.format_exc()[-600:])
        busy["on"] = False


if __name__ == "__main__":
    while True:                                    # self-healing: never die for good
        try:
            main()
        except KeyboardInterrupt:
            break
        except SystemExit:
            raise
        except Exception as e:
            log(f"worker crashed, restarting in 20s: {e}")
            time.sleep(20)
