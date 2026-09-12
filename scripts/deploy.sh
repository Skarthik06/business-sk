#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — deploy a STABLE tagged release to production (run on the server).
#
#   ./scripts/deploy.sh              → deploy the latest v* tag (recommended)
#   ./scripts/deploy.sh v1.2.0       → deploy a specific version
#   ./scripts/deploy.sh main         → deploy the bleeding edge (not recommended)
#
# Production runs a fixed tag so it never moves unexpectedly; roll back any time
# with ./scripts/rollback.sh. The frontend container rewrites package-lock.json on
# start, so we discard that local change before switching versions.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

git checkout -- instagram_automation/frontend/package-lock.json 2>/dev/null || true
git fetch --tags --prune origin

TARGET="${1:-latest}"
if [ "$TARGET" = "latest" ]; then
  TARGET="$(git tag -l 'v*' --sort=-v:refname | head -1)"
  [ -n "$TARGET" ] || { echo "No v* tags found. Create one with scripts/release.sh"; exit 1; }
fi

echo "▶ Deploying $TARGET"
git checkout "$TARGET" 2>/dev/null || git checkout "origin/$TARGET"
echo "$TARGET" > .deployed_version

docker compose up -d --build
docker compose ps
echo "✔ Deployed $TARGET  (recorded in .deployed_version)"
