## What the operator saw

In the group chat (the peristyle session's group page):

- **no thought stream at all**
- **every tool call twice**
- **poor tool-call formatting**
- the trace **disappeared** when the turn ended

## Why

The server was never the problem. `app/agent/group_chat.py` forwards EVERY
event the agent loop emits - `iter_start`, `delta`, `iter_text`, `thought`,
`tool_start`, `tool_end`, `nudge`, `final` - each tagged with the agent it
belongs to. The data reached the browser; the group chat client threw it
away. `app/static/group.js` had never been updated for the streaming work
the 1:1 chat got on 2026-10-25.

Five concrete gaps:

1. **No `case 'thought':`.** The client's event switch had no branch for
   the thought event, so the model's per-iteration narration was invisible,
   and the live bubble had nowhere to render it.
2. **`tool_end` appended a second line.** `tool_start` wrote a
   "tool call (args)" line and `tool_end` appended a *separate* "done"
   line instead of finishing the first one. The 1:1 client updates the
   pending line in place; the group client did not, so every tool call
   appeared twice.
3. **The styles were scoped to the wrong bubble class.** Every trace and
   thought rule in `app.css` was scoped to `.msg` (the 1:1 bubble). The
   group bubble is `.gmsg`, so the group's `<details class="trace">`
   rendered with **no styling at all** - no muted summary, no coloured
   items, no scroll cap. That is what "poor formatting" was.
4. **"thinking..." in the answer slot.** The group's `iter_start` branch
   overwrote the answer body with a placeholder. The operator had already
   rejected exactly that in the 1:1 chat: the body is for the answer, the
   marker belongs in the thought section.
5. **The trace was thrown away at the end of the turn.** The persisted
   transcript row replaces the live bubble wholesale, and the persisted
   bubble carried no trace - so the tool-call summary vanished with the
   turn it described.

## The fix

`app/static/group.js`:

- the live bubble gains a `<details class="thoughts">` section, exactly as
  the 1:1 chat has it - live only, not a turn, and **collapsed by
  default**
- a `thought` branch renders the narration, and the iteration marker moves
  out of the answer body into the thought section
- `tool_start` records the tool name and its args on the line;
  `tool_end` finds that line and finishes it in place - **one line per
  tool call**, `done` or `failed`, with the error preview on the title
- a `nudge` branch shows the "the model narrated a tool call" signal, as
  the 1:1 chat does
- the trace and the thought stream are carried over to the persisted
  transcript bubble, so the tool-call summary survives the turn
- when the turn ends, the trace collapses into the same static summary
  the 1:1 chat uses ("N iter - M tool calls - Zs")

`app/static/app.css`: every trace and thought rule now covers both bubble
classes (`.msg` and `.gmsg`).

## Tests

`tests/test_group_chat_stream_parity.py` (new, 11 tests). They were seen
**failing on the old code** - 8 failed, 2 passed - and pass after the fix.

Two of them pass on the old code on purpose: one pins that the server
already forwards the thought event (so the gap is the client, and nobody
"fixes" the forwarding instead), and one pins that the group's sections
start collapsed.

The last one parses `group.js` with node, when node is installed, and is
skipped when it is not - a syntax error in that file would kill the whole
group chat.

The server-side test is a behavioural one: it runs `run_group_message`
with a fake loop and asserts the thought event comes through tagged with
its agent.
## The service worker cache

`app/static/sw.js` is CACHE-FIRST for the static shell: a browser that
already installed it keeps serving the OLD `app.js`, `group.js` and
`app.css` until the cache name changes. This change alters the bundle,
so the cache name moves `harness-v11` -> `harness-v12`; without that,
the fix would reach the server and never reach an installed PWA.

`tests/test_group_chat_stream_parity.py` pins it: a change that touches
the static bundle must change the cache name in the same change. That
test was seen failing on the old code (`assert 'harness-v11' !=
'harness-v11'`) and passes now. A second test pins that the service
worker precaches every static asset the chat pages load.

(The other open pull request, #8, bumps the cache to `harness-v13`, so
the two bumps differ whichever order they are merged in: a change to
`sw.js` makes the browser re-precache the current bundle.)
