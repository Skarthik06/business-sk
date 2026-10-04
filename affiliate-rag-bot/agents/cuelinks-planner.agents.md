# Cuelinks Planner agent

Step 1 of the **Cuelinks Affiliate** panel: picks WHERE to post from and WHAT to search for, so
Step 2 (the store generator) runs the same way as the Amazon Affiliate flow.

Code: `chains/cuelinks_planner.py` (`plan_markets`) · route `POST /api/cuelinks/plan` ·
catalogue `performance/cuelinks_markets.py` · UI `CuelinksPanel` + `StoreGenerate` in `BusinessSK.jsx`.

## Flow
1. **Niches**: the owner taps Focus categories (Fashion, Beauty, Grooming, …) + goal + audience.
2. **AI plan**: one structured LLM call ranks the eligible stores, each with a reason, a content
   angle and **3–4 product searches** inside those niches.
3. **Tap a search** → Step 2 opens that store with the search filled in → the AI filters load
   (multi-select, like Amazon) → Generate → real products (photos + prices) → Cuelinks links →
   pgvector dedup (only new) → AI copy → Post to IG.

## Constraints
| # | Rule |
|---|---|
| C1 | Only stores that give REAL products right now: not paused (`CUELINKS_PAUSED_ENGINES`, default `shopify` — Shopify rate-limits the server IP) and never Amazon (own panel, own Associates tag, no Cuelinks). |
| C2 | A Marketplace store (Flipkart, Shopsy) sells every niche, so it always passes the category filter. |
| C3 | The model may only return candidate ids; picks ≤ eligible stores; searches are cleaned (2–40 chars, no hashtags, deduped) and topped up to 3 from the niche table. |
| C4 | Pages are fetched ONLY by our own scraper (scraper service → phone/laptop on a home IP). No paid scraping API. |
| C5 | Any failure → a deterministic plan (goal-ranked, niche-table searches) — the panel always has picks. |
| C6 | Step 2 is driven ONLY by the Step-1 plan: its stores are the plan's picks (plan order), the plan's searches are shown in Step 2, a search is required before generating, and Clear resets Step 2. The server refuses paused stores even if called directly. |
