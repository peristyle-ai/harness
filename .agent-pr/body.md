## What

An install whose agent name is not lower case could never take a turn in a
group chat. Every `@mention` failed with:

    could not open the conversation for EXAMPLE: invalid agent name: 'EXAMPLE'

This adds `app/agent/names.py` with `canonical_agent_name()` and uses it for
the **schema identifier** only, in the two places that interpolated an agent
name into a table name.

## The symptom

Found on a live install whose agent name was not lower case
(`app.agents: [EXAMPLE]`, pie directory `pie/<AGENT>/`):

* the 1:1 chat works fine,
* the group chat is 100% broken for that agent - every mention, every group,
* the install looks healthy: the service is up, the schema is right, the
  agent answers in 1:1.

Nothing validates `app.agents` at boot, so there is no early warning.

## Root cause

`app/models/group.py` (and `app/agent/reverie.py`) validated the agent name
against a lower-case-only pattern before interpolating it into SQL:

    _AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

The guard exists for a good reason - the name becomes a table name - but it
was checking the wrong string. The agent name is in fact two things:

| form | example | what it is |
|---|---|---|
| SCHEMA | `example` | the per-agent schema (`<agent>.conversations`, `.turns`, `.memories`) - interpolated into SQL, so it is the injection guard |
| DISPLAY | `EXAMPLE` | what the roster stores, what the HMI shows, what the `conversations.agent_name` column holds |

Postgres folds an unquoted identifier to lower case, so `EXAMPLE.conversations`
already resolved to the schema `example` everywhere the name was interpolated
*without* a guard - which is why the 1:1 chat worked. The group path was the
only place with a hard check, so it was the only place that failed.

And the display form must not be folded: `list_conversations` filters
`c.agent_name = %s` with the config's own spelling, so the column has to keep
`EXAMPLE` exactly.

## The fix

* new `app/agent/names.py` - `canonical_agent_name()` lower-cases, then
  validates against the same `^[a-z][a-z0-9_]{0,39}$` pattern. Injection
  attempts are still refused (there is a test for it).
* `app/models/group.py::ensure_agent_conversation()` - the schema is the
  canonical form; the column keeps the display form.
* `app/agent/reverie.py::activity_count()` - the same guard, which was
  waiting to kill the nightly turn the moment `reverie.enabled` was turned on.

No data migration: the schema is already `example`, and the existing 1:1 rows
keep the display name, so they still match.

## Tests

`tests/test_agent_name.py` (new): the fold; the refusal of
injection attempts; that the INSERT names the lower-case schema while the
column keeps the display form; that a mention of an upper-case agent is
matched, and that `@example` resolves to that same agent rather than to a
second one; and that `reverie.activity_count()` uses the schema form for the
upper-case display name. That half of the fix was untested at first -
reverting `reverie.py` left the whole suite green while the nightly turn
would have died again on an install with an upper-case display name.

Full suite: **235 passed, 9 skipped**.

## Live verification (a real install, before this pull request)

The fix was applied on that install on a local branch first, then:

* the service restarted cleanly (`bootstrap_registry: 1 agent(s) loaded`);
* a real `@EXAMPLE` message through the real HTTP path produced `agent_start`
  -> the agent's reply in the group transcript -> `agent_done`;
* the row that could not exist before now does - a group conversation row in
  the agent's own schema, with the display form in the `agent_name` column
  and `is_group` true.

## Not included (a separate change)

The tidier end state is a lower-case agent *id* (`example`) with the display
name (`EXAMPLE`) in `pie/<agent>/identity/IDENTITY.md`. That is a migration on
an existing install - the pie directory, `app.agents`, the roster rows and the
schema name - not a code fix, so it is deliberately not in this pull request.
A boot-time warning for a non-canonical name would be a sensible follow-up.

## A note on the history

This branch was rewritten into a single commit. The original commit carried
one live install's agent name, its pie directory, a live group conversation
title and a live conversation id - in the code comments, in the tests and in
the commit message. This repository is public, so those are customer-specific
details and they must not ship. The examples are now synthetic placeholders
(`EXAMPLE`, `example`, `pie/<AGENT>/`), and the commit that introduced them
is no longer in this branch's history.

---

*Note on the examples: the agent name, the pie directory and the group title
used in this description are synthetic placeholders. No install-specific name,
identifier or title is included in this pull request.*
