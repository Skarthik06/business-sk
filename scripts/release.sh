#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# release.sh — cut a new stable version from main (run locally) AND keep the docs current.
#
#   ./scripts/release.sh v2.4.2 "What changed in this release"
#
# It (1) updates the "Current stable version" in README.md and docs/MASTER.md,
#    (2) appends the release to the log in RELEASES.md,
#    (3) commits those doc changes to main and pushes,
#    (4) creates the annotated tag and pushes it.
# Then deploy on the server with:  ./scripts/deploy.sh v2.4.2
# Semantic versioning: vMAJOR.MINOR.PATCH (PATCH = fixes, MINOR = features, MAJOR = breaking).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${1:?usage: release.sh vX.Y.Z \"message\"}"
MSG="${2:-Release $TAG}"
case "$TAG" in v[0-9]*.[0-9]*.[0-9]*) ;; *) echo "Tag must look like v1.2.3"; exit 1;; esac
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then echo "Tag $TAG already exists"; exit 1; fi
TODAY="$(date +%Y-%m-%d)"

# 1) current-version lines
sed -i -E "s/^\*\*Current stable version:\*\* v[0-9]+\.[0-9]+\.[0-9]+/**Current stable version:** $TAG/" README.md
sed -i -E "s/^\| \*\*Version\*\* \| v[0-9]+\.[0-9]+\.[0-9]+ /| **Version** | $TAG /" README.md
sed -i -E "s/^\| \*\*Current stable version\*\* \| \*\*v[0-9]+\.[0-9]+\.[0-9]+\*\*/| **Current stable version** | **$TAG**/" docs/MASTER.md
sed -i -E "s/^\| \*\*Last updated\*\* \| [0-9-]+ \|/| **Last updated** | $TODAY |/" docs/MASTER.md

# 2) release log (newest first, right under the heading)
awk -v line="- **$TAG** ($TODAY) — $MSG" '
  { print }
  /^## Release history/ && !done { print line; done=1 }
' RELEASES.md > RELEASES.md.tmp && mv RELEASES.md.tmp RELEASES.md

# 3) commit the doc updates
git add README.md docs/MASTER.md RELEASES.md
if ! git diff --cached --quiet; then
  git commit -q -m "docs: release $TAG — $MSG"
  git push -q origin HEAD
fi

# 4) tag + push
git tag -a "$TAG" -m "$MSG"
git push -q origin "$TAG"
echo "✔ Released $TAG (docs updated). Deploy with: ./scripts/deploy.sh $TAG"
