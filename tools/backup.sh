#!/bin/bash
# Daily pg_dump of the harness database to Seagate Expansion.
# Run as peristyle via systemd --user timer.
#
# Usage: tools/backup.sh [/path/to/backup/dir]
#   default dir: /media/mal/Expansion/Workspace backup/harness/
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

: "${DATABASE_URL:?DATABASE_URL not set}"
: "${HARNESS_DB:=harness}"

# Parse DB host from URL.
HOST=$(echo "$DATABASE_URL" | sed -E 's|^postgresql?://[^@]+@||; s|/.*$||')
PORT=$(echo "$HOST" | sed -E 's|^.*:||')
HOST=$(echo "$HOST" | sed -E 's|:.*$||')

BACKUP_DIR="${1:-/media/mal/Expansion/Workspace backup/harness}"
mkdir -p "$BACKUP_DIR"

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/${HARNESS_DB}-${TS}.sql.gz"

echo "[$(date -Iseconds)] backup start: $HARNESS_DB -> $OUT"
PGPASSWORD="$(echo "$DATABASE_URL" | sed -E 's|^postgresql?://[^:]+:||; s|@.*$||')" \
    pg_dump -h "$HOST" -p "${PORT:-5432}" -U peristyle -d "$HARNESS_DB" --no-owner | gzip > "$OUT"

sha256sum "$OUT" > "$OUT.sha256"
echo "[$(date -Iseconds)] backup done: $OUT ($(stat -c %s "$OUT") bytes)"

# Retention: keep 14 days.
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +14 -delete 2>/dev/null || true
echo "[$(date -Iseconds)] retention sweep done"
