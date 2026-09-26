# BUSINESS_SK — Agentic Instagram Affiliate Autopilot

**Current stable version:** v2.4.2

An end-to-end, agent-driven marketing system that **finds products** (Amazon, Flipkart, Shopsy,
16 Shopify brands, Cuelinks stores), **designs Instagram carousels with an AI Art Director**
(AI-painted scenes + the real product photos), **publishes** them, **DMs affiliate links to
verified followers** who comment "LINK", and keeps a **public storefront** of everything posted.
It runs 24/7 on an Oracle Cloud server, with the owner's laptop acting as a GPU worker (image
models) and a residential scrape worker.

> 📘 **The complete, detailed reference is [`docs/MASTER.md`](docs/MASTER.md)** — every algorithm,
> agent, route, table, knob, operation and the full version history.
> This README is the overview.

Private project — all rights reserved. Instagram: `@lostinframes0605.exe` (profile name "Aura Picks").

---

## At a glance

| | |
|---|---|
| **Version** | v2.4.2 (stable; production runs this tag) — history in [§ Versions](#versions) |
| **Built on** | Python · FastAPI · LangGraph/LangChain · OpenAI `gpt-5-nano` · PostgreSQL 18 + pgvector · Redis · React 19 + Vite 7 + Tailwind 4 · Playwright · PyTorch (CUDA 12.8) · Z-Image-Turbo · BiRefNet · YuNet · Docker Compose · Caddy |
| **Businesses** | **Business-SK** — affiliate autopilot (active) · **Business-JK** — real-estate Instagram platform (shares the stack) |
| **Cost per post (LLM)** | ≈ ₹0.05 (captions + art direction, fully itemised in the Studio) |

## How it works

```
Studio ─► Discover & scrape ─► Rank + dedup ─► ONE LLM call: captions/cover/hashtags
                                                         │
                                                         ▼
          AI Art Director (vision LLM): analyse products → pick look + 2–3 /style presets
                                         → write the scene prompt (rules in an agent file)
                                                         │
          Laptop GPU: BiRefNet cut-outs · YuNet face check · Z-Image paints the post's scene
                                                         │
Render (Playwright): collage cover (no names/prices) → product slides (full details) → closer
                                                         │
Publish to Instagram ─► comment "LINK" ─► follow gate ─► DM affiliate links (once per comment)
                                                         │
                          Storefront (link in bio) · performance loop → better discovery
```

## Key features
* **Multi-store product engine** — Amazon & Flipkart via a residential scrape worker (datacenter IPs
  are blocked), Shopsy direct, 16 Shopify D2C brands via their public feeds, Cuelinks deals.
* **Agentic discovery** — search-planner agent, goal-based ranking, strict brand filters with
  round-robin, count guarantee, two-layer uniqueness (never re-post a product).
* **AI Art Director** — analyses each product, decides the look (8 palettes incl. "Premium dark"),
  applies 2–3 of 15 slash-command style presets (`/premium /vintage /streetwear /cinematic …`),
  writes a structured scene prompt under live rules, and the laptop paints a unique scene per post.
  Product photos are never altered — only cut out and placed (verified 0 % pixel change).
* **One post format** — price-free mixed-aspect **collage cover** with numbered swipe hooks →
  product slides with truthful details (price, MRP, % off, rating, store) → "Want these?" closer.
* **Comment → DM automation** — per-post rules, 30 s poller + webhooks, **verified-followers-only**
  follow gate with automatic re-checks, and exactly **one public reply + one DM per comment**.
* **Storefront** — every posted product, categorised, with affiliate disclosure
  (`lostinframes-sk-store.vercel.app`).
* **Transparent cost** — tokens (input / cached / output / reasoning) and ₹/$ shown per post.
* **Everything is an agent** — 30 engine agent specs + 17 Business-JK charters, each editable
  (`*.agents.md`) with env-tunable knobs.

## Tech stack
| Layer | Technologies |
|---|---|
| Backend | Python 3.12, FastAPI 0.128, Uvicorn, Pydantic 2 |
| Agents / AI | LangGraph, LangChain, OpenAI `gpt-5-nano` (vision), sentence-transformers (all-MiniLM-L6-v2) |
| Data | PostgreSQL 18 + pgvector, SQLAlchemy 2, psycopg 3, Redis 7 |
| Rendering & scraping | Playwright/Chromium, Pillow, OpenCV, rembg |
| Image AI (laptop GPU) | PyTorch 2.11 + CUDA 12.8, diffusers, Z-Image-Turbo (4-bit), BiRefNet, YuNet |
| Frontend | React 19, Vite 7, Tailwind CSS 4, axios |
| Infra | Docker Compose, Caddy (auto-HTTPS), Oracle Cloud ARM, GitHub raw hosting, Vercel |
| Integrations | Instagram Graph API (Meta), Amazon Associates, Cuelinks API, Shopify feeds, Tavily (optional) |

Full dependency pins: [`requirements.txt`](requirements.txt), [`affiliate-rag-bot/requirements.txt`](affiliate-rag-bot/requirements.txt),
[`instagram_automation/requirements.txt`](instagram_automation/requirements.txt), [`instagram_automation/frontend/package.json`](instagram_automation/frontend/package.json).

## Requirements
* **Server:** Linux + Docker Compose (6 services: `db`, `backend`, `affiliate_backend`, `frontend`, `redis`, `caddy`).
* **GPU worker (optional, recommended):** NVIDIA GPU ≥ 8 GB VRAM, 16 GB RAM, ~15 GB disk; Python
  venv with PyTorch (CUDA 12.8), diffusers, transformers, bitsandbytes, OpenCV.
* **Scrape worker:** any always-on machine on a residential connection (Python stdlib only).
* **Accounts / keys** (in git-ignored `.env` files or the encrypted store — never committed):
  OpenAI, Meta/Instagram Graph API app + account tokens, Amazon Associates tag, Cuelinks,
  GitHub token (image hosting), Google OAuth client, worker token. Details: [MASTER §6](docs/MASTER.md#6-requirements).

## Quick start
```bash
# server (repo root)
docker compose up -d                       # db, backends, frontend, redis, caddy
docker compose logs -f backend affiliate_backend

# laptop workers (Windows) — auto-start at login via Startup-folder .vbs launchers
python scripts/scrape_worker.py            # residential Amazon/Flipkart fetches
%USERPROFILE%\sk-ai\venv\Scripts\python scripts\gpu_worker.py   # cut-outs + scenes
```
Open the Studio at `https://140-238-247-18.nip.io` → **Business-SK → Affiliate** (generate) →
**Content Studio** (preview, Look, Style commands, publish) → **Engagement** (automations, follow gate).

## Algorithms (summary)
| Algorithm | Where | Summary |
|---|---|---|
| Discovery & scraping | `affiliate-rag-bot/tools/*` | search-results scraping, worker queue, HTML parsers, Shopify feeds |
| Quality, scoring, ranking | `chains/discovery.py`, `tools/amazon.py` | quality gate, 0–100 scores, S–D tiers, goal ranking, combos |
| Uniqueness | `rag/dedup.py`, `rag/store.py` | within-scrape + cross-run ledger, pgvector novelty |
| Composition | `chains/compose.py` | one structured LLM call, canonical CTA, fact-check, tag bank |
| Art direction | `instagram_automation/app/services/art_director.py` | analyse → look → presets → structured scene → validated plan |
| Scene prompts | `instagram_automation/app/agents/scene-prompt.agents.md` | live rules, palette rows, 15 style presets |
| GPU jobs | `scene_store.py`, `scripts/gpu_worker.py` | file-backed queue, one scene per poll, heartbeat, tight cut-outs |
| Rendering | `sk_render.py` | collage cover, hero/float/split, truthful panel, Playwright PNGs |
| Engagement | `app/engagement/*` | rules engine, follow gate, once-per-comment Redis claim |

Deep dive for each: [MASTER §8](docs/MASTER.md#8-algorithms-in-detail).

## Operations
```bash
./scripts/release.sh v2.4.2 "What changed"   # local: updates README/MASTER/RELEASES, commits, tags, pushes
./scripts/deploy.sh v2.4.2                     # server: deploy that tag
./scripts/rollback.sh                          # server: previous tag
cat ~/business-sk/.deployed_version            # what production runs
```
Health: `/api/health` (both backends) · GPU: `/api/sk/scenes` · scraping: `/sk-api/api/scrape/worker-status`.

## Documentation map
| Document | What it covers |
|---|---|
| [`docs/MASTER.md`](docs/MASTER.md) | **Master document** — the complete project reference |
| [`AUDIT.md`](AUDIT.md) | Running build audit — sections 1–20 of what was built (infra, engine, secure posting, comment→DM, storefront, Still Set, rembg, GitHub migration …) |
| [`RELEASES.md`](RELEASES.md) | Versioning, deploy, rollback and the release log |
| [`affiliate-rag-bot/README.md`](affiliate-rag-bot/README.md) | Affiliate engine setup and the RAG flywheel |
| [`affiliate-rag-bot/AGENTS.md`](affiliate-rag-bot/AGENTS.md) | Agent roster and the global rules |
| [`affiliate-rag-bot/agents/`](affiliate-rag-bot/agents/) | 30 agent specs (`*.agents.md`), incl. `post-art-director` |
| [`affiliate-rag-bot/docs/ENGINE_GUIDE.md`](affiliate-rag-bot/docs/ENGINE_GUIDE.md) | Product-fetching & content agent — complete guide |
| [`affiliate-rag-bot/docs/AUTOPILOT_BLUEPRINT.md`](affiliate-rag-bot/docs/AUTOPILOT_BLUEPRINT.md) | 10-phase architecture blueprint + global rules G1–G18 |
| [`instagram_automation/app/agents/scene-prompt.agents.md`](instagram_automation/app/agents/scene-prompt.agents.md) | Live image-model prompt rules, palette rows, style presets |
| [`instagram_automation/README.md`](instagram_automation/README.md) | Instagram Studio setup, generation and token economics |
| [`instagram_automation/business/README.md`](instagram_automation/business/README.md) | Business-JK real-estate intelligence platform |
| [`instagram_automation/business/AGENTS.md`](instagram_automation/business/AGENTS.md) | Business-JK agent constitution (17 charters in `business/agents/`) |
| [`instagram_automation/business/META_INTEGRATION.md`](instagram_automation/business/META_INTEGRATION.md) | Meta / Instagram Graph API integration notes |
| [`creative-system.html`](creative-system.html) | The "Still Set" creative system (visual playbook) |

## Versions
| Version | Highlights |
|---|---|
| **v2.4.1** (current) | One public reply + one DM per comment, ever |
| v2.4.0 | 15 slash-command style presets (always applied, rotating); GPU heartbeat, faster paints |
| v2.3.x | Analyse-first Art Director, per-post AI scenes, palette Looks, AI decides the look, −60 % LLM cost + per-post cost display |
| v2.2.x | AI Art Director (AI scenes + real cut-outs) becomes the post format; price-free collage cover |
| v2.1.x | Pickers & palettes, parser fixes, clean-slate launch, verified-follower follow gate |
| v2.0.x | Free multi-store affiliate engine (Shopify brands, Shopsy, residential worker) |
| v1.x | Autopilot phases 1–10, Studio, storefront, Cuelinks, templates, comment→DM, follow gate |

Every release with its notes: [`RELEASES.md`](RELEASES.md) · [MASTER §17](docs/MASTER.md#17-version-history) · `git tag -l 'v*' -n1`.

## Security
Secrets live only in git-ignored `.env` files; Instagram tokens, DB URL and GitHub token are
encrypted at rest; every `/api/*` route needs a signed admin token (workers use their own token);
webhooks are signature-verified; this public repository contains no credentials.
