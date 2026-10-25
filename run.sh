#!/bin/bash
# gunicorn entrypoint for the harness app.
#
# There is no .env: the configuration and the secrets live in config.yaml
# next to this script, and the app reads them from there.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] && source .venv/bin/activate

# --- the bind address ----------------------------------------------------
# It comes from the config (app.bind). The HARNESS_BIND environment
# variable overrides it, for an operator who wants to move the port
# without editing the config.
#
# This USED to be read from the .env. When the .env went away, the
# fallback silently took over and the service bound 0.0.0.0:5010 on
# every install -- including one whose config says 127.0.0.1:5010, i.e.
# a loopback-only install that ended up listening on every interface.
BIND="${HARNESS_BIND:-}"
if [ -z "$BIND" ] && [ -f config.yaml ]; then
    BIND="$(python3 -c "
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    print(yaml.safe_load(open('config.yaml'))['app']['bind'])
except Exception:
    pass
" 2>/dev/null || true)"
fi
[ -n "$BIND" ] || BIND="0.0.0.0:5010"

# --- log to BOTH the journal and a readable file (2026-10-21) ---
# gunicorn's --access/--error-logfile are "-" (stdout/stderr), which systemd
# sends to the journal. That is unreadable by the operator's own automation,
# so a chat failure could only be diagnosed by hand. `exec > >(...)` keeps
# gunicorn as the main process with its own PID and exit status -- a plain
# `| tee` would make the pipeline exit status tee's and silently defeat
# Restart=on-failure.
LOG_DIR="$(dirname "$0")/logs"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/harness.log") 2>&1

# Worker timeout raised 600 -> default 1800 on 2026-10-21 (per Mal).
# A real payroll turn reached iteration 12 with a 17k-char thinking
# block and had not finished at 7 minutes; 600s was too tight for
# legitimate long agentic runs. NOTE: gunicorn kills the WORKER and the
# master respawns it, so systemd NRestarts does NOT move. Do not use
# NRestarts as a health signal for this service -- check the worker PID.
# (Comments must stay ABOVE this line: inside a backslash continuation a
#  # line terminates the command and gunicorn loses its app argument.)
exec gunicorn \
    --workers 1 \
    --threads 4 \
    --bind "$BIND" \
    --access-logfile - \
    --error-logfile - \
    --timeout "${GUNICORN_TIMEOUT:-1800}" \
    "app:create_app()"
