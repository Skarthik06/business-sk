"""
server.py  —  JSON API service for the RAG Affiliate Bot.

A minimal, integration-friendly HTTP service: send a JSON request, get a JSON
response. No heavy frontend — just a tiny two-column page (request | response)
served at `/`, and auto-generated API docs at `/docs`.

Run it:
    venv\\Scripts\\python.exe -m uvicorn server:app --reload
    # then open http://127.0.0.1:8000        (minimal UI)
    #      or   http://127.0.0.1:8000/docs   (OpenAPI docs)

Endpoints (all JSON):
  POST /api/run          → run the pipeline once, return the full structured result
  GET  /api/health       → liveness + whether a run is in progress
  GET  /api/config       → masked config + setup checklist (no secrets leaked)
  GET  /api/pipeline     → node metadata (order, labels, icons, descriptions)
  GET  /api/categories   → categories + commission rates
  GET  /api/stats        → dedup / memory stats (graceful if DB is down)
  GET  /api/history      → recently pinned products

Only ONE pipeline run executes at a time (single browser / single account); a
concurrent POST /api/run returns HTTP 409 with a JSON body.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from config import cfg
from pipeline_runner import execute_pipeline, NODE_ORDER
from chains import discovery as _discovery

MAX_PRODUCTS_PER_RUN = 25   # hard ceiling (matches the Amazon scrape cap)
MAX_CATEGORIES       = 8    # categories per request
ALLOWED_MARKETPLACES = {    # Amazon domains the scraper/deep-link support
    "amazon.in", "amazon.com", "amazon.co.uk", "amazon.ca",
    "amazon.de", "amazon.com.au", "amazon.ae", "amazon.sg",
}

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"   # Vite build output


# ─── Pipeline node metadata (single source of truth for the UI) ───────────────

NODE_META = [
    {"id": "scrape_amazon",       "label": "Scrape Amazon Best Sellers", "icon": "🕷",  "desc": "Playwright logs in & scrapes top products"},
    {"id": "check_duplicates",    "label": "Dedup Check",                 "icon": "🔁", "desc": "Filter already-pinned ASINs (PostgreSQL)"},
    {"id": "search_trends",       "label": "Tavily Trend Search",         "icon": "🔍", "desc": "Real-time trending keywords (optional)"},
    {"id": "rag_retrieve",        "label": "RAG Retrieve",                "icon": "🧠", "desc": "Similar past pins from pgvector"},
    {"id": "compose_pins",        "label": "Rank + Write Pins",           "icon": "🤖", "desc": "ONE structured gpt-5-nano call: select best + write all pins"},
    {"id": "get_affiliate_links", "label": "SiteStripe Links",            "icon": "🔗", "desc": "Affiliate link per ASIN"},
    {"id": "post_pinterest",      "label": "Post to Pinterest",           "icon": "📌", "desc": "Publish pins (human-like typing)"},
    {"id": "store_results",       "label": "Store Results",               "icon": "💾", "desc": "Write back to pgvector + PostgreSQL dedup"},
]

# Documented Amazon India commission rates (Pinterest-relevant categories).
CATEGORY_RATES = {
    "fashion": 9, "home": 8, "kitchen": 7, "beauty": 6,
    "fitness": 5, "toys": 5, "books": 4, "electronics": 4,
}
VALID_CATEGORIES = set(CATEGORY_RATES)

# Placeholder values shipped in .env.example — treated as "not configured".
_PLACEHOLDERS = {
    "", "sk-...", "sk-ant-...", "tvly-...", "ls__...",
    "your_amazon_email@gmail.com", "your_amazon_password",
    "your_pinterest_email@gmail.com", "your_pinterest_password",
    "you@gmail.com", "xxxx-xxxx-xxxx-xxxx",
    "postgresql://user:password@localhost:5432/affiliate_rag_bot",
}


def _is_set(value: Optional[str]) -> bool:
    return bool(value) and value.strip() not in _PLACEHOLDERS


# ─── Request / response models (input + output validation) ────────────────────

class RunRequest(BaseModel):
    """
    Validated JSON body for POST /api/run.

    Choose ONE or MORE categories (multi-select). Either send `categories` (a
    list) or `category` (a single string alias) — both are normalized to a
    de-duplicated `categories` list. `products_per_run` applies to EACH category.
    """
    model_config = {"extra": "forbid"}   # reject unknown keys — fail loud, not silent

    categories: Optional[list[str]] = Field(
        default=None,
        description="One or more Amazon category slugs to run.",
        examples=[["home", "fashion"]],
    )
    category: Optional[str] = Field(
        default=None,
        description="Single category (alias for a 1-item `categories`).",
        examples=["home"],
    )
    products_per_run: int = Field(
        default_factory=lambda: cfg.bot.products_per_run,
        ge=1, le=MAX_PRODUCTS_PER_RUN,
        description=f"How many pins to compose per category (1-{MAX_PRODUCTS_PER_RUN}).",
        examples=[3],
    )
    dry_run: bool = Field(
        default=False,
        description="If true, everything runs but NOTHING is posted to Pinterest.",
        examples=[True],
    )

    @model_validator(mode="after")
    def _resolve_categories(self) -> "RunRequest":
        raw = self.categories
        if raw is None:
            raw = [self.category] if self.category else [cfg.amazon.category]
        normalized: list[str] = []
        for c in raw:
            c = (c or "").strip().lower()
            if not c:
                continue
            if c not in VALID_CATEGORIES:
                raise ValueError(
                    f"unknown category '{c}'. Valid: {', '.join(sorted(VALID_CATEGORIES))}"
                )
            if c not in normalized:           # de-dupe, preserve order
                normalized.append(c)
        if not normalized:
            raise ValueError("at least one category is required")
        if len(normalized) > MAX_CATEGORIES:
            raise ValueError(f"at most {MAX_CATEGORIES} categories per request")
        self.categories = normalized
        self.category = None
        return self


class PinOut(BaseModel):
    """One composed pin plus its posting outcome (documents the output shape)."""
    asin: str
    product_title: str
    price: str
    image: str
    product_url: str
    category: str
    rating: str
    pin_title: str
    pin_description: str
    hashtags: list[str]
    affiliate_link: str
    posted: bool
    post_error: Optional[str] = None


class RunResult(BaseModel):
    """Structured result of ONE category's pipeline pass."""
    ok: bool
    status: str                      # done | aborted | error
    input: dict                      # {category, products_per_run, dry_run}
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    posted_count: int = 0
    pins: list[PinOut] = []
    nodes: dict[str, str] = {}
    errors: list[str] = []
    log: list[str] = []


class RunResponse(BaseModel):
    """
    Batch result of POST /api/run — one `runs[]` entry per requested category.
    `status` is done (all ok), partial (some ok), aborted (none produced pins),
    error (all errored), or busy (409).
    """
    ok: bool
    status: str
    input: dict                      # {categories, products_per_run, dry_run}
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    run_count: int = 0
    posted_count: int = 0            # summed across all categories
    runs: list[RunResult] = []
    errors: list[str] = []


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Affiliate Bot — JSON API",
    version="4.0",
    description="Amazon → Pinterest RAG affiliate pipeline as a JSON-in / JSON-out service.",
)

# One run at a time (single browser / single account).
_run_lock = asyncio.Lock()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "running": _run_lock.locked()}


@app.get("/api/config")
def get_config() -> dict:
    setup = [
        {"key": "openai",    "label": "OpenAI API key",   "required": True,
         "ok": _is_set(cfg.openai_api_key),  "hint": "OPENAI_API_KEY in .env"},
        {"key": "amazon",    "label": "Amazon login",     "required": True,
         "ok": _is_set(cfg.amazon.email) and _is_set(cfg.amazon.password),
         "hint": "AMAZON_EMAIL / AMAZON_PASSWORD"},
        {"key": "pinterest", "label": "Pinterest login",  "required": True,
         "ok": _is_set(cfg.pinterest.email) and _is_set(cfg.pinterest.password),
         "hint": "PINTEREST_EMAIL / PINTEREST_PASSWORD"},
        {"key": "database",  "label": "PostgreSQL + pgvector", "required": True,
         "ok": _is_set(cfg.storage.database_url), "hint": "DATABASE_URL in .env (with the vector extension enabled)"},
        {"key": "tavily",    "label": "Tavily (live trends)", "required": False,
         "ok": _is_set(cfg.tavily_api_key), "hint": "TAVILY_API_KEY — optional, has fallback"},
    ]
    ready = all(item["ok"] for item in setup if item["required"])
    return {
        "model": cfg.openai_model,
        "embedding_model": cfg.embedding_model,
        "marketplace": cfg.amazon.marketplace,
        "associate_tag": cfg.amazon.associate_tag,
        "board_name": cfg.pinterest.board_name,
        "products_per_run": cfg.bot.products_per_run,
        "delay_between_pins": cfg.bot.delay_between_pins,
        "headless": cfg.bot.headless,
        "setup": setup,
        "ready": ready,
    }


@app.get("/api/pipeline")
def get_pipeline() -> dict:
    return {"nodes": NODE_META, "order": NODE_ORDER}


@app.get("/api/categories")
def get_categories() -> dict:
    items = [{"name": name, "rate": rate} for name, rate in CATEGORY_RATES.items()]
    items.sort(key=lambda x: x["rate"], reverse=True)
    return {"categories": items, "default": cfg.amazon.category}


@app.get("/api/stats")
async def get_stats() -> dict:
    loop = asyncio.get_event_loop()
    try:
        from rag.dedup import dedup_store
        s = await loop.run_in_executor(None, dedup_store.stats)
        return {
            "db_ok": True,
            "total_seen": s.get("total_seen", 0),
            "by_category": s.get("by_category", {}),
        }
    except Exception as e:  # noqa: BLE001 — DB may be unconfigured/unreachable
        return {"db_ok": False, "total_seen": 0, "by_category": {}, "error": str(e)}


@app.get("/api/history")
async def get_history(limit: int = 20) -> dict:
    limit = max(1, min(int(limit), 200))
    loop = asyncio.get_event_loop()
    try:
        from rag.dedup import dedup_store
        rows = await loop.run_in_executor(None, lambda: dedup_store.history(limit))
        return {"db_ok": True, "items": rows}
    except Exception as e:  # noqa: BLE001
        return {"db_ok": False, "items": [], "error": str(e)}


@app.post("/api/run", response_model=RunResponse)
async def api_run(req: RunRequest) -> JSONResponse:
    """
    Run the pipeline for one or MORE categories and return a batch result.

    Input is validated (unknown keys rejected, each category checked,
    1 <= products_per_run <= 25, up to 8 categories). Categories run
    sequentially under a single-run lock (one browser / one account).
    A concurrent call while a run is active returns HTTP 409.
    """
    categories = req.categories or []

    if _run_lock.locked():
        return JSONResponse(
            status_code=409,
            content={
                "ok": False, "status": "busy",
                "input": {"categories": categories,
                          "products_per_run": req.products_per_run,
                          "dry_run": req.dry_run},
                "errors": ["A run is already in progress — only one run at a time."],
                "runs": [], "run_count": 0, "posted_count": 0,
            },
        )

    started = time.time()
    async with _run_lock:
        runs: list[dict] = []
        for cat in categories:
            runs.append(await execute_pipeline(
                category=cat,
                products_per_run=req.products_per_run,
                dry_run=req.dry_run,
            ))

    oks = [r["ok"] for r in runs]
    if all(oks):
        status = "done"
    elif any(oks):
        status = "partial"
    elif any(r["status"] == "error" for r in runs):
        status = "error"
    else:
        status = "aborted"

    finished = time.time()
    result = {
        "ok": all(oks),
        "status": status,
        "input": {"categories": categories,
                  "products_per_run": req.products_per_run,
                  "dry_run": req.dry_run},
        "started_at": started,
        "finished_at": finished,
        "elapsed_seconds": round(finished - started, 2),
        "run_count": len(runs),
        "posted_count": sum(r["posted_count"] for r in runs),
        "runs": runs,
        "errors": [f"[{r['input']['category']}] {e}" for r in runs for e in r["errors"]],
    }
    # Always return the JSON body (200); the `ok`/`status` fields convey outcome.
    return JSONResponse(status_code=200, content=result)


def _content_item(pin: dict) -> dict:
    """Flatten one composed pin into a consumer-friendly content item.

    Includes news-style aliases (title / summary / source / link / published) so
    downstream consumers that expect that shape (e.g. an Instagram carousel
    generator) can treat an affiliate product exactly like a news item — with the
    affiliate link as `link`, so clicks stay monetized.
    """
    caption = pin.get("pin_description", "")          # FTC disclosure already appended
    affiliate_link = pin.get("affiliate_link", "")
    return {
        # ── rich affiliate fields ──
        "asin":              pin.get("asin", ""),
        "category":          pin.get("category", ""),
        "product_title":     pin.get("product_title", ""),
        "price":             pin.get("price", ""),
        "orig_price":        pin.get("orig_price", ""),
        "discount_pct":      pin.get("discount_pct"),
        "rating":            pin.get("rating"),
        "reviews":           pin.get("reviews"),
        "bought_past_month": pin.get("bought_past_month", ""),
        "badge":             pin.get("badge", ""),
        "image_url":         pin.get("image", ""),
        "product_url":       pin.get("product_url", ""),
        "hashtags":          pin.get("hashtags", []),
        "affiliate_link":    affiliate_link,
        "content_style":     pin.get("content_style", ""),      # Phase 4 A/B style
        "content_warnings":  pin.get("content_warnings", []),   # Phase 4 fact-check (empty=clean)
        "display_title":     pin.get("display_title", ""),      # AI slide name (rendered onto the image)
        "deal_tag":          pin.get("deal_tag", ""),           # AI 1-2 word price-sticker hype word
        "cover_title":       pin.get("cover_title", ""),        # AI cover headline (first slide)
        "cover_subtitle":    pin.get("cover_subtitle", ""),     # AI cover subline
        # ── discovery scores (deterministic, derived from real fields — no fabrication) ──
        **_discovery.score_product(pin),
        # ── Phase 2+3: novelty + trend + confidence + intelligence + winner + evidence ──
        **_discovery.winner_bundle(pin, pin.get("novelty_score"), pin.get("trend_score")),
        # ── news-style aliases (drop-in for slide/caption generators) ──
        "title":          pin.get("pin_title", ""),
        "summary":        caption,
        "source":         "Amazon",
        "link":           affiliate_link,
        "published":      "",
    }


def _resolve_categories(categories: Optional[str], category: Optional[str]) -> list[str]:
    """Parse + validate categories from query params. Raises ValueError on bad input."""
    if categories:
        raw = categories.split(",")
    elif category:
        raw = [category]
    else:
        raw = [cfg.amazon.category]
    norm: list[str] = []
    for c in raw:
        c = (c or "").strip().lower()
        if not c:
            continue
        if c not in VALID_CATEGORIES:
            raise ValueError(f"unknown category '{c}'. Valid: {', '.join(sorted(VALID_CATEGORIES))}")
        if c not in norm:
            norm.append(c)
    if not norm:
        raise ValueError("at least one category is required")
    if len(norm) > MAX_CATEGORIES:
        raise ValueError(f"at most {MAX_CATEGORIES} categories")
    return norm


@app.get("/api/generate")
async def api_generate(
    categories: Optional[str] = Query(
        default=None, description="Comma-separated category slugs, e.g. home,fashion"),
    category: Optional[str] = Query(default=None, description="Single category (alias)"),
    q: Optional[str] = Query(
        default=None, max_length=80,
        description="Free-text keyword search (overrides categories, single run)."),
    products_per_run: int = Query(
        default=None, ge=1, le=MAX_PRODUCTS_PER_RUN,
        description=f"Items per category (1-{MAX_PRODUCTS_PER_RUN})."),
    marketplace: Optional[str] = Query(default=None, description="Amazon domain, e.g. amazon.in"),
    min_rating: Optional[float] = Query(default=None, ge=0, le=5),
    min_reviews: Optional[int] = Query(default=None, ge=0),
    price_min: Optional[int] = Query(default=None, ge=0),
    price_max: Optional[int] = Query(default=None, ge=0, le=1_000_000),
    content: Optional[str] = Query(default=None, description="caption style: auto|DEAL_DROP|STORY|LISTICLE|PROBLEM_SOLUTION|QUESTION|TRANSFORMATION|GIFT_GUIDE|BUDGET|PREMIUM|VIRAL_FIND"),
    goal: Optional[str] = Query(default=None, description="ranking goal: balanced|viral|intent|value|trending|fresh|commission"),
    deals: bool = Query(default=False, description="Deals mode: keep only products with a real current offer (discount % or deal badge)."),
    deals_min: Optional[int] = Query(default=None, ge=0, le=90, description="Deals mode: minimum discount percent to qualify (overrides DEALS_MIN_DISCOUNT)."),
    audience: Optional[str] = Query(default=None, description="Audience/gender targeting: men|women|kids (prefixes the search terms). Blank = everyone."),
    combo_budget: Optional[int] = Query(default=None, ge=0, le=1_000_000, description="if set, also return a combo (products from distinct categories summing <= this budget)"),
    combo_size: int = Query(default=3, ge=2, le=5),
) -> JSONResponse:
    """
    CONTENT SERVICE — generate ready-to-post product content and return it as JSON.

    Scrapes + composes but POSTS NOTHING and logs into nothing. Records every
    product it returns (dedup + pgvector), so repeats are skipped. Options:
      - categories / category  : which category (multi). OR
      - q                      : a free-text keyword (single run, overrides categories).
      - products_per_run       : items per run (1-25).
      - marketplace            : amazon.in / amazon.com / …
      - min_rating/min_reviews/price_min/price_max : per-request quality overrides.

    Example:  /api/generate?q=air+fryer&products_per_run=6&min_rating=4.2&price_max=8000
    """
    # ── options (quality + marketplace overrides) ────────────────────────
    options: dict = {}
    if marketplace:
        mk = marketplace.strip().lower()
        if mk not in ALLOWED_MARKETPLACES:
            return JSONResponse(status_code=422, content={
                "ok": False, "error": f"unsupported marketplace '{mk}'. Allowed: "
                f"{', '.join(sorted(ALLOWED_MARKETPLACES))}", "items": []})
        options["marketplace"] = mk
    for k, v in (("min_rating", min_rating), ("min_reviews", min_reviews),
                 ("price_min", price_min), ("price_max", price_max)):
        if v is not None:
            options[k] = v
    if content and content.strip():
        options["content_style"] = content.strip()
    if deals:
        options["deals"] = True     # keep only products carrying a real current offer
        if deals_min is not None:
            options["deals_min"] = deals_min
    if audience:
        aud = audience.strip().lower()
        _AUD_MAP = {"men": "men", "male": "men", "women": "women", "female": "women",
                    "kids": "kids", "children": "kids", "child": "kids", "everyone": "", "all": ""}
        aud = _AUD_MAP.get(aud, "")
        if aud:
            options["audience"] = aud

    ppr = int(products_per_run or cfg.bot.products_per_run)

    # ── keyword mode vs category mode ────────────────────────────────────
    if q and q.strip():
        label = q.strip()
        runs = [(label, {**options, "q": label})]
        labels = [label]
    else:
        try:
            labels = _resolve_categories(categories, category)
        except ValueError as e:
            return JSONResponse(status_code=422, content={"ok": False, "error": str(e), "items": []})
        runs = [(cat, options) for cat in labels]

    if _run_lock.locked():
        return JSONResponse(
            status_code=409,
            content={"ok": False, "status": "busy",
                     "error": "A run is already in progress — only one at a time.",
                     "items": []},
        )

    started = time.time()
    async with _run_lock:
        items: list[dict] = []
        errors: list[str] = []
        for label, opts in runs:
            try:
                # Bound each run so a hung scrape/compose can NEVER hold the single-run lock
                # forever (which would 409 every future generate until a restart).
                r = await asyncio.wait_for(execute_pipeline(
                    category=label, products_per_run=ppr, dry_run=False,
                    content_only=True, options=opts), timeout=cfg.bot.run_timeout if hasattr(cfg.bot, "run_timeout") else 200)
            except asyncio.TimeoutError:
                errors.append(f"[{label}] timed out (scrape/compose hung) — skipped so the queue stays live")
                continue
            items.extend(_content_item(p) for p in r["pins"])
            errors.extend(f"[{label}] {e}" for e in r["errors"])

    # Phase 7 — fold measured performance priors (per category) into intelligence + winner
    # score, so categories that actually performed rise. No data ⇒ unchanged (G7).
    if cfg.performance.enabled:
        try:
            from performance.learner import learner
            cats = learner.category_priors()
            if cats:
                for it in items:
                    pr = cats.get(it.get("category", ""))
                    prior = pr["prior"] if pr else None
                    if prior is not None:
                        import runtime as _rt
                        it["performance_prior"] = prior
                        it["intelligence_score"] = _discovery.blend_prior(
                            it.get("intelligence_score", it.get("content_score", 0)),
                            prior, _rt.get("PERFORMANCE_PRIOR_WEIGHT", cfg.performance.prior_weight, "float"))
                        it["winner_score"] = _discovery.winner_score(
                            it["intelligence_score"], it.get("confidence", 1.0))
                        it["winner_tier"] = _discovery.tier(it["winner_score"])
        except Exception:
            pass

    # Goal-driven ranking (Find Winners): re-rank by the goal's signal, else winner score.
    _GOAL_KEY = {
        "viral": "instagram_score", "intent": "purchase_intent_score", "value": "value_score",
        "trending": "trend_score", "fresh": "novelty_score",
    }
    g = (goal or "").strip().lower()
    if g == "commission":
        rate = _discovery.COMMISSION
        items.sort(key=lambda it: rate.get((it.get("category") or "").lower(), 0), reverse=True)
    elif g in _GOAL_KEY:
        k = _GOAL_KEY[g]
        items.sort(key=lambda it: (it.get(k) or 0, it.get("winner_score", 0)), reverse=True)
    elif deals:
        # Deals mode + balanced goal: surface the best-PAYING, deepest deals automatically —
        # commission rate × discount depth, tie-broken by Winner Score — so "finest products
        # at the greatest commission" needs no extra goal pick. An explicit goal above wins.
        rate = _discovery.COMMISSION
        def _deal_value(it):
            comm = rate.get((it.get("category") or "").lower(), 0.04) or 0.04
            disc = float(it.get("discount_pct") or 0)
            return (comm * max(disc, 1.0), it.get("winner_score", 0))
        items.sort(key=_deal_value, reverse=True)
    else:
        # balanced (default): Winner Score (intelligence × confidence, novelty/trend/perf-aware).
        items.sort(key=lambda it: it.get("winner_score", it.get("content_score", 0)), reverse=True)

    # Combo builder: a set of products from DISTINCT categories whose real prices sum <= budget.
    combo = None
    if combo_budget and items:
        bundles = _discovery.build_bundles([{**it, "content_score": it.get("winner_score", it.get("content_score", 0))} for it in items],
                                           int(combo_budget), size=int(combo_size))
        if bundles:
            combo = bundles[0]

    # Trend context for this run (JSON) — momentum/direction per category (cold-start empty).
    trend_ctx: list[dict] = []
    if cfg.trends.enabled:
        try:
            from rag.trends import trend_store
            for lab in labels[:MAX_CATEGORIES]:
                trend_ctx.extend(trend_store.category_trends(lab, limit=8))
        except Exception:
            pass

    finished = time.time()
    return JSONResponse(status_code=200, content={
        "ok": len(items) > 0,
        "status": "done" if items else "empty",
        "query": q or None,
        "categories": labels,
        "marketplace": options.get("marketplace") or cfg.amazon.marketplace,
        "products_per_run": ppr,
        "count": len(items),
        "items": items,
        # ONE universal caption + hashtags for this run's carousel (shared across its items).
        "caption": (items[0].get("summary") if items else ""),
        "hashtags": (items[0].get("hashtags") if items else []),
        # AI-written cover copy for the first slide (shared across the carousel).
        "cover_title": (items[0].get("cover_title") if items else ""),
        "cover_subtitle": (items[0].get("cover_subtitle") if items else ""),
        "tiers": {t: sum(1 for it in items if it.get("tier") == t) for t in ("S", "A", "B", "C", "D")},
        # Phase 2 — winner tiers (from winner_score) + novelty coverage of this batch.
        "winner_tiers": {t: sum(1 for it in items if it.get("winner_tier") == t) for t in ("S", "A", "B", "C", "D")},
        "avg_novelty": (round(sum(it["novelty_score"] for it in items if it.get("novelty_score") is not None)
                              / max(1, sum(1 for it in items if it.get("novelty_score") is not None)), 1)
                        if any(it.get("novelty_score") is not None for it in items) else None),
        # Phase 3 — trend context for this run (JSON: keyword, momentum, direction, …).
        "trends": trend_ctx,
        # Phase 4 — content style used + fact-check summary.
        "content_style": (items[0].get("content_style") if items else ""),
        "content_warnings": [w for it in items for w in it.get("content_warnings", [])],
        # goal-driven ranking + optional combo bundle.
        "goal": g or "balanced",
        "combo": combo,
        "elapsed_seconds": round(finished - started, 2),
        "errors": errors,
    })


# ══════════════════════════════════════════════════════════════════════════════
# DISCOVERY — taxonomy + collections (price bands + bundles), all real-data.
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/taxonomy")
def get_taxonomy() -> dict:
    """Category families → subcategories → content angles + price bands. Powers the UI's
    category/collection surfaces and documents how products are retrieved per category."""
    fams: dict = {}
    for base, fam in _discovery.FAMILY.items():
        fams.setdefault(fam, {"family": fam, "base_categories": [], "subcategories": [], "angles": []})
        fams[fam]["base_categories"].append(base)
        fams[fam]["subcategories"] += _discovery.SUBCATEGORIES.get(base, [])
        fams[fam]["angles"] += _discovery.ANGLES.get(base, [])
    by_category = {
        base: {
            "family": _discovery.FAMILY.get(base),
            "subcategories": _discovery.SUBCATEGORIES.get(base, []),
            "angles": _discovery.ANGLES.get(base, []),
        }
        for base in _discovery.FAMILY
    }
    return {
        "families": list(fams.values()),
        "by_category": by_category,
        "price_bands": [{"low": lo, "high": hi, "label": lb} for lo, hi, lb in _discovery.PRICE_BANDS],
        "tiers": [{"tier": "S", "min": 90}, {"tier": "A", "min": 80}, {"tier": "B", "min": 70},
                  {"tier": "C", "min": 60}, {"tier": "D", "min": 0}],
        "weights": _discovery.WEIGHTS,
    }


@app.get("/api/discovery/queries")
def get_discovery_queries(category: Optional[str] = None, limit: int = 30) -> dict:
    """Phase 1 — the Discovery Planner's learned query yields (which search intents
    produce the most fresh, unique products). Powers the Intelligence panel and lets
    you see/tune how discovery is rotating. Empty on first runs (cold start)."""
    from rag.discovery_stats import discovery_stats
    rows = discovery_stats.top(category, limit=max(1, min(limit, 200)))
    return {"ok": True, "count": len(rows), "queries": rows}


@app.get("/api/trends")
def get_trends(category: Optional[str] = None, limit: int = 30) -> dict:
    """Phase 3 — Trend Intelligence. Trending keywords with INTERNAL momentum (0-100) +
    direction (EXPLODING/RISING/STABLE/DECLINING/UNKNOWN), built from persisted
    observations. JSON only. Empty on cold start; momentum is a model score, not a
    market fact. Pass ?category= to scope to one category."""
    from rag.trends import trend_store
    lim = max(1, min(limit, 200))
    rows = trend_store.category_trends(category, limit=lim) if category else trend_store.all_trends(limit=lim)
    return {"ok": True, "category": category, "count": len(rows), "trends": rows}


@app.get("/api/trends/{category}")
def get_trends_for_category(category: str, limit: int = 20) -> dict:
    """Trend Intelligence scoped to one category (path form)."""
    from rag.trends import trend_store
    rows = trend_store.category_trends(category, limit=max(1, min(limit, 200)))
    return {"ok": True, "category": category, "count": len(rows), "trends": rows}


@app.get("/api/seasons")
def get_seasons() -> dict:
    """Festival & seasonal-deal calendar (India) — what's coming up + which categories,
    keywords and caption angle sell best around it. Powers the Discover seasonal banner
    and steers captions toward the occasion."""
    import seasons
    return {"ok": True, **seasons.context()}


@app.get("/api/collections")
def get_collections(category: Optional[str] = None) -> dict:
    """Build truthful collections from ALREADY-POSTED products (real prices): price-band
    collections + budget-fit bundles. Powers the storefront's 'Under ₹X' + 'Setup' sections."""
    from rag.posts import post_store
    raw = post_store.all_products(category)               # [{asin,product_title,price,image,affiliate_link,category}]
    scored = [{**p, **_discovery.score_product(p)} for p in raw]
    scored.sort(key=lambda x: x.get("content_score", 0), reverse=True)
    bundles = []
    for budget in (2000, 3000, 5000):
        bundles += _discovery.build_bundles(scored, budget)
    return {
        "ok": True,
        "count": len(scored),
        "price_bands": _discovery.build_price_bands(scored),
        "bundles": bundles,
        "top_picks": scored[:12],
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST HISTORY — the affiliate only GENERATES + RECORDS. The actual Instagram
# posting is done by the IG backend (POST /api/sk/carousel) using the selected
# rags account's encrypted token, so tokens never live here.
# ══════════════════════════════════════════════════════════════════════════════

class RecordPostRequest(BaseModel):
    model_config = {"extra": "forbid"}
    category:  str
    products:  list[dict] = Field(default_factory=list)   # [{asin, product_title, price, image_url, affiliate_link}]
    media_id:  Optional[str] = None
    permalink: Optional[str] = None
    caption:   str = ""
    status:    str = "posted"
    content_style: str = ""              # Phase 4 A/B style (from /api/generate item)


@app.post("/api/posts")
def record_post(body: RecordPostRequest) -> dict:
    """Record a published carousel as post_<N>#category (uniqueness + history)."""
    from rag.posts import post_store
    minimal = [{
        "asin": p.get("asin", ""), "product_title": p.get("product_title", ""),
        "price": p.get("price", ""), "image": p.get("image_url") or p.get("image", ""),
        "affiliate_link": p.get("affiliate_link", ""),
        # richer fields so the public storefront can show discount + social proof
        "orig_price": p.get("orig_price", ""), "discount_pct": p.get("discount_pct"),
        "rating": p.get("rating"), "reviews": p.get("reviews"),
    } for p in (body.products or [])]
    rec = post_store.record(body.category, minimal, body.media_id, body.permalink,
                            body.caption, status=body.status, content_style=body.content_style)
    return {"ok": True, "post": rec}


@app.get("/api/posts")
def list_posts(limit: int = 50) -> dict:
    limit = max(1, min(int(limit), 200))
    try:
        from rag.posts import post_store
        return {"ok": True, "posts": post_store.list(limit), "stats": post_store.stats()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "posts": [], "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHING PLATFORM (Phase 5) — draft/queue/schedule + emergency stop. The
# intelligence layer stages jobs; the IG automation service executes them.
# ══════════════════════════════════════════════════════════════════════════════

class EnqueueRequest(BaseModel):
    model_config = {"extra": "forbid"}
    category:      str = ""
    payload:       dict = Field(default_factory=dict)   # the draft carousel (items/caption/hashtags)
    account_id:    str = "default"
    status:        str = "QUEUED"                        # DRAFT | QUEUED | SCHEDULED


class JobActionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    job_id: str
    to:     Optional[str] = None                         # for transition
    error:  Optional[str] = None


class EmergencyStopRequest(BaseModel):
    model_config = {"extra": "forbid"}
    on: bool = True


@app.get("/api/publishing/queue")
def publishing_queue(status: Optional[str] = None, account_id: Optional[str] = None,
                     limit: int = 100) -> dict:
    from publishing.queue import publish_queue
    return {"ok": True, "jobs": publish_queue.list(status, account_id, limit)}


@app.post("/api/publishing/queue")
def publishing_enqueue(body: EnqueueRequest) -> dict:
    from publishing.queue import publish_queue
    job = publish_queue.enqueue(body.category, body.payload, body.account_id, status=body.status)
    return {"ok": True, "job": job}


@app.post("/api/publishing/transition")
def publishing_transition(body: JobActionRequest) -> dict:
    """State-machine move (used by the IG automation service): RUNNING/PUBLISHED/FAILED/…"""
    from publishing.queue import publish_queue
    return publish_queue.transition(body.job_id, body.to or "", body.error)


@app.post("/api/publishing/cancel")
def publishing_cancel(body: JobActionRequest) -> dict:
    from publishing.queue import publish_queue
    return publish_queue.cancel(body.job_id)


@app.get("/api/publishing/next")
def publishing_next(account_id: str = "default") -> dict:
    """The next due job for the IG automation service to execute (None if nothing/stopped)."""
    from publishing.queue import publish_queue
    return {"ok": True, "job": publish_queue.next_due(account_id)}


@app.post("/api/publishing/emergency-stop")
def publishing_emergency_stop(body: EmergencyStopRequest) -> dict:
    from publishing.queue import publish_queue
    return {"ok": True, **publish_queue.emergency_stop(body.on)}


@app.get("/api/publishing/account")
def publishing_account(account_id: str = "default") -> dict:
    from publishing.queue import publish_queue
    return {"ok": True, "health": publish_queue.account_health(account_id)}


# ══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE LOOP (Phase 6) + LEARNING (Phase 7) + INTELLIGENCE.
# Metrics come ONLY from a connected source; absent ⇒ "not connected" (never faked).
# ══════════════════════════════════════════════════════════════════════════════

class PerformanceIngestRequest(BaseModel):
    model_config = {"extra": "forbid"}
    post_id:    str
    account_id: str = "default"
    source:     str = "manual"                 # e.g. 'instagram_insights', 'affiliate_tracker'
    metrics:    dict = Field(default_factory=dict)  # {reach, saves, link_clicks, orders, commission, …}


@app.post("/api/performance/ingest")
def performance_ingest(body: PerformanceIngestRequest) -> dict:
    """Ingest observed metrics for a post from a connected source. Only real numbers."""
    from performance.store import performance_store
    return {"ok": True, **performance_store.ingest(body.post_id, body.metrics,
                                                    body.account_id, body.source)}


@app.get("/api/performance/overview")
def performance_overview() -> dict:
    from performance.store import performance_store
    return {"ok": True, **performance_store.overview()}


@app.get("/api/performance/posts")
def performance_posts(limit: int = 50) -> dict:
    from performance.store import performance_store
    return {"ok": True, "posts": performance_store.by_posts(max(1, min(limit, 200)))}


@app.get("/api/performance/categories")
def performance_categories() -> dict:
    from performance.learner import learner
    return {"ok": True, "priors": learner.category_priors()}


# ── Affiliate NETWORK attribution (the results / earnings panel) ──────────────

@app.get("/api/networks")
def networks_summary(days: int = 30) -> dict:
    """Per-network earnings + the registry (live networks + expansion slots) + top products +
    the post funnel — everything the Performance panel renders."""
    from performance.networks import network_store
    from performance.store import performance_store
    from performance import cuelinks
    d = max(1, min(days, 365))
    return {"ok": True,
            **network_store.summary(d),
            "top_products": network_store.top_products(d),
            "funnel": performance_store.overview(),
            "cuelinks_api": cuelinks.configured()}


class NetworkImportRequest(BaseModel):
    model_config = {"extra": "forbid"}
    network: str
    rows:    list[dict] = Field(default_factory=list)   # [{period_date, [asin], [clicks], [orders], [earnings]}]


@app.post("/api/networks/import")
def networks_import(body: NetworkImportRequest) -> dict:
    """Record an affiliate report export (Cuelinks / Amazon / Flipkart / Myntra) — the manual
    path when a network has no live API. Only real report rows; nothing is fabricated."""
    from performance.networks import network_store
    return network_store.record_earnings(body.network, body.rows, source="import")


@app.post("/api/networks/cuelinks/sync")
def networks_cuelinks_sync(days: int = 30) -> dict:
    """Pull the last `days` of Cuelinks earnings via its API (needs CUELINKS_API_TOKEN)."""
    from performance import cuelinks
    return cuelinks.sync(max(1, min(days, 365)))


# ── Universal Search — AI-inferred, per-product filter dimensions ─────────────

@app.get("/api/search/filters")
async def search_filters(q: str = Query(..., min_length=2, max_length=80,
                                        description="Free-text product search, e.g. 'brown shirt' or 'iphone 18 pro max'")) -> dict:
    """Return the filter dimensions most relevant to THIS product (AI-inferred). The UI renders
    them as chips; the user's picks refine the search + soft-rank. Degrades to a generic set."""
    from chains.search_filters import suggest_filters
    filters = await suggest_filters(q)
    if not filters:                              # generic fallback so the card always has filters
        filters = [{"name": "Brand", "options": ["Any", "Top brands only"]},
                   {"name": "Colour", "options": ["Black", "White", "Blue", "Red", "Neutral"]}]
    return {"ok": True, "query": q, "filters": filters}


@app.get("/api/intelligence/insights")
def intelligence_insights() -> dict:
    """Learned recommendations (best categories/styles by measured outcome). Empty until data."""
    from performance.learner import learner
    return {"ok": True, **learner.recommendations()}


@app.get("/api/intelligence/recommendations")
def intelligence_recommendations() -> dict:
    from performance.learner import learner
    return {"ok": True, **learner.recommendations()}


@app.get("/api/intelligence/winners")
def intelligence_winners(limit: int = 12) -> dict:
    """Predicted-winner shortlist (Phase 10) — uses measured priors when available, else
    falls back to the deterministic winner_score over posted products (graceful, no ML needed)."""
    from prediction.model import predict_winners
    return {"ok": True, **predict_winners(limit=max(1, min(limit, 50)))}


# ── Multi-retailer (Phase 8) ──────────────────────────────────────────────────

@app.get("/api/retailers")
def list_retailers() -> dict:
    """Adapter registry + health. Amazon is live; others report not-implemented (no fabrication)."""
    from tools.retailers import health, enabled_retailers
    return {"ok": True, "enabled": enabled_retailers(), "adapters": health()}


# ── Competitor intelligence (Phase 9 — isolated, opt-in) ──────────────────────

class CompetitorRequest(BaseModel):
    model_config = {"extra": "forbid"}
    handle: str
    note:   str = ""


@app.get("/api/competitors")
def list_competitors() -> dict:
    from competitor.store import competitor_store
    if not competitor_store.enabled():
        return {"ok": True, "enabled": False, "competitors": [],
                "note": "competitor intelligence disabled (set COMPETITOR_ENABLED=1)"}
    return {"ok": True, "enabled": True, "competitors": competitor_store.list()}


@app.post("/api/competitors")
def add_competitor(body: CompetitorRequest) -> dict:
    from competitor.store import competitor_store
    if not competitor_store.enabled():
        return {"ok": False, "error": "competitor intelligence disabled (set COMPETITOR_ENABLED=1)"}
    return competitor_store.add(body.handle, body.note)


# ── Agents control panel (editable constraints, runtime overlay) ──────────────

AGENT_ROSTER = [
    {"name": "discovery-planner", "role": "Multi-query mining, pagination, adaptive rotation"},
    {"name": "novelty-analyst", "role": "Semantic freshness vs posted pins"},
    {"name": "winner-engine", "role": "Confidence + intelligence + winner score + evidence"},
    {"name": "trend-analyst", "role": "Persistent trends, momentum + direction, trend-aware"},
    {"name": "content-intelligence", "role": "Content styles (A/B), hashtag bank, caption fact-check"},
    {"name": "publishing-agent", "role": "Queue + state machine + spacing + emergency stop"},
    {"name": "account-safety", "role": "Account health + emergency stop"},
    {"name": "performance-analyst", "role": "Measured results + funnel metrics"},
    {"name": "learning-agent", "role": "Category/style priors folded into ranking"},
    {"name": "retailer-adapter", "role": "Multi-retailer interface + affiliate-link service"},
    {"name": "competitor-intel", "role": "Opt-in watchlist (market signal, never cloning)"},
    {"name": "winner-prediction", "role": "Predicted winners (deterministic → historical)"},
    {"name": "product-scout", "role": "Retrieval + SOFT quality thresholds (rating/reviews/price/deals rank, never hard-exclude) → always fills the requested count"},
    {"name": "search-planner", "role": "Universal search: AI-inferred per-product filters (any product) → refine query + soft-rank"},
    {"name": "product-scorer", "role": "Multi-score ranking + tiers"},
    {"name": "creative-copywriter", "role": "AI cover headline, clean slide names + catchy deal-sticker words (one call)"},
    {"name": "still-set-renderer", "role": "Slide design, product collage cover, cutout + non-repeating covers"},
    {"name": "attribution-analyst", "role": "Real earnings per network + product (Cuelinks/Amazon/…); closes the results loop"},
]


@app.get("/api/render-config")
def render_config() -> dict:
    """Product-cutout / render knobs for the Still-Set renderer, read live by the IG backend.
    Values come from the runtime overlay (Agents panel → still-set-renderer), else defaults."""
    import runtime as _rt
    return {
        "isolate":         bool(_rt.get("RENDER_ISOLATE", 1, "int")),
        "alpha_matting":   bool(_rt.get("RENDER_ALPHA_MATTING", 1, "int")),
        "erode":           _rt.get("RENDER_ALPHA_ERODE", 0, "int"),
        "knockout_thresh": _rt.get("RENDER_KNOCKOUT_THRESH", 30, "int"),
        "model":           (_rt.get("RENDER_ISOLATE_MODEL", "u2net", "str") or "u2net"),
        "brand_logos":     bool(_rt.get("RENDER_BRAND_LOGOS", 1, "int")),
        "brand_max":       _rt.get("RENDER_BRAND_MAX", 4, "int"),
    }


class AgentSettingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    key:   str
    value: str


@app.get("/api/agents")
def list_agents() -> dict:
    """The agent roster + their editable constraints (current effective value + bounds)."""
    import runtime
    overrides = runtime.all_overrides()
    knobs: dict = {}
    for key, spec in runtime.TUNABLE.items():
        knobs.setdefault(spec["agent"], []).append({
            "key": key, "label": spec["label"], "type": spec["type"],
            "min": spec["min"], "max": spec["max"],
            "value": overrides.get(key, ""), "is_overridden": key in overrides,
        })
    agents = [{**a, "editable": knobs.get(a["name"], [])} for a in AGENT_ROSTER]
    return {"ok": True, "count": len(agents), "agents": agents}


@app.post("/api/agents/settings")
def set_agent_setting(body: AgentSettingRequest) -> dict:
    """Set a runtime constraint (validated + bounded). Takes effect within ~5s, no restart."""
    import runtime
    return runtime.set_value(body.key, body.value)


@app.delete("/api/agents/settings/{key}")
def clear_agent_setting(key: str) -> dict:
    """Reset a constraint to its config/env default."""
    import runtime
    return runtime.clear(key)


# ─── LINK HUB — one page with ALL posted products carrying your affiliate tag ──

@app.get("/api/hub")
def hub_json(category: Optional[str] = None) -> dict:
    """All posted products (deduped) with affiliate links — data for the hub."""
    from rag.posts import post_store
    return {"ok": True, "category": category, "products": post_store.all_products(category)}


@app.get("/hub", response_class=HTMLResponse)
def hub_page(category: Optional[str] = None) -> HTMLResponse:
    """The public storefront — the 'link in bio' surface. A polished, converting shop:
    brand hero, live search, category filter chips, sort, a Top-Deals strip, and elegant
    cards. Every product links to Amazon with your associate tag. Client-side search/filter/
    sort over a server-rendered grid, so it's fast, shareable, and works without a build step."""
    from html import escape
    from collections import OrderedDict
    from rag.posts import post_store
    from tools.amazon import _hi_res_image
    products = post_store.all_products(category)

    def _num(v):
        try:
            return float(str(v).replace(",", "").strip() or 0)
        except Exception:
            return 0.0

    def _card(p: dict, featured: bool = False) -> str:
        img = escape(_hi_res_image(p.get("image", "")))
        title = escape((p.get("product_title") or "")[:100])
        price = escape(p.get("price", "") or "")
        orig = escape(p.get("orig_price", "") or "")
        disc = int(p.get("discount_pct") or 0)
        rating = p.get("rating")
        reviews = p.get("reviews")
        cat = ((p.get("category") or "other").strip().lower()) or "other"
        link = escape(p.get("affiliate_link") or f"https://www.{cfg.amazon.marketplace}/dp/{p.get('asin','')}?tag={cfg.amazon.associate_tag}")
        proof = []
        if rating is not None:
            proof.append(f'★ {escape(str(rating))}')
        if reviews:
            proof.append(f'{escape(str(reviews))} reviews')
        proof_html = f'<div class="proof">{" · ".join(proof)}</div>' if proof else ""
        meta = (f'<span class="orig">{orig}</span>' if orig else "") + (f'<span class="off">-{disc}%</span>' if disc else "")
        return f"""<a class="card{' feat' if featured else ''}" href="{link}" target="_blank" rel="nofollow noopener sponsored"
          data-cat="{escape(cat)}" data-title="{title.lower()}" data-disc="{disc}" data-price="{_num(p.get('price'))}" data-rating="{_num(rating)}">
          <div class="imgwrap"><img loading="lazy" src="{img}" alt="">{f'<span class="badge">-{disc}% OFF</span>' if disc else ''}</div>
          <div class="body"><div class="title">{title}</div>{proof_html}
            <div class="prices"><span class="price">{price}</span>{meta}</div>
            <span class="btn">Shop on Amazon →</span></div>
        </a>"""

    by_cat: "OrderedDict[str, list]" = OrderedDict()
    for p in products:
        by_cat.setdefault(((p.get("category") or "other").strip().lower()) or "other", []).append(p)
    all_cards = "\n".join(_card(p) for p in products)
    deals = sorted([p for p in products if (p.get("discount_pct") or 0) >= 40],
                   key=lambda p: p.get("discount_pct") or 0, reverse=True)[:8]
    deals_html = ("".join(_card(p, featured=True) for p in deals)) if deals else ""
    chips = ('<button class="chip on" data-f="all">All</button>'
             + "".join(f'<button class="chip" data-f="{escape(c)}">{escape(c.title())}<b>{len(items)}</b></button>'
                       for c, items in by_cat.items()))
    empty = '' if products else '<p class="empty">No products yet — publish some from Business-SK.</p>'

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SK · The Edit — Amazon Picks</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Hanken+Grotesk:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
 *{{box-sizing:border-box}}
 :root{{--bg:#F7F3EC;--card:#FFFFFF;--ink:#241c17;--muted:#7c6f64;--line:#e6ddd0;--accent:#B4472F;--accent2:#c96a1e}}
 body{{margin:0;background:var(--bg);color:var(--ink);font-family:'Hanken Grotesk',system-ui,sans-serif}}
 a{{text-decoration:none;color:inherit}}
 .hero{{padding:40px 20px 18px;text-align:center;background:radial-gradient(120% 90% at 50% 0%, #fff 0%, var(--bg) 70%)}}
 .brand{{font-family:'Space Mono',monospace;font-size:13px;letter-spacing:.34em;text-transform:uppercase;color:var(--accent)}}
 .hero h1{{font-family:'Instrument Serif',Georgia,serif;font-size:52px;line-height:1;margin:8px 0 6px;letter-spacing:-.01em}}
 .hero p{{margin:0;color:var(--muted);font-size:15px}}
 .search{{max-width:520px;margin:20px auto 0;position:relative}}
 .search input{{width:100%;padding:14px 18px;border:1.5px solid var(--line);border-radius:100px;background:#fff;font-size:15px;font-family:inherit;color:var(--ink);box-shadow:0 8px 24px rgba(20,30,45,.06)}}
 .search input:focus{{outline:none;border-color:var(--accent)}}
 .bar{{position:sticky;top:0;z-index:5;background:rgba(247,243,236,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:12px 0}}
 .chips{{display:flex;gap:9px;overflow-x:auto;padding:0 16px;max-width:1040px;margin:0 auto;scrollbar-width:none}} .chips::-webkit-scrollbar{{display:none}}
 .chip{{flex:none;font-family:'Space Mono',monospace;font-size:13px;font-weight:700;color:var(--muted);background:#fff;border:1.5px solid var(--line);border-radius:100px;padding:8px 16px;cursor:pointer;display:flex;gap:7px;align-items:center;white-space:nowrap;transition:.15s}}
 .chip b{{color:var(--accent);font-weight:700}} .chip.on{{background:var(--accent);color:#fff;border-color:var(--accent)}} .chip.on b{{color:#fff}}
 .sortrow{{max-width:1040px;margin:14px auto 0;padding:0 16px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}}
 .sortrow .n{{font-family:'Space Mono',monospace;font-size:12px;color:var(--muted)}}
 select{{font-family:inherit;font-size:13px;padding:8px 12px;border:1.5px solid var(--line);border-radius:10px;background:#fff;color:var(--ink)}}
 main{{max-width:1040px;margin:0 auto;padding:8px 16px 40px}}
 .sec-h{{font-family:'Instrument Serif',Georgia,serif;font-size:30px;margin:26px 4px 14px;display:flex;align-items:center;gap:10px}}
 .sec-h .fire{{font-size:22px}}
 .strip{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(190px,1fr);gap:14px;overflow-x:auto;padding:2px 2px 10px;scrollbar-width:none}} .strip::-webkit-scrollbar{{display:none}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(184px,1fr));gap:16px}}
 .card{{background:var(--card);border:1.5px solid var(--line);border-radius:18px;overflow:hidden;display:flex;flex-direction:column;transition:transform .15s,box-shadow .15s,border-color .15s;box-shadow:0 6px 18px rgba(20,30,45,.05)}}
 .card:hover{{transform:translateY(-3px);box-shadow:0 16px 34px rgba(20,30,45,.12);border-color:var(--accent)}}
 .imgwrap{{position:relative;aspect-ratio:1;background:#fff;display:grid;place-items:center;padding:10px}} .imgwrap img{{max-width:100%;max-height:100%;object-fit:contain}}
 .badge{{position:absolute;top:10px;left:10px;background:var(--accent);color:#fff;font:700 12px 'Space Mono',monospace;padding:4px 9px;border-radius:8px}}
 .body{{padding:13px 14px 15px;display:flex;flex-direction:column;gap:7px;flex:1}}
 .title{{font-size:14px;line-height:1.32;font-weight:600;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
 .proof{{font:700 11px 'Space Mono',monospace;color:var(--muted)}}
 .prices{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-top:auto}}
 .price{{font-weight:800;font-size:18px}} .orig{{text-decoration:line-through;color:#a99;font-size:13px}} .off{{color:var(--accent);font:700 13px 'Space Mono',monospace}}
 .btn{{margin-top:9px;display:inline-block;font:700 12px 'Space Mono',monospace;color:#fff;background:var(--ink);border-radius:100px;padding:9px 15px;text-align:center;letter-spacing:.02em}}
 .card:hover .btn{{background:var(--accent)}}
 .empty{{text-align:center;color:var(--muted);padding:60px}}
 .disc{{text-align:center;color:var(--muted);font-size:11px;padding:6px 18px}}
 footer{{text-align:center;color:var(--muted);font-size:12px;padding:22px}}
 .nomatch{{display:none;text-align:center;color:var(--muted);padding:40px}}
</style></head><body>
<div class="hero">
  <div class="brand">SK · The Edit</div>
  <h1>Today's Best Finds</h1>
  <p>Handpicked deals on Amazon — updated live. Tap any product to shop.</p>
  <div class="search"><input id="q" type="search" placeholder="Search {len(products)} products…" autocomplete="off"></div>
</div>
<div class="bar"><div class="chips">{chips}</div></div>
<main>
  {f'<h2 class="sec-h" id="dealsH"><span class="fire">🔥</span> Top Deals</h2><div class="strip" id="deals">{deals_html}</div>' if deals_html else ''}
  <div class="sortrow"><span class="n" id="count">{len(products)} products</span>
    <select id="sort">
      <option value="disc">Biggest discount</option>
      <option value="rating">Top rated</option>
      <option value="plow">Price: low to high</option>
      <option value="phigh">Price: high to low</option>
    </select></div>
  <h2 class="sec-h" id="allH">All Products</h2>
  <div class="grid" id="grid">{all_cards}</div>
  <p class="nomatch" id="nomatch">No products match — try another search.</p>
  {empty}
</main>
<p class="disc">#Ad · As an Amazon Associate I earn from qualifying purchases.</p>
<footer>{len(products)} products · {len(by_cat)} categories · updated live · SK · The Edit</footer>
<script>
 var grid=document.getElementById('grid'), q=document.getElementById('q'), sortSel=document.getElementById('sort'),
     count=document.getElementById('count'), nomatch=document.getElementById('nomatch'),
     chips=[].slice.call(document.querySelectorAll('.chip')), cards=[].slice.call(grid.querySelectorAll('.card'));
 var curCat='all';
 function apply(){{
   var term=(q.value||'').trim().toLowerCase(); var shown=0;
   cards.forEach(function(c){{
     var ok=(curCat==='all'||c.dataset.cat===curCat) && (!term||c.dataset.title.indexOf(term)>-1);
     c.style.display=ok?'':'none'; if(ok) shown++;
   }});
   count.textContent=shown+' product'+(shown===1?'':'s');
   nomatch.style.display=shown?'none':'block';
 }}
 function sortCards(){{
   var v=sortSel.value;
   cards.sort(function(a,b){{
     if(v==='disc') return (+b.dataset.disc)-(+a.dataset.disc);
     if(v==='rating') return (+b.dataset.rating)-(+a.dataset.rating);
     if(v==='plow') return (+a.dataset.price)-(+b.dataset.price);
     if(v==='phigh') return (+b.dataset.price)-(+a.dataset.price);
     return 0;
   }}).forEach(function(c){{grid.appendChild(c);}});
 }}
 chips.forEach(function(ch){{ch.onclick=function(){{chips.forEach(function(x){{x.classList.remove('on');}});ch.classList.add('on');curCat=ch.dataset.f;apply();}};}});
 q.addEventListener('input',apply); sortSel.addEventListener('change',sortCards);
 sortCards();
</script>
</body></html>"""
    return HTMLResponse(html)


# Serve the built React UI (frontend/dist). Mounted LAST so /api/* takes
# precedence. For live editing with hot-reload, run the Vite dev server instead:
#     cd frontend && npm install && npm run dev     → http://127.0.0.1:5173
# (Vite proxies /api and /docs to this backend.)
if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover
    @app.get("/")
    def _no_frontend() -> JSONResponse:
        return JSONResponse(
            {"error": "UI not built. Run:  cd frontend && npm install && npm run build",
             "dev": "Or for live editing:  cd frontend && npm run dev  (http://127.0.0.1:5173)",
             "api_docs": "/docs"},
            status_code=200,
        )
