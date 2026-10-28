The SSE -> buffered fallback in the M3.1-Flash adapter re-used the
streaming request body, which still carried `stream: True` from
`_build_body`. The endpoint honours that flag, so the "buffered" retry
came back as `text/event-stream` and `r.json()` raised

    JSONDecodeError: Expecting value: line 1 column 1 (char 0)

That is fatal, not a degradation: the turn ends instead of falling back.

Found on the peristyle install (2026-10-29), where it ended the
2026-10-28 wisp reverie and the 17:06 heartbeat the same day. The
nightly reverie that surfaced it spent 80 iterations chasing the same
error before its own iteration cap stopped it.

The fix strips the `stream` key from the fallback request only. The SSE
path still asks for a stream, and the buffered path (`streaming: false`)
was never affected.

The new test is offline: a fake client that mimics the endpoint, where
a body with `stream: true` comes back as SSE and does not parse as JSON.
It was seen failing on the old code -- the fallback died with
JSONDecodeError -- and it passes now. The other three tests pin the
paths that already worked.

One concern: the adapter only. The safety classifier, the tool
descriptions and the loop diagnostic are untouched.
