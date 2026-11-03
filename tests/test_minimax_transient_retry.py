"""A transient provider error must not kill the turn.

2026-11-04, production harness2 (the Durthu / Drycha install): MiniMax
answered a request with an SSE "error" event INSIDE a 200 response:

    {"type":"error","error":{"type":"api_error",
     "message":"Internal server error. Please retry your request. (500)"}}

The HTTP status was 200, so the retry ladder in `base._post_with_retry`
-- which keys off the status code -- never saw it. The adapter's only
recovery for a failed stream was "fall back to a buffered POST", and that
fallback was skipped whenever the attempt had already streamed some text
(the fallback would have duplicated it).

So the turn died. At iteration 63, with 122 tool calls done, on a
provider message that said "Please retry your request."

These tests are offline. The fake client replays SSE scripts, one per
stream attempt, so the retry can be observed rather than mocked away.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.agent.llm.base import (ContextWindowExceeded, Message,
                                TransientProviderError, user_facing_error)
from app.agent.llm.minimax_m31_flash import LLM

import httpx

# --- SSE scripts -----------------------------------------------------------

_TRANSIENT_ERROR = (
    "data: "
    '{"type":"error","error":{"type":"api_error","message":'
    '"Internal server error. Please retry your request. (500)"}}'
)


def _sse(*payloads: dict) -> list[str]:
    """One SSE script: a list of "data: {...}" lines."""
    import json
    return [f"data: {json.dumps(p)}" for p in payloads]


def _script_with_text_then_error(text: str) -> list[str]:
    """A stream that emits some text and THEN fails transiently.

    This is the production shape: the failure arrives after the user has
    already seen output, which is exactly the case the old code refused
    to retry.
    """
    return _sse(
        {"type": "message_start",
         "message": {"id": "msg_1", "usage": {"input_tokens": 10}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": text}},
        {"type": "error",
         "error": {"type": "api_error",
                   "message": "Internal server error. Please retry your request. (500)"}},
    )


def _script_ok(text: str) -> list[str]:
    """A complete, successful stream."""
    return _sse(
        {"type": "message_start",
         "message": {"id": "msg_1", "usage": {"input_tokens": 10}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    )


def _script_permanent_error() -> list[str]:
    """A bad request. Repeating it changes nothing."""
    return _sse(
        {"type": "error",
         "error": {"type": "invalid_request_error",
                   "message": "invalid params, context window exceeds limit (2013)"}},
    )


# --- fakes -----------------------------------------------------------------

_MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "pong"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 3, "output_tokens": 1},
}


class _FakeResponse:
    def __init__(self, content: bytes, content_type: str, payload: dict | None,
                 status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request,
                response=httpx.Response(self.status_code, content=self.content))

    def json(self) -> Any:
        return self._payload


class _FakeStreamResponse:
    def __init__(self, lines: list[str]):
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)

    def raise_for_status(self) -> None:
        return None

    def read(self) -> bytes:
        return b""


class _FakeStreamCtx:
    def __init__(self, resp: _FakeStreamResponse):
        self.resp = resp

    def __enter__(self) -> _FakeStreamResponse:
        return self.resp

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeClient:
    """Replays one SSE script per stream attempt."""

    def __init__(self, scripts: list[list[str]], post_status: int = 200):
        self.scripts = scripts
        self.stream_calls = 0
        self.posted: list[dict] = []
        self.post_status = post_status

    def stream(self, method, url, headers=None, json=None):
        idx = min(self.stream_calls, len(self.scripts) - 1)
        self.stream_calls += 1
        return _FakeStreamCtx(_FakeStreamResponse(self.scripts[idx]))

    def post(self, url, headers=None, json=None):
        self.posted.append(json)
        if self.post_status >= 400:
            return _FakeResponse(
                b'{"type":"error","error":{"type":"invalid_request_error",'
                b'"message":"invalid params, context window exceeds limit (2013)"}}',
                "application/json", None, status_code=self.post_status)
        return _FakeResponse(b'{"id":"msg_1"}', "application/json", _MESSAGE)


def _adapter(scripts: list[list[str]], post_status: int = 200, **llm_overrides):
    llm_cfg = SimpleNamespace(
        api_key="test-key",
        base_url="https://example.invalid",
        model="MiniMax-M3.1-Flash",
        max_tokens=1024,
        timeout_s=5,
        temperature=0.0,
        thinking=None,
        streaming=True,
        transient_retry_attempts=2,
        transient_retry_backoff_s=0.0,
    )
    for key, value in llm_overrides.items():
        setattr(llm_cfg, key, value)
    llm = LLM(SimpleNamespace(llm=llm_cfg))
    fake = _FakeClient(scripts, post_status=post_status)
    llm._client = fake
    return llm, fake


# --- tests -----------------------------------------------------------------

def test_a_transient_error_after_streamed_text_is_retried(monkeypatch):
    """The production case: text was shown, then the provider failed.

    The turn must continue. The retry's text is suppressed, because the
    client already saw the first attempt's text and the model answers
    differently on the retry (temperature 0.7) -- showing both would
    read as the model saying everything twice.
    """
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter([
        _script_with_text_then_error("I have fixed both defects. "),
        _script_ok("Both defects are fixed and the tests pass."),
    ])

    shown: list[str] = []
    response = llm.complete(
        messages=[Message(role="user", content="fix the defects")],
        on_delta=shown.append,
    )

    assert response.text == "Both defects are fixed and the tests pass."
    assert fake.stream_calls == 2, "the transient error was not retried"
    # The client saw the first attempt's text, and NOT the retry's.
    assert "".join(shown) == "I have fixed both defects. "


def test_the_retry_does_not_reach_the_client_twice(monkeypatch):
    """Guard against the duplication the old code was protecting against."""
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, _ = _adapter([
        _script_with_text_then_error("partial answer"),
        _script_ok("a different answer"),
    ])

    shown: list[str] = []
    llm.complete(messages=[Message(role="user", content="hi")],
                 on_delta=shown.append)

    joined = "".join(shown)
    assert joined.count("partial answer") == 1
    assert "a different answer" not in joined


def test_a_transient_error_before_any_output_uses_the_buffered_post(monkeypatch):
    """Nothing was shown, so the buffered POST is the cheaper recovery.

    It retries 5xx at the HTTP level, where the status code IS visible.
    """
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter([_sse(
        {"type": "error",
         "error": {"type": "api_error",
                   "message": "Internal server error. Please retry your request. (500)"}},
    )])

    response = llm.complete(messages=[Message(role="user", content="ping")])

    assert response.text == "pong"
    assert fake.posted, "the buffered fallback did not POST"
    assert "stream" not in fake.posted[0]


def test_a_caller_without_live_deltas_still_gets_the_buffered_fallback(monkeypatch):
    """A caller that asked for no live deltas has seen nothing.

    Its counters must stay at zero, so the buffered fallback still
    applies -- that path's behaviour is unchanged by this fix.
    """
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter([_script_with_text_then_error("text")])

    response = llm.complete(messages=[Message(role="user", content="ping")])

    assert response.text == "pong"
    assert fake.posted, "the buffered fallback did not POST"
    assert fake.stream_calls == 1, "there was nothing to duplicate"


def test_the_retry_gives_up_and_raises_after_the_attempts_are_spent(monkeypatch):
    """A provider that stays down must fail loudly, not loop forever."""
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter(
        [_script_with_text_then_error("text")] * 5,
        transient_retry_attempts=2,
    )

    shown: list[str] = []
    try:
        llm.complete(messages=[Message(role="user", content="hi")],
                     on_delta=shown.append)
    except TransientProviderError as exc:
        assert "api_error" in str(exc)
    else:
        raise AssertionError("the turn should have failed")

    # The first attempt plus two retries.
    assert fake.stream_calls == 3


def test_a_permanent_error_is_not_retried(monkeypatch):
    """A bad request repeats identically forever. Do not spend time on it.

    The stream is attempted exactly once. With nothing shown yet the
    existing fallback applies (a buffered POST), but the STREAM is not
    repeated -- and the failure is never dressed up as transient.
    """
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter([_script_permanent_error()])

    try:
        llm.complete(messages=[Message(role="user", content="hi")])
    except TransientProviderError:
        raise AssertionError("a permanent error must not be transient")
    except RuntimeError:
        pass

    assert fake.stream_calls == 1, "a permanent error must not be retried"


def test_a_permanent_error_still_maps_to_the_context_window_error(monkeypatch):
    """The typed permanent error must survive the fallback.

    A context window that is full is not a transport failure, and the
    loop needs to tell the agent that -- so the 400 has to arrive as
    ContextWindowExceeded, not as a generic HTTP error.
    """
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter([_script_permanent_error()], post_status=400)

    try:
        llm.complete(messages=[Message(role="user", content="hi")])
    except ContextWindowExceeded:
        pass
    except TransientProviderError:
        raise AssertionError("a 400 is not a transient provider error")
    else:
        raise AssertionError("the turn should have failed")

    assert fake.posted, "the buffered fallback did not POST"


def test_zero_attempts_restores_the_old_behaviour(monkeypatch):
    """The knob is a real rollback: 0 attempts fails on the first error."""
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter(
        [_script_with_text_then_error("text")] * 5,
        transient_retry_attempts=0,
    )

    try:
        llm.complete(messages=[Message(role="user", content="hi")],
                     on_delta=lambda _t: None)
    except TransientProviderError:
        pass
    else:
        raise AssertionError("the turn should have failed")

    assert fake.stream_calls == 1


def test_the_user_reads_a_sentence_not_a_python_repr():
    """The bubble in the chat must say what happened, in words."""
    err = TransientProviderError(
        "minimax_m31_flash: transient provider error (api_error: ...)",
        user_message="MiniMax returned a transient error while answering.")

    message = user_facing_error(err)
    assert "MiniMax returned a transient error" in message
    assert "TransientProviderError" not in message
    # The salvage hint: the turn's work is not lost.
    assert "Nothing from this turn was lost" in message


def test_ordinary_errors_keep_their_historical_form():
    """Every other failure keeps the old "Type: message" form."""
    assert user_facing_error(ValueError("boom")) == "ValueError: boom"
