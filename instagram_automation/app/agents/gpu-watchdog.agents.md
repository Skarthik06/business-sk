# GPU Watchdog agent

Watches the render GPUs and tells the owner — in the Studio and as an Android notification from SK
Helper — when Google Colab needs a human tap. It never starts, restarts or clicks anything in Colab:
Colab's free tier forbids automated control of the notebook, so the one action left to the owner is
**Open Colab → Run all** (with a saved Colab key, no code is needed).

Code: `app/services/gpu_watchdog.py` (started in `api.lifespan`), alerts surfaced by
`/api/sk/scenes` (Studio) and `/api/gpu/device/jobs` (SK Helper).

## Rules

| # | When | Alert | Actionable |
|---|---|---|---|
| W1 | Phone-started render jobs are waiting for Colab and Colab is offline | "Colab needed — open Colab → Run all" | yes |
| W2 | Colab was online and stopped answering (session ended, tab closed, Google reclaimed it) | "Colab stopped" | yes |
| W3 | Colab has run for `SK_WATCHDOG_WARN_HOURS` (free sessions end ≈12 h) | "Colab session ends soon" | yes |
| W4 | Colab came (back) online | "Colab is rendering" — clears W1/W2 | no |
| W5 | The saved Colab key expires within `SK_WATCHDOG_KEY_WARN_DAYS` | "Colab key expires soon — create a new one" | yes |

- One alert per rule per `SK_WATCHDOG_REPEAT_MINUTES` (no spam); alerts expire after 2 h.
- Every alert carries the Colab notebook URL so a tap opens it.
- The agent only reads status; it never touches the queue, tokens or Colab.

## Constraints (env, tunable)

| Env | Default | Meaning |
|---|---|---|
| `SK_WATCHDOG_TICK_SECS` | 30 | how often it checks |
| `SK_WATCHDOG_REPEAT_MINUTES` | 15 | minimum gap between two alerts of the same rule |
| `SK_WATCHDOG_WARN_HOURS` | 11 | W3 threshold (hours of continuous Colab uptime) |
| `SK_WATCHDOG_KEY_WARN_DAYS` | 7 | W5 threshold |
