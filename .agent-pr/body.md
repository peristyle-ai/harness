The env-var blocklist in `app/agent/tools/_safety.py` strips secret-looking
variables from the environment the model inherits. That behaviour is
install-wide: every install runs the same blocklist, against its own
service environment.

Its comment, however, described "the peristyle runtime" -- naming one
install's Unix account in a file that ships to every install. On an
install that runs under a different account, that sentence reads as if the
blocklist belonged to an account that has nothing to do with it. It is the
same class of mistake the exec_bash tool description carried into every
install's tool registry (fixed separately in #12): a shipped string naming
one machine instead of describing the behaviour.

The change is one sentence in one comment: "The peristyle runtime inherits"
becomes "The harness service inherits". What is stripped, how, and why are
untouched -- this is a wording fix, not a behaviour change.

A test pins the wording so it cannot drift back: the comment block above
`EXEC_BASH_SAFE_ENV_KEYS` must not name a Unix account that exists on a
deploy box, and it must keep explaining the threat it removes (the model
could `printenv` what the service inherited). The new test was seen
failing on the old code before the comment was changed; the suite and the
secret scan are green.

Not in this PR, and deliberately: `_safety.py` also refers to the
production database by its name in the DSN helpers. That is a database
name, not an account, and it is a separate concern.

Version: 0.1.6 -> 0.1.8. PR #12 is open and holds 0.1.7, so this one is
numbered after it to keep the two open releases distinct. Merge #12 first.
