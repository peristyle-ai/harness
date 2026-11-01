#!/usr/bin/env bash
# Print the identity a commit should be signed with.
#
# WHY THIS EXISTS
# ---------------
# The operator reads the history of the repository to see which agent did
# which piece of work -- and sometimes asks one agent to step in for
# another. So a commit is signed with the NAME of the agent that made it.
# Attribution is the point of a signed commit.
#
# The ADDRESS is the repository's own no-reply address. A name in a commit
# author field is attribution, and every project in the world has one. A
# MAILBOX is how you reach a person, and a public repository must not
# publish one. The identity gate (scripts/check_commit_identities.py)
# polices the ADDRESS, not the name.
#
# USAGE
#   scripts/agent_signature.sh [--install-root DIR]
#
# PRINTS
#   "Name <address>"
#
# WHERE THE NAME COMES FROM, in order:
#   1. GIT_AUTHOR_NAME      -- an explicit override
#   2. the agent's own IDENTITY.md ("Name: Durthu")
#   3. HARNESS_AGENT_NAME   -- the handle the runtime exports for the
#                              turn that is running
#   4. "Harness Agent"
#
# The runtime exports HARNESS_AGENT_NAME into every exec_bash command, so
# the agent that is doing the work is the one whose name lands on the
# commit -- including when the operator asked it to step in.
set -euo pipefail

# The address every commit is signed with. It is the repository's own
# no-reply address: it identifies the ACCOUNT the commit came from, and
# it reaches nobody.
#
# An override is honoured only when it passes the SAME rule the identity
# gate enforces, so the tooling and the gate cannot disagree about what
# a mailbox is. The rule lives in scripts/check_commit_identities.py and
# is asked, not copied.
REPO_EMAIL="peristyle-ai@users.noreply.github.com"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_EMAIL="$REPO_EMAIL"
if [ -n "${GIT_AUTHOR_EMAIL:-}" ]; then
  if python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from check_commit_identities import address_is_acceptable
sys.exit(0 if address_is_acceptable('$GIT_AUTHOR_EMAIL') else 1)
" 2>/dev/null; then
    DEFAULT_EMAIL="$GIT_AUTHOR_EMAIL"
  fi
fi

INSTALL_ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install-root) INSTALL_ROOT="${2:-}"; shift 2 ;;
    *) echo "usage: agent_signature.sh [--install-root DIR]" >&2; exit 2 ;;
  esac
done

# --- the agent's own name, from its identity file -------------------------
#
# The identity file is the agent's own account of what it is called. It
# lives beside the agent's other identity files, under the install.
identity_name() {
  local handle="$1" file
  for file in "$INSTALL_ROOT/pie/$handle/identity/IDENTITY.md" \
              "$INSTALL_ROOT/pie/$handle/IDENTITY.md"; do
    [ -f "$file" ] || continue
    local name
    name="$(grep -m1 -iE '^[[:space:]]*[-*]?[[:space:]]*\**name\**[[:space:]]*:' "$file" 2>/dev/null \
            | sed -E 's/^[^:]*:[[:space:]]*//; s/^[[:space:]]*(\*\*)?[[:space:]]*//; s/(\*\*)?[[:space:]]*$//' || true)"
    if [ -n "$name" ]; then printf '%s' "$name"; return 0; fi
  done
  return 1
}

HANDLE="${HARNESS_AGENT_NAME:-}"
NAME="${GIT_AUTHOR_NAME:-}"
if [ -z "$NAME" ] && [ -n "$HANDLE" ]; then
  NAME="$(identity_name "$HANDLE" || true)"
fi
[ -n "$NAME" ] || NAME="${HANDLE:-Harness Agent}"

printf '%s <%s>\n' "$NAME" "$DEFAULT_EMAIL"
