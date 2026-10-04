#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# db_backup.sh — nightly backup of every database on the server's Postgres (db container):
#   sk_studio (the Studio: accounts, automations, comments, leads…), affiliate_rag_bot, scraper_api
# Keeps the last 14 days in ~/backups/<date>/ as pg_dump custom-format files.
#   Restore one:  docker compose exec -T db pg_restore -U instagram -d <db> --clean --if-exists < file.dump
# Installed in the opc user's crontab by:  bash scripts/db_backup.sh --install
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--install" ]; then
  line="30 21 * * * cd $PWD && bash scripts/db_backup.sh >> $HOME/backups/backup.log 2>&1   # 03:00 IST"
  mkdir -p "$HOME/backups"
  ( { crontab -l 2>/dev/null || true; } | { grep -v "scripts/db_backup.sh" || true; } ; echo "$line" ) | crontab -
  echo "installed: $line"; exit 0
fi
DAY="$(date -u +%Y-%m-%d)"
OUT="$HOME/backups/$DAY"
mkdir -p "$OUT"
for DB in sk_studio affiliate_rag_bot scraper_api; do
  docker compose exec -T db pg_dump -U instagram -d "$DB" --format=custom > "$OUT/$DB.dump"
  echo "$(date -u +%FT%TZ) $DB $(du -h "$OUT/$DB.dump" | cut -f1)"
done
find "$HOME/backups" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
