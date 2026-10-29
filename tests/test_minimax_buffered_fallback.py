"""The streaming -> buffered fallback must ask for a BUFFERED response.

2026-10-29 (found on the production install, in production): when the SSE
path failed before emitting anything, the fallback re-used the same body
dict, which still carried body["stream"]=True from _build_body. The
endpoint honours that flag and answers text/event-stream, so the
"buffered" retry's r.json() raised

    JSONDecodeError: Expecting value: line 1 column 1 (char 0)

which is a FATAL error: it ended whole turns instead of degrading. It
killed the 2026-10-28 peribot reverie and the 17:06 heartbeat the same day.

These tests are offline. The fake client mimics the endpoint's real
behaviour: a POST whose body carries stream:true comes back as SSE and
does not parse as JSON. That is what made the bug fatal rather than
merely wrong.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx

from app.agent.llm.base import Message
from app.agent.llm.minimax_m31_flash import LLM


_MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "pong"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 3, "output_tokens": 1},
}


class _FakeResponse:
    """A response that either parses as JSON or refuses to, like the real one."""

    def __init__(self, content: bytes, content_type: str, payload: dict | None):
        self.content = content
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        if self._payload is None:
            # Exactly what json.loads() raises on an SSE body.
            raise json.JSONDecodeError(
                "Expecting value: line 1 column 1 (char 0)", "", 0)
        return self._payload


class _FailingStream:
    """The SSE context manager that blows up before the first delta."""

    def __enter__(self):
        raise httpx.RemoteProtocolError("upstream dropped the connection")

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeClient:
    """Records what was sent, and behaves like the endpoint about streams."""

    def __init__(self) -> None:
        self.streamed: list[dict] = []
        self.posted: list[dict] = []

    def stream(self, method, url, headers=None, json=None):
        self.streamed.append(json)
        return _FailingStream()

    def post(self, url, headers=None, json=None):
        self.posted.append(json)
        if json and json.get("stream"):
            # The endpoint honours stream:true: SSE comes back, not JSON.
            return _FakeResponse(
                b'event: message_start\ndata: {"type":"message_start"}\n\n',
                "text/event-stream", None)
        return _FakeResponse(b'{"id":"msg_1"}', "application/json", _MESSAGE)


def _adapter(streaming: bool = True):
    llm_cfg = SimpleNamespace(
        api_key="test-key",
        base_url="https://example.invalid",
        model="MiniMax-M3.1-Flash",
        max_tokens=1024,
        timeout_s=5,
        temperature=0.0,
        thinking=None,
        streaming=streaming,
    )
    llm = LLM(SimpleNamespace(llm=llm_cfg))
    fake = _FakeClient()
    llm._client = fake
    return llm, fake


def test_the_buffered_fallback_does_not_ask_for_a_stream(monkeypatch):
    """The fallback POST must not carry stream:true.

    Regression: the fallback re-used the streaming body, so the
    "buffered" retry came back as SSE and died in r.json().
    """
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter(streaming=True)

    response = llm.complete(messages=[Message(role="user", content="ping")])

    assert response.text == "pong"
    assert len(fake.posted) == 1, "the fallback did not POST"
    assert "stream" not in fake.posted[0], (
        "the buffered fallback still asked for a stream: "
        f"{fake.posted[0].get('stream')}")


def test_the_buffered_fallback_keeps_the_rest_of_the_request(monkeypatch):
    """The fix must not gut the request: only the stream flag goes."""
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter(streaming=True)

    llm.complete(messages=[Message(role="user", content="ping")])

    body = fake.posted[0]
    assert body["model"] == "MiniMax-M3.1-Flash"
    assert body["max_tokens"] == 1024
    assert body["messages"], "the fallback POST lost its messages"


def test_the_buffered_path_still_never_asks_for_a_stream(monkeypatch):
    """streaming: false -- the path that always worked -- is unchanged."""
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, fake = _adapter(streaming=False)

    response = llm.complete(messages=[Message(role="user", content="ping")])

    assert response.text == "pong"
    assert fake.streamed == []
    assert len(fake.posted) == 1
    assert "stream" not in fake.posted[0]


def test_the_streaming_body_still_carries_the_stream_flag(monkeypatch):
    """The fix must not turn streaming off: the SSE path still asks for it."""
    monkeypatch.delenv("LLM_STREAMING", raising=False)
    llm, _ = _adapter(streaming=True)

    streaming_body = llm._build_body(
        [Message(role="user", content="ping")], None, None, None, stream=True)
    buffered_body = llm._build_body(
        [Message(role="user", content="ping")], None, None, None, stream=False)

    assert streaming_body["stream"] is True
    assert "stream" not in buffered_body
