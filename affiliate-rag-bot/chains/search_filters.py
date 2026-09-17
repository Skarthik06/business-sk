"""
chains/search_filters.py — AI-inferred, per-product filter dimensions for Universal Search.

Given a free-text product search ("iphone 18 pro max", "brown shirt", "air fryer"), one small
structured LLM call returns the 3-5 filter dimensions a shopper would actually narrow by, each
with realistic options. The user's picks are folded into the Amazon query (refine) and the
soft-threshold ranking (rank) — never a hard wall. Works for ANY product, no hardcoded lists.

Token discipline: one structured call, tiny output, static cache-friendly system prompt.
"""
from __future__ import annotations

import re
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from config import cfg


class FilterDim(BaseModel):
    name:    str       = Field(description="Filter dimension, 1-2 words, Title Case (e.g. 'Fit', 'Storage', 'Colour', 'Capacity').")
    options: list[str] = Field(description="3-6 short, realistic values a shopper picks for this dimension.")


class SearchFilters(BaseModel):
    filters: list[FilterDim] = Field(description="The 3-5 filter dimensions MOST relevant to this specific product search.")


SYSTEM = (
    "You are a precise product-search assistant for Amazon India. First, silently identify the "
    "EXACT product the shopper typed (brand, type, model). Then return the 3-5 filter dimensions a "
    "real shopper would MOST use to narrow THAT specific product — each with 3-6 short, realistic, "
    "MUTUALLY-EXCLUSIVE options.\n"
    "HARD RULES:\n"
    "1. NEVER return two dimensions that mean the same thing. Pick ONE canonical term only — e.g. "
    "use 'Storage' OR 'Capacity' (never both); 'Colour' not 'Color'; 'Screen Size' not 'Display' + "
    "'Size'. Every dimension must be a DISTINCT concept.\n"
    "2. Options within a dimension must be distinct real values (e.g. Storage: 128 GB, 256 GB, 512 "
    "GB, 1 TB). No duplicates, no overlaps, no 'Any'.\n"
    "3. Tailor tightly to the product: phone→Storage/RAM/Colour/Condition/Network; "
    "laptop→RAM/Storage/Screen Size/Processor; shirt→Fit/Size/Sleeve/Material/Colour; "
    "shoes→Size/Width/Type/Colour; air fryer→Capacity/Power/Type/Material; watch→Type/Strap/"
    "Dial Colour/Features. If unsure of the category, give generic but useful ones (Brand, Colour, "
    "Type, Material).\n"
    "4. Dimension names are 1-2 words, Title Case; options 1-3 words. NEVER include price or rating "
    "(separate sliders handle those).\n"
    "Return JSON only — no prose."
)
HUMAN = "Product the shopper typed: \"{q}\"\nReturn the distinct, non-overlapping filter dimensions."


def _llm() -> ChatOpenAI:
    kw: dict = {"model": cfg.openai_model, "api_key": cfg.openai_api_key}
    if cfg.is_reasoning_model:
        kw["max_tokens"] = cfg.llm_max_output_tokens
        kw["reasoning_effort"] = cfg.llm_reasoning_effort
    else:
        kw["max_tokens"] = 600
        kw["temperature"] = 0.2
    return ChatOpenAI(**kw)


async def suggest_filters(query: str) -> dict:
    """Return {"filters": [{name, options[]}], "tokens": {...}} for the query. Clean JSON in / JSON
    out (structured output), and the exact token usage so the UI can show it. On any failure returns
    empty filters + zero tokens, so the search box never breaks over this optional helper."""
    q = (query or "").strip()
    empty = {"filters": [], "tokens": {"input": 0, "output": 0, "total": 0}}
    if len(q) < 2:
        return empty
    try:
        chain = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", HUMAN)]) \
            | _llm().with_structured_output(SearchFilters, include_raw=True)
        res = await chain.ainvoke({"q": q[:80]})            # {parsed, raw, parsing_error}
        parsed: Optional[SearchFilters] = res.get("parsed")
        usage = getattr(res.get("raw"), "usage_metadata", None) or {}
        tokens = {"input": int(usage.get("input_tokens") or 0),
                  "output": int(usage.get("output_tokens") or 0),
                  "total": int(usage.get("total_tokens") or 0)}
        out, seen = [], set()
        for f in ((parsed.filters if parsed else []) or [])[:5]:
            name = " ".join((f.name or "").split())[:24]
            key = re.sub(r"[^a-z]", "", name.lower())
            # de-dup near-synonym dimensions defensively (storage/capacity, colour/color)
            key = {"capacity": "storage", "color": "colour", "display": "screensize"}.get(key, key)
            if not name or key in seen:
                continue
            opts, oseen = [], set()
            for o in (f.options or []):
                ov = " ".join((o or "").split())[:24]
                ok = ov.lower()
                if ov and ok not in oseen and ok not in ("any", "all"):
                    oseen.add(ok); opts.append(ov)
            if opts:
                seen.add(key); out.append({"name": name, "options": opts[:6]})
        return {"filters": out, "tokens": tokens}
    except Exception:
        return empty
