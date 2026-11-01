#!/usr/bin/env python3
"""Fail the build if a commit carries an identity that names a person.

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

WHAT IT CHECKS
--------------
The author and the committer of every commit in the history.  An
identity is acceptable when its address is:

* at the hosting site's own no-reply domain -- that is the domain the
  site gives a bot so that it does not have to publish a mailbox;
* at a placeholder domain (``example.com`` and friends);
* a ROLE local part at any domain (``noreply@``, ``security@`` ...),
  because a role says "this is a function" and not "this is a person";
* one of the site's own service addresses.

Anything else names a person or a mailbox, and it is a finding.

WHAT IT NEVER PRINTS
--------------------
The address it found.  A gate that prints the identifier it found has
republished it: a CI log on a public repository is public, indexed, and
immutable.  The finding is reported as the commit and the rule; the
address is only ever counted.

EXIT CODES
----------
0  no identity names a person
1  an identity names a person
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

EMAIL_RX = re.compile(r"^[A-Za-z0-9._%+\-\[\]]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + list(args), capture_output=True, text=True)


def _acceptable(address: str) -> bool:
    """True when this address does not name a person."""
    match = EMAIL_RX.match(address.strip())
    if not match:
        # An address that is not an address at all -- an empty one, or a
        # local name with no domain.  It is not a mailbox, so it cannot
        # publish one; but it is also not a name we recognise.
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
    """Every (commit, role) whose identity names a person, and the count.

    ``role`` is "author" or "committer".  The address is never returned:
    see the module docstring.
    """
    commits = _git("-C", repo, "rev-list", "--all").stdout.split()
    if not commits:
        return [], 0
    raw = _git(
        "-C", repo, "log", "--all",
        "--format=%H%x1f%ae%x1f%ce",
    ).stdout
    findings: list[tuple[str, str]] = []
    total = 0
    for line in raw.splitlines():
        if not line:
            continue
        sha, _, rest = line.partition("\x1f")
        author, _, committer = rest.partition("\x1f")
        total += 1
        if not _acceptable(author):
            findings.append((sha, "author"))
        if not _acceptable(committer):
            findings.append((sha, "committer"))
    return findings, total


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    repo = argv[0] if argv else "."
    findings, total = find_bad_identities(repo)
    if total == 0:
        print("INCONCLUSIVE: no commits were scanned -- a gate that cannot see is not a gate")
        return 2
    if not findings:
        print(f"scanned {total} commit(s): every author and committer is a role "
              f"or a no-reply address")
        return 0
    print(f"{len(findings)} identity/identities in {total} commit(s) name a person.")
    print("Each one is a public page: the hosting site prints it, indexes it,")
    print("and keeps it.  The addresses are not printed here, for the same")
    print("reason this file does not contain them.")
    for sha, role in findings:
        print(f"  {sha[:12]}  {role}")
    print()
    print("Set the identity of the machine you push from to the repository's")
    print("own no-reply address, and rewrite the commit.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
