## What

Three changes, one release (0.1.8). Each was validated on its own before it
was batched here: a merge into `main` IS a release, so small fixes that were
each proven belong in one versioned pull request rather than a queue of
one-liners.

| # | change | source |
|---|--------|--------|
| 1 | the buffered fallback must not ask for a stream | #14 |
| 2 | a read timeout on a big request is not retried, and says what was sent | #15 |
| 3 | operator mode — an opt-in per-install config option | #16 |

### 1. The buffered fallback must not ask for a stream (#14)

The M3.1-Flash adapter's streaming-to-buffered fallback re-used the
streaming request body, which still carried `stream: True`. The "buffered"
retry therefore came back as `text/event-stream` and the JSON parse raised
`JSONDecodeError` — fatal, not a degradation. It killed the 2026-10-28 wisp
reverie and a heartbeat.

The fix strips the streaming flag from the fallback body only; the SSE path
still asks for it. New test: `tests/test_minimax_buffered_fallback.py`
(reproduced the production failure first, then passed).

### 2. A read timeout on a big request is not retried (#15)

Evie's turn 559 ran 2h27m and 147 iterations, then died with a read
timeout — after the harness spent 12 of those minutes re-sending the same
2.4 MB body. A read timeout means the upstream *received* the request and
did not answer, so the same retry re-uploads the same body and waits the
same window again. Worse, the streaming-to-buffered fallback sends the same
body where the buffered path must wait for the whole answer — strictly
slower than the stream that just timed out.

The fix: a read timeout on a large request is not retried, and the error
says what was sent (size, window, model). Small requests and dropped
connections still retry. New test:
`tests/test_minimax_large_request_transport.py`.

### 3. Operator mode — an opt-in per-install config option (#16)

`security.allow_sudo: true` lets the agent escalate through the command
classifier, bounded by the operator's own grants on the machine. It is
**off by default** at every layer — the config default, the shipped example
config, the tool context and the system prompt — so merging this changes no
install's posture. Every other hard block stands, the soft-flag rules are
unchanged, and the shipped systemd unit keeps both of its hardening flags
unless the installer is told otherwise.

This change also fixes a real gap the new tests exposed: the *symbolic*
setuid form was not blocked (the `u+s` mode form was allowed while the
numeric form was refused). Both are refused now.

The escalation command itself is named in the diff and in SECURITY.md's new
"Operator mode" section. This body does not repeat it: the install
proposing this change has its own command classifier, which hard-blocks that
token anywhere in a command string, and it is not going to obfuscate a word
to slip past its own guard.

### The trade-off, stated plainly

Operator mode widens the blast radius of a prompt injection: by default a
hostile instruction in content the agent reads can at most run commands as
the install's Unix user; in operator mode it can additionally use whatever
the operator has granted. That is acceptable when the grants are narrow and
the agent's inputs are the operator's own. It is not acceptable if the
agent reads attacker-controlled content. The decision belongs to the
operator of the install, per install.

## Gates

- `python -m pytest tests/ -q` — **222 passed, 9 skipped**
- `scripts/check_no_secrets.py` — clean (working tree, full history, commit messages)

## Validation

Changes 1 and 2 were each deployed to the production install and verified
there, in the production venv with the production config and the live
endpoint — the exact production failure was reproduced against the old path
and survived on the new one. Change 3 is off by default and was not enabled
anywhere by this merge.
