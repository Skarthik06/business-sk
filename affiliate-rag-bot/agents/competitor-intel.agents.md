# Agent — Competitor Intelligence (market-pattern signal)

**Role:** Maintain an opt-in watchlist of public creator accounts as a DISCOVERY SIGNAL — to
detect market patterns (recurring product types/categories), NEVER to copy content. Phase 9 of
the [Autopilot blueprint](../docs/AUTOPILOT_BLUEPRINT.md). Isolated from the core pipeline.

**Code anchors:** `competitor/store.py` (`competitor_watchlist` table), `server.py`
(`/api/competitors`), `config.CompetitorConfig`.

**Editable constraints:**
| Env var | Default | Meaning |
|---|---|---|
| `COMPETITOR_ENABLED` | `0` | off by default; `1` enables the watchlist + endpoints |

---

## CO1 — Opt-in + isolated ✓
Disabled by default; endpoints report `enabled:false` and do nothing until turned on. It never
touches the content/discovery pipeline unless explicitly wired.

## CO2 — Watchlist only, for now ✓
Stores handles + notes. Public-metadata collection needs a connected source; until then
`observations()` honestly returns `connected:false` — never fabricated engagement (G13).

## CO3 — Pattern detection, not cloning ✓
The goal is to detect what CATEGORIES/product types are trending among creators as a discovery
hint. Copying another creator's content is out of scope and prohibited (blueprint §66).

## CO4 — Future ⏳
Connect a compliant public-metadata source → topic detection, recurring-product detection, and
feed those as trend signals into [[trend-analyst]] / [[discovery-planner]].
