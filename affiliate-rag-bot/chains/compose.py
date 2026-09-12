"""
chains/compose.py  —  ONE structured LLM call: rank + write.

Replaces the old two-stage design (ranker.py + content.py, which made
1 + N calls and re-sent the trends/RAG context on every content call).

This module makes a SINGLE OpenAI call per run that:
  1. picks the best `count` products from the candidate list, and
  2. writes the full Pinterest pin (title, description, hashtags) for each,

returning one schema-validated JSON object. We use LangChain's
`with_structured_output(...)`, so the model is forced (via function calling)
to return data matching the Pydantic schema — no fragile text parsing, no
retries on malformed JSON, no wasted tokens.

Token discipline:
  - candidates are sent as compact pipe-delimited lines (not pretty JSON)
  - product titles are truncated (Amazon titles are keyword-stuffed)
  - trends + RAG context are sent ONCE, not once-per-product
  - the system prompt is fully static (cache-friendly prefix)
"""
from __future__ import annotations

from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from config import cfg
from utils.logger import log
from chains.hashtags import merge_hashtags
from chains.validate import audit_caption

FTC = "#Ad | As an Amazon Associate I earn from qualifying purchases."


# ── Unicode "bold" for Instagram captions (IG has no markdown; these glyphs render bold) ──
def _to_bold(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if 0x41 <= o <= 0x5A:      # A-Z → 𝗔-𝗭 (sans-serif bold)
            out.append(chr(0x1D5D4 + o - 0x41))
        elif 0x61 <= o <= 0x7A:    # a-z → 𝗮-𝘇
            out.append(chr(0x1D5EE + o - 0x61))
        elif 0x30 <= o <= 0x39:    # 0-9 → 𝟬-𝟵
            out.append(chr(0x1D7EC + o - 0x30))
        else:
            out.append(ch)
    return "".join(out)


def _bold_caption(cap: str) -> str:
    """Bold the money-grabbing bits: product/brand names on list lines, prices, and % off.
    Instagram renders these Unicode glyphs as bold, so the deal pops without markdown."""
    import re
    lines = cap.split("\n")
    out = []
    for ln in lines:
        # On a product line (has an em/en dash AND a ₹ price), bold the name before the dash.
        if "₹" in ln and ("—" in ln or "–" in ln):
            dash = "—" if "—" in ln else "–"
            head, _, rest = ln.partition(dash)
            m = re.match(r"^([\-•\d\).\s]*)(.+?)\s*$", head)  # strip bullet/number marker
            if m and m.group(2):
                head = m.group(1) + _to_bold(m.group(2).strip()) + " "
            ln = head + dash + rest
        # Bold prices (keep the ₹ symbol) and discounts everywhere.
        ln = re.sub(r"₹\s?([\d,]+(?:\.\d+)?)", lambda x: "₹" + _to_bold(x.group(1)), ln)
        ln = re.sub(r"(\d+)\s?%\s?off", lambda x: _to_bold(x.group(1)) + "% off", ln, flags=re.I)
        out.append(ln)
    return "\n".join(out)

# Trim knobs — the levers that control input noise/tokens.
MAX_CANDIDATES = 25   # cap how many scraped products the LLM sees (== scrape cap)
TITLE_CHARS    = 70   # truncate each candidate title
MAX_TRENDS     = 6
MAX_EXAMPLES   = 2    # past-pin style examples
MAX_IDEAS      = 5    # proven product-type names


# ─── Output schema (guaranteed by structured output) ─────────────────────────

# Content styles (Phase 4 A/B). The composer tags each caption with the style it used,
# so performance can later be compared per style (see agents/content-intelligence).
STYLES = ["DEAL_DROP", "STORY", "LISTICLE", "PROBLEM_SOLUTION", "QUESTION",
          "TRANSFORMATION", "GIFT_GUIDE", "BUDGET", "PREMIUM", "VIRAL_FIND"]


class PinBatch(BaseModel):
    """ONE universal caption for the whole carousel — NOT one per product (saves tokens)."""
    picks:    list[int] = Field(description="0-based indices of the chosen products from CANDIDATES, best first, exactly the requested count")
    caption:  str       = Field(description="ONE catchy Instagram carousel caption covering ALL chosen products together: a scroll-stopping hook, then why they're worth it (weave in a couple of REAL prices/discounts/ratings from the rows — never invent), a 'shop via the link in bio' nudge. Do NOT add any 'As an Amazon Associate' disclosure sentence. Under 1500 chars.")
    hashtags: list[str] = Field(description="15-25 relevant hashtags (no '#' prefix), mixing broad and niche; include 'ad' as one of them")
    style:    str       = Field(default="", description="the caption STYLE you used, exactly one of: DEAL_DROP, STORY, LISTICLE, PROBLEM_SOLUTION, QUESTION, TRANSFORMATION, GIFT_GUIDE, BUDGET, PREMIUM, VIRAL_FIND")


# ─── Prompts (system is fully static → cache-friendly) ───────────────────────

SYSTEM = (
    "You are an Amazon affiliate + Instagram copywriter. In ONE response you PICK the "
    "best products for a single Instagram CAROUSEL of ONE category, and write ONE "
    "category-themed caption for the whole carousel (NOT one per product).\n\n"
    "PICK by: strong social proof (high rating, many reviews, high recent demand, a "
    "real discount, a badge), scroll-stopping visual appeal, commission rate "
    "(Fashion 9% > Home 8% > Kitchen 7% > Beauty 6% > Electronics 2-5%), the "
    "₹200-5000 impulse-buy range, and similarity to the PROVEN winners. Return their "
    "indices in `picks`, best first, exactly the requested count.\n\n"
    "WRITE ONE `caption`, themed on the CATEGORY, SHORT and SKIMMABLE (people don't read "
    "long paragraphs), in this exact structure with real line breaks:\n"
    "Line 1: an emoji-led HOOK about the category (e.g. '🍔👕 Graphic Tees under ₹1K!').\n"
    "Line 2: '🔥 Top picks you can't miss:'\n"
    "Then ONE LINE PER PRODUCT (each on its OWN new line, up to 5), formatted exactly as: "
    "'- <Brand or short product name> — ₹<price> (<X>% off)'. Lead with the BRAND name, "
    "especially popular brands. Use ONLY the exact numbers from the rows; NEVER invent.\n"
    "Then ONE short line on why they're worth it.\n"
    "Then the CTA line: '🛒 Shop all via the link in bio 👆'.\n"
    "Do NOT add any 'As an Amazon Associate' disclosure sentence anywhere in the caption.\n"
    "Use a tasteful EMOJI PACK throughout — relevant and catchy, not spammy.\n\n"
    "Then 15-25 `hashtags` (no # symbol), category-relevant, mixing broad and niche; "
    "include 'ad' as one hashtag (that is the only disclosure needed)."
)

HUMAN = (
    "CATEGORY: {category}\n"
    "Pick the {count} best {category} products for one carousel and write ONE "
    "emoji-rich, {category}-themed caption that LISTS them with their real prices + "
    "discounts, then hashtags.\n"
    "STYLE: {style}\n\n"
    "CANDIDATES (id | title | price | social proof):\n{candidates}\n\n"
    "TRENDS: {trends}\n\n"
    "PROVEN winners: {winners}"
)

_STYLE_HINT = {
    "DEAL_DROP": "punchy deal-drop energy, lead with the biggest discount",
    "STORY": "a short relatable mini-story before the picks",
    "LISTICLE": "clean numbered listicle of the picks",
    "PROBLEM_SOLUTION": "name a common problem, then the products that solve it",
    "QUESTION": "open with an engaging question to the reader",
    "TRANSFORMATION": "before/after glow-up framing",
    "GIFT_GUIDE": "gift-guide framing (who each is perfect for)",
    "BUDGET": "budget-hero framing, emphasise how little they cost",
    "PREMIUM": "premium/aesthetic framing, make them feel high-end",
    "VIRAL_FIND": "'you didn't know you needed this' viral-find energy",
}


def _style_directive(content_style: str) -> str:
    cs = (content_style or "auto").strip().upper()
    if cs in ("", "AUTO"):
        return "choose the single best-fitting style for these products; set `style` to the one you used."
    if cs in _STYLE_HINT:
        return f"write in the {cs} style — {_STYLE_HINT[cs]}; set `style` to {cs}."
    return "choose the best-fitting style; set `style` accordingly."


# ─── Compact input builders (noise reduction) ────────────────────────────────

def _short(n) -> str:
    """26900 -> '27K' · 1800000 -> '1.8M' (keeps candidate rows token-lean)."""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1e3:.0f}K"
    return str(n)


def _candidates_block(products: list[dict]) -> str:
    """Compact pipe rows carrying GROUNDED social proof, so the LLM can both pick
    the most attractive product and cite real numbers — never invent them."""
    rows = []
    for i, p in enumerate(products[:MAX_CANDIDATES]):
        title = " ".join((p.get("title") or "").split())[:TITLE_CHARS]
        price = (p.get("price") or "?").strip() or "?"
        sig = []
        if p.get("rating"):
            sig.append(f"{p['rating']}★")
        if p.get("reviews"):
            sig.append(f"{_short(p['reviews'])} reviews")
        if p.get("discount_pct"):
            sig.append(f"{p['discount_pct']}% off")
        if (p.get("bought_past_month") or "").strip():
            sig.append(f"{p['bought_past_month']} bought/mo")
        if (p.get("badge") or "").strip():
            sig.append(p["badge"])
        proof = " · ".join(sig) or (p.get("category") or "general")
        rows.append(f"{i}|{title}|{price}|{proof}")
    return "\n".join(rows)


def _winners_block(rag_context: list[dict], product_ideas: list[str]) -> str:
    parts = []
    examples = [f'"{(ex.get("pin_title") or "")[:60]}"'
                for ex in (rag_context or [])[:MAX_EXAMPLES] if ex.get("pin_title")]
    if examples:
        parts.append("styles that worked: " + "; ".join(examples))
    ideas = ", ".join(i for i in (product_ideas or [])[:MAX_IDEAS] if i)
    if ideas:
        parts.append("product types that worked: " + ideas)
    return " | ".join(parts) or "none yet (cold start — use your expertise)"


# ─── Public API: the single call ──────────────────────────────────────────────

def _trends_block(trend_keywords: list[str], trend_signals: Optional[list[dict]]) -> str:
    """Compact trend context for the ONE LLM call. Prefers momentum/direction signals
    (e.g. 'wireless earbuds↑EXPLODING') so the model can lead with what's rising — at
    no extra token cost vs a bare keyword list. Direction is an internal signal, not a
    claim the caption should assert as fact."""
    arrow = {"EXPLODING": "↑EXPLODING", "RISING": "↑RISING", "STABLE": "STABLE",
             "DECLINING": "↓DECLINING", "UNKNOWN": ""}
    if trend_signals:
        parts = []
        for t in trend_signals[:MAX_TRENDS]:
            kw = (t.get("keyword") or "").strip()
            if not kw:
                continue
            tag = arrow.get((t.get("direction") or "UNKNOWN"), "")
            parts.append(f"{kw}{(' ' + tag) if tag else ''}")
        if parts:
            return ", ".join(parts)
    return ", ".join((trend_keywords or [])[:MAX_TRENDS]) or "trending, best, popular"


async def compose_pins(
    products:       list[dict],
    trend_keywords: list[str],
    rag_context:    list[dict],
    product_ideas:  list[str],
    count:          int = 3,
    trend_signals:  Optional[list[dict]] = None,
    content_style:  str = "auto",
) -> list[dict]:
    """Rank + write `count` pins in ONE structured LLM call. Returns PinContent dicts."""
    if not products:
        return []

    llm_kwargs: dict = {
        "model":   cfg.openai_model,             # e.g. gpt-5-nano (from .env)
        "api_key": cfg.openai_api_key,
    }
    if cfg.is_reasoning_model:
        # Reasoning models (gpt-5 / o-series): the token budget must ALSO cover
        # billed reasoning tokens, so use the full configured ceiling and never
        # scale it down (truncation would break the structured output). Do NOT
        # pass temperature — reasoning models reject non-default values.
        llm_kwargs["max_tokens"] = cfg.llm_max_output_tokens   # → max_completion_tokens
        llm_kwargs["reasoning_effort"] = cfg.llm_reasoning_effort
    else:
        # Non-reasoning models: budget scales with pin count (~180 tokens each),
        # and a LOW temperature curbs hallucination.
        llm_kwargs["max_tokens"] = max(min(cfg.llm_max_output_tokens, 400 + count * 200), 512)
        llm_kwargs["temperature"] = cfg.llm_temperature

    llm = ChatOpenAI(**llm_kwargs)
    # Structured output → schema guaranteed via function calling (no text parsing).
    structured = llm.with_structured_output(PinBatch)
    chain = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", HUMAN)]) | structured

    category = (products[0].get("category") or "product").strip() or "product"
    inputs = {
        "count":      count,
        "category":   category,
        "candidates": _candidates_block(products),
        "trends":     _trends_block(trend_keywords, trend_signals),
        "winners":    _winners_block(rag_context, product_ideas),
        "style":      _style_directive(content_style),
    }

    log.ai(f"Composing ONE caption for {count} products from {min(len(products), MAX_CANDIDATES)} candidates in ONE structured call...")

    batch: PinBatch = await chain.ainvoke(inputs)

    # ONE universal caption + hashtags for the whole carousel (shared by every pick).
    # No verbose FTC sentence in the caption (per the account owner); disclosure is the
    # '#ad' hashtag + the full line on the storefront page.
    caption = (batch.caption or "").strip()
    for _bad in ("As an Amazon Associate I earn from qualifying purchases.", FTC, "#Ad |"):
        caption = caption.replace(_bad, "").strip()      # strip any disclosure the model still added
    caption = _bold_caption(caption)                     # bold names/prices/discounts for IG

    # Phase 4: curated hashtag bank merge (consistent reach spine) + #ad disclosure.
    from config import cfg
    tags = merge_hashtags(category, batch.hashtags or [],
                          use_bank=cfg.content.use_tag_bank, cap=25)

    # Phase 4: record the style used (normalise to a known value; else UNKNOWN).
    style = (batch.style or "").strip().upper()
    if style not in STYLES:
        style = (content_style or "").strip().upper() if (content_style or "").strip().upper() in STYLES else "UNKNOWN"

    # Phase 4: fact-check numeric claims against the chosen products (non-destructive).
    chosen_products = [products[i] for i in (batch.picks or []) if 0 <= i < len(products)]
    warnings = audit_caption(caption, chosen_products) if cfg.content.validate else []
    if warnings:
        log.warning(f"[content-validate] {len(warnings)} unverified numeric claim(s): {warnings[:2]}")

    results: list[dict] = []
    seen_ids: set = set()
    for pid in (batch.picks or [])[:count]:
        if not (0 <= pid < len(products)) or pid in seen_ids:
            continue
        seen_ids.add(pid)
        p = products[pid]
        results.append({
            "product":         p,
            "pin_title":       (p.get("title") or "")[:90],
            "pin_description": caption,   # SHARED — one caption per carousel, not per product
            "hashtags":        tags,      # SHARED
            "affiliate_link":  "",        # filled by get_affiliate_links node
            "content_style":   style,           # Phase 4 A/B tag
            "content_warnings": warnings,        # Phase 4 fact-check (empty = all grounded)
        })
    return results
