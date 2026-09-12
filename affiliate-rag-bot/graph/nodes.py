"""
graph/nodes.py  —  Every node in the LangGraph pipeline.

Pipeline order:
  1. scrape_amazon        — Playwright: scrape Amazon Best Sellers
  2. check_duplicates     — PostgreSQL: filter already-pinned ASINs
  3. search_trends        — Tavily: real-time trending keywords (optional)
  4. rag_retrieve         — pgvector: similar pins + product discovery
  5. compose_pins         — ONE structured LLM call: rank + write all pins
  6. get_affiliate_links  — Playwright: SiteStripe affiliate links
  7. post_pinterest       — Playwright: publish pins
  8. store_results        — pgvector + PostgreSQL: update RAG memory

Each node:
  - Receives full BotState
  - Returns dict of fields to update
  - Never raises — errors accumulate in state["errors"]
"""
from __future__ import annotations
import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from graph.state import BotState
from config import cfg
from utils.logger import log


def _pages(config: RunnableConfig) -> tuple[Any, Any]:
    c = config.get("configurable", {})
    return c.get("amazon_page"), c.get("pinterest_page")


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — Scrape Amazon Best Sellers
# ══════════════════════════════════════════════════════════════════════════════

async def scrape_amazon(state: BotState, config: RunnableConfig) -> dict:
    log.node("scrape_amazon")
    amazon_page, _ = _pages(config)

    try:
        from tools.amazon import amazon_login, scrape_products, scrape_products_multi
        from chains import discovery as _discovery

        opts        = config.get("configurable", {}).get("options", {}) or {}
        marketplace = opts.get("marketplace") or cfg.amazon.marketplace
        category    = state["category"]
        keyword     = opts.get("q")

        # Amazon login is only needed for the legacy SiteStripe link method.
        # The default "deeplink" method builds official ?tag= URLs with no login,
        # and search results are public — so we skip login entirely there.
        if cfg.amazon.link_method == "sitestripe":
            await amazon_login(amazon_page, cfg.amazon.email, cfg.amazon.password, marketplace)

        # ── Discovery Planner (Phase 1) ──────────────────────────────────────
        # Category mode with discovery ON: mine SEVERAL subcategory intents (rotated
        # by past yield) + pagination + adaptive stopping → far more UNIQUE products.
        # Keyword mode (explicit q) or discovery OFF: original single-query scrape.
        log_line = ""
        if keyword and keyword.strip():
            products = await scrape_products(amazon_page, category, marketplace,
                                             query=keyword, quality=opts)
            log_line = f"scraped {len(products)} products for keyword '{keyword.strip()}'"
        elif cfg.discovery.enabled:
            from rag.discovery_stats import discovery_stats
            candidates = _discovery.category_queries(category)
            # Trend-aware discovery: fold in persisted trending terms (from prior runs'
            # Trend Analyst memory) so mining leans toward what's rising. Cold start ⇒
            # empty ⇒ no change. Deduped, trend terms explored alongside subcategories.
            import runtime
            dterms = runtime.get("TREND_DISCOVERY_TERMS", cfg.trends.discovery_terms, "int")
            if cfg.trends.enabled and dterms > 0:
                try:
                    from rag.trends import trend_store
                    tterms = trend_store.trending_terms(category, dterms)
                    for t in tterms:
                        if t and t not in candidates:
                            candidates.insert(0, t)      # prioritise trending intents
                except Exception as te:
                    log.warning(f"scrape_amazon: trend-aware discovery skipped: {te}")
            mq = runtime.get("DISCOVERY_MAX_QUERIES", cfg.discovery.max_queries, "int")
            mp = runtime.get("DISCOVERY_MAX_PAGES", cfg.discovery.max_pages, "int")
            tp = runtime.get("DISCOVERY_TARGET_POOL", cfg.discovery.target_pool, "int")
            queries = discovery_stats.pick_queries(category, candidates, mq)
            products, yields = await scrape_products_multi(
                amazon_page, category, marketplace, queries, quality=opts,
                max_pages=mp, target_pool=tp)
            discovery_stats.record_yields(category, yields)          # adaptive learning
            log_line = (f"discovery mined {len(queries)} intents "
                        f"({', '.join(queries)}) → {len(products)} unique products")
        else:
            products = await scrape_products(amazon_page, category, marketplace, quality=opts)
            log_line = f"scraped {len(products)} products for '{category}'"

        if not products:
            return {"errors": ["scrape_amazon: no products found — Amazon DOM may have changed"]}

        return {
            "raw_products": products,
            "stream_log": [log_line],
        }

    except Exception as e:
        log.error(f"scrape_amazon: {e}")
        return {"raw_products": [], "errors": [f"scrape_amazon: {e}"]}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — Check Duplicates (PostgreSQL seen_products table)
# ══════════════════════════════════════════════════════════════════════════════

async def check_duplicates(state: BotState, config: RunnableConfig) -> dict:
    """
    Filters raw_products against the seen_products PostgreSQL table.
    Any ASIN already pinned is removed — we never re-pin the same product.

    Also logs duplicate stats so you can see what's been filtered.
    """
    log.node("check_duplicates  [PostgreSQL dedup]")

    raw = state.get("raw_products", [])
    if not raw:
        return {"fresh_products": [], "duplicate_asins": [],
                "errors": ["check_duplicates: no raw_products to check"]}

    try:
        # Run sync DB call in thread executor (non-blocking)
        from rag.dedup import dedup_store

        fresh, dupes = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: dedup_store.filter_unseen(raw),
        )

        stats = await asyncio.get_event_loop().run_in_executor(
            None, dedup_store.stats
        )

        log.info(f"[Dedup] DB stats: {stats['total_seen']} total pinned | {stats['by_category']}")

        if not fresh:
            return {
                "fresh_products":  [],
                "duplicate_asins": dupes,
                "errors":          ["check_duplicates: ALL products already pinned — try a different category or wait"],
                "stream_log":      [f"all {len(dupes)} scraped products already pinned — no new products found"],
            }

        return {
            "fresh_products":  fresh,
            "duplicate_asins": dupes,
            "stream_log": [
                f"dedup: {len(fresh)} new products / {len(dupes)} already pinned (filtered out)"
            ],
        }

    except Exception as e:
        log.error(f"check_duplicates: {e}")
        # Fail open — continue with all products if dedup breaks
        return {
            "fresh_products":  raw,
            "duplicate_asins": [],
            "errors":          [f"check_duplicates: {e} — continuing with all products"],
        }


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — Search Trends (Tavily)
# ══════════════════════════════════════════════════════════════════════════════

async def search_trends(state: BotState, config: RunnableConfig) -> dict:
    log.node("search_trends")

    try:
        from tools.search import fetch_trending_keywords

        category = state["category"]
        keywords = await fetch_trending_keywords(category, cfg.amazon.marketplace)

        # ── Trend Analyst (Phase 3) ──────────────────────────────────────────
        # Persist this run's keywords as trend observations, then read back momentum +
        # direction (built from history). Fail-open: persistence/momentum problems never
        # abort the run — keywords still flow to the composer. Momentum is INTERNAL.
        trend_signals: list[dict] = []
        if cfg.trends.enabled:
            try:
                from rag.trends import trend_store
                source = "tavily" if cfg.tavily_api_key else "fallback"
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: trend_store.record_observations(category, keywords, source))
                trend_signals = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: trend_store.category_trends(category, limit=cfg.trends.max_keywords))
            except Exception as te:
                log.warning(f"search_trends: trend memory skipped: {te}")

        return {
            "trend_keywords": keywords,
            "trend_signals":  trend_signals,
            "stream_log": [
                f"trends: {len(keywords)} keywords, "
                f"{len(trend_signals)} with momentum/direction"
            ],
        }

    except Exception as e:
        log.warning(f"search_trends: {e}")
        return {"trend_keywords": [], "trend_signals": [], "errors": [f"search_trends: {e}"]}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — RAG Retrieve (pgvector — content context + product discovery)
# ══════════════════════════════════════════════════════════════════════════════

async def rag_retrieve(state: BotState, config: RunnableConfig) -> dict:
    """
    Two pgvector queries in parallel:

    A) CONTENT CONTEXT — retrieve similar past pins to use as few-shot
       examples in the content generation prompt. Makes pin quality
       compound over time (the flywheel effect).

    B) PRODUCT DISCOVERY — retrieve what product types have performed
       well in this category. Injected into the ranking prompt to bias
       the LLM toward proven product types.
    """
    log.node("rag_retrieve  [pgvector — content + discovery]")

    category = state["category"]
    fresh    = state.get("fresh_products", [])

    try:
        from rag.store import rag_store

        # Build a rich semantic query from top product titles
        top_titles = " ".join(p["title"] for p in fresh[:5])
        query      = f"{category} {top_titles}".strip()

        # Run both queries concurrently
        similar_pins, product_ideas = await asyncio.gather(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: rag_store.retrieve_similar_pins(query, category=category, n=5),
            ),
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: rag_store.discover_product_ideas(category, n=8),
            ),
        )

        log.info(f"[RAG] {len(similar_pins)} similar pins + {len(product_ideas)} product ideas retrieved")

        return {
            "rag_context":       similar_pins,
            "rag_product_ideas": product_ideas,
            "stream_log": [
                f"pgvector: {len(similar_pins)} similar pins + {len(product_ideas)} successful product types retrieved"
            ],
        }

    except Exception as e:
        log.warning(f"rag_retrieve: {e} — cold start, continuing without context")
        return {
            "rag_context":       [],
            "rag_product_ideas": [],
            "errors":            [f"rag_retrieve: {e}"],
        }


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — Compose Pins  (SINGLE structured LLM call: rank + write)
# ══════════════════════════════════════════════════════════════════════════════

async def compose_pins(state: BotState, config: RunnableConfig) -> dict:
    """
    One LLM call does the work of the old rank_products + generate_content nodes:
    it selects the best `products_per_run` candidates AND writes every pin, in a
    single schema-validated JSON response. Trends + RAG context are sent once.
    """
    log.node("compose_pins  [1 structured LLM call: rank + write]")

    fresh = state.get("fresh_products", [])
    if not fresh:
        return {"ranked_products": [], "generated_content": [],
                "errors": ["compose_pins: no fresh products"]}

    try:
        from chains.compose import compose_pins as _compose

        opts = config.get("configurable", {}).get("options", {}) or {}
        pins = await _compose(
            products=       fresh,
            trend_keywords= state.get("trend_keywords", []),
            rag_context=    state.get("rag_context", []),
            product_ideas=  state.get("rag_product_ideas", []),
            count=          state["products_per_run"],
            trend_signals=  state.get("trend_signals", []),
            content_style=  opts.get("content_style") or __import__("runtime").get("CONTENT_DEFAULT_STYLE", cfg.content.default_style, "str"),
        )

        if not pins:
            return {"ranked_products": [], "generated_content": [],
                    "errors": ["compose_pins: LLM returned no usable pins"]}

        # ── Novelty Analyst (Phase 2) ────────────────────────────────────────
        # Score how UNLIKE already-posted pins each chosen product is — computed HERE,
        # before store_results embeds this run's pins (else products match themselves).
        # Fail-open: any error leaves novelty_score unset (treated as unknown).
        if cfg.novelty.enabled:
            try:
                from rag.store import rag_store
                chosen = [p["product"] for p in pins]
                nov = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: rag_store.novelty_scores(chosen))
                for p in pins:
                    p["novelty_score"] = nov.get(p["product"].get("asin", ""))
            except Exception as e:
                log.warning(f"compose_pins: novelty scoring skipped: {e}")

        # ── Trend Analyst (Phase 3) — per-product trend alignment ────────────
        # Deterministic (keyword match vs this run's trend signals); no extra LLM call.
        if cfg.trends.enabled:
            from chains import discovery as _discovery
            signals = state.get("trend_signals", [])
            for p in pins:
                p["trend_score"] = _discovery.trend_alignment(p["product"], signals)

        ranked = [p["product"] for p in pins]
        for i, p in enumerate(ranked):
            log.product(p, i)
        for p in pins:
            log.success(f'Generated: "{p["pin_title"]}"')

        return {
            "ranked_products":   ranked,
            "generated_content": pins,
            "stream_log": [
                f"1 LLM call → selected + wrote {len(pins)} pins from {len(fresh)} candidates"
            ],
        }

    except Exception as e:
        log.error(f"compose_pins: {e}")
        return {"ranked_products": [], "generated_content": [],
                "errors": [f"compose_pins: {e}"]}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 7 — Get Affiliate Links (Amazon SiteStripe)
# ══════════════════════════════════════════════════════════════════════════════

async def get_affiliate_links(state: BotState, config: RunnableConfig) -> dict:
    log.node("get_affiliate_links")

    amazon_page, _ = _pages(config)
    content        = state.get("generated_content", [])

    if not content:
        return {"errors": ["get_affiliate_links: no generated_content"]}

    try:
        from tools.amazon import get_affiliate_link

        opts        = config.get("configurable", {}).get("options", {}) or {}
        marketplace = opts.get("marketplace") or cfg.amazon.marketplace

        updated = []
        for pin in content:
            link = await get_affiliate_link(
                amazon_page,
                pin["product"],
                cfg.amazon.associate_tag,
                marketplace,
            )
            updated.append({**pin, "affiliate_link": link})
            await asyncio.sleep(1.5)

        return {
            "generated_content": updated,
            "stream_log": [f"fetched {len(updated)} affiliate links via SiteStripe"],
        }

    except Exception as e:
        log.error(f"get_affiliate_links: {e}")
        return {"errors": [f"get_affiliate_links: {e}"]}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 8 — Post to Pinterest (Playwright)
# ══════════════════════════════════════════════════════════════════════════════

async def post_pinterest(state: BotState, config: RunnableConfig) -> dict:
    log.node("post_pinterest")

    _, pinterest_page = _pages(config)
    content           = state.get("generated_content", [])
    configurable      = config.get("configurable", {})
    dry_run           = configurable.get("dry_run", False)
    content_only      = configurable.get("content_only", False)

    # Content-only mode: generate pins but never touch Pinterest (no login, no post).
    if content_only:
        log.info("[content-only] skipping Pinterest posting")
        return {"posted_pins": [], "stream_log": ["content-only: skipped Pinterest posting"]}

    if not content:
        return {"posted_pins": [], "errors": ["post_pinterest: nothing to post"]}

    try:
        from tools.pinterest import pinterest_login, ensure_board_exists, create_pin

        await pinterest_login(pinterest_page, cfg.pinterest.email, cfg.pinterest.password)
        await ensure_board_exists(pinterest_page, cfg.pinterest.board_name)

        results = []
        for i, pin in enumerate(content):
            if dry_run:
                log.warning(f"[DRY RUN] Would post: {pin['pin_title']}")
                results.append({"asin": pin["product"]["asin"], "title": pin["pin_title"], "success": True, "error": None})
            else:
                try:
                    ok = await create_pin(
                        page=            pinterest_page,
                        product=         pin["product"],
                        pin_title=       pin["pin_title"],
                        pin_description= pin["pin_description"],
                        hashtags=        pin["hashtags"],
                        affiliate_link=  pin["affiliate_link"],
                        board_name=      cfg.pinterest.board_name,
                    )
                    results.append({"asin": pin["product"]["asin"], "title": pin["pin_title"], "success": ok, "error": None})
                except Exception as pe:
                    results.append({"asin": pin["product"]["asin"], "title": pin["pin_title"], "success": False, "error": str(pe)})

            if i < len(content) - 1 and not dry_run:
                wait = cfg.bot.delay_between_pins
                log.info(f"Waiting {wait // 60}m {wait % 60}s before next pin...")
                await asyncio.sleep(wait)

        posted = sum(1 for r in results if r["success"])
        return {
            "posted_pins": results,
            "stream_log": [f"posted {posted}/{len(results)} pins to Pinterest"],
        }

    except Exception as e:
        log.error(f"post_pinterest: {e}")
        return {"posted_pins": [], "errors": [f"post_pinterest: {e}"]}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 9 — Store Results (pgvector + PostgreSQL dedup)
# ══════════════════════════════════════════════════════════════════════════════

async def store_results(state: BotState, config: RunnableConfig) -> dict:
    """
    Two writes in parallel:

    A) pgvector  — store pin content vectors for future RAG retrieval
    B) PostgreSQL — record ASINs in seen_products for deduplication

    Full pipeline stores only successfully-POSTED pins. The content service
    (content_only) stores ALL generated pins — so every product it returns is
    embedded into pgvector and marked seen, and is never generated again.
    """
    log.node("store_results  [pgvector + PostgreSQL dedup]")

    content      = state.get("generated_content", [])
    posted_pins  = state.get("posted_pins", [])
    content_only = config.get("configurable", {}).get("content_only", False)

    if content_only:
        successful = content                               # dedup + embed everything generated
    else:
        posted_asins = {r["asin"] for r in posted_pins if r["success"]}
        successful   = [pin for pin in content if pin["product"]["asin"] in posted_asins]

    if not successful:
        log.warning("[Store] Nothing to store")
        return {"stream_log": ["store_results: nothing to record"]}

    try:
        from rag.store import rag_store
        from rag.dedup import dedup_store

        # A) pgvector — pin content vectors
        async def _store_vectors():
            for pin in successful:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda p=pin: rag_store.store_pin(
                        asin=            p["product"]["asin"],
                        pin_title=       p["pin_title"],
                        pin_description= p["pin_description"],
                        hashtags=        p["hashtags"],
                        category=        p["product"].get("category", "general"),
                        price=           p["product"].get("price", ""),
                        product_title=   p["product"].get("title", ""),
                    ),
                )

        # B) PostgreSQL — ASIN deduplication records
        async def _store_dedup():
            products_to_mark = [pin["product"] for pin in successful]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: dedup_store.mark_as_pinned(products_to_mark),
            )

        # Run both writes concurrently
        await asyncio.gather(_store_vectors(), _store_dedup())

        dedup_stats = await asyncio.get_event_loop().run_in_executor(None, dedup_store.stats)
        log.success(f"[Store] Saved {len(successful)} pins | Total pinned ever: {dedup_stats['total_seen']}")

        return {
            "stream_log": [
                f"stored {len(successful)} pins in pgvector",
                f"recorded {len(successful)} ASINs in PostgreSQL dedup (total: {dedup_stats['total_seen']})",
            ]
        }

    except Exception as e:
        log.error(f"store_results: {e}")
        return {"errors": [f"store_results: {e}"]}
