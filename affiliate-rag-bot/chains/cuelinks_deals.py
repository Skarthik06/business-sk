"""
chains/cuelinks_deals.py — AI copy for a Cuelinks DEALS post (the deals-content agent).

Given a batch of live Cuelinks offers (merchant, category, discount, coupon), ONE structured LLM
call writes the whole post: a cover headline + subtitle, a single carousel caption + hashtags, and
one short hook per deal (in order) for its card. Token-efficient (one call, compact JSON out),
grounded (uses only the given offers), and never raises — a deterministic fallback always returns
usable copy so a post is never blocked.
"""
from __future__ import annotations

from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from config import cfg


class DealCopy(BaseModel):
    cover_title:    str       = Field(description="Punchy cover headline for the deals post (<= 6 words).")
    cover_subtitle: str       = Field(description="One short supporting line for the cover.")
    caption:        str       = Field(description="One Instagram caption for the whole carousel (2-4 lines, warm, no emojis spam).")
    hashtags:       list[str] = Field(description="6-12 relevant hashtags, no '#'.")
    hooks:          list[str] = Field(description="One short hook (<= 8 words) per deal, IN THE SAME ORDER as given.")


SYSTEM = (
    "You are a witty Indian shopping-deals copywriter for an Instagram page. You are given a list "
    "of LIVE deals (merchant · category · discount% · coupon). Write the post copy: a cover "
    "headline + subtitle, ONE carousel caption with a soft CTA to comment for the link, relevant "
    "hashtags, and ONE short hook per deal in the SAME ORDER. Be truthful — use only the given "
    "discounts and merchants, never invent numbers. No price is shown. Return JSON only."
)
HUMAN = "Deals (in order):\n{deals}\nWrite the post copy (hooks in the same order)."


def _llm() -> ChatOpenAI:
    kw: dict = {"model": cfg.openai_model, "api_key": cfg.openai_api_key}
    if cfg.is_reasoning_model:
        kw["max_tokens"] = cfg.llm_max_output_tokens
        kw["reasoning_effort"] = cfg.llm_reasoning_effort
    else:
        kw["max_tokens"] = 900
        kw["temperature"] = 0.5
    return ChatOpenAI(**kw)


def _fallback(deals: list[dict]) -> dict:
    hooks = []
    for d in deals:
        pct = d.get("discount")
        hooks.append(f"{int(pct)}% off at {d.get('merchant','')}".strip() if pct else f"Deal at {d.get('merchant','')}".strip())
    return {
        "cover_title": "Today's Best Deals",
        "cover_subtitle": f"{len(deals)} hand-picked offers",
        "caption": "Fresh deals just dropped 🛍️ Comment “LINK” and we’ll DM you every offer. Save now — these don’t last.",
        "hashtags": ["deals", "offers", "shopping", "discount", "india", "sale", "coupons", "shopnow"],
        "hooks": hooks,
        "tokens": {"input": 0, "output": 0, "total": 0},
        "ai": False,
    }


async def compose_deal_post(deals: list[dict]) -> dict:
    """Return {cover_title, cover_subtitle, caption, hashtags, hooks[], tokens, ai}. Never raises."""
    if not deals:
        return {**_fallback([]), "hooks": []}
    lines = "\n".join(
        f"- {d.get('merchant','?')} · {d.get('category','')} · {d.get('discount') or '—'}% · {d.get('title','')[:70]}"
        for d in deals)
    try:
        chain = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", HUMAN)]) \
            | _llm().with_structured_output(DealCopy, include_raw=True)
        res = await chain.ainvoke({"deals": lines})
        parsed: Optional[DealCopy] = res.get("parsed")
        usage = getattr(res.get("raw"), "usage_metadata", None) or {}
        tokens = {"input": int(usage.get("input_tokens") or 0),
                  "output": int(usage.get("output_tokens") or 0),
                  "total": int(usage.get("total_tokens") or 0)}
        if not parsed:
            return _fallback(deals)
        hooks = [(h or "").strip()[:60] for h in (parsed.hooks or [])]
        while len(hooks) < len(deals):                      # pad so every deal has a hook
            hooks.append(f"{deals[len(hooks)].get('discount') or ''}% off".strip("% "))
        return {
            "cover_title": (parsed.cover_title or "Today's Best Deals").strip()[:48],
            "cover_subtitle": (parsed.cover_subtitle or "").strip()[:70],
            "caption": (parsed.caption or "").strip(),
            "hashtags": [re_h.strip().lstrip("#") for re_h in (parsed.hashtags or []) if re_h.strip()][:12],
            "hooks": hooks[:len(deals)],
            "tokens": tokens,
            "ai": True,
        }
    except Exception:
        return _fallback(deals)
