# Releases & production versioning

Production runs a **fixed, stable version tag** — never a moving branch — so it stays
predictable and can be rolled back instantly if a new release misbehaves.

## How it works
- **Develop** on `main` (all commits land here).
- **Cut a release** = create a `vX.Y.Z` git tag from `main`.
- **Production** (the Oracle server) runs a specific tag, recorded in `.deployed_version`.
- **Roll back** = deploy an older tag. Nothing is lost; tags are immutable snapshots.

Versioning is semantic: `vMAJOR.MINOR.PATCH` — bump **PATCH** for fixes, **MINOR** for
new features, **MAJOR** for breaking changes.

## Commands

Cut a new version (locally, after committing to main):
```bash
./scripts/release.sh v1.1.0 "What changed in this release"
```

Deploy to production (on the server, `~/business-sk`):
```bash
./scripts/deploy.sh v1.1.0     # a specific version
./scripts/deploy.sh            # the latest v* tag
```

Roll back if something breaks (on the server):
```bash
./scripts/rollback.sh          # to the previous version
./scripts/rollback.sh v1.0.0   # to a specific version
```

Check what production is running:
```bash
cat ~/business-sk/.deployed_version
```

## Release history
- **v1.0.0** — First stable release. Full Autopilot: Phases 1–10 (discovery, novelty,
  winner, trends, content intelligence, publishing queue, performance loop, learning,
  multi-retailer, prediction), the multi-page studio dashboard (Overview, Discover,
  Winners, Trends, Intelligence, Content Calendar, Content Studio, Revenue, Agents,
  Accounts, Storefront, History), encrypted affiliate-accounts panel, editable agents,
  Google login over HTTPS (nip.io + Caddy), stored-tag link building, IG-insights
  performance sync.
