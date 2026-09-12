# Agent — Publishing Agent (queue + safe state machine + emergency stop)

**Role:** Own a durable publishing queue and a SAFE state machine so carousels are drafted,
scheduled, spaced, retried, and (if needed) halted — without the intelligence layer ever
touching Instagram directly. Phase 5 of the [Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md).
The IG automation service (`instagram_automation/`) executes; this agent decides order/timing/safety.

**Code anchors:** `publishing/queue.py` (`publish_jobs` + `system_flags` tables, state machine,
emergency stop, `next_due`, `account_health`), `server.py` (`/api/publishing/*`),
`config.PublishingConfig`.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `PUBLISH_MIN_INTERVAL` | `1200` | seconds between scheduled jobs (anti-shadowban, G4) |
| `PUBLISH_MAX_RETRIES` | `3` | attempts before a failed job → NEEDS_REVIEW |

---

## PA1 — State machine ✓
`DRAFT → QUEUED → SCHEDULED → RUNNING → PUBLISHED`; failures branch `FAILED →
NEEDS_REVIEW/retry`, and any pending state → `CANCELLED`. Illegal transitions are rejected.

## PA2 — Emergency stop (global) ✓
`POST /api/publishing/emergency-stop {on:true}` sets a `system_flags` row that blocks every
`→RUNNING` transition and makes `next_due` return nothing. Clear with `{on:false}`.

## PA3 — Spacing + retries (G4) ✓
`enqueue` auto-schedules each job `PUBLISH_MIN_INTERVAL` after the last pending one. A job that
fails is retried up to `PUBLISH_MAX_RETRIES`, then routed to `NEEDS_REVIEW` (never blind-retried
into an uncertain double-post — blueprint §41).

## PA4 — Separation of concerns ✓
This agent performs NO Instagram actions. The IG automation service polls `GET
/api/publishing/next`, executes, then reports back via `POST /api/publishing/transition`
(`RUNNING`→`PUBLISHED`/`FAILED`). Dry-run (G12) and emergency stop are honoured by the consumer.

## PA5 — Account health ✓
`GET /api/publishing/account` reports `HEALTHY / ATTENTION / STOPPED` from pending count, recent
failures, and the emergency flag. See [[account-safety]] for the fuller signal set (⏳ session/login).

## PA6 — JSON everywhere ✓
All endpoints take/return JSON; timestamps ISO-8601; jobs carry the full draft `payload`.
