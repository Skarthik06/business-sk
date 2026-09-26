# BUSINESS_SK — Master Document

> The complete, detailed reference for the whole project: what it is, how it is built, every
> algorithm, every agent, the tech stack, requirements, operations and full version history.
> The [README](../README.md) is the short entry point; this document is the deep dive.

| | |
|---|---|
| **Current stable version** | **v2.4.3** (production runs this tag; recorded in `~/business-sk/.deployed_version`) |
| **Built on** | Python 3.12 (server) / 3.14 (Windows workers) · FastAPI · LangGraph/LangChain · OpenAI `gpt-5-nano` · PostgreSQL 18 + pgvector · Redis 7 · React 19 + Vite 7 + Tailwind 4 · Playwright/Chromium · PyTorch 2.11 (CUDA 12.8) · Z-Image-Turbo · BiRefNet · YuNet · Docker Compose · Caddy |
| **Runs on** | Oracle Cloud ARM server (24/7) + the owner's Windows laptop (RTX 5050 GPU worker + residential scrape worker) |
| **Instagram account** | `@lostinframes0605.exe` (profile name "Aura Picks") |
| **Last updated** | 2026-09-26 |

---

## Table of contents
1. [What the project is](#1-what-the-project-is)
2. [How the business works (money flow)](#2-how-the-business-works-money-flow)
3. [System architecture](#3-system-architecture)
4. [Repository map](#4-repository-map)
5. [Tech stack](#5-tech-stack)
6. [Requirements](#6-requirements)
7. [The end-to-end pipeline](#7-the-end-to-end-pipeline)
8. [Algorithms in detail](#8-algorithms-in-detail)
9. [The agents](#9-the-agents)
10. [Global rules (G1–G18)](#10-global-rules-g1g18)
11. [The Studio (frontend)](#11-the-studio-frontend)
12. [API reference](#12-api-reference)
13. [Data model](#13-data-model)
14. [Configuration knobs](#14-configuration-knobs)
15. [Operations](#15-operations)
16. [Security & compliance](#16-security--compliance)
17. [Version history](#17-version-history)
18. [Known limitations & roadmap](#18-known-limitations--roadmap)
19. [Documentation index & how to keep it complete](#19-documentation-index--how-to-keep-it-complete)

---

## 1. What the project is

`BUSINESS_SK` is one workspace that hosts **two businesses** sharing one Instagram-automation
platform, one PostgreSQL (pgvector) database and one Docker stack:

| Business | What it does | Where |
|---|---|---|
| **Business-SK** (main, active) | An **agentic affiliate-marketing autopilot**: finds products on Amazon, Flipkart, Shopsy, 16 Shopify D2C brands and Cuelinks stores; ranks them; writes captions; designs Instagram carousels with an **AI Art Director** (AI-painted scenes + real product cut-outs); publishes; auto-replies to comments and DMs the affiliate links to verified followers; keeps a public storefront. | `affiliate-rag-bot/` (engine) + `instagram_automation/` (publishing, rendering, engagement, Studio UI) |
| **Business-JK** | A real-estate Instagram platform (document intelligence → property knowledge → carousels). Its intelligence layer (`instagram_automation/business/`) is specified (research, architecture, 17 agent charters) with the posting machine shared. | `instagram_automation/business/`, `instagram_automation/app/business/` |

Everything is built as **agents**: each capability has an editable `*.agents.md` spec (the source
of truth for its rules) and reads its tunable constraints from config/env, so behaviour can be
tuned as the marketing operation evolves.

## 2. How the business works (money flow)

```
Product found  ──►  Instagram carousel  ──►  Viewer follows + comments "LINK"
(Amazon/Flipkart/      (AI-designed,                    │
 Shopify/Cuelinks)      real products)                  ▼
                                               Auto-DM with affiliate links
                                               (verified followers only)
                                                        │
                                   Storefront (link in bio) ◄── all posted products
                                                        │
                                                        ▼
                                        Purchase → commission
                                        · Amazon Associates (tag on every amazon.in link)
                                        · Cuelinks (Flipkart, Shopsy, Shopify brands, 1000+ stores)
```

* **Amazon** links carry the Associates tag directly (`amazon.in/dp/<ASIN>?tag=…`).
* **Every other store** is monetised through **Cuelinks** (link conversion API).
* The **storefront** (`https://lostinframes-sk-store.vercel.app`, proxied from the server's `/hub`)
  lists every posted product with a clear affiliate disclosure.
* Posts end with the CTA **"➕ Follow + 💬 comment "LINK" to get the links in your DM 📩"** — the
  follow gate sends links only to verified followers (growth + conversion loop).

## 3. System architecture

```
                         ┌──────────────── Oracle Cloud ARM server (24/7, Docker Compose) ────────────────┐
 Browser / phone ──HTTPS──► caddy :80/:443  (auto-TLS for 140-238-247-18.nip.io)                          │
                         │     └─► frontend :3000  (Vite dev server, React Studio)                         │
                         │            ├─ /api/*    ──► backend :8000          (IG app: publishing, render,  │
                         │            │                                        engagement, Art Director)     │
                         │            └─ /sk-api/* ──► affiliate_backend :8100 (affiliate engine, LangGraph,│
                         │                                                     scrapers, storefront /hub)   │
                         │  db :5432 (PostgreSQL 18 + pgvector)   redis :6379   volumes: pgdata, redisdata…  │
                         └───────────────────────────────────────────────────────────────────────────────────┘
        ▲ poll jobs / post results (token-auth)                    ▲ poll jobs / post HTML (token-auth)
        │                                                          │
 ┌──────┴──────────── Owner's Windows laptop ──────────────────────┴──────────────┐
 │ gpu_worker.py  (RTX 5050, venv %USERPROFILE%\sk-ai)   scrape_worker.py (stdlib)  │
 │  · BiRefNet cut-outs · YuNet face detect              · residential-IP fetch of  │
 │  · Z-Image-Turbo scene painting                         Amazon / Flipkart pages  │
 │ both auto-start at login (Startup folder .vbs), self-healing loops               │
 └──────────────────────────────────────────────────────────────────────────────────┘
 External: Instagram Graph API · OpenAI · Cuelinks API · Shopify public /products.json · GitHub raw
           (IG-fetchable image hosting) · Vercel (storefront proxy) · Hugging Face (model downloads)
```

**Why two local workers?** Amazon and Flipkart block datacenter IPs (the Oracle server and every
cloud). The **scrape worker** fetches those pages from the owner's residential connection. The
**GPU worker** runs the image models (no GPU on the server). Both are optional: the server always
has a fallback path, so a post never fails because the laptop is off.

### Docker services (`docker-compose.yml`, project `business_sk`)

| Service | Image / build | Port | Role |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg18` | 5433→5432 | PostgreSQL 18 + pgvector (named volume `pgdata` — never `down -v`) |
| `backend` | `instagram_automation/Dockerfile.backend` | 8000 | IG app (FastAPI, `uvicorn --reload`, code bind-mounted) |
| `affiliate_backend` | `affiliate-rag-bot/Dockerfile` | 8100 | Affiliate engine (FastAPI, bind-mounted) |
| `frontend` | `node:20-alpine` | 3000 | Studio (Vite dev server, HMR) |
| `redis` | `redis:7-alpine` | 6379 | Follow-gate state, once-per-comment claims, counters |
| `caddy` | `caddy:2-alpine` | 80/443 | HTTPS reverse proxy (Let's Encrypt via nip.io) |

## 4. Repository map

```
BUSINESS_SK/
├── README.md                  ← short entry point
├── docs/MASTER.md             ← this document
├── AUDIT.md                   ← running build audit (history of what was built, v1 era)
├── RELEASES.md                ← how versions are cut / deployed / rolled back + release log
├── docker-compose.yml         ← the whole stack      Caddyfile ← HTTPS proxy
├── creative-system.html       ← the "Still Set" creative system (visual playbook)
├── scripts/
│   ├── release.sh / deploy.sh / rollback.sh   ← versioning & production deploys
│   ├── scrape_worker.py       ← residential scrape worker (runs on the laptop)
│   └── gpu_worker.py          ← GPU worker: BiRefNet + YuNet + Z-Image (runs on the laptop)
├── storefront/                ← Vercel proxy config for the public store
├── affiliate-rag-bot/         ← AFFILIATE ENGINE (Business-SK brain)
│   ├── server.py              ← FastAPI :8100 (63 routes) + storefront /hub
│   ├── config.py              ← every tunable knob (env-overridable)
│   ├── graph/                 ← LangGraph 8-node pipeline (nodes.py, graph.py)
│   ├── chains/                ← compose (captions), discovery (scoring), hashtags, validate, deals
│   ├── tools/                 ← scrapers: amazon(.py/_html), flipkart_scrape, shopsy_scrape,
│   │                            shopify_scrape, my_shopify, scrape_bus, retailers/, affiliate
│   ├── rag/                   ← pgvector store, dedup ledger, posts, trends, discovery stats
│   ├── performance/           ← performance store, learner, cuelinks_markets (19 stores)
│   ├── publishing/ prediction/ competitor/   ← queue, winner prediction, competitor intel
│   ├── agents/*.agents.md     ← 30 agent specs      AGENTS.md ← roster + global rules
│   └── docs/ENGINE_GUIDE.md, docs/AUTOPILOT_BLUEPRINT.md
└── instagram_automation/      ← IG PLATFORM (publishing, design, engagement, Studio UI)
    ├── app/api.py             ← FastAPI :8000 (IG + Business-SK carousel/render/Art Director)
    ├── app/services/          ← sk_render (slides), art_director, scene_store, hosting, instagram, llm
    ├── app/agents/scene-prompt.agents.md   ← LIVE rules for image-model prompts + palettes + presets
    ├── app/engagement/        ← rules engine, service (Graph API), store, follow_gate, api (poller/webhook)
    ├── app/business/          ← Business-JK pipeline + admin auth
    ├── business/              ← Business-JK architecture, AGENTS.md, 17 agent charters, META_INTEGRATION.md
    └── frontend/              ← React Studio (BusinessSK.jsx, Engagement.jsx, …)
```

## 5. Tech stack

### Backend
| Area | Technology |
|---|---|
| Language | Python 3.12 (containers), 3.14 (Windows workers) |
| Web framework | FastAPI 0.128, Uvicorn 0.40 (`[standard]`), Pydantic 2.12 |
| Agent orchestration | LangGraph 0.2–0.3, LangChain 0.3, LangSmith (optional tracing) |
| LLM | OpenAI `gpt-5-nano` (reasoning model; vision) via `openai` 2.38 and `langchain-openai` |
| Embeddings / RAG | `sentence-transformers` all-MiniLM-L6-v2 (CPU, free) + PGVector (`langchain-postgres`, `pgvector`) |
| Database | PostgreSQL 18 + pgvector 0.8, SQLAlchemy 2, psycopg 3 |
| Cache / state | Redis 7 (`redis` ≥ 5) |
| Browser automation | Playwright 1.58 + Chromium (slide rendering, fallback scraping) |
| Imaging | Pillow 12, OpenCV, rembg (u2net, server fallback cut-outs) |
| Documents (Business-JK) | pdfplumber, pypdfium2, pytesseract, docling, jsonschema |
| Security | `cryptography` (Fernet — IG tokens, DB URL, GitHub token at rest), HMAC-signed admin tokens |
| Trends (optional) | Tavily API, Google News RSS (`feedparser`) |

### Frontend
React 19 · Vite 7 · Tailwind CSS 4 · axios · ogl (WebGL accents). Single-page Studio with
deep-linked panels (`#sk-post`, `#engagement`, …).

### AI image stack (laptop GPU worker — `%USERPROFILE%\sk-ai\venv`)
| Model | Job | License | Size | Speed (RTX 5050 8 GB) |
|---|---|---|---|---|
| **BiRefNet** (`ZhengPeng7/BiRefNet`) | product cut-out (alpha mask, pixels untouched) | MIT | ~0.4 GB | ~0.5 s |
| **YuNet** (OpenCV model zoo) | face detection → "model shot" vs "object" | Apache-2.0 | 230 KB | ms |
| **Z-Image-Turbo** 4-bit (`unsloth/Z-Image-Turbo-unsloth-bnb-4bit`, base Tongyi-MAI) | paints the EMPTY scene backdrop | Apache-2.0 | 6.4 GB | ~75–90 s per 1024×1280 (9 steps) |
| PyTorch 2.11 + CUDA 12.8, diffusers 0.40, transformers 5, bitsandbytes 0.50, OpenCV 5 | runtime | — | — | — |

Evaluated and **not** used (reasons documented): RMBG-2.0 (non-commercial), FLUX.1 Kontext [dev]
(non-commercial), IC-Light (re-lights the product), virtual try-on models (alter the product,
non-commercial), SDXL-Turbo (512 px, licence), Qwen-Image / Qwen-Image-Edit (20 B — too heavy for
16 GB RAM; candidate after a RAM upgrade), Wan 2.1/2.2 (video — planned for Reels).

### Infrastructure
Oracle Cloud ARM VM · Docker Compose · Caddy 2 (auto-HTTPS on `140-238-247-18.nip.io`) · GitHub
(code + raw image hosting for IG) · Vercel (storefront proxy) · Windows Startup-folder `.vbs`
launchers for the workers.

## 6. Requirements

### Hardware
* **Server:** any Linux host with Docker (production: Oracle Cloud ARM, always on).
* **GPU worker (optional but recommended):** NVIDIA GPU with ≥ 8 GB VRAM (RTX 5050 laptop in use),
  16 GB RAM, ~15 GB disk for models. Blackwell GPUs need PyTorch built for CUDA ≥ 12.8.
* **Scrape worker:** any always-on machine on a residential internet connection.

### Software
Docker + Docker Compose; Python 3.12+ (workers: 3.14 used); Node 20 (inside the container);
Tesseract binary (Business-JK OCR); Playwright Chromium (installed in the images).

### Accounts & keys (names only — values live in git-ignored `.env` files / encrypted store)
| Key | Used for |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_MODEL` | captions, Art Director, search planner |
| Instagram Graph API app (Meta) + per-account tokens (encrypted in DB) | publishing, comments, DMs, insights |
| `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` | webhook signature/verification |
| `AMAZON_ASSOCIATE_TAG` (stored per account) | Amazon affiliate links |
| Cuelinks API key | link conversion, campaigns, reports |
| `DATABASE_URL` | PostgreSQL |
| `SCRAPE_WORKER_TOKEN` / `GPU_WORKER_TOKEN` | worker authentication (same secret, `~/.sk_worker_token` on the laptop) |
| GitHub token | IG-fetchable image hosting (raw.githubusercontent) |
| Google OAuth client id, `GOOGLE_ALLOWED_EMAILS`, admin credentials | Studio login |
| `TAVILY_API_KEY` (optional), ScraperAPI key (optional, legacy) | trends; proxy fallback |

## 7. The end-to-end pipeline

```
 1 DISCOVER     Studio (Affiliate / Cuelinks Affiliate) → pick store, category, filters, goal
 2 SCRAPE       Amazon/Flipkart via residential worker · Shopsy direct · 16 Shopify /products.json
 3 FILTER+RANK  quality gate → attractiveness/goal scoring → brand round-robin → 2-layer dedup
 4 COMPOSE      ONE gpt-5-nano call: rank + universal caption + cover copy + hashtags (+ fact-check)
 5 STAGE        posts are staged in Content Studio (browser queue), one carousel per group
 6 ART DIRECT   Art Director: analyse products → look/palette → 2–3 style presets → scene fields
 7 GPU          laptop: BiRefNet cut-outs + YuNet facts; Z-Image paints the post's own scene
 8 RENDER       sk_render (HTML → PNG via Playwright): collage cover → product slides → closer
 9 PUBLISH      images re-hosted on GitHub raw → IG Graph API carousel (recovery on rate limit)
10 AUTOMATE     per-post comment→DM rule registered; poller (30 s) + webhook process comments
11 ENGAGE       reply once · follow gate (verified followers) · DM product cards · mark lead
12 STOREFRONT   posted products published to /hub (Vercel link in bio)
13 LEARN        IG insights + Cuelinks reports → performance store → learner → better discovery
```

## 8. Algorithms in detail

### 8.1 Product discovery & scraping
* **Stores (19 markets, `performance/cuelinks_markets.py`):** Amazon, Flipkart, Shopsy, and 16
  Shopify D2C brands (boAt, Noise, Mamaearth, SUGAR, Plum, mCaffeine, Pilgrim, Minimalist, Juicy
  Chemistry, Sirona, The Man Company, Beardo, Bombay Shaving Co., Snitch, Chumbak, Sleepycat).
  Only stores whose products can actually be fetched are offered.
* **Amazon** (`tools/amazon.py`, `amazon_html.py`): search-results pages (not Best Sellers).
  Fetched through the **residential scrape worker** when online (`scrape_bus.fetch`, run in a thread
  so the event loop never blocks), else Playwright. The HTML parser takes the **longest** title
  candidate (the short `<h2>` is the brand line), reads ratings/reviews from aria labels, extracts
  brand (ignoring "Generic"), unescapes HTML entities, and tags every URL with the Associates tag.
* **Flipkart** (`flipkart_scrape.py`): search pages via the worker (or ScraperAPI), hi-res images.
* **Shopsy** (`shopsy_scrape.py`): direct scrape (not blocked).
* **Shopify brands** (`shopify_scrape.py`): the public `/products.json` feed — free, no proxy.
* **Deals** (`chains/cuelinks_deals.py`): Cuelinks campaign/coupon posts from the selected active stores.
* **Search planner agent:** turns a free-text query into validated filters (retry → validate →
  refine, never a generic fallback); brand picks are a hard constraint with fair round-robin.
* **Residential worker protocol:** server enqueues `{url, kind}`; the worker polls
  `/api/scrape/jobs`, fetches with full Chrome headers and retry/back-off (< 80 KB page ⇒ throttled),
  posts gzip+base64 HTML to `/api/scrape/result`.

### 8.2 Quality, scoring, ranking, uniqueness
* **Quality gate** (`_passes_quality`): real price, image, title, rating/review floors (soft
  thresholds with count guarantee — the requested number of products is always returned).
* **Scoring** (`chains/discovery.py`): content/Instagram/purchase-intent/value/content-potential
  scores (0–100) → S–D tiers; **goal-based ranking** (balanced, deals, trending, premium…);
  combo/bundle mode with arithmetic truth (G15).
* **Two-layer uniqueness (G17):** within-scrape dedup (normalised titles, not collapsing distinct
  brand-only titles) + cross-run ledger `seen_products` so an ASIN is never intentionally re-posted.
* **Novelty & RAG flywheel:** pgvector similarity against past posts rewards novel products;
  successes are written back so each run improves.

### 8.3 Content composition (captions)
`chains/compose.py` makes **ONE** structured LLM call per batch (rank + write): a short, specific,
festival-free caption (< 200 chars), AI cover title/subtitle, deal words, hashtags merged with a
curated tag bank, numeric fact-check against the products, and a canonical CTA enforced by
post-processing (any model-written "link in bio" variants are stripped). Token usage is recorded
per post (`content_tokens`).

### 8.4 AI Art Director (agent `post-art-director`)
`instagram_automation/app/services/art_director.py` — designs every product post.
1. **Queue cut-outs** for every photo on the laptop; wait briefly (`ART_META_WAIT_SECS`) so the
   measured facts reach the LLM.
2. **Analyse** (vision LLM, 320 px cut-out thumbnails): per product → type, real colours, material,
   style, vibe.
3. **Look:** default **AI decides** (`ART_LOOK=ai`) among 8 palette rows (Noir Gold "Premium dark",
   Warm Sand, Terracotta, Mono Ink, Sky, Rose, Mint, Lilac); sees the last 6 posts' looks and must
   vary; a palette used by the last 2 posts is excluded. Studio can override.
4. **Style presets (slash commands):** picks **2–3** of 15 presets (`/premium /vintage /minimal
   /streetwear /cinematic /golden-hour /studio /cozy /coastal /scandi /industrial /botanical /tech
   /y2k /editorial`) that fit the analysis and are compatible with the palette; rotates away from
   recently used ones; the Studio "Style commands" box can force them.
5. **Scene:** fills STRUCTURED fields (setting, wall, floor, light, props, colours, camera, mood)
   under the live rules of `scene-prompt.agents.md`; the code assembles them in a fixed order and
   appends the presets' phrases and the empty-scene suffix → the Z-Image prompt.
6. **Layouts** per slide (hero / float / split) — then **measured facts override**: Hero only for
   bottom-cropped photos; a cropped model (face detected) is always Hero.
7. **Guarantees ("no excuses"):** every field is validated; missing presets are auto-filled;
   clashing presets dropped; with no LLM answer at all, the code synthesises a preset-built scene
   from the palette row. The render never blocks: the post waits ≤ `ART_SCENE_WAIT_SECS` for its
   own scene, else uses a same-palette library scene.
8. **Cost:** static instructions first (prompt-cached), per-post data last, compact JSON, minimal
   reasoning → Art Director ≈ 3k input (1.3k cached) + 0.7k output ≈ **$0.0004**; whole post incl.
   captions ≈ **₹0.05**. Every preview returns the priced breakdown.

### 8.5 GPU worker & scene store
* **Queue** (`scene_store.py`): file-backed jobs in `images/sk_scenes/queue/` (survive restarts,
  shared by processes); cut-outs oldest-first, **scenes newest-first**; **at most one scene per
  poll**; lease 420 s; ≤ 3 tries; the worker sends a **heartbeat every 20 s while painting**.
* **Cut-out** (`gpu_worker.py`): BiRefNet mask → 1 px edge pull-in (no white halo) → **tight crop on
  solid pixels** (specks removed) → RGBA PNG with the original RGB. Facts: `subject` (YuNet face ⇒
  person; silhouette fallback), `touches_bottom`, aspect, 3 main colours, fill.
* **Scene:** Z-Image-Turbo 4-bit, 9 steps, guidance 0 (no negative prompt ⇒ positive phrasing),
  1024×1280; BiRefNet is parked on the CPU during a paint (avoids VRAM spill); Z-Image unloads after
  30 min idle.
* **Uploads** are decoded and verified as real images, size-bounded, saved under hash/slug names.

### 8.6 Rendering — the post format (`sk_render.py`)
HTML/CSS slides rendered to 1080×1350 PNG by headless Chromium.
1. **Cover — collage (display only):** up to 6 real cut-outs in frosted tiles of **mixed aspect
   ratios** (tall ← model shots, wide ← wide items), numbered `01…N`, **no names and no prices**,
   AI headline (₹/% guarded) + concept subtitle, bold "Swipe → N picks inside".
2. **Product slides:** `scene_hero` (model stands on the panel edge), `scene_float` (whole object,
   soft shadow), `scene_split` (editorial card). The panel shows only real facts: number + store +
   brand, name, price, struck MRP (if > price), % OFF (if > 0), ★ rating + count (if > 0), CTA.
3. **Closer (last slide):** "Want these?" — the full process **1 · Follow → 2 · Comment "LINK" → 3 · Check
   your DM** (or tap the link in bio), on the same scene. The last slide never says "swipe": its footer
   reads **FOLLOW · COMMENT · DM** (also for a single-product post).
4. **Watermark:** a small circled **SK** monogram bottom-centre on every slide, in the palette colours.
The product photo is never altered — only scaled and shadowed (0.000 % pixels changed in tests).
Coupon/deal posts keep their deal cards; classic templates remain only as the no-library fallback.

### 8.7 Publishing
Slides are pushed to GitHub raw (Instagram-fetchable), then published as an IG carousel via the
Graph API. A rate-limited publish is verified before being reported as failed (the post may have
gone live). Each post is registered for engagement with its own comment→DM automation.

### 8.8 Engagement (comment → DM)
* **Intake:** a 30 s poller (comments of affiliate posts) + Meta webhooks, both producing the same
  event id `comment:<id>` (idempotent `store_event`).
* **Per-post isolation:** an affiliate post fires only its own rule (Business-JK rules never clash).
* **Exactly once:** `process_event` is serialised per event (thread lock) **and** every public
  reply / DM takes a Redis claim `sk:once:{reply|dm}:{account}:{comment}` before sending (released
  on failure/hold) — one reply and one DM per comment, ever.
* **Follow gate (verified followers only):** `is_user_follow_business` decides. Follower → reply
  "🔗 Sent to your DM" + product cards by private reply. Not verified → a warm "Thanks for the love!
  💛" (no follow nag in comments), links held and re-checked every 30 min for 7 days; delivered
  the moment the follow is verified. *Currently every public commenter is held until Meta grants
  Advanced Access (see §18).*
* **Hygiene:** self-author guard (no reply loops), prune-to-live (drops data of posts deleted on
  Instagram), inbox cutoff (pruned DMs never re-imported), leads + conversations tracking.

### 8.9 Storefront
`/hub` renders every posted product (categorised by store, searchable, sortable, deals strip) with
affiliate disclosure; `storefront/vercel.json` proxies `lostinframes-sk-store.vercel.app` to it.

### 8.10 Performance & learning
IG insights and Cuelinks reports feed `performance/store.py`; the learner adjusts discovery weights
and the winner/prediction models; the Studio shows revenue, winners, trends and recommendations.

## 9. The agents

### Business-SK engine (`affiliate-rag-bot/agents/`, roster in `AGENTS.md`)
| Agent | Role |
|---|---|
| Orchestrator | owns the LangGraph DAG, browser session, run lifecycle |
| Amazon Scraper · Product Scout · Retailer Adapter | retrieval per category and store |
| Dedup Guard · Novelty Analyst | two-layer uniqueness, novelty scores |
| Product Scorer · Winner Engine · Winner Prediction | scoring, tiers, winners |
| Trend Scout · Trend Analyst · Seasonal Planner | trends, seasons (captions stay festival-free) |
| RAG Retriever · Memory Writer | pgvector context + the flywheel |
| Pin Composer · Content Strategist · Content Intelligence · Collection Builder | captions, CTAs, hashtags, bundles |
| Affiliate Linker | Associates tag / Cuelinks conversion |
| Discovery Planner | adaptive query planning |
| Publishing Agent · Account Safety · Carousel Publisher · Pinterest Publisher | queue, pacing, safety |
| Performance Analyst · Learning Agent · Competitor Intelligence | feedback loop |
| Still Set Templates · Still Set Renderer | slide look (fallback templates, cut-out rules) |
| **Post Art Director** | THE post design (AD1–AD11) |
| **Scene Prompt Builder** | live image-prompt rules, palette rows, style presets (`instagram_automation/app/agents/scene-prompt.agents.md`) |

### Business-JK (`instagram_automation/business/agents/`, constitution in `business/AGENTS.md`)
carousel-planner · carousel-structure · contradiction · cost-governor · extraction · human-review ·
ingestion · integration · location-intelligence · marketing-strategist · multimodal-vision ·
orchestrator · property-entity · quality-control · rendering · security-privacy · verification.

## 10. Global rules (G1–G18)
Defined in `affiliate-rag-bot/docs/AUTOPILOT_BLUEPRINT.md` and inherited by every agent:
G1 never raise for recoverable errors · G2 fail-open enrichment, fail-closed core · G3 affiliate
disclosure · G4 posting safety · G5 never intentionally re-post an ASIN · G6 one PostgreSQL +
pgvector layer · G7 cold-start tolerance · G8 secrets stay in env/encrypted store · G9 one active run
per account · G10 control LLM cost · G11 resilient selectors · G12 dry-run means no publishing ·
G13 no fabrication · G14 discovery first, commission second · G15 arithmetic truth · G16 quality
over quantity · G17 two-layer uniqueness · G18 public catalogue reflects posted products.

## 11. The Studio (frontend)
Login (Google OAuth / admin) → sidebar with two workspaces:
* **Business-JK:** Dashboard, Business, Engagement, Custom Poster, Properties, Media Library,
  Templates, Analytics, Leads, Calendar, Integrations, Admin.
* **Business-SK — Workspace:** **Affiliate** (Amazon generator: categories, filters, goal, combo,
  universal search), **Cuelinks Affiliate** (per-store generator: Flipkart/Shopsy/Shopify brands,
  deals, AI planner, attribution), **Content Studio** (staged posts, Art Director status, Look +
  Style commands, preview == post, AI direction with analysis / scene prompt / tokens & cost,
  publish all or one, Story posting), **Engagement** (auto-sync, KPIs, charts, automations, follow
  gate, DM inbox, comments, leads, activity, simulator), **Storefront**.
* **Business-SK — Insights:** Overview, Winners, Trends, Intelligence, Content Calendar, Revenue.
* **Business-SK — Affiliate settings:** Agents (editable agent settings), Accounts (encrypted
  affiliate accounts), History.

## 12. API reference

**Affiliate engine (`:8100`, via `/sk-api/api/...`) — 63 routes.** Generation: `GET /api/generate`,
`/api/flipkart/generate`, `/api/cuelinks/store-generate`, `/api/cuelinks/generate`,
`/api/mystore/*` · Cuelinks: `markets, active, plan, constraints, convert, ping, search,
campaigns/refresh, deal-merchants` · Discovery & intelligence: `taxonomy, categories, collections,
search/filters, discovery/queries, trends, seasons, intelligence/{insights,recommendations,winners}`
· Posts & store: `posts, history, stats, hub, /hub` · Publishing: `publishing/{queue,next,
transition,cancel,emergency-stop,account}` · Performance: `performance/{overview,posts,categories,
ingest}`, `networks/*` · Workers: `scrape/{jobs,result,worker-status}` · Admin: `agents,
agents/settings, render-config, config, admin/reset-store (guarded)`, `health`.

**IG platform (`:8000`, via `/api/...`).** Accounts & settings: `accounts, settings` · Business-SK:
`sk/carousel, sk/render-preview, sk/render-options, sk/art-direct, sk/scenes, sk/story, sk/account,
sk/storefront/*` · GPU worker (token-auth, bypasses admin gate): `gpu/worker/{jobs,result}` ·
Engagement (`/api/engagement/*`): `automations (CRUD, toggle, duplicate, test, executions, stats),
summary, charts, top-posts, posts, sync-all, sync-status, conversations, comments, leads, events,
followgate/{status,toggle}, webhook-status, prune-to-live, simulate` · Webhooks: `/api/webhooks/meta`
· Business-JK: `/api/v1/*` (admin login, integrations, pipeline).
Every `/api/*` route requires a signed admin token except health, login, webhooks and the
token-authenticated worker endpoints.

## 13. Data model
| Store | Contents |
|---|---|
| `sk_posts` | posted/recorded carousels (label `Post_N#category`, products, caption, permalink) |
| `seen_products` | cross-run dedup ledger (ASIN / store id) |
| pgvector collections | product/post embeddings for RAG + novelty |
| `eng_posts, eng_rules, eng_events, eng_executions, eng_comments, eng_conversations, eng_messages, eng_leads, eng_lead_events, eng_insights, eng_audit` | engagement platform (per account, workspace-scoped) |
| agent settings, cuelinks config, trends, discovery stats, performance tables | engine state |
| Redis | follow-gate pending/verified counters & config, once-per-comment claims, re-check throttles |
| `images/sk_scenes/` (git-ignored) | `library.json`, `backdrops/*.jpg`, `cutouts/*.png|json`, `queue/*.json`, `worker.json`, `recent_looks.json` |
| `images/sk_slides/` | rendered slide PNGs (previews/posts) |
| Encrypted | IG tokens, DB URL, GitHub token (Fernet key `.ragskey`, never committed) |

## 14. Configuration knobs
Engine knobs live in `affiliate-rag-bot/config.py` (discovery, novelty, trends, content,
publishing, performance, retailers, competitor, scraper proxy, LLM). Art Director / GPU knobs (IG
backend env):

| Knob | Default | Effect |
|---|---|---|
| `ART_DIRECTOR_ENABLED` | 1 | 0 ⇒ deterministic director only |
| `ART_LOOK` | ai | ai / premium / a palette key / a scene key |
| `ART_PRESETS_PER_POST` | 3 | presets per post |
| `ART_ALLOW_NEW_SCENES` | 1 | commission a scene per post |
| `ART_MAX_IMAGES` / `ART_IMG_PX` | 4 / 320 | photos sent to the vision LLM / thumbnail size |
| `ART_REASONING_EFFORT` / `ART_MAX_TOKENS` | minimal / 3000 | LLM budget |
| `ART_META_WAIT_SECS` / `ART_WAIT_SECS` / `ART_SCENE_WAIT_SECS` | 15 / 20 / 240 | bounded waits |
| `LLM_PRICE_IN_PER_M` / `_CACHED_PER_M` / `_OUT_PER_M`, `USD_INR` | 0.05 / 0.005 / 0.40, 88 | cost display |
| `FOLLOWGATE_OFFICIAL`, `FOLLOW_RECHECK_SECS`, `FOLLOW_RECHECK_DAYS`, `FOLLOW_GATE_NEUTRAL_REPLY` | 1, 1800, 7, "Thanks for the love! 💛" | follow gate |
| `ENGAGEMENT_LIVE`, `ENGAGEMENT_AUTO_SYNC`, `ENGAGEMENT_SYNC_INTERVAL` | —, 1, 30 | engagement |
| `GPU_IDLE_UNLOAD_SECS` (worker) | 1800 | free Z-Image VRAM after idle |

The **scene rules, palette rows and style presets** are not env knobs — edit
`instagram_automation/app/agents/scene-prompt.agents.md` (read live on every post).

## 15. Operations

### Release, deploy, roll back (see `RELEASES.md`)
```bash
./scripts/release.sh v2.4.2 "What changed"      # local: tag + push (also updates the docs, see §19)
./scripts/deploy.sh v2.4.2                        # server: deploy a tag
./scripts/rollback.sh                             # server: back to the previous tag
cat ~/business-sk/.deployed_version               # what production runs
```
Code is bind-mounted and hot-reloads; `docker compose restart backend affiliate_backend` applies
backend changes; `env_file` changes need `docker compose up -d --force-recreate --no-deps backend`.

### Workers (laptop)
* Auto-start: `Startup\BusinessSK-ScrapeWorker.vbs`, `Startup\BusinessSK-GpuWorker.vbs`.
* GPU worker log: `%USERPROFILE%\sk-ai\gpu_worker.log`. Status in the Studio ("Laptop GPU online /
  painting a scene… / offline").

### Health checks
`/api/health` (both backends), `/sk-api/api/scrape/worker-status`, `/api/sk/scenes` (GPU),
`/api/engagement/followgate/status`, `/api/engagement/webhook-status`.

### Troubleshooting (seen in production)
| Symptom | Cause / fix |
|---|---|
| Amazon generate returns 0 | momentary Amazon throttle of the residential IP — retry; the log records page size/captcha/asins |
| "Laptop GPU offline" while painting | fixed in v2.4.0 (heartbeat); check the worker log |
| Scene paints ~3 min | VRAM spill — fixed (BiRefNet parked on CPU while painting) |
| Post uses a library scene | own scene still painting (> `ART_SCENE_WAIT_SECS`); Re-direct later |
| Followers not receiving links | Meta Advanced Access pending (§18) |
| Studio preview cropped | fixed (4:5 viewer) — hard-refresh |

## 16. Security & compliance
* Secrets only in git-ignored `.env` files; IG tokens, DB URL and GitHub token encrypted at rest;
  credential text files are git-ignored (the repository is public).
* Admin gate on every `/api/*` route (HMAC-signed tokens with a per-boot nonce ⇒ re-login after a
  restart); Google login restricted to allowed emails.
* Worker endpoints use a separate token (constant-time compare); uploads verified as images.
* Webhooks verified by challenge + signature.
* Affiliate disclosure in the storefront, DMs and captions; Amazon Associates + ASCI rules.
* Meta policy: private replies once per comment, follow status only via the official API, no
  scraping of Instagram.
* The Vite dev server denies `.env`, keys, Dockerfiles, `.git` and hides error overlays.

## 17. Version history
| Version | Highlights |
|---|---|
| **v1.0.0** | First stable: full Autopilot (Phases 1–10), Studio dashboard, accounts, Google login |
| v1.1–v1.2.x | Amazon fetch via proxy, dashboard reorg, deep links, reliable parallel scraping |
| v1.3.x | Affiliate UI redesign, seasonal deals engine, smart-split links (Amazon direct, others Cuelinks), audience targeting |
| v1.4.x | Template System v2 (carousel-first, palettes, teaser cover), elegant comment→DM closer, server-only engagement, canonical CTA |
| v1.5.x | Publish recovery on rate limit, alpha-matted cut-outs, AI cover copy, brand marks, privacy policy, self-healing webhook |
| v1.6–v1.7.x | Webhook signature verification, feedback-loop fix, collage cover, affiliate attribution |
| v1.8.x | Cover without prices, universal search |
| v1.9.0–v1.9.40 | Animated storefront + Vercel, per-post tokens, search-planner agent, strict brand filters, Cuelinks v3 API, Flipkart engine, 6 Instagram templates, follow gate (two-step → official), deals renders |
| **v2.0.0** | STABLE: free multi-store affiliate engine (16 Shopify brands, Shopsy, residential scrape worker) |
| v2.0.1–v2.0.2 | Zero-error audit; dev-server overlay fix |
| v2.1.0–v2.1.3 | Template pickers, 8 palettes, correct handle, Amazon parser fixes, guarded store reset + clean-slate launch |
| v2.1.4–v2.1.6 | No dead bands in slides; engagement prune-to-live + per-event lock; follow gate = verified followers only |
| **v2.2.0** | **AI Art Director**: vision LLM + Z-Image scenes + BiRefNet cut-outs on the laptop GPU — the post format |
| v2.2.1 | Price-free mixed-aspect collage cover with numbered swipe hooks; face-based model detection |
| **v2.3.0** | Analyse-first Art Director, per-post AI scenes under the Scene Prompt Builder agent, palette Looks |
| v2.3.1–v2.3.3 | Look picker layout; AI decides the look (varied feed); −60 % LLM cost + per-post token/cost breakdown |
| **v2.4.0** | 15 slash-command style presets (always applied, rotating); GPU heartbeat, one scene per poll, faster paints |
| **v2.4.1** | One public reply + one DM per comment, ever (Redis claim) — *current* |

Full tag messages: `git tag -l 'v*' -n1`.

## 18. Known limitations & roadmap
* **Meta Advanced Access** for `instagram_manage_messages` is pending: until approved, follow status
  of public users is unreadable, so the follow gate holds every public commenter (testers work).
  After approval, held commenters receive links automatically (no code change).
* Scene painting needs the laptop on (~75–90 s per post); library scenes are used otherwise.
* Shopify "My Store" (write API) is parked (store plan required) — code kept.
* **Next:** Real-ESRGAN 2× for small store photos · Wan 2.2 TI2V-5B for auto Reels · Qwen-Image-Edit
  hero shots after a 32 GB RAM upgrade · more presets as the feed learns.

## 19. Documentation index & how to keep it complete

| Document | What it covers |
|---|---|
| [`README.md`](../README.md) | Entry point: overview, current version, stack, quick start, doc map |
| **`docs/MASTER.md`** | This complete reference |
| [`AUDIT.md`](../AUDIT.md) | Running build audit (sections 1–20: infra, engine, secure posting, comment→DM, storefront, Still Set, rembg, GitHub migration …) |
| [`RELEASES.md`](../RELEASES.md) | Versioning, deploy, rollback + release log |
| [`affiliate-rag-bot/README.md`](../affiliate-rag-bot/README.md) | Engine setup & RAG flywheel |
| [`affiliate-rag-bot/AGENTS.md`](../affiliate-rag-bot/AGENTS.md) | Agent roster + global rules |
| [`affiliate-rag-bot/docs/ENGINE_GUIDE.md`](../affiliate-rag-bot/docs/ENGINE_GUIDE.md) | Product-fetching & content agent guide |
| [`affiliate-rag-bot/docs/AUTOPILOT_BLUEPRINT.md`](../affiliate-rag-bot/docs/AUTOPILOT_BLUEPRINT.md) | 10-phase architecture & G1–G18 |
| [`affiliate-rag-bot/agents/*.agents.md`](../affiliate-rag-bot/agents/) | 30 agent specs (incl. post-art-director AD1–AD11) |
| [`instagram_automation/app/agents/scene-prompt.agents.md`](../instagram_automation/app/agents/scene-prompt.agents.md) | Live image-prompt rules, palette rows, style presets |
| [`instagram_automation/README.md`](../instagram_automation/README.md) | IG Studio setup, generation & token economics |
| [`instagram_automation/business/README.md`](../instagram_automation/business/README.md), [`AGENTS.md`](../instagram_automation/business/AGENTS.md), [`META_INTEGRATION.md`](../instagram_automation/business/META_INTEGRATION.md) | Business-JK platform, constitution, Meta integration |
| [`creative-system.html`](../creative-system.html) | The Still Set creative system |

**Keeping the docs complete (the process):** every release goes through `scripts/release.sh`, which
now (1) updates the "Current stable version" line in `README.md` and this document, (2) appends the
release to the log in `RELEASES.md`, (3) commits those doc changes, then (4) tags and pushes. When a
feature changes an algorithm, agent, knob or route, update the matching section here (§8, §9, §12,
§14) and the agent's `*.agents.md` in the same commit.
