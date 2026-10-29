#!/bin/bash
# install.sh — install the Peristyle Harness on this machine.
#
#   sudo ./install.sh
#
# The ONLY thing you have to supply is `config.yaml`: this install's own
# config, in the directory this script lives in. Everything else — the
# database, the roles, the schema, the admin account, the service — is
# derived from it.
#
# WHAT IT DOES, IN ORDER
#   1. checks the preconditions (root, config.yaml, the repo's files)
#   2. makes the Python environment
#   3. reads config.yaml and refuses to go on if it is not a working
#      config (readable by others, a value still holding a placeholder)
#   4. creates the database roles and the database, and installs the
#      PostgreSQL extensions -- as the postgres superuser
#   5. applies the schema and the runtime role's grants -- as the
#      OPERATOR role, which is what owns the objects
#   6. renders the agent's identity files, if they are not there yet
#   7. creates the first admin
#   8. installs and starts the systemd --user service
#
# EVERY STEP IS IDEMPOTENT. Re-running it on a working install changes
# nothing except the two role passwords, which are set to whatever
# config.yaml says — so the config and the database cannot drift apart.
#
# OPTIONS
#   --admin-email EMAIL      the first admin's login (default: admin@example.com)
#   --admin-password PW      its password (default: generated, and printed)
#   --operator-name NAME     the operator's name, for the identity files
#   --operator-email EMAIL   the operator's email, for the identity files
#   --operator-timezone TZ   the operator's timezone, for the identity files
#   --dry-run                print what would happen, change nothing
set -euo pipefail

# --- who is running this -------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must be run as root:  sudo ./install.sh" >&2
    exit 1
fi
INSTALL_USER="${SUDO_USER:-}"
if [ -z "$INSTALL_USER" ] || [ "$INSTALL_USER" = "root" ]; then
    echo "Run this as the install's own user, with sudo:  sudo ./install.sh" >&2
    echo "(running it as root leaves no way to know whose home to install into)" >&2
    exit 1
fi
INSTALL_UID="$(id -u "$INSTALL_USER")"
INSTALL_HOME="$(getent passwd "$INSTALL_USER" | cut -d: -f6)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- options -------------------------------------------------------------
ADMIN_EMAIL="admin@example.com"
ADMIN_PASSWORD=""
OPERATOR_NAME="the operator"
OPERATOR_EMAIL="operator@example.com"
OPERATOR_TIMEZONE="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"
[ -n "$OPERATOR_TIMEZONE" ] || OPERATOR_TIMEZONE="UTC"
DRY_RUN="no"
# The embedding columns are halfvec; the halfvec type arrived in
# pgvector 0.7.0.
MIN_VECTOR_VERSION="0.7.0"

while [ $# -gt 0 ]; do
    case "$1" in
        --admin-email)       ADMIN_EMAIL="${2:?--admin-email needs a value}"; shift 2 ;;
        --admin-password)    ADMIN_PASSWORD="${2:?--admin-password needs a value}"; shift 2 ;;
        --operator-name)     OPERATOR_NAME="${2:?--operator-name needs a value}"; shift 2 ;;
        --operator-email)    OPERATOR_EMAIL="${2:?--operator-email needs a value}"; shift 2 ;;
        --operator-timezone)  OPERATOR_TIMEZONE="${2:?--operator-timezone needs a value}"; shift 2 ;;
        --dry-run)           DRY_RUN="yes"; shift ;;
        -h|--help)           sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 1 ;;
    esac
done

say()  { echo; echo "==> $*"; }
info() { echo "    $*"; }
die()  { echo; echo "ERROR: $*" >&2; exit 1; }

# run something as the install's user
as_user() { runuser -u "$INSTALL_USER" -- "$@"; }

if [ "$DRY_RUN" = "yes" ]; then
    say "DRY RUN — nothing will be changed"
fi

# --- 1. preconditions ----------------------------------------------------
say "1/8  preconditions"
cd "$REPO"

[ -f config.yaml ] || die "no config.yaml in $REPO.
    Copy config.yaml.example to config.yaml, fill in the YOUR_..._HERE
    values, and chmod 600 it."

CONFIG_MODE="$(stat -c %a config.yaml)"
if [ "$(( 0${CONFIG_MODE} & 0044 ))" -ne 0 ]; then
    die "config.yaml is readable by others (mode $CONFIG_MODE).
    It holds the API keys and the database password.  chmod 600 config.yaml"
fi
info "config.yaml   mode $CONFIG_MODE"

for f in schema/init.sql schema/grants.sql schema/extensions.sql \
         tools/migrate.sh tools/create_user.py systemd/harness.service \
         agent_template/IDENTITY.md.template requirements.txt; do
    [ -e "$f" ] || die "this does not look like a harness checkout: $f is missing"
done
info "the checkout  $REPO"

command -v python3 >/dev/null || die "python3 is not installed"
command -v runuser >/dev/null || die "runuser is not installed (util-linux)"
info "the user      $INSTALL_USER (uid $INSTALL_UID), home $INSTALL_HOME"

# --- 2. the Python environment ------------------------------------------
say "2/8  the Python environment"
if [ ! -x "$REPO/.venv/bin/python" ]; then
    if [ "$DRY_RUN" = "yes" ]; then
        info "would create $REPO/.venv and install requirements.txt"
    else
        as_user python3 -m venv "$REPO/.venv" \
            || die "could not create the virtualenv. Is python3-venv installed?"
        info "created .venv"
        as_user "$REPO/.venv/bin/pip" install --upgrade pip -q
        as_user "$REPO/.venv/bin/pip" install -r "$REPO/requirements.txt" -q \
            || die "could not install the dependencies"
        info "installed the dependencies"
    fi
else
    info ".venv already exists — keeping it"
fi
PY="$REPO/.venv/bin/python"
if [ ! -x "$PY" ]; then
    # No virtualenv yet. A real install creates it in the step above; a
    # dry run has not, so fall back to the system python for the CONFIG
    # READ (which needs only pyyaml) and skip the app's own check, which
    # needs the app's dependencies.
    if python3 -c "import yaml" >/dev/null 2>&1; then
        PY="python3"
        info "no .venv yet — reading the config with the system python"
    else
        die "the virtualenv has no python interpreter, and the system python3
    has no pyyaml either. Install python3-yaml, or run the install for real."
    fi
fi

# --- 3. read + validate config.yaml --------------------------------------
say "3/8  the config"
{
    if [ -x "$REPO/.venv/bin/python" ]; then
        # The app's own check: mode, required values, placeholders.
        as_user "$REPO/.venv/bin/python" - "$REPO/config.yaml" <<'PYEOF' || die "config.yaml is not a working config (see above)"
import sys
from app.security import sanity_check_config
sanity_check_config(sys.argv[1])
print("    the config passed the app's own startup check")
PYEOF
    else
        info "skipped the app's own check (no virtualenv yet)"
    fi

    # And pull the values out of it.
    eval "$(as_user "$PY" - "$REPO/config.yaml" <<'PYEOF'
import shlex, sys
from urllib.parse import urlparse, unquote
import yaml

cfg = yaml.safe_load(open(sys.argv[1])) or {}
app = cfg.get("app") or {}
db = cfg.get("db") or {}

def emit(name, value):
    print(f"{name}={shlex.quote(str(value))}")

url = urlparse(db.get("url") or "")
emit("DB_NAME", db.get("database") or "")
emit("DB_HOST", url.hostname or "127.0.0.1")
emit("DB_PORT", url.port or 5432)
emit("RUNTIME_ROLE", db.get("runtime_role") or "")
emit("RUNTIME_PASSWORD", unquote(url.password or ""))
emit("OPERATOR_ROLE", db.get("operator_role") or "")
emit("OPERATOR_PASSWORD", db.get("operator_password") or "")
emit("DATABASE", db.get("url") or "")
emit("AGENT_NAME", app.get("default_agent") or "")
emit("AGENTS", " ".join(app.get("agents") or []))
emit("BIND", app.get("bind") or "127.0.0.1:5010")
emit("PIE_ROOT", app.get("pie_root") or "pie")
PYEOF
)"
    info "agent         ${AGENT_NAME:-?}"
    info "database      ${DB_NAME:-?} (operator role ${OPERATOR_ROLE:-?}, runtime role ${RUNTIME_ROLE:-?})"
    info "bind          ${BIND:-?}"
}

[ -n "${DB_NAME:-}" ] || die "db.database is not set in config.yaml"
[ -n "${OPERATOR_ROLE:-}" ] || die "db.operator_role is not set in config.yaml"
[ -n "${RUNTIME_ROLE:-}" ] || die "db.runtime_role is not set in config.yaml"
[ -n "${RUNTIME_PASSWORD:-}" ] || die "db.url has no password for the runtime role"

if [ -z "${OPERATOR_PASSWORD:-}" ] && [ "$INSTALL_USER" != "$OPERATOR_ROLE" ]; then
    die "db.operator_password is empty, so the operator role authenticates with
    PEER AUTH over the unix socket — which means the OS USER must have the
    same name as the role.

    Either:
      * set db.operator_password in config.yaml (a password, and the
        operator connects over TCP instead), or
      * run the install as the '$OPERATOR_ROLE' user."
fi

# --- 4. postgres ---------------------------------------------------------
say "4/8  the database"
if ! command -v psql >/dev/null; then
    die "psql is not installed — PostgreSQL is needed on this machine"
fi
if ! runuser -u postgres -- psql -tAc "select 1" >/dev/null 2>&1; then
    die "cannot reach PostgreSQL as the postgres user. Is the server running?"
fi

as_postgres() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 "$@"; }

role_exists() {
    as_postgres -tAc "select 1 from pg_roles where rolname = '$1'" | grep -q 1
}
db_exists() {
    as_postgres -tAc "select 1 from pg_database where datname = '$1'" | grep -q 1
}

if [ "$DRY_RUN" = "yes" ]; then
    info "would create the roles, the database and the extensions"
else
    # The operator role: owns the objects.
    if role_exists "$OPERATOR_ROLE"; then
        if [ -n "$OPERATOR_PASSWORD" ]; then
            as_postgres -qc "ALTER ROLE \"$OPERATOR_ROLE\" LOGIN PASSWORD '$OPERATOR_PASSWORD'" >/dev/null
            info "operator role  $OPERATOR_ROLE (password set from the config)"
        else
            info "operator role  $OPERATOR_ROLE (already there, peer auth)"
        fi
    else
        if [ -n "$OPERATOR_PASSWORD" ]; then
            as_postgres -qc "CREATE ROLE \"$OPERATOR_ROLE\" LOGIN PASSWORD '$OPERATOR_PASSWORD'" >/dev/null
        else
            as_postgres -qc "CREATE ROLE \"$OPERATOR_ROLE\" LOGIN" >/dev/null
        fi
        info "operator role  $OPERATOR_ROLE (created)"
    fi

    # The runtime role: DML only. It cannot create a table.
    if role_exists "$RUNTIME_ROLE"; then
        as_postgres -qc "ALTER ROLE \"$RUNTIME_ROLE\" LOGIN PASSWORD '$RUNTIME_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE" >/dev/null
        info "runtime role   $RUNTIME_ROLE (password set from the config)"
    else
        as_postgres -qc "CREATE ROLE \"$RUNTIME_ROLE\" LOGIN PASSWORD '$RUNTIME_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE" >/dev/null
        info "runtime role   $RUNTIME_ROLE (created)"
    fi

    if db_exists "$DB_NAME"; then
        info "database       $DB_NAME (already there)"
    else
        as_postgres -qc "CREATE DATABASE \"$DB_NAME\" OWNER \"$OPERATOR_ROLE\"" >/dev/null
        info "database       $DB_NAME (created, owned by $OPERATOR_ROLE)"
    fi

    # The extensions, IN THIS DATABASE. pgvector is NOT a trusted
    # extension, so only a superuser may create it -- which is why this
    # step is separate from the schema step below (the operator role
    # cannot do this). schema/extensions.sql is idempotent.
    #
    # The SET silences the "already exists, skipping" notices on a
    # re-run. psql processes -c and -f in the SAME session, so the SET
    # applies to the file that follows it.
    as_postgres -d "$DB_NAME" \
        -c "SET client_min_messages TO warning" \
        -f "$REPO/schema/extensions.sql" >/dev/null
    info "extensions     pgcrypto, pg_trgm, citext, vector (in $DB_NAME)"

    # pgvector must be new enough. The embedding columns are halfvec,
    # and the halfvec type arrived in pgvector 0.7.0 -- 0.6.x has only
    # the full-precision `vector`, and the schema then fails with a
    # confusing "type halfvec does not exist" a few steps later.
    VECTOR_VER="$(as_postgres -d "$DB_NAME" -tAc \
        "select extversion from pg_extension where extname = 'vector'" | tr -d '[:space:]')"
    if [ -z "$VECTOR_VER" ]; then
        die "the vector extension is not installed in $DB_NAME"
    fi
    if [ "$(printf '%s\n%s\n' "$MIN_VECTOR_VERSION" "$VECTOR_VER" | sort -V | head -1)" \
         != "$MIN_VECTOR_VERSION" ]; then
        die "pgvector $VECTOR_VER is too old. The harness needs
    $MIN_VECTOR_VERSION or newer: the embedding columns are halfvec, and
    the halfvec type arrived in pgvector 0.7.0 (0.6.x only has the
    full-precision vector type).

    Install a newer pgvector package for this PostgreSQL, then:
      sudo -u postgres psql -d $DB_NAME -c 'ALTER EXTENSION vector UPDATE'
    and run this again."
    fi
    info "pgvector       $VECTOR_VER"
fi

# --- 5. the schema -------------------------------------------------------
say "5/8  the schema"
if [ "$DRY_RUN" = "yes" ]; then
    info "would apply schema/init.sql and schema/grants.sql as $OPERATOR_ROLE"
else
    MIGRATE_OUT="$(mktemp)"
    as_user bash -c "cd '$REPO' && bash tools/migrate.sh" \
        > "$MIGRATE_OUT" 2>&1 \
        || { cat "$MIGRATE_OUT"; rm -f "$MIGRATE_OUT"; \
             die "the schema could not be applied — see the output above"; }
    rm -f "$MIGRATE_OUT"
    info "the schema and the runtime role's grants are applied"
fi

# --- 6. the identity files ----------------------------------------------
say "6/8  the agent's identity"
if [ "$DRY_RUN" = "yes" ]; then
    info "would render the identity files into the pie directory"
else
    PIE_DIR="$REPO/$PIE_ROOT/$AGENT_NAME/identity"
    if [ -d "$PIE_DIR" ] && [ -n "$(ls -A "$PIE_DIR" 2>/dev/null)" ]; then
        info "already there  $PIE_DIR (left alone — the operator may have edited them)"
    else
        as_user env REPO="$REPO" PIE_DIR="$PIE_DIR" AGENT_NAME="$AGENT_NAME" \
              OPERATOR_NAME="$OPERATOR_NAME" OPERATOR_EMAIL="$OPERATOR_EMAIL" \
              OPERATOR_TIMEZONE="$OPERATOR_TIMEZONE" BIND="$BIND" \
              "$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.path.join(os.environ["REPO"], "tools"))
from pathlib import Path
from apply_install_config import render_identity

inst = {
    "agent_name": os.environ["AGENT_NAME"],
    "operator_name": os.environ["OPERATOR_NAME"],
    "operator_email": os.environ["OPERATOR_EMAIL"],
    "operator_timezone": os.environ["OPERATOR_TIMEZONE"],
    "bind": os.environ["BIND"],
    "operator_business": "their own business",
    "identity_template": "agent_template",
}
out = Path(os.environ["PIE_DIR"])
out.mkdir(parents=True, exist_ok=True)
for name, text in render_identity(inst).items():
    (out / name).write_text(text)
print(f"    rendered {len(render_identity(inst))} identity files into {out}")
PYEOF
    fi
fi

# --- 7. the first admin -------------------------------------------------
say "7/8  the first admin"
if [ "$DRY_RUN" = "yes" ]; then
    info "would create the admin $ADMIN_EMAIL if it does not exist"
else
    # Is there already an admin with that email? If so, LEAVE IT ALONE:
    # create_user.py is idempotent and would RESET the password hash, and
    # an install that quietly changes an existing operator's password is
    # a nasty surprise.
    ADMIN_EXISTS="$(as_user env DATABASE_URL="$DATABASE" "$PY" - "$ADMIN_EMAIL" <<'PYEOF'
import os, sys
import psycopg
email = sys.argv[1].lower()
try:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from public.users where email = %s", (email,))
            print("yes" if cur.fetchone() else "no")
except Exception as e:
    print("no")
    print(f"    (could not check: {type(e).__name__})", file=sys.stderr)
PYEOF
)"
    if [ "$ADMIN_EXISTS" = "yes" ]; then
        info "admin         $ADMIN_EMAIL already exists — left alone"
    else
        if [ -z "$ADMIN_PASSWORD" ]; then
            ADMIN_PASSWORD="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(18))')"
            GENERATED_PASSWORD="yes"
        else
            GENERATED_PASSWORD="no"
        fi
        CREATE_OUT="$(mktemp)"
        as_user env DATABASE_URL="$DATABASE" "$PY" "$REPO/tools/create_user.py" \
              --email "$ADMIN_EMAIL" --password "$ADMIN_PASSWORD" --role admin \
              > "$CREATE_OUT" 2>&1 \
              || { cat "$CREATE_OUT"; rm -f "$CREATE_OUT"; \
                   die "could not create the admin — see the output above"; }
        rm -f "$CREATE_OUT"
        info "admin         $ADMIN_EMAIL (created)"
    fi
fi

# --- 8. the service ------------------------------------------------------
say "8/8  the service"
if [ "$DRY_RUN" = "yes" ]; then
    info "would install and start harness.service"
else
    UNIT_DIR="$INSTALL_HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    # The shipped unit is a template: %h/harness is the install
    # directory. Render it for where this checkout actually is.
    sed "s|%h/harness|$REPO|g" "$REPO/systemd/harness.service" > "$UNIT_DIR/harness.service"
    # Operator mode (2026-10-29). NoNewPrivileges=true stops sudo from
    # running AT ALL ("the no new privileges flag is set, which prevents
    # sudo from running as root") — it does not merely require a
    # password. So an install that enables security.allow_sudo MUST NOT
    # carry the flag, or the agent's sudo calls fail no matter what the
    # sudoers grants say.
    ALLOW_SUDO="$(python3 -c "
import yaml
try:
    sec = (yaml.safe_load(open('$REPO/config.yaml')) or {}).get('security') or {}
    print('yes' if sec.get('allow_sudo') else 'no')
except Exception:
    print('no')
")"
    if [ "$ALLOW_SUDO" = "yes" ]; then
        sed -i '/^NoNewPrivileges=true$/d' "$UNIT_DIR/harness.service"
        info "operator mode  sudo permitted (NoNewPrivileges dropped from the unit)"
    fi
    chmod 644 "$UNIT_DIR/harness.service"
    info "unit          $UNIT_DIR/harness.service"

    as_user env XDG_RUNTIME_DIR="/run/user/$INSTALL_UID" \
          systemctl --user daemon-reload
    as_user env XDG_RUNTIME_DIR="/run/user/$INSTALL_UID" \
          systemctl --user enable --now harness.service \
        || die "the service did not start — systemctl --user status harness.service"
    sleep 2
    as_user env XDG_RUNTIME_DIR="/run/user/$INSTALL_UID" \
          systemctl --user is-active harness.service >/dev/null \
        || die "the service is not running"
    info "service       running"
fi

# --- done ----------------------------------------------------------------
say "installed"
BIND_ADDR="${BIND:-127.0.0.1:5010}"
info "chat at        http://$BIND_ADDR"
if [ "$DRY_RUN" != "yes" ] && [ -n "${GENERATED_PASSWORD:-}" ]; then
    info "admin          $ADMIN_EMAIL"
    info "password       $ADMIN_PASSWORD"
    info "                (generated — save it now; it is not stored anywhere else)"
else
    info "admin          $ADMIN_EMAIL"
fi
echo
info "the TLS paths in config.yaml (app.tls) are the operator's to set."
info "the agent's identity files live in $REPO/$PIE_ROOT/$AGENT_NAME/identity/"
echo
info "logs:   journalctl --user -u harness.service -f"
info "stop:   systemctl --user stop harness.service"
