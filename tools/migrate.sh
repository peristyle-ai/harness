#!/usr/bin/env bash
# Apply the harness's schema.
#
# The schema is ONE file: schema/init.sql. It creates the shared public
# tables and the agent's schema, and it is idempotent -- so re-running it
# against a live database changes nothing.
#
#   tools/migrate.sh                 # every agent the install knows about
#   tools/migrate.sh wisp evie       # just these agents
#
# The agents come from the install config, from the .env, or from the
# command line.
#
# THREE FILES, IN THIS ORDER
#   1. schema/extensions.sql  the extensions (once, as a superuser)
#   2. schema/init.sql        the schema (per agent)
#   3. schema/grants.sql      the runtime role's permissions (per agent)
#
# Step 1 is separate because pgvector's "vector" extension is NOT a
# trusted extension -- only a superuser may create it -- and the schema
# uses its halfvec type. On a database where the extensions already
# exist, step 1 is a no-op and needs no privilege.
#
# Step 3 is separate because the runtime role's NAME is per-install
# (harness_app, peribot_app, ...), so the grants cannot be baked into
# the schema.
#
# THE ROLE
# The schema is applied as the OPERATOR role (APP_DB_OPERATOR_ROLE), not
# as the runtime role in DATABASE_URL. The runtime role has DML rights
# only -- it cannot create a table. (And the password in DATABASE_URL is
# the RUNTIME role's, so it would not authenticate the operator either.)
#
# The operator connects over the unix socket with PEER AUTH, which needs
# no password at all: the OS user must be the role's name. If peer auth
# is not available (a remote database, a different OS user), set
# APP_DB_OPERATOR_PASSWORD and it is used over TCP instead.
set -euo pipefail
cd "$(dirname "$0")/.."

# Load DATABASE_URL from .env if present
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

: "${DATABASE_URL:?DATABASE_URL not set (check .env)}"
: "${APP_DB_OPERATOR_ROLE:=peristyle}"
: "${APP_DB_RUNTIME_ROLE:=harness_app}"

# Parse host/port/db from URL.
HOST=$(echo "$DATABASE_URL" | sed -E 's|^postgresql?://[^@]+@||; s|/.*$||')
PORT=$(echo "$HOST" | sed -E 's|^.*:||')
HOST=$(echo "$HOST" | sed -E 's|:.*$||')
DB=$(echo "$DATABASE_URL" | sed -E 's|^.*@.*/||; s|\?.*$||')

echo "Migrating $DB as $APP_DB_OPERATOR_ROLE"

# The connection. Peer auth over the unix socket when there is no
# operator password; TCP with a password otherwise.
if [ -n "${APP_DB_OPERATOR_PASSWORD:-}" ]; then
    CONNECT=(-h "$HOST" -p "${PORT:-5432}" -U "$APP_DB_OPERATOR_ROLE")
    export PGPASSWORD="$APP_DB_OPERATOR_PASSWORD"
else
    CONNECT=(-h /var/run/postgresql -U "$APP_DB_OPERATOR_ROLE")
fi

run_sql() {
    local file="$1"; shift
    psql "${CONNECT[@]}" -d "$DB" -v ON_ERROR_STOP=1 "$@" -f "$file"
}

# Which agents?
AGENTS="$*"
if [ -z "$AGENTS" ]; then
    # from the install config, then from the .env
    if [ -f config.yaml ]; then
        AGENTS=$(python3 <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    with open("config.yaml") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception:
    sys.exit(0)
agents = (cfg.get("app") or {}).get("agents") or []
print(" ".join(str(a) for a in agents))
PY
)
    fi
    if [ -z "$AGENTS" ] && [ -n "${HARNESS_AGENTS:-}" ]; then
        AGENTS="$HARNESS_AGENTS"
    fi
fi

if [ -z "$AGENTS" ]; then
    echo "No agents given and none configured. Pass them as arguments:" >&2
    echo "  tools/migrate.sh wisp evie" >&2
    exit 1
fi

for AGENT_NAME in $AGENTS; do
    echo "  -> $AGENT_NAME"
    run_sql schema/init.sql -v "agent_name=$AGENT_NAME"
    run_sql schema/grants.sql -v "agent_name=$AGENT_NAME" \
                                -v "runtime_role=$APP_DB_RUNTIME_ROLE" \
                                -v "operator_role=$APP_DB_OPERATOR_ROLE"
done

echo "Schema applied."
