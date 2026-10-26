#!/usr/bin/env bash
# Propose a change as a pull request, without touching main.
#
# This is the path an agent (peribot, peribot) uses when it finds something
# worth fixing. It exists so that proposing a change is ONE command and
# cannot accidentally land on main.
#
#   scripts/agent_propose.sh start <slug>                # make a worktree
#   scripts/agent_propose.sh push  <slug> "<title>" ["<body>"]
#
# WHY A WORKTREE: the working checkout may be a LIVE INSTALL whose
# service is running from it. Checking out a branch there would change
# the code under the running service. The worktree is a sibling
# directory, so the live checkout stays on main and the service is not
# disturbed.
#
# WHY THE VERSION BUMP: the version in pyproject.toml is the released
# version, and every pull request that lands on main is a release. So
# the patch version is bumped as part of the proposal: 0.1.0 -> 0.1.1.
# A merge into main IS the release.
#
# WHAT HAPPENS NEXT: pushing the branch triggers the `agent-pr`
# workflow, which opens the pull request using the repository's own
# GITHUB_TOKEN. The pull request is authored by `github-actions[bot]`,
# which cannot approve its own pull request, so it waits for Grove.
set -euo pipefail

# The repository root. `git rev-parse` first, so the script also works
# when it is run from a copy kept inside the checkout (an agent's
# scripts/ directory), not only from the repository's own scripts/.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKTREE_ROOT="${AGENT_WORKTREE_ROOT:-$(dirname "$REPO_ROOT")/harness-worktrees}"
SLUG="${2:-}"
REMOTE="${AGENT_REMOTE:-origin}"
BASE="${AGENT_BASE:-main}"

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 2
}

cmd="${1:-}"
[ -n "$cmd" ] || usage
SLUG="${2:-}"

case "$cmd" in
start)
  [ -n "$SLUG" ] || die "usage: agent_propose.sh start <slug>"
  BRANCH="agent/$SLUG"
  DIR="$WORKTREE_ROOT/$SLUG"
  mkdir -p "$WORKTREE_ROOT"
  if [ -d "$DIR" ]; then
    echo "worktree already exists: $DIR"
  else
    git -C "$REPO_ROOT" fetch -q "$REMOTE" "$BASE"
    git -C "$REPO_ROOT" worktree add -q -b "$BRANCH" "$DIR" "$REMOTE/$BASE"
    echo "worktree created: $DIR  (branch $BRANCH, based on $REMOTE/$BASE)"
    # Bootstrap: if the base branch does not carry the pull-request
    # workflow yet, copy it in from the checkout this script lives in.
    # The workflow has to be IN the pushed branch for GitHub to run it.
    if [ ! -f "$DIR/.github/workflows/agent-pr.yml" ] \
       && [ -f "$REPO_ROOT/.github/workflows/agent-pr.yml" ]; then
      mkdir -p "$DIR/.github/workflows"
      cp "$REPO_ROOT/.github/workflows/agent-pr.yml" "$DIR/.github/workflows/"
      echo "copied the agent-pr workflow into the worktree"
    fi
  fi
  echo
  echo "Make the change in: $DIR"
  echo "Then: $0 push $SLUG \"<title>\" [\"<body>\"]"
  ;;

push)
  [ -n "$SLUG" ] || die "usage: agent_propose.sh push <slug> \"<title>\" [\"<body>\"]"
  TITLE="${3:-}"
  BODY="${4:-}"
  [ -n "$TITLE" ] || die "a title is required"
  DIR="$WORKTREE_ROOT/$SLUG"
  [ -d "$DIR" ] || die "no worktree at $DIR -- run: $0 start $SLUG"

  cd "$DIR"

  # --- the version bump: a merge into main is a release -----------
  if [ -f pyproject.toml ]; then
    CUR="$(python3 -c "
import re,pathlib
m=re.search(r'(?m)^version\s*=\s*\"([^\"]+)\"', pathlib.Path('pyproject.toml').read_text())
print(m.group(1) if m else '')
")"
    if [ -n "$CUR" ]; then
      NEW="$(python3 -c "
import sys
v='$CUR'.split('.')
v[2]=str(int(v[2])+1)
print('.'.join(v))
")"
      python3 - "$CUR" "$NEW" <<'PY'
import pathlib, re, sys
old, new = sys.argv[1], sys.argv[2]
p = pathlib.Path("pyproject.toml")
p.write_text(re.sub(r'(?m)^(version\s*=\s*)"[^"]+"', r'\1"%s"' % new, p.read_text(), count=1))
print(f"version {old} -> {new}")
PY
    fi
  fi

  # --- the pull request title and body ---------------------------
  mkdir -p .agent-pr
  printf '%s\n' "$TITLE" > .agent-pr/title.md
  if [ -n "$BODY" ]; then
    printf '%s\n' "$BODY" > .agent-pr/body.md
  elif [ ! -f .agent-pr/body.md ]; then
    printf '%s\n' "Proposed by an agent working in this repository." > .agent-pr/body.md
  fi
  git add -A

  if git diff --cached --quiet; then
    die "nothing staged -- the worktree has no changes to propose"
  fi

  git -c user.name="${GIT_AUTHOR_NAME:-Grove}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-operator@example.com}" \
      commit -q -m "$TITLE

$BODY

Proposed by an agent. The pull request is opened by the agent-pr
workflow, so it waits for a code owner to approve it."

  git push -q -u "$REMOTE" "agent/$SLUG"

  echo
  echo "pushed agent/$SLUG"
  echo "the agent-pr workflow will open the pull request in a moment"
  echo "it will wait for Grove to approve it -- agents cannot merge"
  echo
  echo "watch it:"
  echo "  gh pr list --repo peristyle-ai/harness --head agent/$SLUG"
  ;;

*)
  usage
  ;;
esac
