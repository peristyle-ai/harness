The operator reads the history of this repository to see which agent did
which piece of work — and sometimes asks one agent to step in for
another. A commit is signed with the NAME of the agent that made it.
That is what a signed commit is for.

The identity gate built for this repository went the other way: it
rejected an author that "names a person", which would have made the
operator's own workflow impossible — an agent following the
documentation would have pushed a branch whose CI went red on the very
attribution it was supposed to carry.

THE TWO ARE NOT THE SAME KIND OF THING
--------------------------------------
A commit identity is a name and an address:

* the **address** is how you REACH a person, and publishing one in a
  public repository is the leak the gate exists to stop;
* the **name** is attribution.

So the gate now polices the address, and lets the name through. It
still refuses one thing: a name that is itself an address. That is the
leak wearing a hat, and it would slip past a reader looking only at
the name.

WHAT CHANGES

- `scripts/check_commit_identities.py` — the rule is now "no commit
  carries a MAILBOX". The address must be a no-reply, a placeholder or
  a role address; the name may be anything, because the name is
  attribution. `address_is_acceptable()` is public now: the tooling
  that signs a commit asks the same question, rather than keeping a
  second copy of the rule that could drift.
- `scripts/agent_signature.sh` (new) — resolves the identity a commit
  should carry: an explicit override, else the agent's own
  `IDENTITY.md` name, else the handle the runtime exports, else
  "Harness Agent". The address is always the repository's own no-reply
  address, and an override is honoured only if it passes the same rule
  the gate enforces.
- `app/agent/tools/exec_bash.py` — exports the agent's name into the
  environment of every command it runs. The tool context already knew
  which agent was running; the environment is what a command can read,
  and it is what lets the tooling sign with it.
- `scripts/agent_propose.sh` — signs the commit with the resolved
  identity instead of a fixed name.
- Tests for both: one plants the identities that must be refused, and
  one pins where the name comes from. An agent whose identity file is
  missing still signs as itself.

Two test files are exempt from the tree guard for the same reason the
existing one is: they plant an address, to prove the rule works.

The full suite passes, and the gates are clean.
