The working tree is not the whole public repository.

`tests/test_no_install_identifiers.py` scans the tree, and it does its
job. But every branch is a public page too, and a branch that was
merged and then left behind still publishes the tree it was merged
from. For a branch that predates a de-identification sweep, that tree
still names the install it was merged from -- so the sweep fixes `main`
and the names stay one click away on the branch list.

That is not a hypothesis: it is what this repository looked like this
week. The sweep landed, `main` went clean, and the branches merged
before it kept publishing what the sweep had just removed. This change
makes that state impossible to miss again.

WHAT CHANGES

- `scripts/identifier_rules.py` (new) holds the rules, once. The
  working-tree guard and the new gate apply the same rules; two copies
  of "the rules" is how they drift apart, and a rule that only one of
  them applies is a rule that is not enforced.
- `scripts/check_public_refs.py` (new) applies them to every branch
  the remote advertises, and to every text file in that branch's
  tree. It fetches a branch the local clone has never seen, so it
  cannot pass by looking at less than the remote has.
- `tests/test_check_public_refs.py` (new) plants a branch carrying a
  mailbox and watches the gate fail on it. A gate that has never been
  seen failing is not a gate.
- `tests/test_no_install_identifiers.py` now imports the shared rules,
  and gains one test that the rules still MATCH what they look for.
  Every other assertion in that file is an absence, so a rule set that
  quietly stopped matching would have made them all pass.
- `.github/workflows/tests.yml` gains a `public-ref-scan` job.

The agent-roster rule -- the one added in #23 -- moves into the shared
module as well, so the branch gate applies it too. A rule that only
the working-tree guard applies is a rule that is not enforced.

THE GATES REPORT WHERE, NOT WHAT

A gate that prints the identifier it found has republished it. A CI log
on a public repository is public, indexed and immutable, and it
outlives the branch that carried the identifier -- so printing the
value would make "delete the branch" pointless. Both gates report the
branch, the file and the rule, and never the value; the value is still
there for anyone who opens the file.

EXIT CODES

0 clean, 1 a branch carries an identifier, 2 INCONCLUSIVE. A gate that
cannot see is not a gate, so "I looked at nothing" is a failure, not a
pass.

KNOWN STATE

This job is RED on `main` today, and it is telling the truth. Five
public branches still carry identifiers:

- four branches merged before the sweep, which still publish the tree
  they were merged from; and
- the Dependabot branch, which was cut after the first sweep and
  before the second one, so it still carries the agent names the
  second sweep removed.

That fifth one is the gate earning its keep: the two sweeps cleaned
`main`, and neither of them looked at a branch.

The gate names the branches. Deleting them is a decision about this
repository's public record rather than a piece of hygiene, so it is not
one an agent should make on its own -- and the job stays red until it
is made. That is the exit: the gate goes green the moment the stale
branches are gone, and it does not rot in the meantime.

And it cannot be un-published from the history: a commit that was once
a branch tip stays reachable, and stays served. That is a fact about
GitHub, not a defect in this gate.
