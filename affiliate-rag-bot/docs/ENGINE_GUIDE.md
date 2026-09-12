# The Product-Fetching & Content Agent — Complete Guide

> **What this document is.** A single, accurate, code-grounded explanation of how the
> Business-SK affiliate engine finds *unique* Amazon products, scores them, writes the
> Instagram caption + hashtags, remembers what worked, and never repeats a product — plus a
> concrete **enhancement playbook**. Everything here is traced to real code (file + function),
> so you can read it to *understand* the system and hand it to an AI as a *prompt* to *upgrade* it.
>
> Repo: `affiliate-rag-bot/` · Master spec: [`AGENTS.md`](AGENTS.md) · Source of truth for
> scoring: [`chains/discovery.py`](chains/discovery.py)

---

## 0. The one-paragraph mental model

The engine is an **8-node [LangGraph](https://langchain-ai.github.io/langgraph/) pipeline** that
(1) **scrapes Amazon India search results** with a stealth Playwright browser, (2) **filters** them
through a quality gate + a two-layer uniqueness system, (3) **enriches** with trends + a pgvector
"what worked before" memory, (4) makes **exactly one LLM call** that both *picks* the best products
and *writes one carousel caption + hashtags*, (5) attaches **affiliate links**, and (6) **records**
every returned product into a Postgres dedup ledger and a pgvector RAG store so it *improves each run
and never repeats a product*. A separate deterministic **scoring engine** (`discovery.py`) ranks and
tiers every product from *real scraped numbers only* — no fabrication, ever.

The funnel it optimizes:
**Instagram → attention → storefront → product → click → Amazon → purchase → commission.**

---

## 1. File map — where everything lives

| Concern | File | What it does |
|---|---|---|
| **Master rules & spec** | [`AGENTS.md`](AGENTS.md) | The 18 global rules (G1–G18) every agent obeys; agent roster |
| **Agent specs** | `agents/*.agents.md` | Per-agent constraints (product-scout, product-scorer, content-strategist, collection-builder, …) |
| **Orchestration graph** | [`graph/workflow.py`](graph/workflow.py) | The 8-node DAG + conditional aborts |
| **Node implementations** | [`graph/nodes.py`](graph/nodes.py) | Each pipeline step's actual code |
| **Shared state contract** | [`graph/state.py`](graph/state.py) | `BotState` — the dict every node reads/writes |
| **Run launcher** | [`pipeline_runner.py`](pipeline_runner.py) | Boots Playwright, runs the graph two ways (stream / JSON) |
| **Amazon scraper + links** | [`tools/amazon.py`](tools/amazon.py) | Login, search scrape, quality gate, within-scrape dedup, affiliate URL |
| **Scoring & collections** | [`chains/discovery.py`](chains/discovery.py) | Taxonomy, 5 scores, master content_score, tiers, price bands, bundles |
| **The single LLM call** | [`chains/compose.py`](chains/compose.py) | Pick + write caption + hashtags (structured output) |
| **Trend keywords** | [`tools/search.py`](tools/search.py) | Tavily real-time keywords (optional, graceful fallback) |
| **RAG memory (flywheel)** | [`rag/store.py`](rag/store.py) | pgvector store of past pins + product-idea discovery |
| **Cross-run dedup ledger** | [`rag/dedup.py`](rag/dedup.py) | `seen_products` Postgres table (never re-post an ASIN) |
| **Posted-product catalog** | [`rag/posts.py`](rag/posts.py) | The storefront's source of truth (only `status='posted'`) |
| **HTTP API** | [`server.py`](server.py) | `/api/generate`, `/api/taxonomy`, `/api/collections`, `/api/posts`, … |
| **Config** | [`config.py`](config.py) | All env-driven knobs (typed) |

> **v4 reality:** discovery/content live *here* (`server.py /api/generate`). The actual Instagram
> carousel *posting*, comment→DM automation, and storefront hosting live in the **IG backend**
> (`instagram_automation/`). The Pinterest nodes remain in the graph but are skipped in the studio's
> `content_only` path — see §3.

---

## 2. How it's built & orchestrated (the agent architecture)

### 2.1 The 8-node graph

Defined in [`graph/workflow.py`](graph/workflow.py), run by [`pipeline_runner.py`](pipeline_runner.py):

```
START
  → scrape_amazon        Playwright: scrape Amazon SEARCH results into raw_products
  → check_duplicates     Postgres: drop already-posted ASINs (seen_products)
  → search_trends        Tavily: real-time trending keywords (optional)
  → rag_retrieve         pgvector: similar past pins + proven product types
  → compose_pins         ONE structured LLM call: PICK best + WRITE caption/hashtags
  → get_affiliate_links  Build the ?tag= affiliate URL per ASIN
  → post_pinterest       (skipped in studio/content_only mode)
  → store_results        pgvector + Postgres: record everything returned (the flywheel)
END
```

**Conditional aborts** (fail-closed on missing data): if `scrape_amazon` returns nothing → END;
if dedup removes *everything* → END; if the LLM produces no usable pins → END. The engine never
builds content on missing data.

### 2.2 The shared state contract

Every node reads upstream fields and returns a **partial dict** of the fields it owns; LangGraph
merges them (`graph/state.py`). `errors` and `stream_log` are **append-only** (reduced with
`operator.add`). Key fields: `category`, `products_per_run`, `raw_products`, `fresh_products`,
`duplicate_asins`, `trend_keywords`, `rag_context`, `rag_product_ideas`, `ranked_products`,
`generated_content`, `posted_pins`, `errors`, `stream_log`.

### 2.3 Two ways to run the same graph

Both live in [`pipeline_runner.py`](pipeline_runner.py) and drive the **same compiled graph**:

- **`run_pipeline(...)`** — async generator of UI events (`run_start`, `node`, `log`, `summary`).
  Used by `main.py` (Rich terminal) and the streaming dashboard.
- **`execute_pipeline(...)`** — runs to completion, returns **one JSON object**. Used by the API.
  With **`content_only=True`** it generates + records but **posts nothing and logs into nothing** —
  this is the path the studio's `/api/generate` uses.

### 2.4 The browser session (anti-detection)

`_new_browser()` launches stealth Chromium: `--disable-blink-features=AutomationControlled`,
`navigator.webdriver` masked, a real desktop user-agent, `en-IN` locale, 1366×768 viewport. Only
**one run at a time** is allowed (`_run_lock` in `server.py` / `RunManager`) — one browser, one
account.

---

## 3. The product-fetching system (the heart of "bring unique products")

All in [`tools/amazon.py`](tools/amazon.py) → `scrape_products()`.

### 3.1 Why *search results*, not Best Sellers

Amazon's Best Sellers grid renders **client-side** and serves a JS skeleton to headless browsers
(→ 0 products). **Search results are server-rendered** — `data-asin`, title, price, image, rating,
reviews, discount, "bought in past month", and badge are all in the initial HTML — so they scrape
reliably headless *and* headed. The scraper navigates to
`https://www.{marketplace}/s?k={query}` and reads the DOM.

### 3.2 What term it searches

- **Category mode:** each of 8 base categories maps to a search term via `CATEGORY_SEARCH`
  (`home → "home decor"`, `fashion → "fashion clothing"`, `electronics → "electronics gadgets"`, …).
  The richer per-category **subcategory intents** live in `discovery.SUBCATEGORIES` (e.g. fashion →
  `oversized t-shirt, hoodie, cargo pants, sneakers, watch, …`).
- **Keyword mode:** `/api/generate?q=air+fryer` passes a free-text `q` that **overrides** the
  category mapping for a single targeted run.

### 3.3 The extraction (`_SCRAPE_JS`)

A single in-page JS pass reads up to **60** `s-search-result` cards and pulls, per card:
`asin, title, price, orig_price (M.R.P strikethrough), discount_pct, rating, reviews,
bought_past_month, badge, sponsored, image, url`. Notable robustness:
- **Badge repair:** "Amazon's Choice" splits across two spans; the code canonicalizes from the
  card's full visible text (regex), then falls back to joined spans, and repairs a truncated
  `"Amazon's"` → `"Amazon's Choice"`.
- **Hi-res images (`_hi_res_image`):** Amazon encodes thumbnail size in the filename
  (`…71AbCd._AC_UL320_.jpg`). Stripping the `._…_` token yields the **full-resolution original**
  (`…71AbCd.jpg`) so carousels/storefront use crisp images, not tiny search thumbs.
- **Count parsing (`_parse_count`):** `"6K+" → 6000`, `"1.8K" → 1800`.

### 3.4 The quality gate (`_passes_quality`)

A product is kept only if it will **attract** a buyer. Defaults (from `config.py`, overridable
per-request):

| Check | Default | Env / override |
|---|---|---|
| Has a real image **and** a parseable price | required | — |
| Price within impulse range | `199 ≤ price ≤ 5000` | `QUALITY_PRICE_MIN` / `QUALITY_PRICE_MAX` or `price_min`/`price_max` |
| Rating (when present) | `≥ 3.8` | `QUALITY_MIN_RATING` or `min_rating` |
| Reviews (when present) | `≥ 50` | `QUALITY_MIN_REVIEWS` or `min_reviews` |

If a strict filter empties the category, a **relaxed fallback** keeps anything with an image + price
so a category is never returned empty.

### 3.5 Attractiveness ranking (`_attractiveness`)

Survivors are sorted by a composite *pull* score (deterministic, pre-AI):

```
score = rating*20 + log10(reviews+1)*10 + discount_pct*0.4
      + log10(bought_past_month+1)*8 + (6 if badge else 0)
```

### 3.6 Layer-1 uniqueness — within-scrape dedup (`_dedup_products`)

Amazon lists the *same* product under many ASINs (colours/sizes, sponsored + organic) that share the
same photo and title. The scraper collapses them by keying on **three** signals — any collision
drops the item (best-first order preserved, so the strongest of a group survives):
1. **exact ASIN**
2. **base image id** — the Amazon image URL before `._` (identical for the same photo)
3. **normalized title prefix** — lowercased, alphanumerics only, first 40 chars

**Output:** top **40** unique quality-ranked products (a deep pool so a full 10 survive the *next*
dedup layer).

### 3.7 Layer-2 uniqueness — cross-run dedup (`check_duplicates` → `rag/dedup.py`)

The 40 are filtered against the **`seen_products`** Postgres table (indexed PK lookup on ASIN). Any
ASIN ever posted is removed. This is a relational table (not vector) because dedup is an exact match,
which is O(log n) and far cheaper than similarity search. **Fail-open:** if dedup errors, the run
continues with all products.

> **G17 — the uniqueness guarantee:** every product is unique at *both* layers, every returned
> product is embedded into pgvector and marked seen, so **no product ever appears in two posts**.
> Target the requested count (10 = IG carousel max) from the deep pool, but when the fresh unique
> pool is smaller, **return fewer — never repeat or fabricate to pad the number.**

---

## 4. The scoring engine (how "best/unique" is decided) — `chains/discovery.py`

**100% deterministic, derived only from real scraped fields.** A score is an internal ranking
heuristic, *never a claim shown to a buyer*. Five sub-scores (all 0–100):

| Score | Function | Built from |
|---|---|---|
| **value_score** | `value_score()` | rating (≤40) + log-scaled reviews (≤20) + real discount (≤25) + price accessibility (≤15) |
| **purchase_intent_score** | `purchase_intent_score()` | rating (≤30) + reviews (≤25) + "bought past month" (≤20) + price (≤15) + badge (+10) |
| **instagram_score** | `instagram_score()` | category **visual prior** `VISUAL` (≤35) + has-image (+15) + discount appeal (≤20) + price (≤15) + badge (+15) |
| **content_potential_score** | `content_potential_score()` | # of content **angles** for the category (≤70) + affordable (+15) + giftable family (+15) |
| **content_score** (master) | `content_score()` | weighted blend ↓ |

**Master weights** (`WEIGHTS`, tunable via env):

```
instagram 0.30 · purchase_intent 0.20 · value 0.15
content_potential 0.15 · quality 0.10 · commercial 0.10
```

`quality` = rating+reviews blend; `commercial` = Amazon India commission rate for the category
(fashion 9% is the ceiling → normalized to 100). **Commission only nudges ranking (10% weight) — it
never dominates** (rule G14: never feature a product just because it's a bestseller or high-commission).

**Tiers** (`tier()`): `S ≥ 90 · A ≥ 80 · B ≥ 70 · C ≥ 60 · D < 60`.
**Price bands** (`price_band()`): Under ₹500 / ₹1K / ₹1.5K / ₹2K / ₹3K / ₹5K / ₹5K+.

`score_product(p)` returns the whole bundle (`{value_score, purchase_intent_score, instagram_score,
content_potential_score, content_score, tier, price_band, family, retailer, product_type}`) and is
attached to every item by `server._content_item()` and re-applied to storefront products in
`/api/collections`. `/api/generate` **re-ranks by `content_score` descending**, so the strongest
picks lead.

### 4.1 Taxonomy (how retrieval is category-driven)

`discovery.py` holds the maps that power `/api/taxonomy` and drive content angles:
- `FAMILY` — 8 base categories → families (`home → room_desk`, `books → student`, …)
- `SUBCATEGORIES` — real Amazon India search intents per category
- `ANGLES` — grounded Instagram content angles per category (e.g. *"Tech finds under ₹1K"*,
  *"Make your desk look better under ₹2K"*) — the **count** of angles feeds content_potential.
- `VISUAL` — per-category visual-appeal prior (fashion 0.95 … books 0.45), honest proxy for
  scroll-stopping potential.

### 4.2 Collections & bundles (truthful "Under ₹X" / "Setup" sections)

- `build_price_bands()` groups **posted** products into price bands (real prices only).
- `build_bundles(products, budget, size=3)` picks the highest-scored products from **distinct
  categories** whose **combined real price ≤ budget** (₹2K/₹3K/₹5K). This enforces **G15 budget
  truth** — a "Setup under ₹3K" claim is arithmetically real; counts must match.

---

## 5. The RAG flywheel (why it gets better each run) — `rag/store.py`

- **Embeddings:** `all-MiniLM-L6-v2` via HuggingFace — **free, local, CPU, no API key** (≈80 MB
  first-run download). Normalized vectors, cosine distance.
- **Store:** LangChain `PGVector` over the shared Postgres DB, collection `affiliate_pins`, metadata
  as JSONB (filterable).
- **Two retrievals per run** (`rag_retrieve`, run concurrently):
  1. **Content context** (`retrieve_similar_pins`) — past pins similar to the current products, used
     as **few-shot style examples** for the writer.
  2. **Product discovery** (`discover_product_ideas`) — product *types* that performed well in this
     category, injected to bias picks toward **proven winners**.
- **Write** (`store_results` → `store_pin`) — every returned product's pin is embedded (a rich
  `Category / Product / Pin Title / Description / Tags` summary) and its ASIN is marked seen.

**G7 cold-start tolerance:** on the first runs the store is empty; retrieval returns `[]` gracefully
and the writer falls back to its own expertise. Emptiness is normal, not an error.

---

## 6. Caption & hashtag algorithm (the copywriting) — `chains/compose.py`

**Exactly ONE LLM call per run** (G10 token discipline). It both **picks** and **writes**, returning
one schema-validated object via LangChain `with_structured_output(PinBatch)` — no fragile text
parsing, no retries.

### 6.1 The output schema (`PinBatch`)

```python
picks:    list[int]   # 0-based indices of chosen products, best first, exactly `count`
caption:  str         # ONE caption for the WHOLE carousel (not per product), < 1500 chars
hashtags: list[str]   # 15-25 tags (no '#'), broad + niche, must include 'ad'
```

> **Key design choice:** ONE universal caption + hashtag set is shared across every product in the
> carousel (not one per product) — this is what keeps it to a single cheap call.

### 6.2 How it picks (the system prompt)

Rank by: strong social proof (rating, reviews, recent demand, real discount, badge) · scroll-stopping
visual appeal · commission rate (Fashion 9% > Home 8% > Kitchen 7% > Beauty 6% > Electronics 2–5%) ·
the ₹200–5000 impulse range · similarity to the **proven winners** from RAG.

### 6.3 How it writes (the caption structure)

Enforced by the prompt, skimmable, emoji-led:

```
Line 1: 🍔👕 emoji-led HOOK about the category (e.g. "Graphic Tees under ₹1K!")
Line 2: 🔥 Top picks you can't miss:
        - <Brand or short name> — ₹<price> (<X>% off)     ← one line per product, up to 5
        - …                                                  (lead with the BRAND name)
        one short line on why they're worth it
🛒 Shop all via the link in bio 👆                          ← CTA
```

**Grounding rule (G13):** the model may only use the **exact numbers from the candidate rows** —
never invent a price, discount or rating.

### 6.4 Token discipline (the input side)

- Candidates sent as **compact pipe rows**: `id|title(≤70 chars)|price|social-proof` (rating★,
  reviews, % off, bought/mo, badge) — `_candidates_block`.
- Trends + RAG winners sent **once** (`_winners_block`), not per product.
- System prompt is **fully static** (cache-friendly prefix).
- Caps: `MAX_CANDIDATES=25`, `TITLE_CHARS=70`, `MAX_TRENDS=6`, `MAX_EXAMPLES=2`, `MAX_IDEAS=5`.
- Model config from `.env`: `OPENAI_MODEL` (default `gpt-5-nano`). Reasoning models (gpt-5/o-series)
  use `reasoning_effort` (default `minimal`) + full token ceiling and **omit temperature**;
  non-reasoning models scale tokens with count and use a **low temperature (0.2)** to curb
  hallucination. (`config.is_reasoning_model` switches behaviour.)

### 6.5 Post-processing the caption

1. **Strip disclosure sentences** — any "As an Amazon Associate…" the model adds is removed (per the
   owner's choice; see §7).
2. **Bold the money bits (`_bold_caption`)** — Instagram has no markdown, so brand names on list
   lines, **prices**, and **% off** are converted to Unicode sans-serif bold glyphs (`𝗔–𝗭`, `𝟬–𝟵`)
   so the deal pops.
3. **Hashtags** — lowercased, `#` stripped, capped at 25; **`ad` force-inserted** if missing.

---

## 7. Affiliate links & compliance

- **Link building** (`tools/amazon.get_affiliate_link`), priority order:
  1. **Network template** (`AFFILIATE_LINK_TEMPLATE`) if set — EarnKaro/Cuelinks/INRDeals redirect
     with `{url}`/`{url_encoded}`/`{asin}`/`{tag}` placeholders.
  2. **Official Amazon deep link** (default): `https://www.{marketplace}/dp/{ASIN}?tag={tag}` — no
     login, no browser, tracks correctly. Tag: `sparkle060b-21` / `AMAZON_ASSOCIATE_TAG`.
  3. **Legacy SiteStripe** (only if `AMAZON_LINK_METHOD=sitestripe`) — scrapes a short URL from a
     logged-in session.
  - The URL is built **only from the real ASIN + configured tag** (G13) — never invented.
- **Disclosure (G3):** every published caption carries a single **`#ad`** hashtag (auto-inserted).
  The verbose "As an Amazon Associate…" sentence is **stripped from captions** and shown on the
  **storefront page** instead.
- **G18 — the public catalog reflects ONLY posted products:** the storefront and `/api/collections`
  read `post_store.all_products()`, which returns only `status='posted'` rows. Generated-but-unposted
  and dry-run/failed products never appear publicly.

---

## 8. The API surface (`server.py`)

| Endpoint | Purpose |
|---|---|
| `GET /api/generate` | **The main content service.** Scrape + score + write, return JSON, record everything. Params: `categories`/`category` (multi, up to 8), `q` (keyword mode), `products_per_run` (1–25), `marketplace`, `min_rating`/`min_reviews`/`price_min`/`price_max`. Returns `items[]` (each with full scores + tier), shared `caption` + `hashtags`, `tiers` histogram. |
| `GET /api/taxonomy` | Families → subcategories → angles, price bands, tiers, weights (documents the retrieval strategy). |
| `GET /api/collections` | Truthful price-band collections + budget bundles + top picks, from posted products only. |
| `POST /api/posts` / `GET /api/posts` | Record / list published carousels (history + uniqueness). |
| `GET /api/health`, `/api/config`, `/api/pipeline`, `/api/categories`, `/api/stats`, `/api/history` | Ops/UI metadata (config is masked — booleans only, secrets never leaked, G8). |

Guards: one run at a time (`409 busy`), category/marketplace validation (`422`),
`MAX_PRODUCTS_PER_RUN=25`, `MAX_CATEGORIES=8`, `ALLOWED_MARKETPLACES` (8 Amazon domains).

---

## 9. The 18 global rules (the guardrails you must not break)

From [`AGENTS.md`](AGENTS.md) — enforce these in any enhancement:

- **G1** never raise; accumulate errors · **G2** fail-open (enrichment) vs fail-closed (scrape/dedup/compose)
- **G3** `#ad` disclosure mandatory · **G4** anti-shadowban delays (≥600s between posts) · **G5** never re-post an ASIN
- **G6** one Postgres+pgvector DB for both stores · **G7** cold-start tolerance · **G8** secrets only in `.env`, masked in API
- **G9** single run at a time · **G10** one LLM call per run · **G11** resilient selectors (`utils.alerts.find_element`)
- **G12** dry-run posts nothing · **G13** **no fabrication, ever** · **G14** category-taxonomy retrieval, not commission-first
- **G15** budget-claim arithmetic truth · **G16** quality over quantity · **G17** two-layer uniqueness + embeddings
- **G18** public catalog = posted products only

---

## 10. Configuration knobs (`config.py` / `.env`)

| Area | Env var | Default |
|---|---|---|
| Affiliate tag | `AMAZON_ASSOCIATE_TAG` | `sparkle060b-21` |
| Link method | `AMAZON_LINK_METHOD` | `deeplink` (or `sitestripe`) |
| Marketplace | `AMAZON_MARKETPLACE` | `amazon.in` |
| Items per run | `PRODUCTS_PER_RUN` | `3` (UI usually asks 10) |
| Quality gate | `QUALITY_MIN_RATING` / `QUALITY_MIN_REVIEWS` / `QUALITY_PRICE_MIN` / `QUALITY_PRICE_MAX` | `3.8` / `50` / `199` / `5000` |
| LLM | `OPENAI_MODEL` / `LLM_REASONING_EFFORT` / `LLM_TEMPERATURE` / `LLM_MAX_OUTPUT_TOKENS` | `gpt-5-nano` / `minimal` / `0.2` / `5000` |
| Embeddings | `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` |
| Trends | `TAVILY_API_KEY` | optional (falls back to static keywords) |
| Anti-shadowban | `DELAY_BETWEEN_PINS` | `1200`s (warns below 600) |
| DB | `DATABASE_URL` | required (Supabase in cloud) |

---

## 11. Enhancement playbook (where to make it stronger — and how, safely)

Each item names the **exact file/function** to touch and the **rule to respect**.

### A. Bring more *unique* / novel products
1. **Rotate subcategory intents.** Today category mode uses one term from `CATEGORY_SEARCH`. Instead
   iterate `discovery.SUBCATEGORIES[category]` (and/or combine with `trend_keywords`) across runs, so
   each run mines a *different* slice → far more unique ASINs before dedup. *Touch:* `graph/nodes.scrape_amazon`
   (loop over terms, merge + `_dedup_products`), keep G17.
2. **Deepen the pool.** Raise the scrape cap (currently 60 cards → top 40) and paginate search
   (`&page=2`) so the fresh-unique pool survives seen-filtering even for heavily-posted categories.
   *Touch:* `_SCRAPE_JS` slice + `scrape_products`.
3. **"Novelty" score.** Add a sub-score that *rewards* low similarity to already-posted pins
   (query pgvector for nearest posted pin; higher distance = more novel). Blend into `content_score`
   via a new `WEIGHTS` key. *Touch:* `discovery.py` + `rag/store.py`. Respect G13 (novelty is
   internal, not a buyer claim).

### B. Upgrade the retrieval system
4. **Real visual scoring.** `instagram_score` uses a category prior (`VISUAL`). Add optional image
   analysis (aesthetic/CLIP score of the product photo) to make it product-specific. *Touch:*
   `discovery.instagram_score` (keep the prior as fallback → G7).
5. **Multi-retailer.** `retailer` is hard-coded `amazon_in`. Abstract the scraper behind a retailer
   interface (Flipkart/Meesho) and merge pools before dedup. *Touch:* `tools/amazon.py` → `tools/<retailer>.py`.
6. **Better RAG queries.** `discover_product_ideas` uses a fixed query string; enrich it with the
   season/trend + tier of recent winners for sharper "what worked" recall. *Touch:* `rag/store.py`.

### C. Upgrade the agent / content
7. **Per-product micro-captions** (optional second structured field) for the storefront while keeping
   the single carousel caption — still one LLM call (extend `PinBatch`). *Touch:* `chains/compose.py`.
   Mind G10 (stay at one call) + token budget.
8. **A/B caption styles.** Add a `style` param (e.g. "listicle" vs "story") that swaps the `SYSTEM`
   template; record which style's pins performed and feed back via RAG. *Touch:* `compose.py` + `rag`.
9. **Smarter hashtags.** Today the model free-writes 15–25 tags. Add a curated per-category tag bank
   (broad + niche + branded) and have the model *select + extend* from it for reach consistency.
   *Touch:* `compose.py` (`_winners_block`-style injection) or post-process `tags`.

### D. Scoring transparency
10. **Expose the score breakdown in the UI** (you already return all five sub-scores per item from
    `/api/generate`). A small "why this tier" panel builds trust and helps you tune `WEIGHTS`.
    *Touch:* frontend only — no backend change.

**Golden rules for any change:** never fabricate a product fact (G13); keep the two-layer uniqueness
(G17); keep it to one LLM call (G10) unless you consciously change the cost model; keep secrets in
`.env` (G8); and remember the **public storefront only shows posted products** (G18).

---

## 12. Quick reference — one run, start to finish

1. `GET /api/generate?categories=fashion&products_per_run=10`
2. `scrape_amazon` → search "fashion clothing" → 60 cards → quality gate → attractiveness sort →
   within-scrape dedup → **top 40 unique**.
3. `check_duplicates` → drop ASINs already in `seen_products` → **fresh pool**.
4. `search_trends` (Tavily, optional) + `rag_retrieve` (similar pins + proven types).
5. `compose_pins` → **one LLM call** → pick best 10 + write one caption + hashtags (bolded, `#ad`).
6. `get_affiliate_links` → `?tag=sparkle060b-21` per ASIN.
7. `store_results` → embed pins to pgvector + mark ASINs seen (**flywheel + uniqueness**).
8. API returns `items[]` (each with scores + tier), shared `caption`/`hashtags`, `tiers` histogram —
   ranked by `content_score`. The IG backend then renders + posts the carousel and refreshes the
   storefront.

---

*Generated as a living reference for understanding and enhancing the affiliate engine. Source of
truth is always the code — if this doc and the code disagree, the code wins; update this doc.*
