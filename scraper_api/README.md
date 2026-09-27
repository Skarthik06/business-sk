# Business-SK Scraper API

Self-hosted, ScraperAPI-style extraction service (MVP of the *Open-Source Scraper API* blueprint).
Runs as the `scraper_api` Docker service — **internal only** (no public port); the affiliate engine
calls `http://scraper_api:8200` with an `X-API-Key`.

## What it does
`URL → domain strategy → cache → HTTP (httpx) or browser (Playwright) → classify → bounded retry → extract → cache + log`

| Piece | Where |
|---|---|
| API gateway (auth, per-key rate limit, validation) | `app/main.py` |
| Domain strategies (mode, timeouts, min page size, challenge markers, residential) | `strategies/domains.yaml`, `app/strategies.py` |
| HTTP worker (streamed, size-limited, SSRF check on every redirect) | `app/engine.py` |
| Browser worker (lazy Chromium, context per request, heavy resources blocked, idle close) | `app/browser.py` |
| Classifier + retry (2xx / 403 / 429 / 5xx / timeout / challenge, backoff + jitter, Retry-After) | `app/classifier.py` |
| Extractors (title, metadata, JSON-LD, price, image URLs + normaliser) | `app/extractors.py` |
| Proxy registry (encrypted creds, health checks, lease, cooldown) | `app/proxies.py` |
| Cache (gzip JSON on disk; Redis holds only pointers + TTL) | `app/cache.py` |
| Postgres: api_keys, scrape_jobs, scrape_attempts, proxies, usage_daily | `app/store.py` |
| Prometheus metrics | `app/metrics.py`, `GET /metrics` |

**Retry rule:** transient errors (429 / 5xx / network) are retried after a backoff; a block or challenge
page is only retried on a *different route* (browser instead of HTTP, or another proxy) — never hammered.
**Residential sites** (Amazon, Flipkart block datacenter IPs): with no healthy proxy the API answers
`NEEDS_RESIDENTIAL` immediately and the affiliate engine uses the laptop worker instead.

## Endpoints
`GET /v1/scrape?url=…&render=&extract=html,title,images,metadata,jsonld,price,links&cache_ttl=&timeout=&country=&proxy=`
· `POST /v1/jobs` · `GET /v1/jobs/{id}` · `GET /v1/health` · `GET /v1/usage` · `GET /v1/domains/{domain}`
· `GET /v1/stats` · `GET|POST|DELETE /v1/admin/proxies` · `GET /metrics`

## Setup (server)
`scraper_api/.env` (git-ignored) — see `.env.example`: `SCRAPER_API_KEYS`, optional `SCRAPER_SECRET_KEY`,
optional proxy `SCRAPER_PROXY_SERVER/USER/PASS`. The affiliate engine needs `SCRAPER_API_URL` +
`SCRAPER_API_KEY` in `affiliate-rag-bot/.env`.

```bash
docker compose up -d --build scraper_api
docker compose exec scraper_api python -m pytest -q tests
```

## Security
Key hashes only in the DB · proxy credentials encrypted (Fernet) and never returned or logged ·
http/https only · SSRF guard (loopback, private, link-local, metadata addresses blocked, also on
redirects and browser sub-requests) · response-size limit · per-key rate limit · non-root container.
Use only for sites and data you are allowed to access.
