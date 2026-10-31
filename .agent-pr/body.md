## What

A model that returns a **thinking block and nothing else** — no text, no
`tool_use` — leaves the harness with no answer at all, because the minimax
adapter drops thinking blocks from `.text`. The turn then simply **ended**:
the task half done, with hundreds of iterations unused.

Incident (**a production install, 2026-10-31**): a turn that had run **30 real
tool iterations of the 500 allowed** returned a reasoning-only response and
the turn ended. The work in flight was not written — the operator found a
half-finished edit in the live install's working tree.

## The fix

The narrate path already nudges the model and re-issues the call; this
failure mode had no equivalent. Now it does:

- a reasoning-only response is **nudged**, under the same `_MAX_NUDGES`
  cap the narrate path uses, and the turn continues;
- the nudge carries the model's **own last reasoning** back to it, so it
  resumes the thread instead of starting over;
- once the nudges are spent, the turn ends with the existing *"ended this
  turn without an answer"* diagnostic — so this cannot loop.

An adapter that returns nothing at all (no thinking either) is a different
failure — a broken transport — and still ends the turn at once.

The iteration count in that diagnostic now includes the nudges, so it says
what the turn actually used.

## Tests

`tests/test_loop_end_of_turn.py` — three new cases:

- the turn continues and ends with the answer (not the no-answer
  diagnostic);
- the nudge carries the model's last reasoning back to it;
- the nudges cannot loop forever (a model that only ever reasons still
  ends the turn).

The existing *"iterations actually used"* assertion is updated for the
nudges. Full suite: **225 passed, 9 skipped**.

## Note for the operator

The fix is in the loop, not in the install: an install on an older harness
still ends the turn the old way, so it needs this merged and pulled before
the agent's reasoning-only responses are nudged rather than fatal.
