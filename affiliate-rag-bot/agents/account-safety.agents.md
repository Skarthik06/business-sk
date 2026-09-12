# Agent — Account Safety (health + emergency stop)

**Role:** Keep the Instagram account safe: surface a health signal and provide a global
emergency stop that halts all new publishing. Part of Phase 5 of the
[Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md). Works with [[publishing-agent]].

**Code anchors:** `publishing/queue.py` (`account_health`, `emergency_stop`, `is_stopped`,
`system_flags` table), `server.py` (`/api/publishing/account`, `/api/publishing/emergency-stop`),
`config.PublishingConfig`.

---

## AS1 — Health signal ✓
`GET /api/publishing/account` → `HEALTHY / ATTENTION / STOPPED` from pending job count, recent
failures/needs-review, and the emergency flag.

## AS2 — Emergency stop (global) ✓
`POST /api/publishing/emergency-stop {on:true}` blocks every `→RUNNING` transition and makes
`next_due` return nothing until cleared. This is the "🛑 disable new publishing" control.

## AS3 — Spacing + retry safety (G4) ✓
Publishing spacing (`PUBLISH_MIN_INTERVAL`) and bounded retries (`PUBLISH_MAX_RETRIES` →
`NEEDS_REVIEW`) live in [[publishing-agent]]; this agent is the safety/observability face of them.

## AS4 — Future ⏳
Richer signals — login/session status, recent automation errors, last successful post, last
verification — sourced from the IG automation service, plus per-account status when multiple
accounts exist (blueprint §42/§76).
