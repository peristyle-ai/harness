#!/bin/bash
# pg_dump of the harness database.
#
# Run as the install's own Unix user -- ideally from a systemd --user
# timer, so it runs as often as the database changes.
#
# Usage: tools/backup.sh [/path/to/backup/dir]
#
#   backup dir   : the argument, else $HARNESS_BACKUP_DIR, else
#                  ~/harness-backups
#   database     : $HARNESS_DB
#   role         : $APP_DB_OPERATOR_ROLE -- the role that OWNS the
#                  schema. The runtime role in DATABASE_URL usually
#                  cannot read the whole database, so it is not used
#                  for the dump.
#   password     : $APP_DB_OPERATOR_PASSWORD. When it is empty the
#                  script connects over the unix socket and relies on
#                  peer auth, which needs the OS user to have the same
#                  name as the role.
#   scope        : $HARNESS_BACKUP_SCHEMAS -- a space-separated list of
#                  schemas to dump. Leave it empty to dump the whole
#                  database, which is right when the database belongs
#                  to the harness alone. Set it when the database is
#                  SHARED with other applications and you only want
#                  the harness's own schemas, e.g.
#
#                    HARNESS_BACKUP_SCHEMAS="public wisp evie"
#
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

: "${DATABASE_URL:?DATABASE_URL not set}"
: "${HARNESS_DB:=harness}"

# Pull the parts out of the URL: postgresql://user:pass@host:port/db
CONN="${DATABASE_URL#postgresql://}"
CONN="${CONN#postgres://}"
DB_USER="${CONN%%@*}"
DB_PASS=""
case "$DB_USER" in
    *:*) DB_PASS="${DB_USER#*:}"; DB_USER="${DB_USER%%:*}" ;;
esac
HOSTPART="${CONN#*@}"
DB_HOST="${HOSTPART%%/*}"
DB_PORT="${HOSTPART#*/}"
DB_PORT="${DB_PORT%%/*}"
case "$DB_HOST" in
    *:*) DB_PORT="${DB_HOST##*:}"; DB_HOST="${DB_HOST%%:*}" ;;
esac

BACKUP_ROLE="${APP_DB_OPERATOR_ROLE:-$DB_USER}"
BACKUP_DIR="${1:-${HARNESS_BACKUP_DIR:-$HOME/harness-backups}}"

# Connection mode. A password means TCP (the password is a scram
# verifier, so it must go over the wire). No password means the unix
# socket, where peer auth applies and no password is needed.
PG_ARGS=(-d "$HARNESS_DB" -U "$BACKUP_ROLE")
if [ -n "${APP_DB_OPERATOR_PASSWORD:-}" ]; then
    export PGPASSWORD="$APP_DB_OPERATOR_PASSWORD"
    PG_ARGS+=(-h "$DB_HOST" -p "$DB_PORT")
    CONN_MODE="tcp $DB_HOST:$DB_PORT as $BACKUP_ROLE"
else
    PG_ARGS+=(-h /var/run/postgresql)
    CONN_MODE="unix socket as $BACKUP_ROLE"
fi

SCHEMA_ARGS=()
for s in ${HARNESS_BACKUP_SCHEMAS:-}; do
    SCHEMA_ARGS+=(-n "$s")
done

mkdir -p "$BACKUP_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/${HARNESS_DB}-${TS}.sql.gz"

echo "[$(date -Iseconds)] backup start: $HARNESS_DB via $CONN_MODE -> $OUT"
if [ ${#SCHEMA_ARGS[@]} -gt 0 ]; then
    echo "[$(date -Iseconds)]   scope: ${HARNESS_BACKUP_SCHEMAS}"
fi

pg_dump "${PG_ARGS[@]}" "${SCHEMA_ARGS[@]}" --no-owner | gzip > "$OUT"

sha256sum "$OUT" > "$OUT.sha256"
echo "[$(date -Iseconds)] backup done: $OUT ($(stat -c %s "$OUT") bytes)"

# Retention: keep 14 days.
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +14 -delete 2>/dev/null || true
echo "[$(date -Iseconds)] retention sweep done"
