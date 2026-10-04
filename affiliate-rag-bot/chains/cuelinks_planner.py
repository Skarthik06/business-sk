"""
chains/cuelinks_planner.py — the AI Cuelinks affiliate agent (the `cuelinks-planner` agent).

Given the market catalogue + the panel's constraints, it decides WHERE to focus: it ranks the
markets worth activating, with a one-line reason and a content angle for each, plus a short
overall strategy. Token-efficient and grounded:

  1. Deterministic PRE-FILTER — drop markets below the commission floor / AOV floor and outside
     the chosen categories BEFORE the model sees them, so the LLM only reasons over real, eligible
     candidates and can never invent a market or waste tokens on ineligible ones.
  2. ONE structured LLM call — compact JSON in, compact JSON out (ids from the candidate set only),
     with the exact token usage returned so the panel can show it.

Never raises: any failure falls back to a deterministic ranking so the panel always has a plan.
"""
from __future__ import annotations

from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from config import cfg


class MarketPick(BaseModel):
    id:       str = Field(description="Market id — MUST be one of the candidate ids given.")
    reason:   str = Field(description="One short sentence: why this market fits the constraints.")
    angle:    str = Field(description="A concrete content angle/hook for this market (<= 12 words).")
    priority: int = Field(description="Rank, 1 = activate first.")
    queries:  list[str] = Field(default_factory=list, description=(
        "3-4 product SEARCHES to run on this store (2-4 words each, real product types it sells, "
        "inside the owner's niches/audience), e.g. 'men leather jacket'."))


class CuelinksPlan(BaseModel):
    picks:   list[MarketPick] = Field(description="Ranked markets to activate, best first.")
    summary: str              = Field(description="One or two sentences on the overall focus strategy.")


SYSTEM = (
    "You are an affiliate strategy agent for an Indian Instagram shopping page monetised via "
    "Cuelinks. You are given a shortlist of ELIGIBLE markets (already filtered to the owner's "
    "constraints) and the constraints themselves. Rank the markets the page should ACTIVATE and "
    "post about, best first, up to the max the owner allows.\n"
    "RULES:\n"
    "1. Use ONLY the candidate ids provided — never invent a market.\n"
    "2. Respect the goal: 'commission' = favour the highest payout %, 'volume' = favour broad, "
    "high-frequency low-AOV markets, 'balanced' = the best mix of payout, AOV and audience fit.\n"
    "3. Each pick gets ONE crisp reason and ONE concrete content angle (a hook, not a sentence).\n"
    "4. Return at most the allowed number of picks. Order by priority (1 = first).\n"
    "5. For EACH pick give 3-4 product searches (2-4 words, concrete product types that store "
    "really sells) inside the owner's niches and audience — these are typed into the store's "
    "search, so no slang, no prices, no emojis.\n"
    "Return JSON only — no prose, no markdown."
)
HUMAN = (
    "Constraints (focus_categories = the owner's niches): {constraints}\n"
    "Max picks: {max_active}\n"
    "Eligible candidate markets (id · name · category · commission% · AOV): \n{candidates}\n"
    "Rank the markets to activate."
)


def _llm() -> ChatOpenAI:
    kw: dict = {"model": cfg.openai_model, "api_key": cfg.openai_api_key}
    if cfg.is_reasoning_model:
        kw["max_tokens"] = cfg.llm_max_output_tokens
        kw["reasoning_effort"] = cfg.llm_reasoning_effort
    else:
        kw["max_tokens"] = 700
        kw["temperature"] = 0.3
    return ChatOpenAI(**kw)


def _aov_floor_ok(aov: str, floor: float) -> bool:
    """The AOV band is a string like '₹1k–3k'; treat its LOWER bound as the floor check."""
    if not floor:
        return True
    import re
    m = re.search(r"([\d.]+)\s*([kK]?)", aov or "")
    if not m:
        return True
    lo = float(m.group(1)) * (1000 if m.group(2).lower() == "k" else 1)
    return lo >= floor


def _usable(markets: list[dict]) -> list[dict]:
    """Stores that can generate REAL products right now: not paused (Shopify is parked), and not
    Amazon (it has its own Affiliate panel with your own Associates tag — never via Cuelinks)."""
    return [m for m in markets if not m.get("paused") and m.get("engine") != "amazon"]


def _eligible(markets: list[dict], c: dict) -> list[dict]:
    """Deterministic pre-filter: usable stores, commission floor, AOV floor, focus categories
    (a Marketplace store sells every niche, so it always matches)."""
    cats = set(c.get("focus_categories") or [])
    floor = float(c.get("commission_floor") or 0)
    aov_floor = float(c.get("min_aov") or 0)
    out = []
    for m in _usable(markets):
        if floor and float(m.get("commission") or 0) < floor:
            continue
        if cats and m.get("category") not in cats and m.get("category") != "Marketplace":
            continue
        if aov_floor and not _aov_floor_ok(m.get("aov", ""), aov_floor):
            continue
        out.append(m)
    return out


def _deterministic(cands: list[dict], c: dict, max_active: int) -> dict:
    """Fallback plan (no LLM): rank by the goal, so the panel always has a usable plan."""
    goal = (c.get("goal") or "balanced").lower()
    if goal == "commission":
        key = lambda m: float(m.get("commission") or 0)
    elif goal == "volume":
        # low AOV + broad categories = frequency plays
        broad = {"Marketplace", "Grocery"}
        key = lambda m: (m.get("category") in broad, -_aov_low(m))
    else:
        key = lambda m: float(m.get("commission") or 0) * 1.0
    ranked = sorted(cands, key=key, reverse=True)[:max_active]
    picks = [{"id": m["id"], "reason": f"{m['commission']}% payout · {m['category']} · AOV {m['aov']}",
              "angle": m.get("note", ""), "priority": i + 1, "queries": _default_queries(m, c)}
             for i, m in enumerate(ranked)]
    return {"picks": picks, "summary": f"Deterministic {goal} ranking of {len(picks)} eligible markets.",
            "tokens": {"input": 0, "output": 0, "total": 0}, "eligible": len(cands), "ai": False}


# niche → searches, for the no-LLM fallback (and to top up a pick the model gave too few for)
_NICHE_QUERIES = {
    "Fashion": ["leather jacket", "oversized t-shirt", "cargo pants", "casual sneakers"],
    "Beauty": ["vitamin c serum", "matte lipstick", "sunscreen spf 50", "face wash"],
    "Grooming": ["beard trimmer", "beard oil", "perfume", "hair wax"],
    "Electronics": ["wireless earbuds", "smartwatch", "power bank", "bluetooth speaker"],
    "Home": ["table lamp", "bedsheet double", "wall clock", "storage organiser"],
    "Marketplace": ["sneakers", "smartwatch", "backpack", "sunglasses"],
}


def _default_queries(m: dict, c: dict) -> list[str]:
    cats = [x for x in (c.get("focus_categories") or []) if x in _NICHE_QUERIES] or [m.get("category") or "Marketplace"]
    aud = (c.get("audience") or "").strip().lower()
    aud = aud if aud in ("men", "women", "kids", "unisex") else ""
    out: list[str] = []
    for i in range(4):
        for cat in cats:
            q = _NICHE_QUERIES.get(cat, _NICHE_QUERIES["Marketplace"])[i % 4]
            q = f"{aud} {q}".strip() if aud and cat in ("Fashion", "Marketplace") else q
            if q not in out:
                out.append(q)
    return out[:4]


def _clean_queries(qs, m: dict, c: dict) -> list[str]:
    out: list[str] = []
    for q in qs or []:
        q = " ".join(str(q).replace("#", " ").split())[:40].strip(" .,-")
        if len(q) >= 2 and q.lower() not in [x.lower() for x in out]:
            out.append(q)
    for q in _default_queries(m, c):                 # always 3-4 to tap
        if len(out) >= 3:
            break
        if q.lower() not in [x.lower() for x in out]:
            out.append(q)
    return out[:4]


def _aov_low(m: dict) -> float:
    import re
    x = re.search(r"([\d.]+)\s*([kK]?)", m.get("aov", "") or "")
    return float(x.group(1)) * (1000 if x and x.group(2).lower() == "k" else 1) if x else 0.0


async def plan_markets(markets: list[dict], constraints: dict) -> dict:
    """Rank the markets to activate. Returns {picks, summary, tokens, eligible, ai}. Never raises."""
    c = constraints or {}
    cands = _eligible(markets, c)
    if not cands:                                    # constraints too tight → widen to every usable store
        cands = _usable(markets)
    max_active = max(1, min(int(c.get("max_active") or 8), len(cands) or 1))
    cand_lines = "\n".join(
        f"- {m['id']} · {m['name']} · {m['category']} · {m['commission']}% · {m['aov']}" for m in cands)
    try:
        chain = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", HUMAN)]) \
            | _llm().with_structured_output(CuelinksPlan, include_raw=True)
        res = await chain.ainvoke({"constraints": _compact(c), "max_active": max_active,
                                   "candidates": cand_lines})
        parsed: Optional[CuelinksPlan] = res.get("parsed")
        usage = getattr(res.get("raw"), "usage_metadata", None) or {}
        tokens = {"input": int(usage.get("input_tokens") or 0),
                  "output": int(usage.get("output_tokens") or 0),
                  "total": int(usage.get("total_tokens") or 0)}
        by_id = {m["id"]: m for m in cands}
        picks, seen = [], set()
        for p in (parsed.picks if parsed else []) or []:
            if p.id in by_id and p.id not in seen:
                seen.add(p.id)
                picks.append({"id": p.id, "reason": (p.reason or "").strip()[:140],
                              "angle": (p.angle or "").strip()[:80], "priority": int(p.priority or len(picks) + 1),
                              "queries": _clean_queries(p.queries, by_id[p.id], c)})
        picks = sorted(picks, key=lambda x: x["priority"])[:max_active]
        if not picks:
            return _deterministic(cands, c, max_active)
        return {"picks": picks, "summary": (parsed.summary or "").strip()[:220] if parsed else "",
                "tokens": tokens, "eligible": len(cands), "ai": True}
    except Exception:
        return _deterministic(cands, c, max_active)


def _compact(c: dict) -> str:
    """A tiny one-line JSON-ish constraints string (token-frugal)."""
    import json
    keep = {k: c.get(k) for k in ("goal", "focus_categories", "commission_floor", "min_aov", "audience", "content_style") if c.get(k) not in (None, "", [])}
    return json.dumps(keep, separators=(",", ":"))
