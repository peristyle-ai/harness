"""A large request that times out must fail fast, and must say why.

2026-10-29, on the peristyle install (production): evie's turn 559 ran
2h27m and 147 iterations, then died with

    ReadTimeout: The read operation timed out

and the log shows what the harness did with the ~15 minutes it spent
failing:

    13:11:44  streaming path failed (ReadTimeout); falling back to buffered POST
    13:14:45  transient network error on attempt 1/4 (ReadTimeout); retrying in 1.0s
    13:17:47  transient network error on attempt 2/4 (ReadTimeout); retrying in 2.0s
    13:20:51  transient network error on attempt 3/4 (ReadTimeout); retrying in 4.0s
    13:23:56  transport error: The read operation timed out

That is ONE model call, retried four times, each attempt re-uploading
the same ~2.4 MB request body and waiting out the full 180s read
timeout: 12 minutes of a 2h27m turn re-sending a request the upstream
was not answering. The turn then ended with a bare "ReadTimeout: The
read operation timed out" -- which tells the reader nothing about the
request that was actually being sent.

So two things must be true of the adapter:

  1. A read timeout on a LARGE request must not be retried. Re-sending
     a multi-megabyte body is the expensive part, and the evidence from
     the same incident is that the upstream simply was not answering
     that request -- it stayed down for the whole 15 minutes. A cheap
     fault (a dropped connection) is still worth retrying, because the
     retry costs seconds, not minutes.

  2. The error the user sees must name the request that was being sent.
     "The read operation timed out" is true and useless; "the upstream
     did not answer a 2.4 MB request within 180s" tells the operator the
     request was huge, which is the thing they can act on.

These tests are offline: a fake client that records every POST and can
be told to fail with a chosen exception.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.agent.llm.base import Message
from app.agent.llm.minimax_m31_flash import LLM

# Above this the request body is "large": re-sending it is expensive
# enough that a blind retry is not worth the minutes.
LARGE = 1_000_000

_MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "pong"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 3, "output_tokens": 1},
}


class _FakeResponse:
    def __init__(self):
        self.content = b'{"id":"msg_1"}'
        self.status_code = 200
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return _MESSAGE


class _FakeClient:
    """Records every POST; fails with the configured exception."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.posted: list[dict] = []

    def post(self, url, headers=None, json=None):
        self.posted.append(json)
        if self.exc is not None:
            raise self.exc
        return _FakeResponse()

    def stream(self, method, url, headers=None, json=None):
        raise self.exc if self.exc is not None else RuntimeError("no stream")


def _adapter(client, timeout_s: int = 180, streaming: bool = False):
    llm_cfg = SimpleNamespace(
        api_key="test-key",
        base_url="https://example.invalid",
        model="MiniMax-M3.1-Flash",
        max_tokens=1024,
        timeout_s=timeout_s,
        temperature=0.0,
        thinking=None,
        streaming=streaming,
    )
    llm = LLM(SimpleNamespace(llm=llm_cfg))
    llm._client = client
    return llm


def _body(nbytes: int) -> list[Message]:
    return [Message(role="user", content="x" * nbytes)]


def test_a_large_request_that_times_out_is_not_re_posted():
    """One attempt, not four: a big body must not be re-uploaded to a
    slow upstream that has already refused to answer it."""
    client = _FakeClient(httpx.ReadTimeout("The read operation timed out"))
    llm = _adapter(client)

    with pytest.raises(httpx.ReadTimeout):
        llm.complete(messages=_body(LARGE))

    assert len(client.posted) == 1, (
        "the large request was re-sent after a read timeout: "
        f"{len(client.posted)} POSTs")


def test_a_small_request_that_times_out_is_still_retried():
    """A cheap body is cheap to re-send, so the retry ladder still runs:
    this is the pre-existing behaviour and it must not change."""
    client = _FakeClient(httpx.ReadTimeout("The read operation timed out"))
    llm = _adapter(client)

    with pytest.raises(httpx.ReadTimeout):
        llm.complete(messages=_body(1000))

    assert len(client.posted) == 4, (
        "a small request lost its retries: "
        f"{len(client.posted)} POSTs")


def test_a_dropped_connection_is_still_retried_on_a_large_request():
    """A dropped connection fails in seconds, not minutes, so retrying it
    is worth it even for a big body."""
    client = _FakeClient(httpx.RemoteProtocolError("Server disconnected"))
    llm = _adapter(client)

    with pytest.raises(httpx.RemoteProtocolError):
        llm.complete(messages=_body(LARGE))

    assert len(client.posted) == 4, (
        "a dropped connection lost its retries on a large request: "
        f"{len(client.posted)} POSTs")


def test_the_error_names_the_request_that_was_not_answered():
    """The user must be told the request was huge. 'The read operation
    timed out' does not say that."""
    client = _FakeClient(httpx.ReadTimeout("The read operation timed out"))
    llm = _adapter(client)

    with pytest.raises(Exception) as excinfo:
        llm.complete(messages=_body(LARGE))

    message = str(excinfo.value)
    assert "MB" in message, message
    assert "180" in message, message
    assert "minimax" in message.lower() or "upstream" in message.lower(), message


def test_the_streaming_path_does_not_re_send_a_large_request():
    """The streaming path and its buffered fallback send the SAME body.

    On a big body the fallback is not a second chance -- it is the same
    multi-megabyte request again, and the buffered path has to wait for
    the WHOLE answer before it can report anything, so it is strictly
    slower than the stream that just timed out. The failure must be
    reported instead of spending another window on the same request.
    """
    client = _FakeClient(httpx.ReadTimeout("The read operation timed out"))
    llm = _adapter(client, streaming=True)

    with pytest.raises(httpx.ReadTimeout) as excinfo:
        llm.complete(messages=_body(LARGE))

    assert client.posted == [], (
        "the large request was re-sent through the buffered fallback: "
        f"{len(client.posted)} POSTs")
    assert "MB" in str(excinfo.value), str(excinfo.value)


def test_the_streaming_path_still_falls_back_for_a_small_request():
    """A small body keeps its fallback: the buffered POST is cheap and
    may well answer what the stream did not."""
    client = _FakeClient(httpx.ReadTimeout("The read operation timed out"))
    llm = _adapter(client, streaming=True)

    with pytest.raises(httpx.ReadTimeout):
        llm.complete(messages=_body(1000))

    # The fallback POSTs it, and a small body is cheap enough that the
    # retry ladder still runs (4 attempts).
    assert len(client.posted) == 4, (
        "the small request lost its buffered fallback: "
        f"{len(client.posted)} POSTs")
