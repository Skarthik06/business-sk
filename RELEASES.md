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
