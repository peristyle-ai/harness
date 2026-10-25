"""The secret scanner must actually catch secrets.

A gate that cannot fail is not a gate, so this test plants secrets in a
throwaway git repository and asserts that the scanner finds them -- and
that a CLEAN result is only ever reported when something was really
scanned.

scripts/check_no_secrets.py is the CI gate for this repository while it
is private: GitHub's own secret scanning and push protection need a paid
Advanced Security subscription on a private repo, and are free once the
repo is public.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "scripts" / "check_no_secrets.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    return result.stdout


def scan(repo: Path) -> tuple[int, str]:
    """Run the scanner against `repo`. Returns (exit code, output)."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_SYSTEM": "/dev/null"}
    result = subprocess.run([sys.executable, str(SCANNER)],
                            cwd=str(repo), capture_output=True, text=True,
                            env=env)
    return result.returncode, result.stdout + result.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An empty git repository with one harmless commit."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "README.md").write_text("harmless\n")
    git(r, "add", "-A")
    git(r, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "init")
    return r


def test_the_scanner_exists():
    assert SCANNER.is_file(), "scripts/check_no_secrets.py is missing"


def test_a_clean_repository_passes(repo: Path):
    code, out = scan(repo)
    assert code == 0, out
    assert "no secret found" in out


def test_an_api_key_in_a_file_is_caught(repo: Path):
    (repo / "config.py").write_text(
        'API_KEY = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA"\n')
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "oops")
    code, out = scan(repo)
    assert code == 1, out
    assert "A SECRET WAS COMMITTED" in out
    assert "anthropic/openai-style key" in out


def test_a_github_token_is_caught(repo: Path):
    (repo / "token.txt").write_text(
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "oops")
    code, out = scan(repo)
    assert code == 1, out
    assert "github classic token" in out


def test_an_aws_key_is_caught(repo: Path):
    (repo / "k.txt").write_text("AKIAIOSFODNN7EXAMPLE\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "oops")
    code, out = scan(repo)
    assert code == 1, out
    assert "aws access key id" in out


def test_a_private_key_block_is_caught(repo: Path):
    (repo / "id_ed25519.pem").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n"
        "-----END OPENSSH PRIVATE KEY-----\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "oops")
    code, out = scan(repo)
    assert code == 1, out
    assert "private key block" in out


def test_a_deleted_secret_is_still_caught(repo: Path):
    """Deleting the file does not un-leak it -- the history still has it."""
    (repo / "leak.txt").write_text("ghp_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "oops")
    git(repo, "rm", "-q", "leak.txt")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "remove it")
    code, out = scan(repo)
    assert code == 1, out
    assert "github classic token" in out


def test_a_secret_in_a_commit_message_is_caught(repo: Path):
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "--allow-empty", "-qm",
        "my token is ghp_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC")
    code, out = scan(repo)
    assert code == 1, out
    assert "COMMIT MESSAGES" in out


def test_an_empty_repository_is_inconclusive_not_clean(tmp_path: Path):
    """A scanner that scanned nothing must not say CLEAN."""
    r = tmp_path / "empty"
    r.mkdir()
    git(r, "init", "-q")
    code, out = scan(r)
    assert code == 2, out
    assert "INCONCLUSIVE" in out
    assert "no secret found" not in out


def test_the_allowlist_only_applies_to_its_own_place(repo: Path):
    url = "postgresql://postgres:password@localhost:5432/harness\n"
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "tests.yml").write_text(f"DATABASE_URL: {url}")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "ci")
    code, out = scan(repo)
    assert code == 0, out

    # the same password in an ordinary file is a finding
    (repo / "app.py").write_text(f"DATABASE_URL: {url}")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "app")
    code, out = scan(repo)
    assert code == 1, out


def test_the_exemption_list_is_exactly_one_file():
    """Only the scanner's own test file may be exempt, and it is pinned.

    A file whose purpose is to contain secret-shaped strings cannot be
    scanned for secret-shaped strings. That is the ONLY such file, and
    the list is pinned here so it cannot grow quietly.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import check_no_secrets as scanner
    finally:
        sys.path.pop(0)

    exempt = {pattern for pattern, _ in scanner.EXEMPT_PATHS}
    assert exempt == {"tests/test_check_no_secrets.py"}, exempt

    # the scanner's own SOURCE is not exempt: it must scan clean
    assert scanner.is_exempt("scripts/check_no_secrets.py") is None

    # and a secret anywhere else is not excused
    assert scanner.excused("app/config.py",
                            "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") is None


def test_an_exempt_file_is_still_reported_as_exempted(repo: Path):
    """The exemption is printed, not silent."""
    (repo / "leak.txt").write_text("ghp_EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "oops")
    code, out = scan(repo)
    assert code == 1, out
    # the repo under test is not this repo, so its files are not exempt
    assert "exempted" not in out


def test_the_repository_itself_is_clean():
    """The gate must pass on the repository it guards."""
    code, out = scan(REPO_ROOT)
    assert code == 0, out
