# BUSINESS_SK — Agentic Instagram Affiliate Autopilot

**Current stable version:** v2.23.4

An end-to-end, agent-driven marketing system for the Instagram account **@lostinframes0605.exe**
("Aura Picks"). It **finds products** (Amazon, Flipkart, Shopsy, 16 Shopify brands, Cuelinks
stores), **designs Instagram carousels** (AI-painted scenes, or AI-styled flat-lays, around the
real product photos), **publishes** them at the **two best times of the day**, **DMs the real
affiliate links** to people who comment, and keeps a **public storefront** of everything posted.

It runs 24/7 on an **Oracle Cloud server**. The owner's **laptop GPU**, a free **Google Colab GPU**
and an **Android phone app** act as helper workers.

> 📘 Complete reference: [`docs/MASTER.md`](docs/MASTER.md) — every algorithm, route, table, knob and
> operation. Release log: [`RELEASES.md`](RELEASES.md). Build audit: [§ Audit](#audit--what-was-built-and-how-it-was-verified) below + [`AUDIT.md`](AUDIT.md).

Private project — all rights reserved.

---

## Contents
1. [At a glance](#at-a-glance)
2. [System architecture](#system-architecture)
3. [How a post is made](#how-a-post-is-made-end-to-end)
4. [How each part works](#how-each-part-works)
5. [Costs](#costs)
6. [Tech stack](#tech-stack)
7. [Setup & operations](#setup--operations)
8. [Audit — what was built and how it was verified](#audit--what-was-built-and-how-it-was-verified)
9. [Known limits](#known-limits)
10. [Documentation map](#documentation-map) · [Versions](#versions) · [Security](#security)

---

## At a glance

| | |
|---|---|
| **Version** | v2.23.4 (production runs this tag) · Android app **SK Helper 1.7.0** |
| **Server** | Oracle Cloud ARM (2 vCPU, 11 GB RAM), Docker Compose — 7 services |
| **Workers** | Laptop RTX 5050 (GPU + scraping) · Google Colab T4 (GPU) · Android phone (scraping, cut-outs, reminders) |
| **Database** | PostgreSQL 18 on the server (`sk_studio`, `affiliate_rag_bot`, `scraper_api`) + pgvector · Redis |
| **AI** | OpenAI `gpt-5-nano` (text + vision) · `gpt-image-1-mini` (AI Stylist, opt-in) · Z-Image-Turbo, BiRefNet, YuNet (own GPU, free) |
| **Agents** | 30 engine agent specs + 4 Studio agents (Scene Prompt, AI Stylist, GPU Watchdog, Post Timing) + 17 Business-JK charters |
| **Cost per post** | ≈ ₹0.05 of LLM text · AI Stylist (optional) ≈ $0.03–0.07 per 6-product post |
| **Businesses** | **Business-SK** — affiliate autopilot (active) · **Business-JK** — real-estate Instagram platform (shares the stack) |

---

## System architecture

```
                    ┌──────────────────────── Oracle Cloud server (24/7, Docker) ─────────────────────────┐
  Owner             │  caddy (HTTPS) ─► frontend  (Studio, React)                                          │
  laptop / phone ──►│                 ─► backend   (FastAPI: posting, rendering, AI Stylist, engagement,  │
  (Studio / app)    │                               GPU job queue, post ledger, post timing)               │
                    │                 ─► affiliate_backend (LangGraph: discovery, ranking, captions, RAG)  │
                    │                 ─► scraper_api (multi-route scraper: server / laptop / phone / proxy)│
                    │  db: PostgreSQL 18 + pgvector  ·  redis  ·  nightly backups (~/backups, 14 days)     │
                    └───────────────▲───────────────────▲──────────────────▲────────────────▲──────────────┘
                                    │ jobs (HTTPS poll) │                  │                │ Graph API
                 Laptop RTX 5050 ───┘     Colab T4 ─────┘   Android app ───┘     Instagram ─┘ (publish, comments, DMs)
                 (scenes, cut-outs,       (scenes, cut-outs  (scraping, ML Kit cut-outs,
                  scraping, backup copy)   when laptop off)   post reminders, Studio app)
```

* Workers only make **outbound HTTPS** calls (poll → lease → submit); nothing on the laptop/phone is exposed.
* **Device-aware rendering:** work started on the **phone renders on Colab**, work on the **laptop renders on
  the laptop GPU** (Colab if the laptop is off); the Studio says which device is rendering.

---

## How a post is made (end to end)

```
1 Discover   Studio → Affiliate / Cuelinks / Flipkart: search planner + AI filters → scrape (best route)
2 Rank       quality gate · 0–100 score · goal ranking · never re-post a product (pgvector + ledger)
3 Write      ONE LLM call → caption, cover hook, hashtags, comment CTA (fact-checked)
4 Direct     AI Art Director (vision) → look, palette, style presets, scene prompt
5 Paint      laptop GPU / Colab: Z-Image paints the scene · BiRefNet cuts out the products (free)
6 Style      optional ✨ Style with AI: every product as a styled flat-lay (paid, confirmed first)
7 Render     Playwright → collage cover (no names/prices) → product slides with the details card → closer
8 Publish    at the best time (phone reminder) · max 2 posts/day · server-side ledger, never twice
9 Engage     comment → public reply + DM with the REAL Amazon/Flipkart links (once per comment)
10 Store     storefront (link in bio) + dedup memory updated · performance feeds discovery
```

---

## How each part works

### 1. Product engine & Scraper API
* **Stores:** Amazon & Flipkart (via residential routes — datacenter IPs are blocked), Shopsy,
  16 Shopify D2C brands (public feeds), Cuelinks deals.
* **Scraper API** (`scraper_api/`, our own service): a Strategy Engine scores each route per site from
  its history (server, laptop worker, phone worker, proxy), with circuit breakers, bounded failover,
  caching and an attempt log. The **device you're working on scrapes first**.
* **Ranking:** quality gate (rating, reviews, price), 0–100 scores with S–D tiers, goal ranking
  (viral, intent, value, trending, fresh, commission), strict brand filters, count guarantee.
* **Uniqueness:** within-scrape dedup + cross-run ledger + pgvector novelty → a product is never posted twice.

### 2. AI Art Director & the GPUs
* A vision LLM analyses the products, picks the look (8 palettes) and 2–3 of 15 style presets, and
  writes a structured scene prompt under live rules (`scene-prompt.agents.md`).
* **Own image models, free:** Z-Image-Turbo paints the scene; BiRefNet cuts the products out
  (original pixels, alpha only); YuNet detects model shots. Product photos are never altered.
* **Where it runs:** laptop RTX 5050, or **Google Colab T4** when the laptop is off (one-tap notebook
  that works from a phone, saved key in Colab Secrets), or phone ML Kit for cut-outs.
* **GPU Watchdog agent:** alerts in the Studio and on the phone when Colab is needed, stopped, near its
  session end, or its key is expiring. It never automates Colab (against Colab's free-tier rules).

### 3. AI Stylist — styled flat-lays (opt-in, money-guarded)
Turns a product photo (often worn by a model) into a premium **top-down flat-lay** on a styled surface.
**Only the image itself is paid; every decision and check around it is our own deterministic code.**

| Step | How | Cost |
|---|---|---|
| Surface | stone / oak / linen / marble painted once by our own Z-Image model, reused forever | free |
| Read the product | colour (k-means) + product type from the title (40+ types) | free |
| Emblem detector | local-contrast blobs filtered by shape, on-fabric, outline sharpness, button rows, background gaps — calibrated on 74 real photos | free |
| Quality | **medium only for an emblem/symbol or a printed graphic, else low** | — |
| Style | `gpt-image-1-mini`, downscaled inputs, short prompt, real logo crop as reference, "no text" | ~$0.004–0.014 |
| Fidelity | colour must survive; a different logo is erased and the **real logo is pasted back**; black fabric gets a fill light so folds show | free |
| Slide | our own **details card** (brand, full name, rating, price, MRP, % off) — no AI text | free |

**Money rules:** nothing runs without the button + an in-page cost confirmation; every image cached
(never paid twice); daily cap ($0.30) and reserve floor on a local spend ledger (OpenAI has no balance API).

### 4. Rendering — one post format
Collage **cover with numbers only (never names or prices)** → product slides (AI scene or styled
flat-lay + the details card) → "Follow → comment → DM" closer, SK watermark. Preview **is** the post:
one plan and one saved render per post, invalidated automatically when the post is restyled.

### 5. Publishing — at the right time, never twice
* **Post Timing agent:** picks the **2 best times each day** (India Instagram peaks in IST, blended with
  your followers' online hours from ~100 followers and your own posts' engagement by hour). Weekdays
  ≈ 1:00 PM & 8:30 PM, weekends ≈ 11:30 AM & 8:00 PM.
* **Strictly 2 posts a day:** the server refuses a 3rd (posts in progress count too).
* **Phone reminders** 30 min before each slot; the phone re-checks first and stays silent if that post is done.
* **Server-side post ledger:** a post that went live while the phone app was in the background leaves
  the queue on return, gets recorded, and can never be published twice.

### 6. Engagement — comment → DM at scale
* Any comment on an affiliate post → **one public reply + one DM with the real product links**
  (product cards with the original photos and your tagged affiliate links), exactly once per comment
  (Redis claim + per-event lock + idempotent events).
* **Follow gate:** links go to verified followers (Instagram's `is_user_follow_business`), held and
  re-checked otherwise.
* **Two paths:** Meta webhooks (instant, own 6-worker pool) + a 30-second backup poller that makes **one**
  call for every post's comment count and only reads posts with new comments — newest first, resuming
  where a flood left off, 4 commenters at a time.
* **Meta rate limits:** sends pause per account (1 → 15 min) and resume automatically.
* **Database:** pooled connections; hot reads (rules, accounts, a post's products) cached briefly.

### 7. Storefront
Every posted product, categorised, with the real Amazon images and tagged links and an affiliate
disclosure — `lostinframes-sk-store.vercel.app` (link in bio), refreshed after each post.

### 8. Android app — SK Helper 1.7.0 + Business-SK Studio
* **Business-SK Studio:** the whole Studio as a full-screen app (Trusted Web Activity) — always the live version.
* **SK Helper:** the phone as a scraping route and ML Kit cut-out worker, GPU-watchdog alerts,
  **2 daily post reminders** (phone alarms, ring even when the worker is off), QR pairing, in-app updates.

### 9. Data, backups & egress
* All databases live on the server's own PostgreSQL (moved off Supabase on 2026-10-04 — no egress quota).
* **Backups:** nightly at 03:00 IST on the server (`scripts/db_backup.sh`, 14 days) and copied to the laptop
  every day at 10:00 / log-on (`scripts/pull_db_backups.ps1`, 30 days, checksums verified).
* Queries stay light: no large JSON on hot paths, short in-process caches (`app/cache.py`).

---

## Costs

| Item | Cost |
|---|---|
| Server, database, Colab, own image models, storefront hosting | **free** (Oracle free tier, Colab free tier, Vercel, GitHub) |
| LLM text per post (captions + art direction) | ≈ ₹0.05 (itemised per post in the Studio) |
| AI Stylist (optional) per product | low ≈ $0.0043 · medium ≈ $0.0139 (measured) |
| AI Stylist per 6-product post | ≈ $0.03 (all plain) – $0.08 (all logos); typical ≈ $0.045 |
| 2 styled posts a day | ≈ $0.09/day ≈ $2.70/month (daily cap $0.30) |

---

## Tech stack

| Layer | Technologies |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic 2 |
| Agents / AI | LangGraph, LangChain, OpenAI `gpt-5-nano` + `gpt-image-1-mini`, sentence-transformers |
| Computer vision (own code) | OpenCV, NumPy, Pillow — emblem detector, fidelity check, logo restore, fabric lift |
| Data | PostgreSQL 18 + pgvector, psycopg 3 (pooled), Redis 7 |
| Rendering & scraping | Playwright/Chromium, own multi-route Scraper API |
| Image AI (own GPUs) | PyTorch + CUDA, diffusers, Z-Image-Turbo, BiRefNet, YuNet |
| Frontend | React 19, Vite 7, Tailwind CSS 4 |
| Android | Kotlin, Jetpack Compose, ML Kit, WorkManager/AlarmManager, Trusted Web Activity |
| Infra | Docker Compose, Caddy (auto-HTTPS), Oracle Cloud ARM, Google Colab, GitHub raw hosting, Vercel |
| Integrations | Instagram Graph API, Amazon Associates, Cuelinks, Flipkart, Shopify feeds |

---

## Setup & operations

```bash
# server (repo root) — 7 services: db, redis, backend, frontend, affiliate_backend, scraper_api, caddy
docker compose up -d

# release + deploy (docs, tag and RELEASES.md are updated by the script)
./scripts/release.sh v2.22.2 "What changed"     # local
./scripts/deploy.sh v2.22.2                       # server (or: git checkout <tag>)
./scripts/rollback.sh                             # server: previous tag
cat ~/business-sk/.deployed_version               # what production runs

# Android app (bump versionCode/versionName first)
bash android-helper/release.sh "notes"            # builds + publishes; phones update in-app

# backups
bash scripts/db_backup.sh            # server: run a backup now (also nightly via cron)
bash scripts/db_backup.sh --install  # server: install the nightly schedule
powershell -File scripts/pull_db_backups.ps1 -Install   # laptop: daily copy task
docker compose exec -T db pg_restore -U instagram -d sk_studio --clean --if-exists < ~/backups/<date>/sk_studio.dump
```

* Studio: `https://140-238-247-18.nip.io` → **Business-SK → Affiliate** (find products) → **Content Studio**
  (posting plan, preview, ✨ Style with AI, publish) → **Engagement** (automations, follow gate).
* Colab GPU: open `colab/sk_gpu_worker.ipynb` in Colab (phone or laptop) and tap ▶.
* Health: `/api/health` · GPUs: `/api/sk/scenes` · posting plan: `/api/sk/post-plan` · stylist budget: `/api/sk/stylist/budget`.

---

## Audit — what was built and how it was verified

| Area | What was built | How it was verified |
|---|---|---|
| Product engine (v1–v2.0) | multi-store discovery, ranking, uniqueness, one-call captions, storefront | live posts; zero-error audit (v2.0.1) |
| Art Director & scenes (v2.2–v2.4) | vision art direction, per-post AI scenes, cut-outs, collage cover, presets | product pixels unchanged (0 % diff); per-post cost display |
| Scraper API (v2.5–v2.6) | self-hosted multi-route scraper with strategy engine | live Amazon/Flipkart/10 categories scraped through it |
| Android app (v2.7–v2.13) | SK Helper + Studio TWA, pairing, phone scraping & cut-outs, in-app updates | published builds, phone paired, cut-outs from the phone |
| Colab GPU (v2.12–v2.15) | render on Colab when the laptop is off, saved key, watchdog alerts | rendered on a T4 (cut-outs ~36 s); watchdog alert received |
| AI Stylist (v2.16–v2.21) | styled flat-lays with deterministic CV around one paid call | emblem detector calibrated on **74 real photos**; free end-to-end run on **10 product types / 40 products** (0 crashes, $0); real logo restore checked pixel-level; spend ledger exact |
| Publishing (v2.18–v2.20) | post ledger, 2-a-day limit, Post Timing agent, phone reminders | duplicate publish refused (409); 3rd post refused; plan for every weekday |
| Engagement scale (v2.19) | DB pool, activity-driven poller, flood resume, rate-limit cooldown | stress test with Instagram faked: **1,500 comments → 1,500 replies + 1,500 DMs, 0 duplicates**; 300 webhook comments delivered twice → once each; 8 DB connections peak |
| Real products (v2.19) | DMs and store use the original photos and real links | DM card image byte-identical to Amazon's; storefront 70/70 tagged Amazon links |
| Egress & database (v2.21–v2.22) | light queries + caches; move to the server's Postgres; nightly + laptop backups | DB traffic measured **≈ 6.7 MB → 0.1 MB per 5 min**; migration: 27 tables + 22 sequences identical; restore test passed; laptop copies checksum-identical |

Full per-release detail: [`RELEASES.md`](RELEASES.md) · earlier build sections 1–20: [`AUDIT.md`](AUDIT.md).

---

## Known limits
* **Public auto-DMs need Meta App Review (Live mode).** Until approved, only accounts with a role on the Meta app receive DMs.
* **Instagram limits automated DMs per account per hour**; beyond that, DMs are delivered later (within Instagram's 7-day window).
* **Colab free sessions end** after several hours or when the phone pauses the tab; the watchdog notifies, one tap restarts.
* **AI Stylist:** tone-on-tone tiny logos and same-colour back prints without "print" in the title use the cheaper
  low setting; the AI's picture quality for a brand-new product type isn't guaranteed — the checks catch most problems.
* **Posting times** use India-wide peaks until the account has ~100 followers / enough engagement data.

---

## Documentation map

| Document | What it covers |
|---|---|
| [`docs/MASTER.md`](docs/MASTER.md) | Master document — the complete project reference |
| [`RELEASES.md`](RELEASES.md) | Versioning, deploy, rollback and the release log |
| [`AUDIT.md`](AUDIT.md) | Build audit sections 1–22 |
| [`instagram_automation/app/agents/`](instagram_automation/app/agents/) | Studio agents: `ai-stylist`, `post-timing`, `gpu-watchdog`, `scene-prompt` |
| [`affiliate-rag-bot/agents/`](affiliate-rag-bot/agents/) | 30 engine agent specs (`*.agents.md`) |
| [`affiliate-rag-bot/docs/ENGINE_GUIDE.md`](affiliate-rag-bot/docs/ENGINE_GUIDE.md) | Product-fetching & content agent guide |
| [`affiliate-rag-bot/docs/AUTOPILOT_BLUEPRINT.md`](affiliate-rag-bot/docs/AUTOPILOT_BLUEPRINT.md) | 10-phase architecture blueprint + global rules |
| [`scraper_api/README.md`](scraper_api/README.md) | Self-hosted Scraper API |
| [`android-helper/README.md`](android-helper/README.md) | SK Helper + Studio Android app |
| [`colab/sk_gpu_worker.ipynb`](colab/sk_gpu_worker.ipynb) | One-tap Colab render GPU |
| [`instagram_automation/business/README.md`](instagram_automation/business/README.md) | Business-JK real-estate platform |
| [`creative-system.html`](creative-system.html) | The "Still Set" creative system |

---

## Versions

| Version | Highlights |
|---|---|
| **v2.22** (current) | Studio database on the server's own Postgres; nightly server + laptop backups |
| v2.21 | Supabase egress cut ~98 %; full readable details card; cover without prices |
| v2.20 | Post Timing agent (2 best times/day, strict 2-a-day), phone reminders, one-tap Colab; AI Stylist for every product type; in-page confirms |
| v2.19 | Engagement at scale: DB pool, activity-driven poller, flood resume, rate-limit cooldown |
| v2.18 | Server-side post ledger — no stuck or double posts |
| v2.16–v2.17 | AI Stylist: styled flat-lays, deterministic emblem detector, logo restore |
| v2.12–v2.15 | Colab GPU, device-aware rendering, GPU Watchdog, Android 1.6 |
| v2.5–v2.11 | Self-hosted Scraper API, Android SK Helper + Studio app, phone scraping & cut-outs |
| v2.0–v2.4 | Free multi-store engine, AI Art Director, collage cover, style presets, once-per-comment DMs |
| v1.x | Autopilot phases 1–10, Studio, storefront, Cuelinks, comment→DM, follow gate |

Every release: [`RELEASES.md`](RELEASES.md) · `git tag -l 'v*' -n1`.

---

## Security
* Secrets only in git-ignored `.env` files; Instagram tokens, DB URLs and the GitHub token encrypted at rest;
  this public repository contains no credentials and no database data (backups live only on the server and laptop).
* Every `/api/*` route needs the signed admin session; GPU/phone workers use their own tokens (kept out of logs);
  Meta webhooks are signature-verified.
* Paid AI never runs on its own: in-page confirmation, daily cap and reserve floor.
