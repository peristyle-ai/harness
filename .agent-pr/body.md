One batch, one release. It supersedes six open pull requests that were
each a piece of the same work, and adds the fix for the report that
prompted it.

## What is in it

**The group chat renders what the 1:1 chat renders** (was #7). The
server was never the problem -- `group_chat.py` forwards every event the
agent loop emits, each tagged with its agent -- but the group client
threw the data away: no `thought` branch, every tool call drawn twice
(`tool_start` appended a line and `tool_end` appended a second one
instead of finishing the first), the trace and thought styles scoped to
`.msg` while the group bubble is `.gmsg` (so the group's `<details>`
rendered with no styling at all), "thinking..." written into the answer
slot, and the trace thrown away when the persisted row replaced the
live bubble.

**The thought stream and the tool-call trace are collapsed by default**
(was #8). It used to be open for admins. Now it is closed for everyone,
and the summary still says how much is inside, so a closed section is
informative.

**The live status line** (was #11). The model's latest thought now shows
at the top of the bubble, live, in both the group chat and the 1:1 chat.
It is a separate element from the answer on purpose: a thought must not
overwrite the answer, and the answer must not wipe the thought.

**A finished turn keeps its thought stream** (new). This is the fix for
the report that prompted the batch: *"check on evie's session, I can't
see the thought stream."* The thought stream was LIVE ONLY -- the client
built it from the `thought` event and it lived in the live bubble's DOM.
When the turn ended, the client re-rendered the history from
`GET /entities/<name>/turns`, and that endpoint returns a thin row:
role, content, model, attachments, created_at. So the persisted bubble
was the answer and nothing else.

The narration was never lost. The loop writes the per-iteration
narration and the tool calls into `turns.trajectory`; it was in the
database and on no wire. Two halves:

  * `GET /entities/<name>/turns?trail=1` adds a compact `trail` per
    assistant turn -- one line of narration plus the tool names per
    iteration -- computed **in SQL** from the stored trajectory.
  * The client renders it: the same collapsible thought section and the
    same tool-call trace the live bubble has, closed by default.

It is opt-in because the raw trajectory is large: ~670 KB for one busy
turn, almost all of it the thinking blocks, and the loop's own
measurements put a turn's replayed thinking at 2.3 MB. Fifty of those
is ~33 MB for one page load. The compact form is ~20 KB for the same
50 turns, because Postgres computes it and the thinking blocks are never
read.

**A turn that ends on a promise is not an answer** (was #12). The
"intent without execution" nudge existed, but it was suppressed for the
whole turn once any earlier iteration had landed a tool result -- which
is a wrap-up summary saying "sent"/"written", and must not be nudged.
So the guard was reachable only on the *first* iteration of a turn, and
from iteration 2 on the model could end a turn on a promise and the
harness took the promise as the answer. The detector now matches the
*shape* of an announcement -- a forward frame, or a bare progressive, in
the last sentence -- and not a report of work already done.

**A long turn wraps up on purpose instead of losing its tail** (was #14).
The context ceiling is now 800k tokens with a soft threshold at 600k.
Past the soft threshold the model is told, in the system prompt, to
write a pinned memory carrying the handoff, report where it got to, and
end with an ordered plan to be resumed next turn. A turn told to wrap
up gets a wrap-up nudge -- not the intent nudge, which would push it
straight back into the work loop it was asked to leave.

**The public-repo hygiene rules, without publishing a person** (was #13).
The templates teach the rules once and every new install inherits them.
The addresses in the example are placeholders: a real mailbox quoted in
a commit message is a real mailbox in the public history.

## What breaks

Nothing that was working. The trail is opt-in and off by default, so the
ordinary chat load stays thin. The `?trail=1` response is larger than
before by roughly the size of the narration.

## What to do

Reload the page once. The service worker is cache-first for the static
shell, so the new bundle needs a new cache name: `harness-v15`, which
replaces the three names the separate branches claimed (`v12`, `v13`,
`v14`). Two changes on one cache name means the second never reaches an
installed PWA.

## The gates

- `pytest`: 411 passed, 9 skipped. The three failures are pre-existing
  and identical on a pristine `origin/main` (the signature resolver
  reads this install's configured git name; the identity gate scans
  `git log --all` over three stale local branches).
- `secret-scan`: clean over the working tree and the full history.
- `public-ref-scan` / `identity-scan`: clean.
- The new test file was **seen failing first**: 8 failed / 4 passed on
  the old code, and 4 of those 4 are the guards that must keep working.
- The trail's SQL is proved by a **database** test, not by reading the
  source. That is not decoration: the first version of this built the
  statement by splicing a column into it, and with the trail off that
  left a bare `AS trail` -- a syntax error. Every source-reading test
  passed; it reached production, and the only thing that caught it was
  calling the deployed code against the production database. The
  statement is now one shape, always, with the flag as a bind
  parameter, and `tests/test_trail_sql_runs.py` runs the service's own
  statement against a real database in both modes. It was seen failing
  on the old code: 5 failed, then 5 passed.
