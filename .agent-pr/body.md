## What

This repository is public. It shipped with identifiers of one install still
in it. This change removes them and pins the properties that let them in.

What was in the tree:

| Where | What |
|---|---|
| `SECURITY.md` | a personal mailbox, as the second way to report a vulnerability |
| `scripts/agent_propose.sh` | the commit identity: an operator's name and a personal mailbox |
| `.github/CODEOWNERS`, `.github/workflows/agent-pr.yml` | the operator's name, four times |
| `app/`, `scripts/` | the operator's name, in 46 comments, in three grammatical forms |
| `app/agent/llm/base.py`, `minimax_m31_flash.py` | one install's name used as if it were the vendor's, in a comment describing behaviour for every install |
| `app/agent/loop.py`, `app/agent/turn_guard.py` | one install's agent name, in a comment about a duplicate turn |
| `app/config.py` | one install's agent name, as the example of an env var |
| `app/routes/api.py` | an operator-and-assistant pair named in a route comment |
| `tools/create_user.py` | a Unix account, in a usage example |
| `tests/test_safety_comment.py` | a module-level tuple of the Unix accounts this code is deployed under |
| `tests/` | one install's agent roster |

(The identifiers themselves are not repeated here. A pull request that removes
a leak must not put it back in the history.)

## Why it matters

A public repository is read by strangers, and it is indexed. A comment that
names the account, the mailbox or the roster of ONE install does not stay in
that install — it ships, and it tells a reader who runs this and who to write
to. The vendor's name is fine (it is the repository's own name); the
operator's personal details and the machine's account names are not.

The tuple of account names is the sharpest example. It existed to assert
that an account name did not appear in a comment — and it was itself the
leak, because it wrote the account names down in the repository.

## How it is fixed

The identifiers are gone, and each replacement is a generic word: *a production
install*, *the operator*, *a runtime role*.

Three properties are then pinned by tests:

**1. `tests/test_safety_comment.py` — the blocklist comment, pinned by shape.**
The old test listed the accounts to ban. The new one bans the *shape* of an
account (a lowercase token with an underscore or a digit) and the vendor's
name, and skips any token that also appears in another shipped file (a tool
name, a role name, a config key). The old list is gone; the assertion is
stronger, because it now catches an account nobody wrote down.

**2. `tests/test_no_install_identifiers.py` — new, six rules.** None of them
contains an account name, a hostname or an address:

- a mailbox is acceptable only at a placeholder domain or with a role local
  part (`security@`, `support@`) — a personal local part is a person;
- a path into a home directory is acceptable only with a placeholder account;
- a `sudo` switch to another user is acceptable only for a well-known system
  account;
- a systemd user unit is never acceptable — it names the account a unit runs
  as;
- a constant named for a collection of accounts or hosts is never acceptable
  when it is assigned one — that is the shape of the old leak;
- the agent roster must live in `config.yaml`, not in the source.

Each rule was checked against the tree it replaced: on `main`, three of them
fail. The hostname rule is deliberately absent — a generic hostname shape also
matches hex colours and unicode escapes, so it is too noisy to gate on, and
the docstring says so rather than pretending otherwise.

**3. The roster in the tests.** One install's agent names become `alpha`,
`beta`, `gamma` and `example`, and `default_agent` becomes the template's own
`peribot` — which is what `config.yaml.example` already ships, so the CI
installs the template's schema and not this project's.

## Note: the published history still contains the mailbox

This change fixes the tree. The commit that put the mailbox in `SECURITY.md`
is already on `main` and already public, and "fix the leak and move on" is not
enough — the commit is in the history, and a rewrite is an operator's
decision, not an agent's. It is flagged here, not taken.

The version is bumped to 0.1.12: a merge into `main` is a release.
