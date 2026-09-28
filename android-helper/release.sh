#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# release.sh — build the Android app and publish it to the server, so the phone's
# Settings → App updates sees it and updates itself.
#
#   JAVA_HOME=<JDK 17> ./android-helper/release.sh "What changed"
#
# Bump versionCode + versionName in app/build.gradle.kts first. It builds the release APK,
# writes version.json (versionCode, versionName, url, sha256, size, notes) and uploads both to
# ~/business-sk/helper-dist on the server (Caddy serves them at /helper/). The APK must be signed
# with the SAME key as the installed app (this machine's debug key) or Android refuses the update.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

NOTES="${1:-}"
HOST="opc@140.238.247.18"
KEY="${SK_SSH_KEY:-$HOME/.ssh/oracle_business_sk}"
BASE_URL="https://140-238-247-18.nip.io/helper"
SDK="${ANDROID_SDK_ROOT:-${LOCALAPPDATA:-}/Android/Sdk}"

./gradlew --no-daemon -q assembleRelease
APK=app/build/outputs/apk/release/app-release.apk

AAPT2="$(ls "$SDK"/build-tools/*/aapt2.exe "$SDK"/build-tools/*/aapt2 2>/dev/null | tail -1)"
[ -n "$AAPT2" ] || { echo "aapt2 not found under $SDK/build-tools (set ANDROID_SDK_ROOT)"; exit 1; }
BADGING="$("$AAPT2" dump badging "$APK" | head -1)"
CODE="$(sed -n "s/.*versionCode='\([0-9]*\)'.*/\1/p" <<<"$BADGING")"
NAME="$(sed -n "s/.*versionName='\([^']*\)'.*/\1/p" <<<"$BADGING")"
SHA="$(sha256sum "$APK" | cut -d' ' -f1)"
SIZE="$(wc -c < "$APK" | tr -d ' ')"

OUT="$(mktemp -d)"
cp "$APK" "$OUT/sk-helper.apk"
python - "$OUT/version.json" "$CODE" "$NAME" "$BASE_URL/sk-helper.apk" "$SHA" "$SIZE" "$NOTES" <<'PY'
import json, sys
path, code, name, url, sha, size, notes = sys.argv[1:]
json.dump({"versionCode": int(code), "versionName": name, "url": url, "sha256": sha,
           "size": int(size), "notes": notes}, open(path, "w"), indent=2)
PY

# APK first, version.json last — a phone never sees a version whose APK isn't there yet
ssh -i "$KEY" "$HOST" 'mkdir -p ~/business-sk/helper-dist'
scp -q -i "$KEY" "$OUT/sk-helper.apk" "$HOST:business-sk/helper-dist/sk-helper.apk.new"
ssh -i "$KEY" "$HOST" 'mv ~/business-sk/helper-dist/sk-helper.apk.new ~/business-sk/helper-dist/sk-helper.apk'
scp -q -i "$KEY" "$OUT/version.json" "$HOST:business-sk/helper-dist/version.json"
rm -rf "$OUT"
echo "✔ Published SK Helper $NAME ($CODE) — $BASE_URL/sk-helper.apk"
