#!/usr/bin/env python3
"""Fail if a public BRANCH of this repository carries one install's details.

WHY THIS EXISTS
---------------
``tests/test_no_install_identifiers.py`` scans the working tree.  The
working tree is not the whole public repository.  Every branch is a
public page, and a branch that was merged and then left behind still
publishes the tree it was merged from -- which, for a branch that
predates a de-identification sweep, still names the install it was
merged from.

That is not a hypothesis.  It is what this repository looked like: the
sweep fixed ``main`` and left four merged branches publishing what the
sweep had just removed, so the names were still one click away.

So this applies the SAME rules (``scripts/identifier_rules.py``) to
every branch the remote advertises.

WHAT IT PRINTS
--------------
The branch, the file, the rule -- and never the value.  A CI log on a
public repository is public, indexed and immutable: it outlives the
branch that carried the identifier, so a gate that printed the
identifier would republish exactly what it is here to remove.  The
value is in the branch; the log says where to look.

WHAT IT SCANS
-------------
Every branch, and every text file in that branch's tree.  The whole
tree, not only the application: a README, a workflow, a test fixture
and a pull-request body are all public, and every one of them has
carried an install's details in this repository.

WHAT IT DOES NOT SCAN
---------------------
The pull-request refs (``refs/pull/*/head``).  GitHub keeps those
forever and serves them, so they are public too -- but they belong to
GitHub rather than to this repository, and deleting one is a decision
about the project's record, not a piece of hygiene.  Run this script
with ``--include-pull-refs`` to audit them.

Exit codes:
  0  every public branch is clean
  1  a public branch carries an identifier
  2  INCONCLUSIVE -- the branches could not be enumerated or read.
     That is a failure too: a gate that cannot see is not a gate.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import identifier_rules as rules  # noqa: E402

# The namespace the gate fetches a branch into when the local clone has
# not seen it yet.  It is not a branch: nothing pushes to it, nothing
# serves it, and it is removed when the run ends.
SCRATCH_REF = "refs/public-ref-scan"

# The namespace the remote's own branch names live in, locally.
TRACKING_REF = "refs/remotes/{remote}/{name}"

# The namespace the pull-request refs live in, locally.
PULL_REF = "refs/pull/{number}/head"


class CannotSee(Exception):
    """The gate could not read something it must read."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )


def _require(repo: Path, *args: str) -> str:
    proc = _git(repo, *args)
    if proc.returncode != 0:
        raise CannotSee(
            f"`git {' '.join(args)}` failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def advertised_branches(repo: Path, remote: str) -> dict[str, str]:
    """Every branch the remote has, as {name: sha}.

    The remote is the authority.  A branch this clone has never heard
    of is still a public page.
    """
    out = _require(repo, "ls-remote", "--heads", remote)
    branches: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            branches[ref[len("refs/heads/"):]] = sha.strip()
    if not branches:
        raise CannotSee(f"the remote {remote!r} advertises no branches")
    return branches


def local_ref(repo: Path, ref: str) -> str | None:
    """The sha a local ref points at, or None."""
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def resolve(repo: Path, remote: str, name: str, sha: str) -> str:
    """A local ref holding this branch's tip, fetching it if needed.

    The gate must see every public branch.  If this clone has not
    fetched the branch yet, the gate fetches it -- into a scratch
    namespace, not into the branch list, so a failed run cannot leave a
    stray branch behind.
    """
    tracked = TRACKING_REF.format(remote=remote, name=name)
    if local_ref(repo, tracked) == sha:
        return tracked
    scratch = f"{SCRATCH_REF}/{name}"
    proc = _git(repo, "fetch", "--no-tags", remote,
                f"+refs/heads/{name}:{scratch}")
    if proc.returncode != 0 or local_ref(repo, scratch) != sha:
        raise CannotSee(
            f"branch {name!r} is advertised by the remote but could not be read: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return scratch


def _read_tree(repo: Path, ref: str) -> dict[str, str]:
    """Every text file in one ref's tree, as {path: text}."""
    files: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tarball = os.path.join(tmp, "tree.tar")
        with open(tarball, "wb") as fh:
            proc = subprocess.run(
                ["git", "-C", str(repo), "archive", "--format=tar", ref],
                stdout=fh, stderr=subprocess.PIPE,
            )
        if proc.returncode != 0:
            raise CannotSee(
                f"could not read the tree of {ref}: "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        with tarfile.open(tarball) as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                data = handle.read()
                if b"\0" in data[:8192]:
                    continue  # a binary file: not text, so not a document
                files[member.name] = data.decode("utf-8", errors="replace")
    return files


def scan_tree(repo: Path, ref: str) -> list[tuple[str, str]]:
    """Every (path, rule) in one ref's tree.

    The VALUE is deliberately not carried out of this function: what a
    gate prints is public, and the value is the thing being kept out
    of the public repository.
    """
    files = _read_tree(repo, ref)
    # The agent names this branch may name come from the BRANCH's own
    # example config: a branch that ships a different template ships a
    # different roster, and the gate has to judge that branch by the
    # rules that branch ships.
    allowed = rules.GENERIC_AGENT_NAMES | rules.template_agent_names(
        files.get(rules.CONFIG_EXAMPLE, "")
    )
    findings: list[tuple[str, str]] = []
    for name, text in files.items():
        if name.split("/")[-1] in rules.GUARD_FILES:
            continue
        for rule, _detail in rules.find_offenders(
            text, path=name, allowed_agents=allowed
        ):
            findings.append((name, rule))
    return findings


def pull_refs(repo: Path) -> list[str]:
    """Every pull-request head ref this clone has."""
    out = _git(repo, "for-each-ref", "--format=%(refname)", "refs/pull/")
    return [line for line in out.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=None,
                    help="the repository to scan (default: the one this script is in)")
    ap.add_argument("--remote", default="origin",
                    help="the remote whose branches are public (default: origin)")
    ap.add_argument("--include-pull-refs", action="store_true",
                    help="also scan the pull-request head refs")
    args = ap.parse_args(argv)

    if args.repo:
        repo = Path(args.repo).resolve()
    else:
        repo = Path(__file__).resolve().parent.parent
    if not (repo / ".git").exists() and not _git(repo, "rev-parse", "--git-dir").stdout:
        print(f"INCONCLUSIVE -- {repo} is not a git repository")
        return 2

    scratch_refs: list[str] = []
    findings: list[tuple[str, str, str]] = []
    try:
        branches = advertised_branches(repo, args.remote)
        for name, sha in sorted(branches.items()):
            ref = resolve(repo, args.remote, name, sha)
            if ref.startswith(SCRATCH_REF):
                scratch_refs.append(ref)
            for path, rule in scan_tree(repo, ref):
                findings.append((name, path, rule))

        if args.include_pull_refs:
            for ref in pull_refs(repo):
                name = ref.rsplit("/", 2)[-2]
                for path, rule in scan_tree(repo, ref):
                    findings.append((name, path, rule))
    except CannotSee as exc:
        print(f"INCONCLUSIVE -- {exc}")
        return 2
    finally:
        for ref in scratch_refs:
            _git(repo, "update-ref", "-d", ref)

    if findings:
        print(f"{len(findings)} finding(s) in the public branches:\n")
        for name, path, rule in findings:
            print(f"  {name}:{path}  [{rule}] {rules.describe(rule)}")
        print("\nA branch is a public page.  Delete the branch, or remove the "
              "identifier from it and re-merge.")
        return 1

    print(f"every public branch is clean ({len(branches)} branch(es) scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
