"""The commit signature must be the agent's own name.

The operator reads the history of the repository to see which agent did
which piece of work -- and sometimes asks one agent to step in for
another.  So a commit is signed with the NAME of the agent that made it,
and the ADDRESS is the repository's own no-reply address.

scripts/agent_signature.sh resolves that.  This test pins where the name
comes from, because "the agent's name" has to mean one thing on every
install: an agent whose identity file is missing must still sign as
itself.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNATURE = REPO_ROOT / "scripts" / "agent_signature.sh"

NO_REPLY = "peristyle-ai@users.noreply.github.com"


def run_signature(install_root: Path, env: dict[str, str]) -> str:
    """Run the signature resolver and return its output.

    Only the trailing newline is stripped.  A leading space would be a
    bug in the name extraction, and ``strip()`` would hide it -- which it
    did once: the resolver returned " Durthu" and every test here passed.
    """
    full = {**os.environ, **env}
    result = subprocess.run(
        ["bash", str(SIGNATURE), "--install-root", str(install_root)],
        capture_output=True, text=True, env=full,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.rstrip("\n")


def write_identity(install_root: Path, handle: str, name: str) -> None:
    directory = install_root / "pie" / handle / "identity"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "IDENTITY.md").write_text(
        f"# Identity\n\n- **Name:** {name}\n- **Creature:** an agent\n",
        encoding="utf-8",
    )


def test_the_signature_script_exists():
    assert SIGNATURE.is_file(), "the signature resolver is missing"


def test_an_explicit_override_wins(tmp_path: Path):
    write_identity(tmp_path, "durthu", "Durthu")
    out = run_signature(tmp_path, {"GIT_AUTHOR_NAME": "Grove",
                                   "HARNESS_AGENT_NAME": "durthu"})
    assert out == f"Grove <{NO_REPLY}>"


def test_the_identity_file_gives_the_agents_own_name(tmp_path: Path):
    """The handle is what the runtime knows; the name is what it is called."""
    write_identity(tmp_path, "durthu", "Durthu")
    out = run_signature(tmp_path, {"HARNESS_AGENT_NAME": "durthu"})
    assert out == f"Durthu <{NO_REPLY}>"


def test_without_an_identity_file_the_handle_is_used(tmp_path: Path):
    """An agent whose identity file is missing still signs as itself."""
    out = run_signature(tmp_path, {"HARNESS_AGENT_NAME": "durthu"})
    assert out == f"durthu <{NO_REPLY}>"


def test_with_nothing_at_all_it_is_still_a_signature(tmp_path: Path):
    out = run_signature(tmp_path, {})
    assert out == f"Harness Agent <{NO_REPLY}>"


def test_the_address_is_always_the_no_reply_one(tmp_path: Path):
    """The address must never be a mailbox -- that is what the gate polices."""
    for env in ({}, {"HARNESS_AGENT_NAME": "durthu"},
                {"GIT_AUTHOR_NAME": "Grove"}):
        out = run_signature(tmp_path, env)
        assert out.endswith(f"<{NO_REPLY}>"), out


def test_the_resolver_is_not_affected_by_a_mailbox_in_the_environment(tmp_path: Path):
    """A GIT_AUTHOR_EMAIL in the environment is a mailbox, and it is refused.

    The resolver signs with the repository's own address whatever the
    environment says, because the environment is not a trusted source
    for it -- and because the whole point of the no-reply address is
    that it reaches nobody.
    """
    out = run_signature(tmp_path, {"GIT_AUTHOR_EMAIL": "a.person@an-operator-domain.example"})
    assert out == f"Harness Agent <{NO_REPLY}>"
