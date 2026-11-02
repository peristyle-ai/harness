A turn that ends with the model promising work it has not done is not an answer, and the harness was accepting it as one.

## WHAT HAPPENED (2026-11-02, a production install)

A turn ran 50 tool iterations and ended like this, with no tool call:

> "... Let me check one last fact before I touch the shared code."

The user asked for a continuation. The next turn repeated the same shape and ended the same way: the model announced one more check each iteration and never made the edit it had promised.

## THE CAUSE

The harness has a guard for exactly this: the "intent without execution" nudge (`app/agent/loop.py`, `_INTENT_RE` / `_NUDGE_INTENT_MESSAGE`). It tells the model "you described an action but you did not issue a `tool_use` content block".

The guard is suppressed for the WHOLE turn once any earlier iteration landed a tool result:

```python
prev_successful_tool_intent = any(
    it.get("tool_results") for it in emission_iters
)
intent_narrates = not prev_successful_tool_intent
```

The suppression is there for a real reason - a wrap-up summary after a successful tool call mentions "sent", "rendered", "written" and must not be nudged. But it leaves the guard reachable only on the FIRST iteration of a turn. From iteration 2 onwards the model can end the turn on a promise and the harness takes the promise for the answer.

## THE FIX

The distinction is not "did a tool run" but "is the turn ENDING on a promise".

- `_FORWARD_PROMISE_RE` matches a promise of work not yet done ("let me check", "I'll verify") in the LAST sentence of the response.
- A promise is not suppressed by an earlier tool result: the tool call that does the promised work is still missing.
- A report of work already done ("I checked it, it is correct") is still not nudged, and a closing offer ("let me know if you want the other currency") is not a promise.
- When the nudges run out and the model is still promising, the turn ends with `reason=unfulfilled_promise` and says so, instead of presenting the promise as the answer:

  > The model ended this turn on an unfulfilled promise: it announced work it had not done (a "let me check" with no tool call), and that was NOT an iteration-cap problem (N of the 500 allowed tool iterations). Its last output is below.

The model's own words are kept - the work is not thrown away.

## TESTS

`tests/test_loop_unfulfilled_promise.py` - 8 tests, all driving the real agent loop over a fake adapter (no database, no model, no tool executed).

Three of them fail on the old code (the regression). The five that guard against over-firing - the wrap-up summary, the promise in an earlier sentence, the offer of later work, the first-iteration nudge, the normal answer - pass on both.

The full suite passes and the secret scan is clean.
