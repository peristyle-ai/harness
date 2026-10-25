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
#   database     : db.database in config.yaml
#   role         : db.operator_role -- the role that OWNS the schema.
#                  The runtime role in db.url usually cannot read the
#                  whole database, so it is not used for the dump.
#   password     : db.operator_password. When it is empty the script
#                  connects over the unix socket and relies on peer
#                  auth, which needs the OS user to have the same name
#                  as the role.
#   scope        : $HARNESS_BACKUP_SCHEMAS -- a space-separated list of
#                  schemas to dump. Leave it empty to dump the whole
#                  database, which is right when the database belongs
#                  to the harness alone. Set it when the database is
#                  SHARED with other applications and you only want
#                  the harness's own schemas, e.g.
#
#                    HARNESS_BACKUP_SCHEMAS="public peribot"
#
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${HARNESS_CONFIG:-config.yaml}"
[ -f "$CONFIG" ] || { echo "no $CONFIG -- copy config.yaml.example to config.yaml" >&2; exit 1; }

eval "$(python3 - "$CONFIG" <<'PY'
import shlex, sys
import yaml

cfg = yaml.safe_load(open(sys.argv[1])) or {}
db = cfg.get("db") or {}


def emit(name, value):
    print(f"{name}={shlex.quote(str(value))}")


emit("CFG_DB_URL", db.get("url") or "")
emit("CFG_OPERATOR_ROLE", db.get("operator_role") or "")
emit("CFG_OPERATOR_PASSWORD", db.get("operator_password") or "")
emit("CFG_DB_NAME", db.get("database") or "harness")
PY
)"

: "${CFG_DB_URL:?db.url is not set in ${CONFIG}}"
DB="$CFG_DB_NAME"

# Pull the parts out of the URL: postgresql://user:***@host:port/db
CONN="${CFG_DB_URL#postgresql://}"
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

BACKUP_ROLE="$CFG_OPERATOR_ROLE"
BACKUP_DIR="${1:-${HARNESS_BACKUP_DIR:-$HOME/harness-backups}}"

# Connection mode. A password means TCP (the password is a scram
# verifier, so it must go over the wire). No password means the unix
# socket, where peer auth applies and no password is needed.
PG_ARGS=(-d "$DB" -U "$BACKUP_ROLE")
if [ -n "$CFG_OPERATOR_PASSWORD" ]; then
    export PGPASSWORD="$CFG_OPERATOR_PASSWORD"
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
OUT="$BACKUP_DIR/${DB}-${TS}.sql.gz"

echo "[$(date -Iseconds)] backup start: $DB via $CONN_MODE -> $OUT"
if [ ${#SCHEMA_ARGS[@]} -gt 0 ]; then
    echo "[$(date -Iseconds)]   scope: ${HARNESS_BACKUP_SCHEMAS}"
fi

pg_dump "${PG_ARGS[@]}" "${SCHEMA_ARGS[@]}" --no-owner | gzip > "$OUT"

sha256sum "$OUT" > "$OUT.sha256"
echo "[$(date -Iseconds)] backup done: $OUT ($(stat -c %s "$OUT") bytes)"

# Retention: keep 14 days.
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +14 -delete 2>/dev/null || true
echo "[$(date -Iseconds)] retention sweep done"
