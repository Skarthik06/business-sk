# Business-SK Android app

One APK (`com.businesssk.helper`) with two launcher icons:

| Icon | What it is |
|---|---|
| **Business-SK** (gold ✦ tile) | The whole Studio website full-screen (Trusted Web Activity, Chrome engine — Google sign-in works). Always the live site; never needs an app update for Studio changes. |
| **SK Helper** (dark ✦ tile) | The phone as a scraper route: pairs with the Studio, fetches Amazon / Flipkart / Shopsy pages from the phone's own connection. Home · Activity (pull down to refresh) · Settings. |

Download: <https://140-238-247-18.nip.io/helper/sk-helper.apk> (also: Studio → Overview → Scraper routes → *Get the Android app*).

## Which device scrapes

The Studio sends `X-SK-Device: phone | laptop` (from the browser it runs in). Run **Find products** on the
phone → the phone fetches first; on the laptop → the laptop. The other routes stay the fallback.
Each fetch is logged with the device and its public IP — in SK Helper → Activity (phone fetches) and in
Studio → Overview → Scraper routes → *Recent fetches* (all devices).

## Releasing an update (every app change)

1. Bump `versionCode` (+1) and `versionName` in `app/build.gradle.kts`.
2. Build + publish (JDK 17, Android SDK; sign on the SAME machine — its debug key signs every build):

   ```bash
   JAVA_HOME=<jdk17> ANDROID_SDK_ROOT=<sdk> bash android-helper/release.sh "What changed"
   ```

   It builds the release APK and uploads `sk-helper.apk` + `version.json` (code, name, url, sha256, size,
   notes) to `~/business-sk/helper-dist` on the server (Caddy serves `/helper/`). APK first, version file last.
3. On the phone: **SK Helper → Settings → App updates → Update to x.y.z** (Home also shows a banner).
   The app downloads the APK, checks its SHA-256 and installs it via PackageInstaller. The first update
   through the button asks to confirm; after that Android 12+ updates install without a prompt.

A build signed with a different key cannot update the installed app (uninstall + re-pair instead). The key's
SHA-256 is also in the Caddyfile's `/.well-known/assetlinks.json` (needed for the full-screen Studio icon).
