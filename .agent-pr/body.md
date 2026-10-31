## What

When a turn's event stream ended without a `final` event, the chat UI
showed the operator a JavaScript error:

    ⚠️ streamingError is not defined

...instead of the reason the turn had failed.

## Why

`app/static/app.js` keeps the server's error event in a variable, so
that when the stream ends without a `final` event it can show the
server's own message ("the server said X") rather than the generic
"stream ended without a final event".

That variable was **assigned** but never **declared**:

```js
} else if (ev.type === 'error') {
  streamingError = ev;      // assigned, never declared
  break;
}
...
const detail = (streamingError && streamingError.message)   // read
```

In JavaScript an identifier that was never declared is not `undefined`
— it is a `ReferenceError`. So on the exact path the variable exists
for (a stream that ended with neither a `final` event nor an `error`
event) the read threw, and the thrown `ReferenceError` replaced the
message the code was trying to build.

The failure was not hypothetical. A conversation in a live install holds
an assistant turn whose entire content is `⚠️ streamingError is not
defined` — a client-error turn the browser persisted through
`POST /api/v1/entities/<name>/turns/error`. The message reached the
database, which means it reached the browser as an error message.

## The fix

One line: declare the variable next to the other stream locals.

```js
let streamingError = null;
```

The behaviour is otherwise unchanged: the server's own error message
still wins when there was one, and the generic "stream ended without a
final event" still appears when there was not.

## Tests

`tests/test_streaming_error_declaration.py` pins the declaration, pins
that it is declared *before* the read, and pins that the generic
fallback message survives — so it cannot drift back.
