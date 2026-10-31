"""The public branches must not carry one install's identifying details.

WHY THIS TEST EXISTS
--------------------
``scripts/check_public_refs.py`` is a gate that can see something the
working-tree guard structurally cannot: a BRANCH.  A branch is a
public page, and a branch that was merged and then left behind still
publishes the tree it was merged from.

A gate that has never been seen failing is not a gate, so every test
here plants the failure and watches the gate catch it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_public_refs.py"

# Assembled at run time, never written out: a literal personal mailbox
# in this file would be a finding in the very file that hunts for them.
PERSONAL_BOX = "someone@" + "not-a-placeholder.invalidtld"
ROLE_BOX = "security@example.com"

# The same for an agent name in an agent-name position: the tree guard
# scans this file too, so the shape has to be built, not written.
REAL_AGENT = "a_" + "real_agent"
AGENT_POSITION = "agent_name=" + '"' + REAL_AGENT + '"'


def _git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


@pytest.fixture
def public_repo(tmp_path):
    """A bare remote with one clean branch, and a clone that tracks it."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "--quiet", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "--quiet", str(remote), str(work))
    _git(work, "config", "user.email", "agent@example.com")
    _git(work, "config", "user.name", "an agent")
    (work / "README.md").write_text("A harness.\n")
    _git(work, "add", "-A")
    _git(work, "commit", "--quiet", "-m", "the first commit")
    _git(work, "push", "--quiet", "-u", "origin", "HEAD:refs/heads/main")
    return remote, work


def _gate(repo, *args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *args],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _push_a_branch(work, name, path, content):
    _git(work, "checkout", "--quiet", "-b", name)
    target = work / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(work, "add", "-A")
    _git(work, "commit", "--quiet", "-m", f"a branch called {name}")
    _git(work, "push", "--quiet", "origin", name)
    _git(work, "checkout", "--quiet", "main")


def test_a_clean_remote_passes(public_repo):
    _, work = public_repo
    code, out = _gate(work)
    assert code == 0, out
    assert "clean" in out


def test_a_branch_carrying_a_mailbox_fails(public_repo):
    _, work = public_repo
    _push_a_branch(work, "stale", "SECURITY.md", f"Email {PERSONAL_BOX}.\n")
    code, out = _gate(work)
    assert code == 1, out
    assert "stale" in out
    assert "SECURITY.md" in out
    assert "mailbox" in out


def test_the_gate_reads_every_file_not_only_the_documents(public_repo):
    _, work = public_repo
    _push_a_branch(work, "stale", "app/thing.py", f"# contact {PERSONAL_BOX}\n")
    code, out = _gate(work)
    assert code == 1, out
    assert "app/thing.py" in out


def test_a_branch_carrying_an_agent_roster_fails(public_repo):
    """The agent roster is a rule of the shared set, so the branch gate
    applies it too.

    The rules live in ``scripts/identifier_rules.py`` because they have
    to hold in more than one place.  A rule that only the working-tree
    guard applies is a rule that is not enforced -- so the branch gate
    has to catch the roster as well, and this watches it catch one.
    """
    _, work = public_repo
    _push_a_branch(work, "stale", "app/thing.py", f"{AGENT_POSITION}\n")
    code, out = _gate(work)
    assert code == 1, out
    assert "stale" in out
    assert "app/thing.py" in out
    assert "agent roster" in out


def test_the_template_agent_is_not_a_finding(public_repo):
    """The template's own agent is the one name a branch may use.

    The gate judges a branch by the rules that branch ships: a branch
    that carries the example config may name the agent that config
    names, and no other.
    """
    _, work = public_repo
    (work / "config.yaml.example").write_text(
        "app:\n  default_agent: template_agent\n  agents:\n    - template_agent\n"
    )
    _git(work, "add", "-A")
    _git(work, "commit", "--quiet", "-m", "ship the template config")
    # The fixture publishes HEAD as the remote's "main", so push the
    # same way: the clone's local branch name is not the remote's.
    _git(work, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    allowed = "agent_name=" + '"' + "template_agent" + '"'
    _push_a_branch(work, "stale", "app/thing.py", f"{allowed}\n")
    code, out = _gate(work)
    assert code == 0, out


def test_the_gate_does_not_print_the_identifier_it_found(public_repo):
    """A gate that republishes what it found cannot be acted on.

    The CI log of a public repository is public, indexed and immutable.
    It outlives the branch that carried the identifier -- so a gate
    that printed the identifier would make deleting that branch
    pointless.  The log says WHERE; the branch says WHAT.
    """
    _, work = public_repo
    _push_a_branch(work, "stale", "SECURITY.md", f"Email {PERSONAL_BOX}.\n")
    code, out = _gate(work)
    assert code == 1, out
    assert PERSONAL_BOX not in out, (
        "the gate must not print the identifier it found"
    )
    assert "mailbox" in out
    assert "stale" in out


def test_a_role_address_is_not_a_finding(public_repo):
    _, work = public_repo
    _push_a_branch(work, "stale", "SECURITY.md", f"Email {ROLE_BOX}.\n")
    code, out = _gate(work)
    assert code == 0, out


def test_the_gate_fetches_a_branch_the_clone_has_never_seen(public_repo, tmp_path):
    remote, work = public_repo
    other = tmp_path / "other"
    _git(tmp_path, "clone", "--quiet", str(remote), str(other))
    _git(other, "config", "user.email", "agent@example.com")
    _git(other, "config", "user.name", "an agent")
    _push_a_branch(other, "unseen", "SECURITY.md", f"Email {PERSONAL_BOX}.\n")
    code, out = _gate(work)
    assert code == 1, out
    assert "unseen" in out


def test_a_gate_that_cannot_see_is_not_a_pass(public_repo):
    remote, work = public_repo
    # Take the remote away.  A gate that cannot look must say so and
    # fail: reporting "clean" because it saw nothing is the one thing
    # a gate must never do.
    shutil.rmtree(remote)
    code, out = _gate(work)
    assert code == 2, out
    assert "INCONCLUSIVE" in out
