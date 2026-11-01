#!/usr/bin/env python3
"""Fail the build if a commit carries a MAILBOX.

WHY THIS EXISTS
---------------
This repository is public.  Every commit in it is a public page: the
author and the committer are printed by the hosting site, indexed, and
permanent.  The work here is done by agents that push from the installs,
and an agent pushes with the git identity of the install it pushes from
-- which, until this check existed, was a person's mailbox at the
operator's own domain.

A scrubbed history is not a scrubbed repository if the next commit puts
the address back.  This check is what makes the scrub hold.

WHAT IT CHECKS, AND WHY THE NAME IS NOT ONE OF THE THINGS
---------------------------------------------------------
A commit identity is a NAME and an ADDRESS, and the two are not the
same kind of thing:

* the **address** is how you REACH a person.  Publishing one in a
  public repository is the leak this gate exists to stop.  An address
  is acceptable when it is at the hosting site's own no-reply domain, at
  a placeholder domain, or a ROLE local part (``noreply@``,
  ``security@`` ...), because a role says "this is a function";
* the **name** is ATTRIBUTION.  The operator reads the history of this
  repository to see which agent did which piece of work -- and sometimes
  asks one agent to step in for another -- so every commit is signed
  with the name of the agent that made it.  A name in a commit author
  field is what signed commits are for.

So the gate polices the address, and lets the name through.  The one
thing it will not accept is a NAME that is itself an address: that is
the leak wearing a hat, and it gets past a reader who is only looking
at the name.

WHAT IT NEVER PRINTS
--------------------
The identity it found.  A gate that prints the identifier it found has
republished it: a CI log on a public repository is public, indexed, and
immutable.  The finding is reported as the commit and the rule; the
value is only ever counted.

EXIT CODES
----------
0  no commit carries a mailbox
1  a commit carries a mailbox
2  INCONCLUSIVE -- nothing could be scanned.  That is a failure too: a
   gate that cannot see is not a gate.
"""
from __future__ import annotations

import re
import subprocess
import sys

# --- the addresses that are allowed ---------------------------------------

# The hosting site's own no-reply domain.  Every bot account is given an
# address here so that it does not have to publish a real mailbox.
NOREPLY_DOMAINS = {
    "users.noreply.github.com",
    "noreply.github.com",
}

# Domains that mean "this is a placeholder".
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "example.test",
    "example", "invalid", "localhost", "test",
}

# Local parts that name a FUNCTION rather than a person.
ROLE_LOCAL_PARTS = {
    "admin", "abuse", "billing", "bounce", "bouncer", "contact", "devops",
    "donotreply", "hello", "help", "hostmaster", "info", "mail", "mailer-daemon",
    "no-reply", "noreply", "ops", "postmaster", "root", "sales", "security",
    "support", "sysadmin", "team", "webmaster",
}

# An address, in the shape git writes them: local part, "@", domain.
EMAIL_RX = re.compile(r"^[A-Za-z0-9._%+\-\[\]]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")

# A git identity: a name and an address, the address in angle brackets.
IDENTITY_RX = re.compile(r"^(.*?)\s*<([^>]*)>\s*$")

# The rule names.  They describe the KIND of finding, never the value.
RULE_MAILBOX = "a mailbox"
RULE_NAME_IS_ADDRESS = "a name that is an address"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + list(args), capture_output=True, text=True)


def _split_identity(raw: str) -> tuple[str, str]:
    """(name, address) from a git identity string."""
    raw = (raw or "").strip()
    match = IDENTITY_RX.match(raw)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    # No angle brackets: git wrote the address bare.  Treat the whole
    # thing as the address -- there is no name to attribute it to.
    return "", raw


def address_is_acceptable(address: str) -> bool:
    """True when this address does not reach a person.

    Public, because the tooling that SIGNS a commit asks the same
    question: one implementation of the rule, so the tooling and the
    gate cannot drift apart and end up disagreeing about what a mailbox
    is.
    """
    match = EMAIL_RX.match(address.strip())
    if not match:
        # Not an address at all -- an empty one, or a local name with no
        # domain.  It is not a mailbox, so it cannot publish one; but it
        # is also not a name we recognise.
        return False
    domain = match.group(1).lower()
    local = address.split("@", 1)[0].lower()
    if domain in NOREPLY_DOMAINS:
        return True
    if domain in PLACEHOLDER_DOMAINS:
        return True
    # A role local part, with any "+suffix" the site adds to it.
    if local.split("+", 1)[0] in ROLE_LOCAL_PARTS:
        return True
    return False


def find_bad_identities(repo: str = ".") -> tuple[list[tuple[str, str]], int]:
    """Every (commit, rule) whose identity carries a mailbox, and the count.

    ``rule`` is "author" or "committer" for a mailbox, and
    ``RULE_NAME_IS_ADDRESS`` for a name that is an address.  The values
    are never returned: see the module docstring.
    """
    commits = _git("-C", repo, "rev-list", "--all").stdout.split()
    if not commits:
        return [], 0
    raw = _git(
        "-C", repo, "log", "--all",
        "--format=%H%x1f%an <%ae>%x1f%cn <%ce>",
    ).stdout
    findings: list[tuple[str, str]] = []
    total = 0
    for line in raw.splitlines():
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        sha, author_raw, committer_raw = parts
        total += 1
        for role, identity_raw in (("author", author_raw), ("committer", committer_raw)):
            name, address = _split_identity(identity_raw)
            if not address_is_acceptable(address):
                findings.append((sha, role))
            if name and EMAIL_RX.match(name.strip()):
                findings.append((sha, RULE_NAME_IS_ADDRESS))
    return findings, total


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    repo = argv[0] if argv else "."
    findings, total = find_bad_identities(repo)
    if total == 0:
        print("INCONCLUSIVE: no commits were scanned -- a gate that cannot see is not a gate")
        return 2
    if not findings:
        print(f"scanned {total} commit(s): no commit carries a mailbox")
        print("the names are attribution and are allowed; the addresses are all "
              "no-reply, placeholder or role addresses")
        return 0
    print(f"{len(findings)} finding(s) in {total} commit(s).")
    print("A MAILBOX in a public repository is how you reach a person, and it")
    print("outlives the branch that carried it.  The names are fine -- they are")
    print("attribution, and the operator reads the history to see who did what.")
    print("The identities are not printed here, for the same reason this file")
    print("does not contain them.")
    for sha, rule in findings:
        print(f"  {sha[:12]}  {rule}")
    print()
    print("Sign commits with the agent's name and the repository's own no-reply")
    print("address (scripts/agent_signature.sh does this), and rewrite the")
    print("commit.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
