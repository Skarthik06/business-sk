# Phone as a scrape route (Android · Termux)

Your phone can be a **worker route** of the Scraper API, just like the laptop. When the laptop is off,
the Strategy Engine automatically sends Amazon / Flipkart jobs to the phone; when both are on, it
picks whichever is healthier. Mobile-data IPs are shared by many real users, so shopping sites rarely
block them. Nothing to configure on the server — the phone announces itself with a heartbeat.

**Data use:** pages are downloaded compressed (~0.3 MB per Amazon results page) — a normal product
run is roughly 5–10 MB. On home Wi-Fi it uses your home internet instead.

## One-time setup (about 10 minutes)
1. Install **Termux** from **F-Droid** (the Play Store version is outdated).
2. In Termux:
   ```bash
   pkg update && pkg install python curl
   curl -O https://raw.githubusercontent.com/Skarthik06/business-sk/main/scripts/scrape_worker.py
   ```
3. Put the worker token in a file (copy it from the laptop's `%USERPROFILE%\.sk_worker_token`; never
   share it or paste it into chats):
   ```bash
   nano ~/.sk_worker_token        # paste the token, save with Ctrl-O, exit with Ctrl-X
   chmod 600 ~/.sk_worker_token
   ```
4. Android settings → Apps → Termux → Battery → **Unrestricted** (so Android doesn't kill it).

## Run it
```bash
termux-wake-lock
SK_WORKER_ID=phone-01 SK_WORKER_KIND=phone python scrape_worker.py
```
Leave Termux open in the background. In the Studio → Business-SK → **Overview → Scraper routes**,
`phone-01` shows as **online**. Stop with Ctrl-C (or close Termux) — the server notices within
a minute and stops sending it jobs.

Optional: install **Termux:Boot** (F-Droid) and put the run command in `~/.termux/boot/start-worker`
to start the worker automatically when the phone boots.

## How routing treats the phone
* Heartbeat every 20 s: online < 60 s · stale 60–120 s · offline > 120 s. A worker that misses a job
  is taken out of rotation until its next heartbeat.
* Each worker is its own route with its own success/latency history per site, and its own circuit
  breaker — a flaky phone connection can't slow down the laptop route.
* Only outbound HTTPS to `https://140-238-247-18.nip.io/scraper-worker/*`, authenticated with the
  worker token — no ports opened on the phone.
