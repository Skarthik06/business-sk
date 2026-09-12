# Agent — Performance Analyst (measured results → funnel metrics)

**Role:** Store observable results for every published post and derive the funnel + efficiency
metrics that tell us what actually worked. Phase 6 of the
[Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md). The data foundation for [[learning-agent]].

**Code anchors:** `performance/store.py` (`post_performance` table, `ingest`, `overview`,
`by_posts`, `outcome_score`), `server.py` (`/api/performance/*`), `config.PerformanceConfig`.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `PERFORMANCE_ENABLED` | `1` | master switch for the performance loop |
| `PERFORMANCE_LOOKBACK_DAYS` | `90` | window for aggregation |

---

## PF1 — Connected-source-only (G13) ✓
Metrics (`reach, impressions, likes, comments, shares, saves, profile_visits, link_clicks,
orders, commission`) are stored ONLY from a connected source via `POST /api/performance/ingest`
(IG insights, affiliate tracker). Unavailable ⇒ `None`; `overview` reports `connected: false`
and derived metrics stay null ("not connected"). Nothing is ever fabricated.

## PF2 — Funnel + efficiency ✓
`overview` derives `profile_visit_rate, product_ctr, conversion_rate, commission_per_click,
commission_per_post, commission_per_1000_reach` — each only when its inputs exist (an absent
upstream metric leaves the downstream one null, blueprint §28).

## PF3 — Outcome score ✓
`outcome_score` = weighted, log-scaled blend of AVAILABLE metrics (commission > orders > clicks
> saves > comments/likes). `None` when nothing usable. This is what the Learning agent averages.

## PF4 — Snapshots ✓
Each ingest is a snapshot; the latest per post wins in aggregation, so metrics can be re-ingested
as they mature. Timestamps ISO-8601.

## PF5 — JSON everywhere ✓
`/api/performance/overview|posts|categories` + `/api/performance/ingest` all take/return JSON.
