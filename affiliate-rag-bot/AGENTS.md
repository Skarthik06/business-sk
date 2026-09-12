# AGENTS.md — Amazon → Instagram RAG Affiliate Engine (v4)

This repository is a **multi-agent affiliate-marketing engine** for Amazon **India**. It
scrapes Amazon **search results**, uses an LLM to select scroll-stopping products and write
content, generates affiliate links (tag `sparkle060b-21`), and (via the Instagram-automation
backend) posts **carousels** with a per-post comment→DM automation and a public storefront —
storing every success in pgvector RAG memory so the system improves on every run.

> **v4 note (current reality).** The pipeline began as a Pinterest poster; it is now an
> **API-driven Amazon→Instagram engine**. Product discovery + content live here
> (`server.py /api/generate`); **carousel posting, engagement, and hosting live in the IG
> backend** (`instagram_automation/`). The Pinterest-era agents (rows 1–8 below) describe the
> original graph and remain for reference; the **Discovery & content-strategy layer** (new
> table) is the governing constraint set for how products are retrieved-by-category, scored,
> collected, written, and posted. Where a v3 rule says "Pinterest", read "Instagram carousel".

This file is the master index and the home of the **global rules every agent must obey**.

> **v4 wiring status (implemented).** The discovery engine is now live code:
> `chains/discovery.py` computes the taxonomy, the 5 scores (value / purchase-intent /
> instagram / content-potential / master **content_score**), S–D tiers, price bands, and
> budget-true bundles — all from real scraped fields. It is wired into `server.py`
> (`/api/generate` returns scores + tier per item and ranks by content_score; `/api/taxonomy`
> and `/api/collections` expose the taxonomy + collections) and surfaced in the frontend
> (tier badge + score bars on product cards, Smart Collections in the Storefront). Many rules
> tagged ⏳ in the agent files below are therefore now ✓ — the tags mark original intent;
> `chains/discovery.py` is the source of truth for what is enforced.

---

## Agent roster

Each agent is specified in its own `agents/<name>.agents.md` file. They run in this
fixed order; edges may abort the run early (see the Orchestrator).

| # | Agent | File | Node | Responsibility |
|---|-------|------|------|----------------|
| 0 | Orchestrator | [orchestrator.agents.md](agents/orchestrator.agents.md) | *(graph + runner)* | Owns the DAG, browser session, run lifecycle, conditional aborts |
| 1 | Amazon Scraper | [amazon-scraper.agents.md](agents/amazon-scraper.agents.md) | `scrape_amazon` | Login + scrape Best Sellers into `raw_products` |
| 2 | Dedup Guard | [dedup-guard.agents.md](agents/dedup-guard.agents.md) | `check_duplicates` | Drop already-pinned ASINs (PostgreSQL ledger) |
| 3 | Trend Scout | [trend-scout.agents.md](agents/trend-scout.agents.md) | `search_trends` | Tavily real-time trending keywords (optional) |
| 4 | RAG Retriever | [rag-retriever.agents.md](agents/rag-retriever.agents.md) | `rag_retrieve` | pgvector few-shot context + product discovery |
| 5 | Pin Composer | [pin-composer.agents.md](agents/pin-composer.agents.md) | `compose_pins` | ONE structured LLM call: rank + write all pins |
| 6 | Affiliate Linker | [affiliate-linker.agents.md](agents/affiliate-linker.agents.md) | `get_affiliate_links` | SiteStripe affiliate link per ASIN |
| 7 | Pinterest Publisher | [pinterest-publisher.agents.md](agents/pinterest-publisher.agents.md) | `post_pinterest` | Publish pins, human-like, spaced out |
| 8 | Memory Writer | [memory-writer.agents.md](agents/memory-writer.agents.md) | `store_results` | Persist successes to pgvector + PostgreSQL (the flywheel) |

```
START → scrape_amazon → check_duplicates → search_trends → rag_retrieve
      → compose_pins → get_affiliate_links → post_pinterest → store_results → END
```

### Discovery & content-strategy layer (v4 — the retrieval + posting constraints)

These agents encode the product-discovery strategy as **enforceable constraints** over the
existing code. Each rule is tagged ✓ (already enforced) or ⏳ (pending code wiring) inside
its file, so the spec never overstates reality.

| Agent | File | Governs | Key code |
|-------|------|---------|----------|
| Product Scout | [product-scout.agents.md](agents/product-scout.agents.md) | Retrieval per category: taxonomy (families→subcategories→search intent), 5-question gate, quality filter, no-fabrication, multi-retailer model | `tools/amazon.py`, `server.py` |
| Product Scorer | [product-scorer.agents.md](agents/product-scorer.agents.md) | Content/Instagram/Purchase-Intent/Value/Content-Potential scores (0–100), S–D tiers, learning hook | `tools/amazon.py`, `config.py`, `chains/compose.py` |
| Collection Builder | [collection-builder.agents.md](agents/collection-builder.agents.md) | Price bands, bundles, **budget-claim truth**, website sections | `chains/compose.py`, `rag/posts.py` |
| Content Strategist | [content-strategist.agents.md](agents/content-strategist.agents.md) | Hooks, descriptions, CTAs, formats, claim safety | `chains/compose.py` |
| Carousel Publisher | [carousel-publisher.agents.md](agents/carousel-publisher.agents.md) | IG carousel selection/order, secure posting, comment→DM, storefront refresh | `instagram_automation/app/*` |
| Still Set Templates | [still-set-templates.agents.md](agents/still-set-templates.agents.md) | Designed-slide look: product-is-hero (big/clean), index frame, tint system, type, layout-by-count, no-fabrication | `instagram_automation/app/services/sk_render.py` |

### Autopilot layer (v5 — Phases 1–2 of the [Autopilot blueprint](docs/AUTOPILOT_BLUEPRINT.md))

Built as editable agents (spec + env-tunable constraints) so behaviour can be re-tuned as the
project runs. The 10-phase roadmap lives in `docs/AUTOPILOT_BLUEPRINT.md`; `docs/ENGINE_GUIDE.md`
explains the base engine.

| Agent | File | Governs | Key code |
|-------|------|---------|----------|
| Discovery Planner | [discovery-planner.agents.md](agents/discovery-planner.agents.md) | Multi-query subcategory mining, pagination, adaptive stopping, yield-based rotation | `tools/amazon.py`, `rag/discovery_stats.py`, `graph/nodes.py`, `config.DiscoveryConfig` |
| Novelty Analyst | [novelty-analyst.agents.md](agents/novelty-analyst.agents.md) | Semantic freshness vs already-posted pins (avoid repetitive ideas) | `rag/store.py` (`novelty_scores`), `graph/nodes.py` (`compose_pins`), `config.NoveltyConfig` |
| Winner Engine | [winner-engine.agents.md](agents/winner-engine.agents.md) | Confidence, product-intelligence, winner score + tier, "why this product" evidence | `chains/discovery.py`, `server.py` (`/api/generate`) |

Funnel this layer optimises: **Instagram → attention → website/storefront → discovery →
product → click → Amazon → purchase → commission.**

---

## Shared state (the contract between agents)

All agents read and write a single `BotState` (`graph/state.py`). An agent **reads**
the fields produced upstream and **returns a partial dict** of the fields it owns;
LangGraph merges the result. `errors` and `stream_log` are append-only (reduced with
`operator.add`). Never mutate another agent's fields.

Key fields: `category`, `products_per_run`, `raw_products`, `fresh_products`,
`duplicate_asins`, `trend_keywords`, `rag_context`, `rag_product_ideas`,
`ranked_products`, `generated_content`, `posted_pins`, `errors`, `stream_log`.

---

## GLOBAL RULES — every agent MUST follow these

These are non-negotiable. Individual agent files add their own rules on top.

### G1 — Never raise; accumulate errors
An agent MUST NOT let an exception escape. Wrap the body in `try/except`, log the
failure, and return the fields you own with the error appended to `errors`. A crash
in one agent must never take down the run without a recorded reason.

### G2 — Fail-open vs fail-closed (know which you are)
- **Fail-open** (continue on error) for *enrichment* agents whose absence only
  lowers quality: Dedup Guard, Trend Scout, RAG Retriever. On failure, return empty
  results and let the run proceed.
- **Fail-closed** (abort the run) for *correctness-critical* agents: an empty
  `raw_products`, empty `fresh_products`, or empty `generated_content` aborts to
  `END` via a conditional edge. Never post pins built on missing data.

### G3 — Compliance: affiliate disclosure is mandatory
Every published caption MUST carry an affiliate disclosure. Per the account owner, this is a
single **`#ad`** hashtag (auto-inserted if missing) — NOT the verbose "As an Amazon
Associate…" sentence, which is stripped from captions and shown on the storefront page
instead. The caption is always published WITH its hashtags appended (see
[[content-strategist]] CG6/CG7); the Publisher must not drop the hashtags or the `#ad`.

### G4 — Anti-shadowban discipline (platform safety)
- Respect `DELAY_BETWEEN_PINS` between posts (default 1200s / 20 min; **never below
  600s** — config warns below that). Only the last pin skips the wait.
- Use human-like typing cadence and randomized delays for all form input.
- Keep the browser stealth flags (`--disable-blink-features=AutomationControlled`,
  `navigator.webdriver` masked). Never post in a tight loop.

### G5 — Never re-pin the same product
An ASIN recorded in the SQLite `seen_products` ledger must never be posted again.
Dedup Guard filters before compose; Memory Writer records only **successful** posts.

### G6 — PostgreSQL + pgvector storage (single database)
Persistence lives in one PostgreSQL database (`DATABASE_URL`), which must have the
`vector` extension enabled. It holds BOTH the pgvector RAG store (pin memory, via
`langchain-postgres` PGVector, collection `affiliate_pins`) and the relational
`seen_products` dedup ledger. One connection string powers both. Keep credentials in
`.env` only (URL-encode special chars, e.g. `@` → `%40`); never commit or log them.

### G7 — Cold-start tolerance
On the first runs the RAG store is empty. Retrieval MUST return `[]` gracefully and
the Composer MUST fall back to its own expertise. Emptiness is normal, not an error.

### G8 — Secrets discipline
All credentials come from `.env` via `config.cfg` only. Never hardcode, never log,
and never echo secrets into `stream_log`, alerts, or the web API responses (the API
masks config and only reports booleans).

### G9 — Single run at a time
One browser, one Amazon account, one Pinterest account. Only one pipeline run may be
active at once (`server.RunManager` enforces this). Do not parallelize posting.

### G10 — Token & cost discipline (LLM agents)
Exactly **one** LLM call per run (Pin Composer). Send trends/RAG context once, use
compact pipe-delimited candidate rows, truncate titles, keep the system prompt
static (cache-friendly), and force a schema via structured output — no free-text
JSON parsing, no retries on malformed output.

### G11 — Resilient selectors
Browser agents use `utils.alerts.find_element` with an ordered list of fallback
selectors. When all fail, it logs a `[selector-miss]` warning that surfaces in the
run log and the JSON `errors` output, so a UI change is caught fast. Add fallbacks;
don't rely on a single brittle selector.

### G12 — Dry-run honors intent
When `dry_run` is true, agents that cause outward side effects (the Publisher) must
simulate success and post nothing. Read/enrichment agents behave normally.

### G13 — No fabrication, ever (data integrity)
Never invent price, discount, rating, reviews, specs, availability, images, affiliate URLs,
or trend claims. Preserve exactly what the source returned; if a field is unavailable, mark
it unavailable rather than guessing. Affiliate URLs are built ONLY from the real ASIN + the
configured tag. This binds every discovery/content/posting agent (see
[[product-scout]] S4, [[content-strategist]] CG5).

### G14 — Category-taxonomy retrieval (how products are extracted)
Retrieval is category-driven per the [[product-scout]] taxonomy: a request names a family or
base category, which expands to subcategory search intent; only products passing the
5-question gate + quality filter are returned. Ranking uses the [[product-scorer]] scores and
tiers — NOT commission and NOT raw bestseller rank. Commission may only influence ranking
after the five questions.

### G15 — Budget-claim truth (arithmetic honesty)
Any budget/collection claim ("Setup Under ₹3K", "5 Under ₹1K") must be arithmetically true:
sum the real prices of the selected products first; if they exceed the band, drop items or
relabel. Counts must match ("5 …" ⇒ 5 items). See [[collection-builder]] CB2/CB4.

### G16 — Quality over quantity
The goal is a high-quality product-discovery engine, not "a website full of Amazon links".
Prefer fewer, genuinely scroll-stopping, buy-worthy, content-rich products over a large,
padded catalog. Never feature a product merely because it is an Amazon bestseller.

### G17 — Two-layer uniqueness + embeddings (never repeat a product)
Every retrieval MUST be unique at two layers: within-scrape dedup (ASIN + base image id +
normalized title, `tools/amazon._dedup_products`) AND cross-run dedup against `seen_products`.
Every returned product is embedded into pgvector and marked seen (`store_results`), so no
product ever appears in two posts. Target the requested count (10 = IG carousel max) from a
deep scrape pool, but when the fresh unique pool is smaller, return fewer — NEVER repeat or
fabricate to pad the number. See [[dedup-guard]] R6–R8.

### G18 — Public catalog reflects ONLY posted products
The public storefront / GitHub-Pages link and `/api/collections` are built from
`post_store.all_products`, which returns ONLY `status='posted'` rows. Generated-but-unposted
and dry-run/failed products NEVER appear on the public link. The storefront is (re)published
only after a real (non-dry) publish. See [[carousel-publisher]] CP6.

---

## How to run (context for the Orchestrator)

```bash
# Web dashboard
python -m uvicorn server:app --reload      # → http://127.0.0.1:8000
# CLI
python main.py [--category fashion] [--dry-run]
```

Both entry points consume the same `pipeline_runner.run_pipeline`, which is the only
sanctioned way to execute the agent graph.
