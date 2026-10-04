# Post Timing agent

Picks the **two best times to post each day** and reminds you before each (guidance only — there is **no posting limit**; post as often as you like). Deterministic —
no AI tokens. Code: `app/services/post_timing.py` · `GET /api/sk/post-plan` (Studio) ·
`GET /api/gpu/device/post-plan` + `post_plan` in `/api/gpu/device/jobs` (phone) · Android
`worker/PostReminders.kt`.

## How the times are chosen

| Signal | Weight | Source |
|---|---|---|
| India Instagram peak hours (IST): lunch ~1 PM, evening ~8:30 PM (Fri ~7:45 PM, weekend ~12 PM / 8 PM) | base | built in |
| Your followers' online hours (`online_followers`) | up to 60 % | Instagram, from ~100 followers |
| Your posts' likes + comments by posting hour (settled posts, vs your median) | up to 40 % at ~40 posts | Instagram |

Slots: best half hour in **10:00–16:00** and best in **17:00–22:30** → always 2 posts ≥ 4 h apart.
Data refreshed every 6 h (2 Graph calls).

## Rules

| # | Rule |
|---|---|
| T1 | No posting limit (removed 2026-10-04 at the owner's request). `SK_MAX_POSTS_PER_DAY` (2) is only how many best-time slots and reminders a day gets; a 3rd post is allowed. |
| T2 | Phone reminder `SK_POST_REMIND_LEAD_MIN` (30) min before each slot — alarms live on the phone, so they ring even when the helper's worker is off. |
| T3 | Right before ringing, the phone re-checks the Studio: a slot already covered (or the day's 2 done) stays silent. |
| T4 | Tapping the reminder opens the Studio app in Content Studio (`/#sk-post`). |

Env: `SK_POST_TZ` (Asia/Kolkata) · `SK_MAX_POSTS_PER_DAY` (2) · `SK_POST_REMIND_LEAD_MIN` (30) ·
`SK_POST_ACCOUNT_ID` (default: the account with the affiliate posts).
