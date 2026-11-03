## What happened

A production turn on harness2 (the Durthu / Drycha install) died at
iteration 63, with 122 tool calls done, and the user saw:

```
⚠️ RuntimeError: minimax_m31_flash stream error: api_error: Internal
server error. Please retry your request. (500)
```

The provider said "Please retry your request". The harness did not
retry.

## Why

MiniMax delivered the 500 **inside a 200 response**, as an SSE `error`
event:

```json
{"type":"error","error":{"type":"api_error",
 "message":"Internal server error. Please retry your request. (500)"}}
```

The HTTP status was 200, so the retry ladder in
`base._post_with_retry` -- which keys off the status code -- never saw
it. The adapter's only recovery for a failed stream was "fall back to a
buffered POST", and that fallback was skipped whenever the attempt had
already streamed some text, because the fallback would have duplicated
it.

So the failure landed in the one case with no recovery at all: text
already shown. The same 500 hit the other install on 2026-11-03 and
RECOVERED -- purely because that attempt had streamed nothing yet.

## What this changes

- A new typed error, `TransientProviderError`, for a provider failure
  the same request may survive (a 5xx, an overload, a rate limit).
- The SSE `error` event is classified. Transient failures are retried;
  permanent ones (a bad request, a context window that is full) are
  not -- repeating those changes nothing.
- **With nothing shown yet**: the buffered POST, as before. It retries
  5xx at the HTTP level, where the status code is visible.
- **With text already shown**: the request is repeated, and the
  retry's text is suppressed. The client already saw the first
  attempt's text, and a retry answers differently (temperature 0.7),
  so the authoritative text arrives with the iteration's `iter_text`
  event instead -- the answer is never shown twice.
- The chat bubble reads a sentence, not a Python repr: "MiniMax
  returned a transient error while answering ...". It says the turn was
  not lost.

`llm.transient_retry_attempts` (default 2) and
`llm.transient_retry_backoff_s` (default 3.0) control the ladder.
Setting attempts to 0 restores the old behaviour.

## Tests

`tests/test_minimax_transient_retry.py` -- 10 offline tests: the
production case, no duplication of the answer, the buffered path when
nothing was shown, giving up when the provider stays down, a permanent
error not being retried, the context-window mapping surviving the
fallback, a caller with no live deltas keeping its old behaviour, and
the message the user reads.

Full suite: 433 passed, 16 skipped.
