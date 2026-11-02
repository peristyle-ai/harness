## What

The two identity templates now carry the rules that keep a public repository
publishable: which repositories are public, the difference between EXTERNAL
information (about the outside world) and INTERNAL information (ours), the
five-step check before a push, and the rule that a commit identity is a NAME
(attribution) and an ADDRESS (how you reach a person) - and only the first
may be a person name.

Every fresh install renders its agent identity from these two files, so this
is the one place the rules are taught once and inherited everywhere.

## The one change I made to the text

The working copy this came from quoted two real mailboxes in the examples.
One was a third party address, and it is not in the published history of this
repository today - this pull request would have put it there. The other was a
placeholder local part on a real domain.

Both are now placeholders. The lesson is unchanged and reads better for it: a
repository that publishes the real address has published the person.

## Gates

- tests/test_no_install_identifiers.py: the mailbox rule failed on this file
  before the change and passes after it (9 passed).
- python -m pytest tests/ -q: 332 passed, 9 skipped. The 3 failures are
  pre-existing on a pristine origin/main and are not in these files: two
  assert the git signature resolver returns the name Harness Agent (it
  returns the locally configured name), and one scans the whole local git
  history, which includes branches that are not published.
- scripts/check_no_secrets.py: clean over the working tree and the full
  history.

Not merged - the diff is for the operator to read.
