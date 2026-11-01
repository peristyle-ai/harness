The identifier rules read file CONTENT. The author of a commit is not
in any file -- and a commit is a public page: the hosting site prints
the author and the committer, indexes them, and keeps them.

The work in this repository is done by agents pushing from the
installs, and an agent pushes with the git identity of the install it
pushes from. Until this change, that identity was a person's mailbox at
the operator's own domain. The history of this repository was scrubbed
of it; the next commit after a scrub would have put it straight back,
and nothing would have said so.

WHAT CHANGES

- `scripts/check_commit_identities.py` (new) reads the author and the
  committer of every commit in the history. An identity is acceptable
  when it is a no-reply address at the hosting site's own domain, a
  placeholder domain, or a ROLE local part -- a role says "this is a
  function", and a person's name says "this is a person".
- `.github/workflows/tests.yml` gains an `identity-scan` job that runs
  it over the whole history.
- `tests/test_check_commit_identities.py` (new) plants commits with the
  identities that must not be published, and asserts that the scanner
  finds them.

THE SCANNER NEVER PRINTS THE ADDRESS

A gate that prints the identifier it found has republished it: a CI log
on a public repository is public, indexed, and immutable, and it
outlives the branch that carried the identifier. The finding is
reported as the commit and the role; the address is only counted. There
is a test for that too.

A RULE THAT CANNOT SEE IS NOT A RULE

Scanning zero commits reports INCONCLUSIVE and exits non-zero. Silence
is not a pass.

ONE MORE FILE IS EXEMPT FROM THE IDENTIFIER GUARD

The new test plants an address, to prove the scanner catches one. A
file that has to contain the shape to prove the rule works cannot also
obey the rule about that shape -- which is the same reason the rules
module and the tree guard are already exempt.

The full suite passes, and the other gates are clean.
