"""The chat UI must not fail with a JavaScript ReferenceError.

``app/static/app.js`` streams a turn's events as NDJSON.  When the
stream ends without a ``final`` event, the code reads the variable that
holds the server's error event so it can show the operator the server's
own message instead of the generic one.

That read used to be of an identifier that was never DECLARED.  In
JavaScript an undeclared identifier is a ``ReferenceError``, not an
``undefined`` value -- so the very path that exists to explain a
failure threw a JavaScript error instead, and the operator was shown
"streamingError is not defined" where the reason for the failure
should have been.

The persisted evidence: a conversation in a live install holds an
assistant turn whose entire content is "streamingError is not defined" --
a client-error turn written by the browser through
``POST /api/v1/entities/<name>/turns/error``.  The message reached the
database, which means it reached the browser as an error message.

These tests pin the declaration so it cannot drift back.  They read the
source rather than run a browser, because the defect is in the source:
a missing ``let``.
"""

import pathlib
import re

APP_JS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app"
    / "static"
    / "app.js"
)


def _js() -> str:
    return APP_JS.read_text()


def test_streaming_error_is_declared():
    source = _js()
    assert re.search(r"\b(?:let|const|var)\s+streamingError\b", source), (
        "app.js must DECLARE streamingError. It is read on the "
        "'stream ended without a final event' path, and reading an "
        "undeclared identifier is a ReferenceError -- which is what "
        "replaced the real failure message with "
        "'streamingError is not defined'."
    )


def test_streaming_error_is_declared_before_it_is_read():
    source = _js()
    declaration = re.search(
        r"\b(?:let|const|var)\s+streamingError\b", source
    )
    assert declaration, "streamingError must be declared"
    read = re.search(r"streamingError\s*&&\s*streamingError\.message", source)
    assert read, (
        "the error path must read streamingError -- that is the read that "
        "threw the ReferenceError"
    )
    assert declaration.start() < read.start(), (
        "streamingError must be declared before the error path reads it"
    )


def test_the_error_path_still_falls_back_to_a_readable_message():
    """The fix must not remove the fallback message.

    The point of the variable is that the operator is told what went
    wrong.  If there was no error event either, the generic message
    has to survive.
    """
    source = _js()
    assert "stream ended without a final event" in source, (
        "the fallback message must stay: it is what the operator sees "
        "when the server sent neither a final event nor an error event"
    )
    assert re.search(r"streamingError\s*&&\s*streamingError\.message", source), (
        "the server's own error message must still win over the generic "
        "one when there was one"
    )
