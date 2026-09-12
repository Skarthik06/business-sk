#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# rollback.sh — instantly return production to a previous stable version.
#
#   ./scripts/rollback.sh            → roll back to the PREVIOUS v* tag
#   ./scripts/rollback.sh v1.1.0     → roll back to a specific version
#
# Use when a new release misbehaves. It just deploys an older tag.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --tags --prune origin >/dev/null 2>&1 || true

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  # second-newest tag = the previous release
  TARGET="$(git tag -l 'v*' --sort=-v:refname | sed -n '2p')"
  [ -n "$TARGET" ] || { echo "No previous tag to roll back to."; exit 1; }
fi
echo "⏪ Rolling back to $TARGET"
exec "$(dirname "$0")/deploy.sh" "$TARGET"
