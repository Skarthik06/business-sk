# Releases & production versioning

Production runs a **fixed, stable version tag** — never a moving branch — so it stays
predictable and can be rolled back instantly if a new release misbehaves.

## How it works
- **Develop** on `main` (all commits land here).
- **Cut a release** = create a `vX.Y.Z` git tag from `main`.
- **Production** (the Oracle server) runs a specific tag, recorded in `.deployed_version`.
- **Roll back** = deploy an older tag. Nothing is lost; tags are immutable snapshots.

Versioning is semantic: `vMAJOR.MINOR.PATCH` — bump **PATCH** for fixes, **MINOR** for
new features, **MAJOR** for breaking changes.

## Commands

Cut a new version (locally, after committing to main) — this also updates the version in
`README.md` + `docs/MASTER.md` and appends the release to the log below:
```bash
./scripts/release.sh v2.4.2 "What changed in this release"
```

Deploy to production (on the server, `~/business-sk`):
```bash
./scripts/deploy.sh v1.1.0     # a specific version
./scripts/deploy.sh            # the latest v* tag
```

Roll back if something breaks (on the server):
```bash
./scripts/rollback.sh          # to the previous version
./scripts/rollback.sh v1.0.0   # to a specific version
```

Check what production is running:
```bash
cat ~/business-sk/.deployed_version
```

## Release history
- **v2.25.4** (2026-10-07) — Cuelinks: price/rating/reviews/deals filters are strict and work again
- **v2.25.3** (2026-10-07) — Auto-DM: no follow requirement — every commenter gets the product links
- **v2.25.2** (2026-10-04) — Captions: no stray separator line
- **v2.25.1** (2026-10-04) — Flipkart/Shopsy posts: store-correct hashtags + captions; Go to Post to IG button; storefront names the real stores
- **v2.25.0** (2026-10-04) — Cuelinks Step 2 has the full Amazon Affiliate controls (categories, product types, goals, deals, filters, multi-post)
- **v2.24.2** (2026-10-04) — Cuelinks: Step 2 is driven only by the Step-1 AI plan
- **v2.24.1** (2026-10-04) — Cuelinks AI planner: Clear button
- **v2.24.0** (2026-10-04) — Cuelinks Affiliate: AI planner suggests stores + product searches, tap one to generate (like Amazon); own scraper only
- **v2.23.6** (2026-10-04) — Cuelinks wired end to end: Flipkart/Shopsy fetched free via your phone/laptop; DMs and slides name the real store
- **v2.23.5** (2026-10-04) — Daily posting limit code removed entirely
- **v2.23.4** (2026-10-04) — No posting limit; Style with AI asks first then opens Colab, never replaces a running Colab
- **v2.23.3** (2026-10-04) — Phone = Colab only: work started on the phone never goes to the laptop; Studio fix
- **v2.23.2** (2026-10-04) — Style with AI opens Colab automatically when needed and waits for it
- **v2.23.1** (2026-10-04) — Backdrop Composer: no lamps or softboxes painted into studio backdrops
- **v2.23.0** (2026-10-04) — Backdrop Composer agent: a unique, verified background for every post (own code + own model)
- **v2.22.1** (2026-10-04) — Nightly DB backup schedule installs on a fresh server
- **v2.22.0** (2026-10-04) — Studio database moved to the server's own Postgres (no egress limits) + nightly backups
- **v2.21.1** (2026-10-04) — Cut Supabase egress: light queries + short caches
- **v2.21.0** (2026-10-04) — Styled slides: full readable product details (our template, no AI text); cover without prices
- **v2.20.10** (2026-10-04) — AI Stylist: jacket/hoodie/polo naming
- **v2.20.9** (2026-10-04) — AI Stylist: product-type naming fixes
- **v2.20.8** (2026-10-04) — AI Stylist: works for every product type (names, wording, small items, 3-tile cover)
- **v2.20.7** (2026-10-04) — Content Studio: Style with AI + Post use an in-page confirm (never silently blocked)
- **v2.20.6** (2026-10-03) — Styled collage cover shows no prices
- **v2.20.5** (2026-10-03) — AI Stylist: readable product details band; black garments show folds
- **v2.20.4** (2026-10-03) — AI Stylist: no invented logos from background gaps/specks
- **v2.20.3** (2026-10-03) — Fix: Style with AI now shows in the preview and is what gets posted
- **v2.20.2** (2026-10-03) — Content Studio: ✨ Style with AI moved to the top publish bar
- **v2.20.1** (2026-10-03) — Content Studio: ✨ Style with AI button made prominent after a preview
- **v2.20.0** (2026-10-03) — Post Timing agent: 2 best times a day + strict 2-a-day limit; one-tap Colab for phones
- **v2.19.1** (2026-10-03) — Engagement: every commenter reached in a flood (resume where the last poll stopped), 4 handled at a time
- **v2.19.0** (2026-10-03) — Engagement scale: DB pool, activity-driven comment poller (all commenters, not just 50), rate-limit cooldown
- **v2.18.0** (2026-10-03) — Post to IG: a post that went live while the phone app was in the background leaves the queue; no double posting
- **v2.17.2** (2026-10-03) — AI Stylist: a logo the model drops is reported, never pasted at a guessed spot
- **v2.17.1** (2026-10-03) — AI Stylist: half-zip item names
- **v2.17.0** (2026-10-03) — AI Stylist: deterministic emblem detector decides medium vs low, free fidelity check, real logo restored when the model draws a different one
- **v2.16.2** (2026-10-03) — AI Stylist: real-logo reference + styled collage cover
- **v2.16.1** (2026-10-03) — AI Stylist: small logos detected reliably
- **v2.16.0** (2026-10-03) — AI Stylist: styled flat-lays (gpt-image-1-mini on free local surfaces), money-guarded
- **v2.15.1** (2026-09-30) — Colab backdrops ~3x faster; Content Studio renders only on click
- **v2.15.0** (2026-09-30) — GPU Watchdog agent + no-code Colab key; phone notifications; app 1.6.0
- **v2.14.4** (2026-09-30) — Overview: Render GPUs box with Get Colab code
- **v2.14.3** (2026-09-30) — Colab: never-stop by default (notebook setting), 12-hour session key
- **v2.14.2** (2026-09-30) — Worker tokens out of access logs; Colab verified on a T4
- **v2.14.1** (2026-09-28) — Sidebar: Business-SK first, Business-JK collapsed; honest rating filter
- **v2.14.0** (2026-09-28) — Cuelinks flow end to end: planner angle in captions, Goal ranking, constraints as defaults
- **v2.13.1** (2026-09-28) — Phone cut-outs report failures + fetch the ML Kit model; app 1.5.1
- **v2.13.0** (2026-09-28) — Device-aware rendering: phone → Colab, laptop → laptop GPU, with Studio notices
- **v2.12.0** (2026-09-28) — Render with Colab when the laptop GPU is off
- **v2.11.0** (2026-09-28) — Phone cut-outs (ML Kit) when the laptop GPU is off; QR with any camera; Studio app-update card; app 1.5.0
- **v2.9.0** (2026-09-28) — Your device scrapes (phone ↔ laptop) + IPs in Activity/Studio; app 1.3.0
- **v2.8.0** (2026-09-28) — App 1.2.0 (in-app updates, Business-SK / SK Helper names + icons); remove old workers
- **v2.7.1** (2026-09-28) — SK Studio Android app (the Studio full-screen) + Helper 1.1.0
- **v2.7.0** (2026-09-28) — Business-SK Helper Android app (download + QR/code/link pairing)
- **v2.6.3** (2026-09-28) — Flipkart products again: new price layout + no false 'Access Denied' challenge
- **v2.6.2** (2026-09-28) — Affiliate: posted/cleared posts leave the panels; Flipkart products work again (Scraper API false challenge)
- **v2.6.1** (2026-09-27) — Security: the affiliate API (/sk-api) now requires the Studio admin session (verified via the IG backend); service calls use an internal key; hub/storefront/health/worker endpoints stay open
- **v2.6.0** (2026-09-27) — Intelligent Multi-Route Scraper API: Strategy Engine (route scoring from domain history), circuit breakers, bounded failover, remote worker pool (laptop/phone) with heartbeats + leases, dashboard in the Studio; laptop is optional
- **v2.5.0** (2026-09-27) — Self-hosted Scraper API service (blueprint MVP): HTTP-first + Playwright, classification + bounded retry, disk cache, proxy registry, attempt log, metrics; affiliate engine uses it first
- **v2.4.9** (2026-09-27) — Scenes back to plain, elegant studio backdrops (no invented rooms); presets change light/tone only
- **v2.4.8** (2026-09-27) — /gallery preset made palette-neutral
- **v2.4.7** (2026-09-27) — Elegant scenes: quiet-luxury real interiors (boutique/gallery) replace photo-studio sets
- **v2.4.6** (2026-09-27) — Scraping can run fully online through a residential proxy (no laptop needed); laptop worker kept as fallback
- **v2.4.5** (2026-09-27) — Preview returns the post snapshot URLs (every view shows the same files)
- **v2.4.4** (2026-09-27) — One plan + one render per post (preview == post); GPU worker only uses the GPU when needed and fully releases it when idle
- **v2.4.3** (2026-09-26) — Last slide = Follow → Comment → DM process (no swipe); SK watermark on every slide
- **v2.4.2** (2026-09-26) — Docs: complete README + Master document; release process keeps docs current
- **v2.4.1** (2026-09-26) — One public reply + one DM per comment, ever (Redis claim)
- **v2.4.0** (2026-09-26) — 15 slash-command style presets (always applied, rotating); GPU heartbeat + one scene per poll, faster paints
- **v2.3.3** (2026-09-26) — LLM token optimisation (−60 % cost) + per-post token/cost breakdown in the Studio
- **v2.3.2** (2026-09-26) — AI decides the look (varied feed), override menu, "painting" status
- **v2.3.1** (2026-09-26) — Studio Look picker on its own row
- **v2.3.0** (2026-09-26) — Analyse-first Art Director, per-post AI scenes under the Scene Prompt Builder agent, palette Looks
- **v2.2.1** (2026-09-26) — Price-free mixed-aspect collage cover with numbered swipe hooks; face-based model-shot detection
- **v2.2.0** (2026-09-26) — AI Art Director: vision LLM + Z-Image scenes + BiRefNet cut-outs on the laptop GPU is the post format
- **v2.1.6** (2026-09-26) — Follow gate sends links to verified followers only (held + auto re-check); follow CTA in caption
- **v2.1.5** (2026-09-26) — Engagement prune-to-live + inbox cutoff; per-event lock stops double replies
- **v2.1.4** (2026-09-26) — No dead bands in any slide template; fallback handle @lostinframes0605.exe
- **v2.1.3** (2026-09-25) — Guarded store-reset endpoint + clean-slate launch
- **v2.1.2** — Amazon parser fixes (brand-as-title, missing review counts)
- **v2.1.1** — Fix top-heavy "Why We Love It" slide
- **v2.1.0** — Template/palette pickers, correct handle, storefront differentiation
- **v2.0.2** — Fixes the public dev-server error overlay
- **v2.0.1** — Verified zero-error audit
- **v2.0.0** — STABLE: free multi-store affiliate engine (16 Shopify brands, Shopsy, residential scrape worker)
- **v1.1.0 → v1.9.40** — Amazon proxy fetching, dashboard reorg, deep links, Template System v2, comment→DM closer, publish recovery, cut-outs, AI cover copy, storefront + Vercel, universal search, search-planner agent, Cuelinks v3 + Flipkart engine, follow gate (see `git tag -l 'v1.*' -n1`)
- **v1.0.0** — First stable release. Full Autopilot: Phases 1–10 (discovery, novelty,
  winner, trends, content intelligence, publishing queue, performance loop, learning,
  multi-retailer, prediction), the multi-page studio dashboard (Overview, Discover,
  Winners, Trends, Intelligence, Content Calendar, Content Studio, Revenue, Agents,
  Accounts, Storefront, History), encrypted affiliate-accounts panel, editable agents,
  Google login over HTTPS (nip.io + Caddy), stored-tag link building, IG-insights
  performance sync.
