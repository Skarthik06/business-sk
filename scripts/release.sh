#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# release.sh — cut a new stable version tag from main (run locally).
#
#   ./scripts/release.sh v1.1.0 "Trend intelligence + accounts panel"
#
# Then deploy it on the server with:  ./scripts/deploy.sh v1.1.0
# Semantic versioning: vMAJOR.MINOR.PATCH (bump PATCH for fixes, MINOR for features).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${1:?usage: release.sh vX.Y.Z \"message\"}"
MSG="${2:-Release $TAG}"
case "$TAG" in v[0-9]*) ;; *) echo "Tag must look like v1.2.3"; exit 1;; esac

git tag -a "$TAG" -m "$MSG"
git push origin "$TAG"
echo "✔ Tagged $TAG and pushed. Deploy with: ./scripts/deploy.sh $TAG"
