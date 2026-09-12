"""
pipeline_runner.py  —  Shared async runner for the LangGraph pipeline.

Launches Playwright, runs the 8-node graph, and exposes it two ways:
  - run_pipeline(...)      → async generator of UI events (streamed)
  - execute_pipeline(...)  → async coroutine returning ONE structured JSON result

This is the single source of truth for "running the bot", consumed by:
  - main.py    → renders streamed events in a Rich terminal dashboard
  - server.py  → POST /api/run calls execute_pipeline and returns JSON

Event shapes (every event is a dict with a "type" key):
  {"type": "run_start", "category", "products_per_run", "dry_run", "ts"}
  {"type": "node",      "node", "status": "running"|"done"|"error", "ts"}
  {"type": "log",       "level": "info"|"error", "message", "ts"}
  {"type": "summary",   "category", "elapsed", "posted", "errors", "log", "ts"}
  {"type": "error",     "message", "ts"}        # fatal — run aborts
"""
from __future__ import annotations

import time
from typing import AsyncIterator

from config import cfg

# Canonical node execution order (must match graph/workflow.py).
NODE_ORDER = [
    "scrape_amazon",
    "check_duplicates",
    "search_trends",
    "rag_retrieve",
    "compose_pins",          # single structured LLM call: rank + write
    "get_affiliate_links",
    "post_pinterest",
    "store_results",
]


def _now() -> float:
    return time.time()


# ─── Shared execution helpers (single source of truth) ────────────────────────

def _initial_state(category: str, products_per_run: int) -> dict:
    """Fresh BotState seed with every list field initialized empty."""
    return {
        "category": category,
        "products_per_run": products_per_run,
        "raw_products": [], "fresh_products": [], "duplicate_asins": [],
        "trend_keywords": [], "rag_context": [], "rag_product_ideas": [],
        "ranked_products": [], "generated_content": [], "posted_pins": [],
        "errors": [], "stream_log": [],
    }


async def _new_browser(pw):
    """Launch a stealth Chromium context with the two pages the graph needs."""
    browser = await pw.chromium.launch(
        headless=cfg.bot.headless,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-IN",
    )
    await context.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )
    amazon_page = await context.new_page()
    pinterest_page = await context.new_page()
    return browser, amazon_page, pinterest_page


def _run_config(amazon_page, pinterest_page, dry_run: bool, content_only: bool = False,
                options: dict | None = None) -> dict:
    return {"configurable": {
        "amazon_page": amazon_page,
        "pinterest_page": pinterest_page,
        "dry_run": dry_run,
        # content_only: generate pins but DON'T post (content service).
        "content_only": content_only,
        # options: per-request overrides (q, marketplace, min_rating, min_reviews,
        # price_min, price_max) read by the scrape / affiliate-link nodes.
        "options": options or {},
    }}


async def run_pipeline(
    category: str,
    products_per_run: int,
    dry_run: bool = False,
) -> AsyncIterator[dict]:
    """Run one full pipeline pass, yielding UI events as work happens."""
    yield {
        "type": "run_start",
        "category": category,
        "products_per_run": products_per_run,
        "dry_run": dry_run,
        "ts": _now(),
    }

    # ── Validate required config before spinning up a browser ────────────
    try:
        cfg.validate()
    except ValueError as e:
        yield {"type": "log", "level": "error", "message": str(e), "ts": _now()}
        yield {"type": "error", "message": "Missing required configuration (see log).", "ts": _now()}
        return

    if dry_run:
        yield {"type": "log", "level": "info",
               "message": "DRY RUN — Pinterest posts will be skipped.", "ts": _now()}

    from playwright.async_api import async_playwright
    from graph.workflow import graph

    start = _now()
    stream_log: list[str] = []
    errors_all: list[str] = []
    posted = 0

    # First node begins immediately.
    yield {"type": "node", "node": NODE_ORDER[0], "status": "running", "ts": _now()}

    try:
        async with async_playwright() as pw:
            browser, amazon_page, pinterest_page = await _new_browser(pw)
            initial_state = _initial_state(category, products_per_run)
            run_config = _run_config(amazon_page, pinterest_page, dry_run)

            try:
                async for event in graph.astream(initial_state, config=run_config):
                    for node_name, update in event.items():
                        if node_name == "__end__":
                            continue

                        status = "done"
                        if isinstance(update, dict):
                            for line in update.get("stream_log", []):
                                stream_log.append(line)
                                yield {"type": "log", "level": "info",
                                       "message": line, "ts": _now()}

                            for err in update.get("errors", []):
                                status = "error"
                                errors_all.append(err)
                                yield {"type": "log", "level": "error",
                                       "message": err, "ts": _now()}

                            if "posted_pins" in update:
                                posted = sum(1 for r in update["posted_pins"]
                                             if isinstance(r, dict) and r.get("success"))

                        yield {"type": "node", "node": node_name, "status": status, "ts": _now()}

                        # Optimistically mark the next node as running.
                        if node_name in NODE_ORDER:
                            idx = NODE_ORDER.index(node_name)
                            if idx + 1 < len(NODE_ORDER):
                                yield {"type": "node", "node": NODE_ORDER[idx + 1],
                                       "status": "running", "ts": _now()}
            finally:
                await browser.close()

    except Exception as e:  # noqa: BLE001 — surface any crash to the UI
        yield {"type": "log", "level": "error", "message": f"pipeline crashed: {e}", "ts": _now()}
        yield {"type": "error", "message": str(e), "ts": _now()}
        return

    yield {
        "type": "summary",
        "category": category,
        "elapsed": _now() - start,
        "posted": posted,
        "errors": errors_all,
        "log": stream_log,
        "ts": _now(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# JSON API collector — run the graph to completion, return ONE structured result.
# ══════════════════════════════════════════════════════════════════════════════

# Additive state fields (LangGraph merges these with operator.add).
_ADDITIVE_FIELDS = ("errors", "stream_log")


def _pin_to_json(pin: dict, posted_map: dict) -> dict:
    """Shape one PinContent (+ its post result) into a stable JSON object."""
    product = pin.get("product", {}) or {}
    asin = product.get("asin", "")
    result = posted_map.get(asin) or {}
    return {
        "asin":              asin,
        "product_title":     product.get("title", ""),
        "price":             product.get("price", ""),
        "orig_price":        product.get("orig_price", ""),      # M.R.P (strikethrough)
        "discount_pct":      product.get("discount_pct"),        # int or None
        "rating":            product.get("rating"),              # float or None
        "reviews":           product.get("reviews"),             # int or None
        "bought_past_month": product.get("bought_past_month", ""),
        "badge":             product.get("badge", ""),
        "image":             product.get("image", ""),
        "product_url":       product.get("url", ""),
        "category":          product.get("category", ""),
        "pin_title":         pin.get("pin_title", ""),
        "pin_description":   pin.get("pin_description", ""),
        "hashtags":          pin.get("hashtags", []),
        "affiliate_link":    pin.get("affiliate_link", ""),
        "novelty_score":     pin.get("novelty_score"),          # Phase 2 (None if disabled)
        "trend_score":       pin.get("trend_score"),            # Phase 3 (None if no trend data)
        "content_style":     pin.get("content_style", ""),      # Phase 4 A/B style
        "content_warnings":  pin.get("content_warnings", []),   # Phase 4 fact-check
        "posted":            bool(result.get("success")),
        "post_error":        result.get("error"),
    }


async def execute_pipeline(
    category: str,
    products_per_run: int,
    dry_run: bool = False,
    content_only: bool = False,
    options: dict | None = None,
) -> dict:
    """
    Run one full pipeline pass and return a single JSON-serializable result.

    content_only=True generates pins but skips posting AND storing (no Pinterest
    login, no dedup writes) — the content-service path used by /api/generate.

    This is the request/response counterpart to `run_pipeline` (which streams UI
    events). It drives the SAME compiled graph, accumulates the final BotState,
    and returns a stable contract for API integrators.

    Returns a dict:
      {
        "ok": bool,                       # True only when status == "done"
        "status": "done"|"aborted"|"error",
        "input": {category, products_per_run, dry_run},
        "started_at": float, "finished_at": float, "elapsed_seconds": float,
        "posted_count": int,
        "pins": [ {asin, product_title, price, image, product_url, category,
                   rating, pin_title, pin_description, hashtags[],
                   affiliate_link, posted, post_error}, ... ],
        "nodes": {node_name: "done"|"error"|"skipped"|"pending"},
        "errors": [str, ...],
        "log": [str, ...],
      }
    """
    start = _now()
    result: dict = {
        "ok": False,
        "status": "error",
        "input": {
            "category": category,
            "products_per_run": products_per_run,
            "dry_run": dry_run,
        },
        "started_at": start,
        "finished_at": None,
        "elapsed_seconds": None,
        "posted_count": 0,
        "pins": [],
        "nodes": {n: "pending" for n in NODE_ORDER},
        "errors": [],
        "log": [],
    }

    # ── Validate required config before spinning up a browser ────────────
    try:
        cfg.validate()
    except ValueError as e:
        result["errors"] = [str(e)]
        result["finished_at"] = _now()
        result["elapsed_seconds"] = round(result["finished_at"] - start, 2)
        return result

    from playwright.async_api import async_playwright
    from graph.workflow import graph

    final = _initial_state(category, products_per_run)
    node_status = result["nodes"]
    fatal: str | None = None

    try:
        async with async_playwright() as pw:
            browser, amazon_page, pinterest_page = await _new_browser(pw)
            run_config = _run_config(amazon_page, pinterest_page, dry_run, content_only, options)
            try:
                async for event in graph.astream(
                    _initial_state(category, products_per_run), config=run_config
                ):
                    for node_name, update in event.items():
                        if node_name == "__end__":
                            continue
                        status = "done"
                        if isinstance(update, dict):
                            for key, value in update.items():
                                if key in _ADDITIVE_FIELDS:
                                    final[key] = final.get(key, []) + list(value)
                                else:
                                    final[key] = value
                            if update.get("errors"):
                                status = "error"
                        node_status[node_name] = status
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001 — surface any crash as structured JSON
        fatal = str(e)

    # ── Build the pins array (content + posting outcome) ─────────────────
    posted_map = {
        r.get("asin"): r
        for r in final.get("posted_pins", [])
        if isinstance(r, dict) and r.get("asin")
    }
    pins = [_pin_to_json(pin, posted_map) for pin in final.get("generated_content", [])]

    result["pins"] = pins
    result["posted_count"] = sum(1 for p in pins if p["posted"])
    result["errors"] = list(final.get("errors", []))
    result["log"] = list(final.get("stream_log", []))

    # Nodes never reached (early abort / crash) → skipped.
    for name, st in node_status.items():
        if st == "pending":
            node_status[name] = "skipped"

    # ── Final status ─────────────────────────────────────────────────────
    if fatal:
        result["errors"].append(f"pipeline crashed: {fatal}")
        result["status"] = "error"
        result["ok"] = False
    elif not final.get("generated_content"):
        # Ran cleanly but produced nothing (no products, all duplicates, etc.)
        result["status"] = "aborted"
        result["ok"] = False
    else:
        result["status"] = "done"
        result["ok"] = True

    result["finished_at"] = _now()
    result["elapsed_seconds"] = round(result["finished_at"] - start, 2)
    return result
