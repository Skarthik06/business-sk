# BUSINESS_SK — Build Audit

_Last updated: 2026-09-01_

A running log of what has been built across the consolidated `BUSINESS_SK` workspace,
which houses two businesses that share one Instagram-automation frontend, one Postgres
(pgvector) database, and one Docker stack:

- **Business-JK** — the existing real-estate Instagram platform (unchanged).
- **Business-SK** — the affiliate (Amazon Associates) business, added on top.

Everything below is production code (no dummy/smoke paths). Secrets are encrypted at rest.

---

## 1. Workspace & infrastructure

| Item | State |
|------|-------|
| `BUSINESS_SK/` root | Both projects moved in (`instagram_automation`, `affiliate-rag-bot`) |
| Database | PostgreSQL 18 + pgvector 0.8.6, single server, DB `affiliate_rag_bot` (NOT the docuextract DB) |
| Docker | `docker-compose.yml`, project `business_sk`, 4 services (below) |
| Data safety | Named volume `pgdata` at `/var/lib/postgresql`; compose header warns: never `down -v` |
| Secrets | `DATABASE_URL`, GitHub token, IG tokens encrypted via Fernet (`rags/.ragskey`) — never in plaintext, never logged |

**Containers (all healthy):**
- `business_sk_db` — Postgres+pgvector (`:5433→5432`)
- `business_sk_ig_backend` — real-estate + shared API (`:8000`)
- `business_sk_ig_frontend` — shared Vite/React UI with HMR (`:3000`)
- `business_sk_affiliate_backend` — affiliate content API (`:8100`)

---

## 2. Affiliate content engine (`affiliate-rag-bot`)

- **Model:** `gpt-5-nano` (reasoning model) — `reasoning_effort=minimal`, `max_completion_tokens`,
  no temperature (temperature only applies to non-reasoning models; low-temp requested but N/A here).
- **Single optimised LLM call** per product set (`chains/compose.py`), grounded on scraped
  social-proof rows to curb hallucination.
- **Scraping (`tools/amazon.py`):** Amazon **search results** (`/s?k=`) — server-rendered and
  reliable (Best Sellers are JS skeletons/blocked). Extracts rating, reviews, discount, MRP
  (via `M.R.P: ₹…` regex, not the per-unit selector), "bought in past month", badges.
  Quality gate `_passes_quality` + `_attractiveness` ranking.
- **Affiliate links:** deep-link (`?tag=`) default, network-agnostic template.
  **Associate tag = `sparkle060b-21`** (the user's real Amazon StoreID; corrected 2026-09-01
  from the placeholder `karu8749-21`). Set via `affiliate-rag-bot/.env` → `AMAZON_ASSOCIATE_TAG`;
  the container was recreated so the env file reloads. All links/DMs/hub now credit this tag.
- **Storage migration:** ChromaDB + SQLite → **PostgreSQL + pgvector** (`langchain-postgres`
  `PGVector`, SQLAlchemy ORM). Concurrent table creation race fixed with a double-checked `RLock`.
- **RAG dedup flywheel:** `seen_products` + pgvector embeddings so previously generated
  products are not repeated (named-value vector embeddings).
- **Dependencies modernised** to the LangChain 0.3 line + pydantic ≥2.9 for Python 3.13 wheels;
  `requirements.txt` kept easy to extract.

### API service (`server.py`, `:8100`)
- JSON-in / JSON-out. Key endpoints:
  - `GET /api/generate` — params: `categories`, `category`, `q` (keyword), `products_per_run`,
    `marketplace` (validated set), `min_rating`, `min_reviews`, `price_min`, `price_max`.
  - `POST /api/posts` / `GET /api/posts` — record + list posts, labelled `post_<N>#<category>`.
  - `GET /api/hub` (JSON) + `GET /hub` (styled mobile HTML) — the **link hub**: one page of
    all posted products carrying your affiliate tag, with FTC disclosure.
- **Removed** all direct IG-token posting from the affiliate service (posting now goes through
  the secure rags account dropdown on the IG backend — see §4).

### Post store (`rag/posts.py`, table `sk_posts`)
- Columns incl. `products` (full JSON), idempotent migration
  (`ALTER TABLE … ADD COLUMN IF NOT EXISTS products …`).
- `all_products(category=None)` — deduped products across posts → powers the hub.

---

## 3. Frontend — Business-SK panel (`instagram_automation/frontend`)

Sidebar is two collapsible dropdown groups + a Universal section (`src/App.jsx`):

- **Business-JK** — all real-estate panels + Admin (unchanged).
- **Business-SK** — 5 separate sidebar sub-items:
  1. **Affiliate** (generate) — keyword/category, quality sliders, export batch, favorites.
  2. **Post to IG** — account dropdown + multi-category carousel posting, per-category progress,
     ≤5 products/carousel, labelled `post_N#category`.
  3. **Storefront** (`HubTab`) — the Amazon link-hub: product count, category filter, "Open ↗"
     + "Copy URL", product cards, public-hosting note.
  4. **History** — posting history from `sk_posts`.
  5. **Engagement** — renders the **same** Business-JK Engagement component (per user request).
- **Universal (outside both businesses):** Accounts, Settings, Personal — tokens/keys live here.
- Client `src/services/skApi.js` (→ `/sk-api` Vite proxy → affiliate `:8100`) and
  `src/services/api.js` `skCarousel(...)` (→ IG backend `:8000`).

---

## 4. Secure posting (no .env token)

- Posting uses the **rags-encrypted IG accounts** chosen from a dropdown — exactly like
  Business-JK. No IG token in the affiliate `.env` (an earlier `.env` approach was reverted
  at the user's instruction).
- `POST /api/sk/carousel` (IG backend) publishes via `rags.get_account(id, with_secret=True)`
  + the existing multi-account `publish()` (carousel if >1 image). Token stays server-side.

---

## 5. Comment → DM engagement loop (affiliate) — isolated from real-estate

**Goal:** a comment on an affiliate carousel auto-replies publicly and DMs the buyer the
actual Amazon affiliate links for *that* post's products — without clashing with the
real-estate (JK) engagement on the same account.

**How isolation is guaranteed (no clash):** the rule engine (`rules.evaluate`) makes
**post-specific rules suppress account-wide rules**. Each affiliate post gets its own
post-specific rule, so real-estate account-wide rules never fire on affiliate posts, and the
affiliate rule never fires on real-estate posts. Affiliate posts also live in their own
`eng_posts` lane (`source='affiliate'`), separate from the real-estate flow which reads
`bstore.list_published_posts()`.

**Changes:**
- `engagement/store.py` — `eng_posts` gains `source`, `category`, `products` (idempotent
  ALTERs); helpers `register_affiliate_post`, `list_affiliate_posts`, `affiliate_rule_for_post`.
- `engagement/api.py`:
  - `_affiliate_dm_text(category, products)` — grounded DM listing each product's title, price,
    and real `amazon.in/dp/…?tag=` link + FTC disclosure.
  - `ensure_affiliate_automation(...)` — registers the post and attaches/refreshes a
    post-specific `COMMENT_RECEIVED` rule: **REPLY_TO_COMMENT** ("Sent to your DM 📩") +
    **SEND_DM** (the grounded links) + **MARK_LEAD**. Idempotent per post.
  - `run_account_sync(...)` now also syncs affiliate posts, so the background poller (and the
    webhook path) pick up affiliate comments automatically.
- `app/api.py` — `POST /api/sk/carousel` accepts `category` + `products` and calls
  `ensure_affiliate_automation` after a successful publish (a failed automation never fails a
  good post).
- Frontend — `api.skCarousel(account, images, caption, { category, products })`; `PostTab`
  passes the generated products so each post's DM is grounded on its own products.

**Verified (deterministic, no Meta calls):**
- Register affiliate post → post-specific rule created; simulated comment → 1 rule matched →
  public reply + DM with real product links + lead. `{{username}}` rendered.
- Non-clash: comment on affiliate post fires ONLY the affiliate rule (account-wide JK rules
  suppressed); comment on a JK post fires the account-wide rules and NOT the affiliate rule.
- Test rows cleaned up afterward.

Affiliate engagement is visible in the shared Engagement panel's **Activity / Conversations /
Leads / Comments** tabs (all account-scoped). The Posts tab (campaign-scoped) is deliberately
left untouched to avoid affiliate posts leaking into the real-estate view.

---

## 6. How the money works (for reference)

Buyer taps the clickable affiliate link (DM, Story sticker, or bio link-hub) → buys on Amazon
→ you earn the Associates commission. Captions are **not** clickable, which is why the
comment→DM loop and the bio hub matter.

**Amazon Associates signup (Instagram qualifies; Pinterest does not):** apply at
`affiliate-program.amazon.in`, list your Instagram professional account as the platform,
complete profile + payment/tax, get your tag, then make qualifying sales in the trial window.

---

## 7. Public Storefront (GitHub Pages) — the "all products in one Amazon-tagged link"

Amazon provides **no API** to auto-add products to a native Amazon list/storefront, so the
auto-updating "one link with all my products" is served as our own page where **every card
links to Amazon with your tag**. It's published to **GitHub Pages** (free, public), reusing the
same repo + token as the real-estate image hosting.

- **Live URL:** `https://skarthik06.github.io/instagram_automation/storefront/`
  (repo `skarthik06/instagram_automation`, Pages already enabled, path `storefront/index.html`).
- **Backend (IG `app/api.py`):** `POST /api/sk/storefront/publish` renders the affiliate `/hub`
  HTML and pushes it via the GitHub Contents API (only that one file is staged — no secrets);
  `GET /api/sk/storefront/url` returns the public URL + repo status.
- **Frontend Storefront tab:** a **Publish / refresh** button + the copyable public link (paste
  into IG bio). The link also **auto-refreshes at the end of every real posting run**, so it
  stays current "as the project finds products."
- Verified: pushed, GitHub Pages build `built`, URL returns HTTP 200.

## 8. Housekeeping done

- Corrected the associate tag to `sparkle060b-21` (see §2).
- Removed the unused placeholder `EngagementTab` (+ `LS_ENG`) from `BusinessSK.jsx` — SK
  engagement routes to the shared JK `Engagement` panel; the placeholder was dead code.

## 9. Business-SK UI redesign (professional flow)

- **State persists across panel switches:** `BusinessSK` is now always-mounted in `App.jsx`
  (hidden when inactive) and all its tabs render with a display toggle — an in-progress
  find/queue survives switching sidebar panels. The prepared batch is also saved to
  `localStorage` (`sk_queue`), so it survives a full reload.
- **Affiliate tab = find products only:** professional **checkbox category cards**, each with a
  **per-category product-count stepper** (choose how many products per category), optional
  keyword + quality filters, per-category find progress, grouped results, and a **sticky
  "Send to Post to IG →"** action bar.
- **Post to IG tab = publish only:** no category setup — it shows the **queued batch** (thumbnails
  per category) handed over from Affiliate, plus **account selection** and a clear **Dry run /
  Live** toggle, then publishes. Consumes the queue and refreshes the storefront on a live run.
- Cleaner visual system: numbered step headers, card-based selectors, queue rows, live/dry
  pill, larger primary buttons. Verified: bundle compiles, no console errors.

## 10. Product-discovery strategy as enforceable agents (`*.agents.md`)

The full product-discovery strategy is encoded as **constraint files** extending the existing
`affiliate-rag-bot/agents/` system (not a rebuild). `AGENTS.md` bumped to **v4** (Amazon→
Instagram reality) with 4 new binding global rules — **G13** no-fabrication, **G14**
category-taxonomy retrieval, **G15** budget-claim truth, **G16** quality-over-quantity — plus
5 new agent specs, each rule tagged ✓ enforced / ⏳ pending-wiring against real code anchors:

- `agents/product-scout.agents.md` — retrieval per category: family→subcategory→search-intent
  taxonomy, the 5-question scroll-stop gate, quality filter, multi-retailer product model.
- `agents/product-scorer.agents.md` — Content / Instagram / Purchase-Intent / Value /
  Content-Potential scores (0–100), S–D tiers, learning hook (all weights config-driven).
- `agents/collection-builder.agents.md` — price bands, bundle engine, **budget-claim truth**.
- `agents/content-strategist.agents.md` — hooks, descriptions, CTAs, formats, claim safety.
- `agents/carousel-publisher.agents.md` — IG carousel selection/order, secure posting,
  comment→DM attach, storefront refresh.

These are the governing spec; the discovery engine below now implements the core of it.

### 10a. Discovery engine — implemented & wired (real data, no fabrication)

- **`affiliate-rag-bot/chains/discovery.py`** (new) — deterministic engine derived ONLY from
  real scraped fields: category **taxonomy** (families → subcategories → content angles),
  the **5 scores** (value, purchase-intent, instagram [heuristic proxy], content-potential,
  and the weighted master **content_score**), **S–D tiers**, **price bands**, and
  **budget-true bundles** (combined price ≤ claimed budget, rule G15).
- **`server.py`** — `/api/generate` now attaches the score bundle to every item and **ranks
  by content_score** (S→D), returning a tier histogram; new **`/api/taxonomy`** (families/
  subcategories/angles/bands/weights) and **`/api/collections`** (price-band collections +
  bundles from posted products).
- **Frontend** — product cards show a **tier badge (S/A/B/C/D · score)**, a **price-band tag**,
  and **4 score bars** (IG / Buy / Val / Cnt); the Storefront shows **Smart Collections**
  (auto price bands + budget-true bundles). `skApi.taxonomy()` / `skApi.collections()`.
- **Docker fix (per user):** the affiliate backend now **bind-mounts its source + runs
  `--reload`** (`docker-compose.yml`), so the container always runs current host code — the
  same live-edit setup as the IG backend (previously code was baked into the image).
- **Validated live:** real scrape of 3 fashion products in 27s returned tiers S/S/B with all
  sub-scores + bands; `/api/taxonomy` and `/api/collections` return correctly; all 4
  containers healthy; frontend compiles with no console errors.

## 11. Endpoint ↔ frontend coverage (validated)

Every user-facing endpoint is wired and returns 200 (SK storefront endpoints are admin-gated
→ 401 without the browser token, which is correct).

| Endpoint | Frontend | Where |
|----------|----------|-------|
| `GET /api/health` | `skApi.health` | Affiliate header (API live / running) |
| `GET /api/config` | `skApi.config` | header (model, tag, ready) |
| `GET /api/categories` | `skApi.categories` | category picker |
| `GET /api/stats` | `skApi.stats` | header ("N remembered" — RAG dedup flywheel) |
| `GET /api/generate` | `skApi.generate` | Affiliate (find), Post (queue) |
| `GET /api/taxonomy` | `skApi.taxonomy` | Affiliate subcategory refine |
| `GET /api/collections` | `skApi.collections` | Storefront Smart Collections |
| `POST /api/posts` | `skApi.recordPost` | Post to IG |
| `GET /api/posts` | `skApi.posts` | History |
| `GET /api/hub` · `GET /hub` | `skApi.hub` + link | Storefront |
| `POST /api/sk/carousel` | `api.skCarousel` | Post to IG |
| `GET /api/sk/storefront/url` | `api.skStorefrontUrl` | Storefront |
| `POST /api/sk/storefront/publish` | `api.skPublishStorefront` | Storefront + after posting |

**Intentionally not exposed (legacy/internal, superseded):** `POST /api/run` (old Pinterest
LangGraph posting — replaced by `/api/sk/carousel`), `GET /api/pipeline` (old node viz),
`GET /api/history` (dedup ledger log — post history is `/api/posts`), `GET /` (SPA fallback).

## 12. How the AI is used (one optimised LLM call, no noise)

**Retrieval is NOT the LLM.** Product discovery is deterministic: Playwright scrapes Amazon
search results → `_passes_quality` gate → `_attractiveness` + the new `chains/discovery.py`
scores/tiers rank them. No AI touches prices, ratings, or selection filtering — so nothing
can be hallucinated there.

**The LLM does exactly one thing, once per run** (`chains/compose.py` → `compose_pins`): in a
SINGLE structured call it (1) picks the best `count` products from the candidate rows and
(2) writes each caption (title, 2–3 sentence description, 5 hashtags). Optimisations:
- **One call** replaces the old 1+N design; trends + RAG examples sent ONCE.
- **Structured output** (`with_structured_output(PinBatch)`) → schema guaranteed via function
  calling; no text parsing, no retries on bad JSON.
- **Compact input** — candidates are pipe-delimited rows (`id|title|price|proof`), titles
  truncated to 70 chars, counts shortened (`27K`), caps: 25 candidates / 6 trends / 2 style
  examples / 5 idea names.
- **Static system prompt** (cache-friendly prefix).
- **Grounding** — the prompt forbids inventing numbers; the model may only cite the real
  values in each row, and the description must end with the FTC disclosure (safety-net check).
- **Model** `gpt-5-nano` reasoning model: `reasoning_effort=minimal`, `max_completion_tokens`
  budget, no temperature (reasoning models reject it); non-reasoning models get low temp 0.2.

## 13. Multi-select posts + per-post publishing (Affiliate → Post to IG)

- **Multi-select subcategories** — the refine strip is now multi-select; **each picked
  subcategory becomes its own post**. The old "custom keyword" box was removed.
- **1–10 posts** — a live **posts meter** enforces Instagram's 10-post ceiling; each post is a
  carousel of up to **10 products** (per-category stepper).
- **Processing panel** — per-post status pills while finding (queued → generating → done/error).
- **Auto-reflect** — generated posts drop straight into **Post to IG** (no manual "send").
- **Per-post publishing** — each post card in Post to IG has its **own "Publish to Instagram"**
  button; **"Publish all"** runs them **sequentially, one after another** (no clashes — each
  post gets its own post-specific comment→DM automation). Storefront auto-refreshes after.
- Backend already accepts up to 10 carousel images (`image_urls[:10]`), so nothing to change
  server-side. Frontend compiles (module 200, app renders).

## 14. Uniqueness, embeddings, 10-per-post, posted-only catalog

- **Two-layer uniqueness** — (1) within-scrape dedup `tools/amazon._dedup_products` collapses
  Amazon variant listings by ASIN + base image id + normalized title (fixes the duplicate
  cards); (2) cross-run dedup against `seen_products`. Verified: hoodie 10/10 unique images;
  Run A 10 unique → Run B **0 overlap**.
- **Embeddings flywheel** — every generated product is embedded into pgvector + marked seen
  (`store_results`), so no product ever appears in two posts. Live log: "Stored pin … in
  pgvector | Total pinned ever: 137".
- **Target 10 per post** — scrape pool deepened (60 raw → keep 40 unique) so a full 10 usually
  survive dedup + seen-filter; when the fresh pool is exhausted it returns **fewer, never
  repeats/fakes** (honest — the "Jacket · 1" case is a depleted pool, not a bug).
- **Public catalog = posted only** — `post_store.all_products` now filters `status='posted'`,
  so the storefront / GitHub-Pages link and `/api/collections` reflect products ONLY after a
  real Instagram publish (dry-run/failed excluded). Verified: collections = 0 until a live post.
- **Codified as agent rules** — `AGENTS.md` G17 (uniqueness + embeddings) & G18 (posted-only
  catalog); `dedup-guard` R6–R8; `carousel-publisher` CP6/CP6b.

## 15. One universal caption + categorized storefront

- **One caption per carousel (token-efficient)** — `chains/compose.py` schema changed from
  per-product captions to `{picks, caption, hashtags}`: the single LLM call now writes ONE
  catchy, valuable caption + 15-25 hashtags for the whole carousel (all picks share it),
  grounded in real numbers. Verified: 4-item run → 1 caption, 24 hashtags, real prices woven
  in. `/api/generate` returns top-level `caption`/`hashtags`; the frontend carries it per post
  (Affiliate shows it once per group; the IG card shows it as the single post caption; posting
  uses it). Codified in `content-strategist` CG1.
- **Categorized storefront** — `/hub` now groups products into **category sections** with
  headers + counts, and each card shows **price, strikethrough MRP, −% discount badge, rating
  & reviews**. `recordPost` widened to store `orig_price/discount_pct/rating/reviews` so the
  public page has the detail. Still posted-only (G18).

## 15b. JK↔SK clash fix + DM-card images (2026-09-05)

- **Hard per-post isolation (no JK/SK clash).** Root cause of the "Business-JK is also
  crashing" report: historical Jacob/"FREE site visit" DMs (Business-JK real-estate rule 1,
  account 2, account-wide) had landed on the affiliate (lostinframes, account 8) thread from
  before the account cleanup. The current DB is already clean (account 8 has ONLY affiliate
  post-scoped rules), but `process_event` now enforces a **structural guard**: a comment/DM on
  an affiliate post (`store.affiliate_products_for_media` non-empty) loads **only that post's
  own post-scoped rule** — account-wide rules (e.g. the JK real-estate auto-reply) can NEVER
  fire on an affiliate post, even if the affiliate rule were disabled or missing. Combined with
  the account-scoped `load_engine_rules(account_id)`, JK and SK can never touch each other's
  media. Automations are strictly per-post. `app/engagement/api.py` `process_event`.
- **DM product-card images now IG-fetchable.** Root cause of "the image didn't come in the DM":
  the comment→DM cards used the raw **Amazon CDN** `image_url` (`m.media-amazon.com`), which
  Instagram's Messenger template often can't fetch ("Couldn't load image"). Fix: `sk_carousel`
  now re-hosts each product image to **GitHub raw** (same host as the carousel slides) and
  **stamps the re-hosted URL back into the products** saved for the DM cards — so the card image
  == the slide image. `_rehost_for_ig` was made **1:1 aligned** (a failed item keeps its slot,
  so slides never shift). Added `_hi_res` (mirrors the frontend `hiRes()`) so a slide URL maps
  back onto its product. `app/api.py`. Codified in `carousel-publisher` CP5d/CP5e.
- **Backfill of existing posts:** `app/backfill_card_images.py` repointed the 8 already-posted
  affiliate carousels' card images to the GitHub-raw copies already uploaded at post time
  (reconstructed from the deterministic `sk_media/<md5(hi_res)>.jpg` name; re-hosted any missing
  one). Safe to re-run.

## 17. The Still Set — creative system + designed render (2026-09-05)

- **Creative-system playbook** published as an artifact (`creative-system.html`, "The Still Set"):
  original visual identity (paper/ink/ember + slate + per-category tints; Instrument Serif /
  Hanken Grotesk / Space Mono; the "index-frame" signature), 12 rendered templates, multi-product
  layout logic (1→hero … 7+→carousel), zero-cost image pipeline, 10-angle caption engine, brand
  voice (prefer/avoid), hashtag engine, QC gate, honest zero-cost tool table.
- **Designed slide renderer** — `app/services/sk_render.py`. Playwright renders the Still Set
  templates to 1080×1350 PNGs at 2× retina (Instrument Serif/Hanken/Space Mono via Google Fonts
  CDN; Noto Kannada/Devanagari from the container for the language module). Templates: cover,
  hero, deal, value, duo, rank, grid3/4, lead_rail, closer. `plan_slides()` picks the layout
  family by product count and builds a carousel arc (cover → features → closer; ranking arc
  optional). **Product stays true to source** — only the stage/shadow/type are designed.
  **No fabrication** — MRP/discount/rating render only when present. Prices use Indian grouping +
  tabular figures.
- **Free image prep** — `_prep_image`: fetch → optional **rembg** isolation IF installed →
  otherwise a zero-dependency **corner white-knockout** (Pillow flood-fill from the 4 corners,
  numpy-vectorised) that drops plain white catalog backgrounds while preserving interior whites →
  contain-fit onto a padded transparent canvas + light unsharp. Verified end-to-end on 5 real
  products: cover + 5 feature slides + closer, product floating on the tinted stage with a real
  shadow (screenshotted and reviewed).
- **Wiring** — `POST /api/sk/carousel` gained `design` (default true), `arc`, `theme`. When
  design=true it renders the Still Set slides from `products`, pushes the PNGs to GitHub raw
  (IG-fetchable, CDN-verified) and posts THOSE instead of raw product photos; falls back to raw
  images if rendering is unavailable so a post never fails over design. DM cards still use the
  clean source product image (recognizable/shoppable). New `POST /api/sk/render-preview` renders
  and returns `/cdn` URLs WITHOUT posting, so slides can be previewed first.
- **LLM** stays the user's own single structured call (gpt-5-nano, JSON) — no local model added.
- Codified in `carousel-publisher` CP10 (designed render) + the playbook artifact.

## 18. Badge scrape fix + trust-fact overlay + fashion validation (2026-09-05)

- **Badge scrape fixed** — `affiliate-rag-bot/tools/amazon.py`: the "Amazon's Choice" badge splits
  across two `.a-badge-text` spans, so the old `t('.a-badge-text')` truncated it to "Amazon's".
  Now the badge is **canonicalised from the card's full text** (Amazon's Choice / Best Seller /
  #1 Best Seller / Limited time deal), falling back to the joined spans. Verified on a fresh
  fashion scrape: clean "Best Seller", "Limited time deal" (and an unknown "New Season" is safely
  dropped, not shown).
- **Badge chip on slides** — `sk_render.py` `_badge_pill` + `_badge_text` sanitizer: only a
  known-good badge renders (as a filled ✓ pill in the tint); the legacy truncated "Amazon's" is
  repaired to "Amazon's Choice"; anything partial/unknown is dropped (G3, no fabrication). Wired
  into `_info_block` (hero/value) and the deal slide's chip row.
- **Header/footer cleanup** — removed the top-right edition code (`.code{display:none}`) and the
  footer slide counter per the user's request; footer is just the handle.
- **End-to-end fashion validation** — ran `POST /api/run {category:fashion, products_per_run:6,
  dry_run:true}` (68s): scraped + composed 6 real products (First Kick, Amazon Brand-Symbol [Best
  Seller], ANNI DESIGNER, Nermosa, Shining Diva, GRECIILOOKS), then rendered an 8-slide Still Set
  carousel + a 4-product grid. Products render large & clean with real rating/demand/savings/badge.
  Samples sent to the user for validation.

## 19. Perfect background removal (rembg) + fact hygiene (2026-09-05)

- **rembg installed as the primary cutout** — the colour-based flood-knockout eroded white product
  parts touching a white background (a model's white shorts/shoes got jagged holes). Fixed by
  installing **rembg==2.0.59 + onnxruntime==1.19.2** (pinned in `requirements.txt`; u2net model
  ~176MB cached at `/root/.u2net/`). `_prep_image(isolate=True)` — the default used by
  `render_carousel`/`sk_carousel` — now segments the product/subject semantically, preserving white
  product regions. The flood-knockout stays only as a fallback. Verified on the polo/white-shorts
  shot: clean, professional cutout (screenshotted, sent to user). Product pixels never altered (G3).
- **PERMANENT bake-in (2026-09-05)** — rembg + onnxruntime + the u2net model are now built into the
  image via a dedicated LATE layer in `Dockerfile.backend` (`pip install rembg==2.0.59
  onnxruntime==1.19.2` + `new_session('u2net')` + `test -f /root/.u2net/u2net.onnx`). Kept OUT of
  `requirements.txt` so the heavy torch/docling/playwright layers stay cache-valid (rebuild was ~2
  min vs a full ~15-min reinstall). Verified end-to-end: image rebuilt, backend **recreated** from
  it (wiping any runtime install), fresh container reports `rembg 2.0.59` + `MODEL_BAKED_IN=168M`,
  and a render returns `isolated=True` offline. Survives any `docker compose down/up/build`.
- **Fact hygiene** — review counts grouped ("39800" → "39,800"), demand suffixes preserved
  ("1K+"/"5K+"), and weak demand ("1 bought") suppressed (needs +/K/M or ≥ 50). `_fmt_count` +
  `_demand_chip` in `sk_render.py`. Codified in `still-set-templates` ST2/ST11/ST13.

- **Amazon tax info:** ✅ completed by the user (2026-09-01).
- Optionally add a GitHub token in Settings if publishing ever reports "no token" (currently
  the shared token works).

## 20. GitHub migration — repos + secret hygiene (2026-09-05)

- **Secret audit passed.** Universal `.gitignore` at `BUSINESS_SK/` + one in `affiliate-rag-bot/`.
  Leak-checks verified empty before every commit — NO `.env`, `.ragskey`, `*.db`, tokens, `venv`,
  `node_modules`, or the 3.2MB conversation dump are tracked. `.env.example` templates kept.
- **`affiliate-rag-bot` → new PUBLIC repo** https://github.com/Skarthik06/business-sk-affiliate
  (`main`, 48 files, clean) — the SK engine is on GitHub for the first time.
- **`instagram_automation` renamed → `business-sk`** (https://github.com/Skarthik06/business-sk,
  PUBLIC); old name 301-redirects. Local `origin` updated. Latest session code on branch
  `feat/realestate-studio`; **PR #2** opens it against `main` (merge pending — server-side auto-merge
  was blocked by the safety classifier; merge via GitHub UI or re-authorize).
- **Storefront config:** `settings.GITHUB_REPO` default updated to `business-sk`. **PENDING (needs
  Docker up):** set the rags DB setting `github_repo` → `business-sk` + republish the storefront, so
  new image/storefront pushes and the IG-bio shop link target the renamed repo. GitHub redirects cover
  old URLs meanwhile; the app is stopped, so nothing pushes to the wrong repo before this is fixed.
- **gh CLI installed** (v2.100.0), authenticated as Skarthik06.

## 21. v2.x — where to read the rest (2026-09-26)
The build history after section 20 (the multi-store engine v2.0, the AI Art Director v2.2, the
Scene Prompt Builder and palette Looks v2.3, slash-command style presets and GPU scheduling v2.4,
verified-follower follow gate and once-per-comment replies) is documented in full in
[`docs/MASTER.md`](docs/MASTER.md) (§7–§18) and the release log in [`RELEASES.md`](RELEASES.md).
