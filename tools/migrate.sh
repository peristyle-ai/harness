#!/usr/bin/env bash
# Apply the harness's schema.
#
# The schema is ONE file: schema/init.sql. It creates the shared public
# tables and the agent's schema, and it is idempotent -- so re-running it
# against a live database changes nothing.
#
#   tools/migrate.sh                 # every agent the install knows about
#   tools/migrate.sh peribot         # just these agents
#
# The agents come from the install's config.yaml or from the command line.
#
# TWO FILES, IN THIS ORDER
#   1. schema/init.sql        the schema (per agent)
#   2. schema/grants.sql      the runtime role's permissions (per agent)
#
# The THIRD file, schema/extensions.sql, is NOT run here: pgvector's
# "vector" extension is NOT a trusted extension, so only a superuser may
# create it, and this script runs as the OPERATOR role. The install
# steps apply it once, as a superuser, before this script:
#
#   sudo -u postgres psql -d <db> -f schema/extensions.sql
#
# On a database where the extensions already exist that step is a no-op.
#
# The grants are separate because the runtime role's NAME is per-install
# (harness_app, agent_app, ...), so they cannot be baked into the
# schema.
#
# THE ROLE
# The schema is applied as the OPERATOR role (db.operator_role in
# config.yaml), not as the runtime role in db.url. The runtime role has
# DML rights only -- it cannot create a table. (And the password in
# db.url is the RUNTIME role's, so it would not authenticate the operator
# either.)
#
# The operator connects over the unix socket with PEER AUTH, which needs
# no password at all: the OS user must be the role's name. If peer auth
# is not available (a remote database, a different OS user), set
# db.operator_password in config.yaml and it is used over TCP instead.
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${HARNESS_CONFIG:-config.yaml}"
[ -f "$CONFIG" ] || { echo "no $CONFIG -- copy config.yaml.example to config.yaml" >&2; exit 1; }

# The values this script needs out of the config.
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
emit("CFG_RUNTIME_ROLE", db.get("runtime_role") or "")
emit("CFG_DB_NAME", db.get("database") or "")
PY
)"

: "${CFG_DB_URL:?db.url is not set in ${CONFIG}}"
: "${CFG_OPERATOR_ROLE:=harness_operator}"
: "${CFG_RUNTIME_ROLE:=harness_app}"

# Parse host/port/db from the URL.
HOST=$(echo "$CFG_DB_URL" | sed -E 's|^postgresql?://[^@]+@||; s|/.*$||')
PORT=$(echo "$HOST" | sed -E 's|^.*:||')
HOST=$(echo "$HOST" | sed -E 's|:.*$||')
DB=$(echo "$CFG_DB_URL" | sed -E 's|^.*@.*/||; s|\?.*$||')
[ -n "$DB" ] || DB="$CFG_DB_NAME"

echo "Migrating $DB as $CFG_OPERATOR_ROLE"

# The connection. Peer auth over the unix socket when there is no
# operator password; TCP with a password otherwise.
if [ -n "$CFG_OPERATOR_PASSWORD" ]; then
    CONNECT=(-h "$HOST" -p "${PORT:-5432}" -U "$CFG_OPERATOR_ROLE")
    export PGPASSWORD="$CFG_OPERATOR_PASSWORD"
else
    CONNECT=(-h /var/run/postgresql -U "$CFG_OPERATOR_ROLE")
fi

run_sql() {
    local file="$1"; shift
    psql "${CONNECT[@]}" -d "$DB" -v ON_ERROR_STOP=1 "$@" -f "$file"
}

# Which agents?
AGENTS="$*"
if [ -z "$AGENTS" ]; then
    # from the install's config
    AGENTS=$(python3 - "$CONFIG" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    with open(sys.argv[1]) as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception:
    sys.exit(0)
agents = (cfg.get("app") or {}).get("agents") or []
print(" ".join(str(a) for a in agents))
PY
)
    if [ -z "$AGENTS" ] && [ -n "${HARNESS_AGENTS:-}" ]; then
        AGENTS="$HARNESS_AGENTS"
    fi
fi

if [ -z "$AGENTS" ]; then
    echo "No agents given and none configured. Pass them as arguments:" >&2
    echo "  tools/migrate.sh peribot" >&2
    exit 1
fi

for AGENT_NAME in $AGENTS; do
    echo "  -> $AGENT_NAME"
    run_sql schema/init.sql -v "agent_name=$AGENT_NAME"
    run_sql schema/grants.sql -v "agent_name=$AGENT_NAME" \
                                -v "runtime_role=$CFG_RUNTIME_ROLE" \
                                -v "operator_role=$CFG_OPERATOR_ROLE"
done

echo "Schema applied."
