"""
chains/search_filters.py — AI-inferred, per-product filter dimensions for Universal Search.

Given a free-text product search ("iphone 18 pro max", "brown shirt", "air fryer"), one small
structured LLM call returns the 3-5 filter dimensions a shopper would actually narrow by, each
with realistic options. The user's picks are folded into the Amazon query (refine) and the
soft-threshold ranking (rank) — never a hard wall. Works for ANY product, no hardcoded lists.

Token discipline: one structured call, tiny output, static cache-friendly system prompt.
"""
from __future__ import annotations

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
    "You help shoppers narrow an Amazon India product search. Given a search query, return the "
    "3-5 filter dimensions a shopper would MOST want to narrow by FOR THAT PRODUCT TYPE, each with "
    "3-6 short realistic options. Tailor to the product:\n"
    "• apparel → Fit, Size, Sleeve, Material, Colour, Pattern\n"
    "• phones → Storage, RAM, Colour, Condition, Network\n"
    "• laptops → RAM, Storage, Screen Size, Processor\n"
    "• kitchen/appliances → Capacity, Wattage, Type, Material\n"
    "• footwear → Size, Type, Colour, Closure\n"
    "• beauty → Skin Type, Concern, Finish, Volume\n"
    "Dimension names 1-2 words, Title Case; options short (1-3 words). Do NOT include price or "
    "rating (handled by separate sliders). Only dimensions that make sense for THIS query."
)
HUMAN = "Product search: {q}\nReturn the most relevant filter dimensions with options."


def _llm() -> ChatOpenAI:
    kw: dict = {"model": cfg.openai_model, "api_key": cfg.openai_api_key}
    if cfg.is_reasoning_model:
        kw["max_tokens"] = cfg.llm_max_output_tokens
        kw["reasoning_effort"] = cfg.llm_reasoning_effort
    else:
        kw["max_tokens"] = 600
        kw["temperature"] = 0.2
    return ChatOpenAI(**kw)


async def suggest_filters(query: str) -> list[dict]:
    """Return [{name, options[]}] for the query. Empty list on any failure (caller degrades to a
    generic filter set), so the search box never breaks over this optional helper."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    try:
        chain = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", HUMAN)]) \
            | _llm().with_structured_output(SearchFilters)
        res: SearchFilters = await chain.ainvoke({"q": q[:80]})
        out = []
        for f in (res.filters or [])[:5]:
            name = " ".join((f.name or "").split())[:24]
            opts = [" ".join((o or "").split())[:24] for o in (f.options or []) if (o or "").strip()][:6]
            if name and opts:
                out.append({"name": name, "options": opts})
        return out
    except Exception:
        return []
