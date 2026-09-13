# Agent — Seasonal Planner (festival & seasonal deals)

**Role:** Know the calendar — Indian festivals + seasons — and steer the engine toward the
categories, keywords, and caption angle that sell best right now (Diwali gifts, winter wear,
Navratri outfits, monsoon picks…). An extra intelligence layer on the
[Autopilot](../docs/AUTOPILOT_BLUEPRINT.md).

**Code anchors:** `seasons.py` (curated `FESTIVALS` + `SEASONS`, `context()`), `server.py`
(`/api/seasons`), `graph/nodes.py` (`compose_pins` passes the angle to the ONE LLM call),
`chains/compose.py` (`OCCASION` in the prompt), frontend Discover **seasonal banner**.

---

## SP1 — Offline curated calendar ✓
Festival dates + month-based seasons live in `seasons.py` — no external API. Each entry maps to
`categories`, `keywords`, and a caption `angle`. Dates are approximate (lunar festivals shift);
refresh `FESTIVALS` yearly. `context()` returns the current season + nearest festival (≤45 days)
+ merged suggested categories/keywords + a headline.

## SP2 — Steers content, doesn't fabricate (G13) ✓
The season/festival `angle` is fed to the single caption call so the hook + a hashtag lean into
the occasion — but it must be **natural** and never invent a "sale" or discount that the product
data doesn't support. Prices/discounts still come only from real scraped fields.

## SP3 — One-click category push ✓
The Discover banner shows what's coming up and offers **"Select these"** to auto-select the
suggested categories. Optional: fold seasonal keywords into discovery rotation (⏳).

## SP4 — No extra LLM call (G10) ✓
The angle rides the existing structured compose call — a single string in the prompt.

## SP5 — JSON ✓
`GET /api/seasons` → `{season, nearest_festival, upcoming[], suggested_categories,
suggested_keywords, angle, headline}`.

## SP6 — Future ⏳
Auto-schedule festival campaigns into the Content Calendar; pull Amazon sale-event dates; learn
which festivals historically converted best (via the performance loop).
