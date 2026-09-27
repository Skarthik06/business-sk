"""Strategy Engine core: routes, domain×route health, circuit breakers, scoring.

A ROUTE is one way to fetch a page:  direct_http · direct_browser · worker_http:<id> ·
worker_browser:<id> · proxy_http:<id> · proxy_browser:<id>.
Nothing is hard-coded per site: the engine picks the healthiest eligible route from recorded history.
Per-domain PRIORS (strategies/domains.yaml `routes:`) only seed the first guess — e.g. a site known to
reject datacenter IPs starts with its direct circuit OPEN, and a periodic half-open probe lets it
recover if that ever changes. Weights are configurable (SCRAPER_ROUTE_WEIGHTS)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from . import store
from .classifier import Outcome

# failures that say something about the ROUTE (not the URL / request)
ROUTE_FAILURES = {Outcome.NETWORK_ERROR, Outcome.ACCESS_DENIED, Outcome.CHALLENGE, Outcome.RATE_LIMITED,
                  Outcome.SERVER_ERROR}

DEFAULT_WEIGHTS = {"success": 0.50, "availability": 0.20, "latency": 0.10, "recent": 0.10, "failure": 0.10}
# prior success rates per route kind: the server's own connection first (free, always on), workers
# are a scarce resource kept for sites that need them (their priors rise via `residential`), paid
# proxies last; http is preferred over the heavier browser
DEFAULT_PRIORS = {"direct_http": 0.80, "direct_browser": 0.65, "worker_http": 0.70, "worker_browser": 0.60,
                  "proxy_http": 0.60, "proxy_browser": 0.50}


def weights() -> Dict[str, float]:
    try:
        w = {**DEFAULT_WEIGHTS, **json.loads(os.getenv("SCRAPER_ROUTE_WEIGHTS") or "{}")}
        return {k: float(v) for k, v in w.items()}
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


CB_THRESHOLD = int(_num("SCRAPER_CIRCUIT_FAILURES", 5))        # consecutive failures → OPEN
CB_COOLDOWN = _num("SCRAPER_CIRCUIT_COOLDOWN_SECS", 600)       # first cooldown; doubles per re-open
CB_MAX_COOLDOWN = _num("SCRAPER_CIRCUIT_MAX_COOLDOWN_SECS", 21600)


@dataclass
class Route:
    id: str                    # e.g. "worker_http:laptop-01"
    kind: str                  # direct | worker | proxy
    mode: str                  # http | browser
    availability: float = 1.0  # 1 online · 0.5 stale · 0 offline
    ref: Any = None            # Proxy / worker info

    @property
    def family(self) -> str:   # "worker_http" etc. — the key priors are expressed in
        return f"{self.kind}_{self.mode}"


@dataclass
class Health:
    recent: Deque[int] = field(default_factory=lambda: deque(maxlen=20))   # 1 = success
    attempts: int = 0
    successes: int = 0
    ewma_ms: Optional[float] = None
    last_success: float = 0.0
    last_failure: float = 0.0
    consecutive_failures: int = 0


@dataclass
class Circuit:
    state: str = "CLOSED"      # CLOSED | OPEN | HALF_OPEN
    open_until: float = 0.0
    opens: int = 0
    probe_in_flight: bool = False
    reason: str = ""


class RouteBook:
    """In-memory health + circuits (one scraper_api process), persisted to Postgres."""

    def __init__(self):
        self.health: Dict[Tuple[str, str], Health] = {}
        self.circuits: Dict[Tuple[str, str], Circuit] = {}
        self.lock = asyncio.Lock()

    # ── persistence ──────────────────────────────────────────────────────────
    async def load(self) -> None:
        for r in await store.q("select * from route_health") or []:
            h = Health(attempts=r["attempts"], successes=r["successes"], ewma_ms=r["ewma_ms"],
                       last_success=r["last_success"] or 0.0, last_failure=r["last_failure"] or 0.0,
                       consecutive_failures=r["consecutive_failures"])
            h.recent.extend(int(c) for c in (r["recent"] or ""))
            self.health[(r["domain"], r["route"])] = h
        for r in await store.q("select * from circuit_breakers") or []:
            self.circuits[(r["domain"], r["route"])] = Circuit(r["state"], r["open_until"] or 0.0, r["opens"], False,
                                                              r["reason"] or "")

    async def _save(self, domain: str, route: str) -> None:
        if store.pool is None:                       # unit tests / no database attached
            return
        h = self.health.get((domain, route))
        c = self.circuits.get((domain, route))
        if h:
            await store.q("""insert into route_health (domain, route, attempts, successes, recent, ewma_ms,
                               last_success, last_failure, consecutive_failures, updated_at)
                             values (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                             on conflict (domain, route) do update set attempts=excluded.attempts,
                               successes=excluded.successes, recent=excluded.recent, ewma_ms=excluded.ewma_ms,
                               last_success=excluded.last_success, last_failure=excluded.last_failure,
                               consecutive_failures=excluded.consecutive_failures, updated_at=now()""",
                          (domain, route, h.attempts, h.successes, "".join(map(str, h.recent)), h.ewma_ms,
                           h.last_success, h.last_failure, h.consecutive_failures))
        if c:
            await store.q("""insert into circuit_breakers (domain, route, state, open_until, opens, reason, updated_at)
                             values (%s,%s,%s,%s,%s,%s,now())
                             on conflict (domain, route) do update set state=excluded.state,
                               open_until=excluded.open_until, opens=excluded.opens, reason=excluded.reason,
                               updated_at=now()""",
                          (domain, route, c.state, c.open_until, c.opens, c.reason))

    # ── circuits ─────────────────────────────────────────────────────────────
    def circuit(self, domain: str, route: Route, prior: Dict[str, Any]) -> Circuit:
        key = (domain, route.id)
        if key not in self.circuits:
            c = Circuit()
            if str(prior.get("circuit", "")).lower() == "open":      # known-bad seed → probe later
                c.state, c.open_until = "OPEN", time.time() + float(prior.get("cooldown", CB_MAX_COOLDOWN))
                c.reason = "prior: this site rejects this route"
            self.circuits[key] = c
        return self.circuits[key]

    def allowed(self, domain: str, route: Route, prior: Dict[str, Any], now: Optional[float] = None) -> bool:
        """CLOSED → yes · OPEN → no until cooldown, then ONE half-open probe · HALF_OPEN → only that probe."""
        now = now or time.time()
        c = self.circuit(domain, route, prior)
        if c.state == "CLOSED":
            return True
        if c.state == "OPEN" and now >= c.open_until:
            c.state, c.probe_in_flight = "HALF_OPEN", False
        if c.state == "HALF_OPEN" and not c.probe_in_flight:
            return True
        return False

    def claim_probe(self, domain: str, route: Route) -> None:
        c = self.circuits.get((domain, route.id))
        if c and c.state == "HALF_OPEN":
            c.probe_in_flight = True

    # ── recording ────────────────────────────────────────────────────────────
    async def record(self, domain: str, route: Route, outcome: Outcome, ms: int, prior: Dict[str, Any]) -> None:
        key = (domain, route.id)
        now = time.time()
        async with self.lock:
            h = self.health.setdefault(key, Health())
            c = self.circuit(domain, route, prior)
            counts = outcome == Outcome.SUCCESS or outcome in ROUTE_FAILURES
            if counts:
                ok = outcome == Outcome.SUCCESS
                h.attempts += 1
                h.recent.append(1 if ok else 0)
                if ok:
                    h.successes += 1
                    h.last_success = now
                    h.consecutive_failures = 0
                    h.ewma_ms = ms if h.ewma_ms is None else 0.7 * h.ewma_ms + 0.3 * ms
                    c.state, c.opens, c.probe_in_flight, c.reason = "CLOSED", 0, False, ""
                else:
                    h.last_failure = now
                    h.consecutive_failures += 1
                    if c.state == "HALF_OPEN" or h.consecutive_failures >= CB_THRESHOLD:
                        c.opens += 1
                        c.state = "OPEN"
                        c.open_until = now + min(CB_MAX_COOLDOWN, CB_COOLDOWN * 2 ** (c.opens - 1))
                        c.probe_in_flight = False
                        c.reason = f"{h.consecutive_failures} consecutive failures ({outcome.value})"
            elif c.state == "HALF_OPEN":
                c.probe_in_flight = False            # URL-level error: the probe says nothing
        await self._save(domain, route.id)

    # ── scoring ──────────────────────────────────────────────────────────────
    def score(self, domain: str, route: Route, prior_success: float, now: Optional[float] = None,
              w: Optional[Dict[str, float]] = None) -> float:
        """score = 0.50·success + 0.20·availability + 0.10·latency + 0.10·recent − 0.10·failure (configurable)."""
        now = now or time.time()
        w = w or weights()
        h = self.health.get((domain, route.id)) or Health()
        k = 3.0                                                        # prior strength (pseudo-attempts)
        success = (sum(h.recent) + prior_success * k) / (len(h.recent) + k)
        default_ms = 2500 if route.mode == "http" else 6000
        latency = 1.0 / (1.0 + (h.ewma_ms or default_ms) / 5000.0)
        recent = max(0.0, 1.0 - (now - h.last_success) / 3600.0) if h.last_success else 0.0
        failure = min(1.0, h.consecutive_failures / 5.0)
        return (w["success"] * success + w["availability"] * route.availability + w["latency"] * latency
                + w["recent"] * recent - w["failure"] * failure)

    def rank(self, domain: str, routes: List[Route], priors: Dict[str, Dict[str, Any]],
             exclude: set) -> List[Tuple[float, Route]]:
        """Eligible routes (online, circuit allows, not already tried for this request), best first."""
        out = []
        now = time.time()
        for r in routes:
            if r.id in exclude or r.availability <= 0:
                continue
            p = priors.get(r.family, {})
            if not self.allowed(domain, r, p, now):
                continue
            out.append((self.score(domain, r, float(p.get("success", DEFAULT_PRIORS.get(r.family, 0.6))), now), r))
        return sorted(out, key=lambda x: x[0], reverse=True)

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        rows = []
        for (d, rid), h in self.health.items():
            c = self.circuits.get((d, rid)) or Circuit()
            rows.append({"domain": d, "route": rid, "attempts": h.attempts,
                         "success_rate": round(sum(h.recent) / len(h.recent), 3) if h.recent else None,
                         "latency_ms": int(h.ewma_ms) if h.ewma_ms else None,
                         "consecutive_failures": h.consecutive_failures, "circuit": c.state,
                         "reopens_in_s": max(0, int(c.open_until - now)) if c.state == "OPEN" else 0})
        circuits = [{"domain": d, "route": rid, "state": c.state, "reason": c.reason,
                     "reopens_in_s": max(0, int(c.open_until - now)) if c.state == "OPEN" else 0}
                    for (d, rid), c in self.circuits.items() if c.state != "CLOSED"]
        return {"routes": sorted(rows, key=lambda r: (r["domain"], r["route"])), "open_circuits": circuits}


book = RouteBook()
