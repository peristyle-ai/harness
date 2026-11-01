## What the operator asked for

> "i would like the thought stream and tool call summarys to be collapsed
> by default"

The 1:1 chat has two collapsible sections under an answer:

- the **thought stream** -- the model's per-iteration narration
- the **tool-call trace** -- one line per tool call

Both are `<details>` elements, so both are closed by default. There was
one exception, and it was role-based: **an admin's chat opened both
sections automatically** -- at the start of the turn, and again at the
end, where the admin branch also dropped the scroll cap so every trace
item stayed visible. So the answer was buried under a wall of interim
narration and tool calls for exactly the person who reads this chat
most.

## The fix

`app/static/app.js`:

- the live bubble no longer opens the trace or the thought section for
  anyone; both start closed
- at the end of the turn both collapse for everyone, into the same
  static summary the non-admin path always used
- the `admin view` marker is gone from the bubble's meta line -- it
  marked the open trace, and there is no longer anything role-specific
  about the bubble

Nothing is lost by collapsing: the trace still carries its summary
("N iter - M tool calls - Zs") and the thought section still states how
many thoughts it holds, so a collapsed section still says what is
inside it. One click opens it.

Two things deliberately left alone:

- **the nudge still opens the trace while the turn runs.** A nudge means
  the model narrated a tool call instead of making one; that is an
  alarm, not a default state, and it is closed again when the turn ends.
- **the sections are `<details>`**, so the operator can leave one open
  during a turn and read it live.

## Tests

`tests/test_collapsed_trace_by_default.py` (new, 5 tests). Seen
**failing on the old code** first: 3 failed, 2 passed. The two that
pass on the old code are the guards -- the trace must still collapse at
the end of the turn, and a collapsed section must still carry its
summary.

## Merge order

This branch is based on `main` and carries version **0.1.23**, because
the other open pull request (#7, the group chat stream parity) already
carries **0.1.22**. Merge **#7 first**, then this one -- otherwise the
version walks backwards.

This pull request touches `app/static/app.js` only; #7 touches
`app/static/group.js` and `app/static/app.css`. They do not overlap.
