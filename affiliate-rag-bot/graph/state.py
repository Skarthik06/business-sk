"""
graph/state.py  —  Single source of truth for the LangGraph pipeline.

Every node receives this state and returns a partial dict to update.
LangGraph merges updates automatically (Annotated fields use reducer functions).
"""
from __future__ import annotations
import operator
from typing import TypedDict, Annotated, Optional


# ─── Sub-types ────────────────────────────────────────────────────────────────

class ProductData(TypedDict):
    title:    str
    price:    str
    image:    str
    url:      str
    asin:     str
    category: str
    rating:   str


class PinContent(TypedDict):
    product:         ProductData
    pin_title:       str
    pin_description: str
    hashtags:        list[str]
    affiliate_link:  str


class RAGContext(TypedDict):
    """A past successful pin retrieved from pgvector."""
    pin_title:       str
    pin_description: str
    category:        str
    product_title:   str
    score:           float   # relevance score (higher = more similar)


class PostResult(TypedDict):
    asin:    str
    title:   str
    success: bool
    error:   Optional[str]


# ─── Main graph state ─────────────────────────────────────────────────────────

class BotState(TypedDict):
    # ── Inputs ───────────────────────────────────────────────────────
    category:         str
    products_per_run: int

    # ── NODE 1: scrape_amazon ─────────────────────────────────────────
    raw_products:     list[ProductData]    # all scraped products (may include duplicates)

    # ── NODE 2: check_duplicates  (NEW) ──────────────────────────────
    fresh_products:   list[ProductData]    # raw_products minus already-pinned ASINs
    duplicate_asins:  list[str]            # ASINs filtered out (already pinned)

    # ── NODE 3: search_trends ─────────────────────────────────────────
    trend_keywords:   list[str]
    trend_signals:    list[dict]   # [{keyword, momentum, direction}] — Phase 3 Trend Analyst

    # ── NODE 4: rag_retrieve ──────────────────────────────────────────
    rag_context:      list[RAGContext]     # similar past pins from pgvector
    rag_product_ideas: list[str]           # successful product types from pgvector

    # ── NODE 5: compose_pins (single LLM call: rank + write) ──────────
    ranked_products:  list[ProductData]
    generated_content: list[PinContent]

    # ── NODE 6: get_affiliate_links ───────────────────────────────────
    # (updates generated_content in-place with affiliate_link field)

    # ── NODE 7: post_pinterest ────────────────────────────────────────
    posted_pins:      list[PostResult]

    # ── NODE 8: store_results ─────────────────────────────────────────
    # (writes to pgvector + seen_products table)

    # ── Accumulated lists (LangGraph merges with operator.add) ───────
    errors:     Annotated[list[str], operator.add]
    stream_log: Annotated[list[str], operator.add]
