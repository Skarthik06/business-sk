"""GPU Watchdog agent (spec: app/agents/gpu-watchdog.agents.md).

Watches the render GPUs and raises alerts when Google Colab needs a human tap (Open → Run all):
Colab needed (phone posts waiting), Colab stopped, session ending, key expiring. Read-only: it never
starts or controls Colab (Colab's free tier forbids automating the notebook). Alerts are shown by
the Studio (/api/sk/scenes) and pushed to the phone by SK Helper (/api/gpu/device/jobs).
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List

from app.services import scene_store

NOTEBOOK = "https://colab.research.google.com/github/Skarthik06/business-sk/blob/main/colab/sk_gpu_worker.ipynb"
_STATE = scene_store.ROOT / "watchdog.json"
_LOCK = threading.Lock()


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


TICK = _num("SK_WATCHDOG_TICK_SECS", 30)
REPEAT = _num("SK_WATCHDOG_REPEAT_MINUTES", 15) * 60
WARN_HOURS = _num("SK_WATCHDOG_WARN_HOURS", 11)
KEY_WARN_DAYS = _num("SK_WATCHDOG_KEY_WARN_DAYS", 7)
ALERT_TTL = 2 * 3600


def _load() -> Dict[str, Any]:
    try:
        return json.loads(_STATE.read_text("utf-8"))
    except Exception:
        return {"colab_on": False, "colab_since": 0, "last": {}, "alerts": []}


def _save(st: Dict[str, Any]) -> None:
    tmp = _STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), "utf-8")
    tmp.replace(_STATE)


def _raise(st: Dict[str, Any], rule: str, message: str, actionable: bool = True) -> None:
    now = time.time()
    if now - float(st["last"].get(rule) or 0) < REPEAT:
        return
    st["last"][rule] = now
    st["alerts"].append({"id": f"{rule}-{int(now)}", "rule": rule, "message": message,
                         "actionable": actionable, "url": NOTEBOOK, "at": now})


def tick() -> None:
    """One check (W1–W5). Safe to call any time; never raises."""
    try:
        now = time.time()
        with _LOCK:
            st = _load()
            status = scene_store.status()
            on = bool(status.get("colab_online"))
            if on and not st.get("colab_on"):                                   # W4 back online
                st["colab_since"] = now
                st["alerts"] = [a for a in st["alerts"] if a["rule"] not in ("W1", "W2")]
                _raise(st, "W4", f"☁️ Colab is online ({status.get('colab_gpu') or 'GPU'}) — rendering phone posts", False)
                st["last"].pop("W3", None)
            if not on and st.get("colab_on"):                                   # W2 stopped
                _raise(st, "W2", "☁️ Colab stopped — open Colab → Run all to keep rendering phone posts")
            if not on and int(status.get("waiting_colab") or 0) > 0:            # W1 needed
                _raise(st, "W1", f"☁️ {status['waiting_colab']} render job(s) need Colab — open Colab → Run all")
            if on and st.get("colab_since") and now - st["colab_since"] > WARN_HOURS * 3600:   # W3
                _raise(st, "W3", "☁️ Colab has run ~12 h — Google ends free sessions soon; Run all again after it stops")
            exp = scene_store.colab_key_expiry()
            if exp and exp - now < KEY_WARN_DAYS * 86400:                      # W5
                _raise(st, "W5", f"🔑 Your Colab key expires in {max(0, int((exp - now) // 86400))} day(s) — create a new one in Overview → Render GPUs")
            st["colab_on"] = on
            st["alerts"] = [a for a in st["alerts"] if now - a["at"] < ALERT_TTL][-20:]
            _save(st)
    except Exception as e:                                                      # noqa: BLE001
        print(f"[gpu-watchdog] tick failed: {e}", flush=True)


def alerts(actionable_only: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    return [a for a in _load().get("alerts", []) if now - a["at"] < ALERT_TTL and (a["actionable"] or not actionable_only)]


def start() -> None:
    def _loop():
        while True:
            tick()
            time.sleep(TICK)
    threading.Thread(target=_loop, name="gpu-watchdog", daemon=True).start()
