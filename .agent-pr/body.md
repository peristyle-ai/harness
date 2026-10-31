## What

`GET /api/v1/entities/<name>/turns/active` looked the in-flight turn up
with the user who STARTED it:

```python
state = turn_guard.find(agent_name=name, user_id=user_id)
```

So a browser watching an agent that another session had started saw
`{"active": false}` — while the agent was deep inside a turn.

## Why that matters

That endpoint exists for one reason: the live bubble in the web chat is
client-side DOM, so a refresh (or a dropped connection) loses it while
the turn keeps running server-side. The endpoint replays the turn's
buffered events so the bubble can be rebuilt.

With a user-scoped lookup, the rebuild silently could not happen for
any browser that was not the one that sent the message — a second tab,
a second browser, or a colleague watching the same agent. The chat then
looked dead for the whole turn, and the answer arrived in the database
with nobody watching it. That is the "the agent is stuck" symptom this
endpoint exists to prevent, reproduced by the lookup itself.

Observed on a live install: a turn that ran 151 iterations over 43
minutes was `active: true` for the session that started it and
`active: false` (a 17-byte body) for the browser that was watching it.

## The fix

The read is split from the steering lookup, which stays user-scoped on
purpose (one user's message must never be queued into another user's
turn).

- the user's own turn is found first, exactly as before;
- for an **admin**, the lookup falls back to the agent's turn,
  whichever session started it;
- a member still only ever sees their own turn.

The fallback is admin-only on purpose: an in-flight turn's buffered
events contain the turn's own text, and that is the operator's business,
not every member's.

## Tests

`tests/test_active_turn_visibility.py` pins the behaviour: an admin
sees the agent's turn, a member does not, the user's own turn is still
found by the user-scoped lookup, and the route goes through the helper
rather than doing its own lookup.
