"""The identity scanner must actually catch a MAILBOX -- and must let a
signed NAME through.

A gate that cannot fail is not a gate.  This test plants commits in a
throwaway git repository, with the identities that must not be published,
and asserts that the scanner finds them -- and that a CLEAN result is
only ever reported when something was really scanned.

It also pins the other half of the rule, which matters just as much: the
NAME on a commit is attribution.  The operator reads the history of the
repository to see which agent did which piece of work, so a commit signed
with an agent's name must PASS, and a scanner that blocked it would be
fighting the operator's own workflow.

scripts/check_commit_identities.py is the enforcement for a rule that
the tree-level rules cannot express: the rules read file CONTENT, and
the author of a commit is not in any file.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "scripts" / "check_commit_identities.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    return result.stdout


def scan(repo: Path) -> tuple[int, str]:
    """Run the scanner against `repo`.  Returns (exit code, output)."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_SYSTEM": "/dev/null"}
    result = subprocess.run([sys.executable, str(SCANNER)],
                            cwd=str(repo), capture_output=True, text=True,
                            env=env)
    return result.returncode, result.stdout + result.stderr


_counter = iter(range(1, 1000))


def commit(repo: Path, name: str, email: str, message: str = "a commit") -> None:
    """Create one commit with the given identity.

    The file content changes every time, so the commit is never a no-op:
    git refuses to commit an unchanged tree, and a commit that was never
    created would make the scanner look like it had found nothing.
    """
    (repo / "file.txt").write_text(f"{message} {name} {next(_counter)}\n")
    git(repo, "add", "-A")
    env = {**os.environ,
           "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    result = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An empty git repository with one commit by a no-reply address."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.name", "Peristyle")
    git(r, "config", "user.email", "peristyle-ai@users.noreply.github.com")
    commit(r, "Peristyle", "peristyle-ai@users.noreply.github.com")
    return r


def test_the_scanner_exists():
    assert SCANNER.is_file(), "the identity scanner is missing"


def test_a_no_reply_identity_passes(repo: Path):
    code, out = scan(repo)
    assert code == 0, out


def test_a_person_address_is_caught(repo: Path):
    commit(repo, "A Person", "a.person@an-operator-domain.example")
    code, out = scan(repo)
    assert code == 1, out
    assert "author" in out


def test_a_committer_address_is_caught(repo: Path):
    commit(repo, "A Person", "a.person@an-operator-domain.example")
    # Rewrite only the COMMITTER, leaving the author acceptable.
    git(repo, "commit", "-q", "--amend", "--no-edit",
        "--author=Peristyle <peristyle-ai@users.noreply.github.com>",
        "-c", "user.email=a.person@an-operator-domain.example")
    code, out = scan(repo)
    assert code == 1, out
    assert "committer" in out


def test_a_role_address_passes(repo: Path):
    commit(repo, "Support", "support@github.com")
    code, out = scan(repo)
    assert code == 0, out


def test_a_placeholder_domain_passes(repo: Path):
    commit(repo, "Operator", "operator@example.com")
    code, out = scan(repo)
    assert code == 0, out


def test_an_empty_repository_is_inconclusive_not_clean(tmp_path: Path):
    r = tmp_path / "empty"
    r.mkdir()
    git(r, "init", "-q")
    code, out = scan(r)
    assert code == 2, out
    assert "INCONCLUSIVE" in out


def test_the_repository_itself_is_clean():
    """This repository's own history must pass its own scanner."""
    code, out = scan(REPO_ROOT)
    assert code == 0, out


def test_the_scanner_never_prints_the_address_it_found(repo: Path):
    """A gate that prints the identifier it found has republished it."""
    secret = "a.person@an-operator-domain.example"
    commit(repo, "A Person", secret)
    code, out = scan(repo)
    assert code == 1
    assert secret not in out
    assert "a.person" not in out


# --- the name is attribution, and it is allowed --------------------------


def test_a_signed_agent_name_passes(repo: Path):
    """The operator reads the history to see which agent did the work.

    A commit signed with an agent's NAME is the whole point of a signed
    commit, so it must pass -- as long as the ADDRESS is a no-reply one.
    """
    commit(repo, "Durthu", "peristyle-ai@users.noreply.github.com")
    code, out = scan(repo)
    assert code == 0, out


def test_a_signed_name_with_a_real_mailbox_is_still_caught(repo: Path):
    """The name does not launder the address."""
    commit(repo, "Durthu", "a.person@an-operator-domain.example")
    code, out = scan(repo)
    assert code == 1, out


def test_a_name_that_is_itself_an_address_is_caught(repo: Path):
    """A name that is an address is the leak wearing a hat."""
    commit(repo, "a.person@an-operator-domain.example",
           "peristyle-ai@users.noreply.github.com")
    code, out = scan(repo)
    assert code == 1, out
    assert "name that is an address" in out
