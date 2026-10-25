"""Tests for the sliding context window (2026-10-25).

The bug this exists to prevent: Evie's turn reached ~1.4M tokens in one
request against MiniMax-M3.1-Flash's 1,000,000-token window and the API
answered 400 "invalid params, context window exceeds limit (2013)".

What these tests pin down:
  - the window drops the OLDEST iterations, whole
  - a dropped group never orphans a tool_result (the API 400s on that)
  - the notice tells the model to use its memory tools and its commits
  - tool output is trimmed HEAD+TAIL, not head-only (the tail holds the
    error message)
  - the trimmed output still fits, and the full output is untouched
"""
from app.agent.context_window import ContextWindow
from app.agent.llm.base import Message


def _tool_use_msg(text="thinking", call_id="t1"):
    return Message(role="assistant", content=[
        {"type": "thinking", "thinking": text},
        {"type": "tool_use", "id": call_id, "name": "exec_bash", "input": {}},
    ])


def _tool_result_msg(call_id="t1", out="x" * 100):
    return Message(role="user", content=[
        {"type": "tool_result", "tool_use_id": call_id, "content": out},
    ])


def test_estimate_is_characters_over_four():
    w = ContextWindow()
    assert w.estimate("a" * 400) == 100
    assert w.estimate("") == 0
    assert w.estimate(None) == 0


def test_enforce_drops_the_oldest_groups_whole():
    w = ContextWindow(limit_tokens=1000, head_chars=10, tail_chars=10)
    messages = [Message(role="user", content="the question")]
    live_start = len(messages)
    groups = []
    live_tokens = 0
    # three iterations, each ~600 tokens of tool output
    for i in range(3):
        start = len(messages)
        messages.append(_tool_use_msg(call_id=f"t{i}"))
        messages.append(_tool_result_msg(call_id=f"t{i}", out="y" * 2400))
        groups.append((start, len(messages), w.tokens_of(messages[start:])))
        live_tokens += groups[-1][2]
    assert live_tokens > w.limit_tokens

    dropped, live_tokens = w.enforce(messages, live_start, groups, live_tokens)

    assert dropped.groups == 2, "the two oldest iterations go"
    assert live_tokens <= w.limit_tokens
    # the fresh user message is still there, and it is still first
    assert messages[0].content == "the question"
    # the surviving group is the LAST one, and it is intact
    assert len(messages) == 3
    assert messages[1].content[1]["id"] == "t2"
    assert messages[2].content[0]["tool_use_id"] == "t2"


def test_enforce_never_orphans_a_tool_result():
    """A tool_result whose tool_use was dropped is an API 400."""
    w = ContextWindow(limit_tokens=200, head_chars=10, tail_chars=10)
    messages = [Message(role="user", content="q")]
    live_start = len(messages)
    groups = []
    live_tokens = 0
    for i in range(4):
        start = len(messages)
        messages.append(_tool_use_msg(call_id=f"c{i}"))
        messages.append(_tool_result_msg(call_id=f"c{i}", out="z" * 800))
        groups.append((start, len(messages), w.tokens_of(messages[start:])))
        live_tokens += groups[-1][2]
    w.enforce(messages, live_start, groups, live_tokens)

    seen_use = set()
    for m in messages:
        for block in (m.content if isinstance(m.content, list) else []):
            if block.get("type") == "tool_use":
                seen_use.add(block["id"])
    for m in messages:
        for block in (m.content if isinstance(m.content, list) else []):
            if block.get("type") == "tool_result":
                assert block["tool_use_id"] in seen_use


def test_notice_says_use_memory_and_commits():
    w = ContextWindow()
    notice = w.notice(type("D", (), {"groups": 3, "tokens": 12345})())
    assert "memory" in notice.lower()
    assert "commit" in notice.lower()
    assert "truncated" in notice.lower()
    assert w.notice(type("D", (), {"groups": 0, "tokens": 0})()) == ""


def test_trim_keeps_head_and_tail():
    w = ContextWindow(head_chars=100, tail_chars=100)
    text = "H" * 100 + ("." * 5000) + "T" * 100
    out = w.trim_text(text)
    assert out.startswith("H" * 100)
    assert out.endswith("T" * 100)
    assert "elided" in out
    assert len(out) < len(text)


def test_trim_leaves_short_strings_alone():
    w = ContextWindow(head_chars=100, tail_chars=100)
    text = "a short result"
    assert w.trim_text(text) == text


def test_trim_value_walks_dicts_and_lists():
    w = ContextWindow(head_chars=50, tail_chars=50)
    result = {
        "ok": True,
        "stdout": "S" * 100 + "." * 500 + "E" * 100,
        "nested": [{"text": "T" * 100 + "." * 500 + "Z" * 100}],
        "count": 3,
    }
    trimmed = w.trim_value(result)
    assert trimmed["ok"] is True
    assert trimmed["count"] == 3
    assert len(trimmed["stdout"]) < len(result["stdout"])
    assert trimmed["stdout"].startswith("S" * 50)
    assert trimmed["stdout"].endswith("E" * 50)
    assert len(trimmed["nested"][0]["text"]) < len(result["nested"][0]["text"])
    # the caller's own result is untouched (the trajectory keeps it whole)
    assert len(result["stdout"]) == 700


def test_content_text_flattens_blocks():
    w = ContextWindow()
    msg = _tool_use_msg(text="12345", call_id="t9")
    text = w.content_text(msg.content)
    assert "12345" in text


# --- the loop's seam --------------------------------------------------------

def test_loop_enforcement_drops_oldest_and_returns_a_notice():
    """The loop calls _enforce_context_window; this is that seam."""
    from app.agent.loop import AgentLoop

    w = ContextWindow(limit_tokens=1000, head_chars=10, tail_chars=10)
    messages = [Message(role="system", content="SYSTEM"),
                Message(role="user", content="the question")]
    live_start = len(messages)
    groups = []
    live_tokens = 0
    for i in range(3):
        start = len(messages)
        messages.append(_tool_use_msg(call_id=f"s{i}"))
        messages.append(_tool_result_msg(call_id=f"s{i}", out="y" * 2400))
        groups.append((start, len(messages), w.tokens_of(messages[start:])))
        live_tokens += groups[-1][2]

    live_tokens, notice = AgentLoop._enforce_context_window(
        w, messages, live_start, groups, live_tokens, "SYSTEM")

    assert notice, "the model must be told its tail was truncated"
    assert "memory" in notice.lower()
    assert "commit" in notice.lower()
    assert live_tokens <= w.limit_tokens
    assert messages[0].content == "SYSTEM", "the system prompt is replaced by the caller, not here"
    # the oldest iterations went, the newest survived with its tool_use
    # (system, user, then the surviving group)
    assert messages[1].content == "the question"
    assert messages[2].content[1]["id"] == "s2"
    assert messages[3].content[0]["tool_use_id"] == "s2"


def test_loop_enforcement_is_a_no_op_under_budget():
    from app.agent.loop import AgentLoop

    w = ContextWindow(limit_tokens=1_000_000, head_chars=10, tail_chars=10)
    messages = [Message(role="system", content="SYSTEM"),
                Message(role="user", content="q")]
    groups = []
    live_tokens, notice = AgentLoop._enforce_context_window(
        w, messages, len(messages), groups, 10, "SYSTEM")
    assert live_tokens == 10
    assert notice == ""


# --- the provider 400 --------------------------------------------------------

class _FakeResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 400


def test_context_window_400_is_recognised():
    from app.agent.llm.minimax_m31_flash import _is_context_window_error

    body = ('{"type":"error","error":{"type":"invalid_request_error",'
            '"message":"invalid params, context window exceeds limit (2013)"}}')
    assert _is_context_window_error(_FakeResponse(body)) is True


def test_the_other_2013_is_not_a_context_window_error():
    """MiniMax uses 2013 for a DIFFERENT 400 too -- the tool-id mismatch.
    The tool-repair layer owns that one; mapping it here would swallow it."""
    from app.agent.llm.minimax_m31_flash import _is_context_window_error

    body = ("invalid params: tool result's tool id toolu_x not found (2013)")
    assert _is_context_window_error(_FakeResponse(body)) is False


def test_context_window_exceeded_carries_a_readable_message():
    from app.agent.llm.base import ContextWindowExceeded

    err = ContextWindowExceeded()
    assert "context window" in str(err).lower()
    assert "memory" in str(err).lower()


# --- the failed turn's row (2026-10-25, per Mal) ----------------------------

def test_failed_turn_row_carries_the_error_AND_the_trajectory():
    """The failed turn must be debuggable from the turns table alone.

    It used to be written at generator teardown -- after the response had
    been flushed -- so the browser's /turns/error POST won the race and
    left a stub row carrying nothing but the error message.
    """
    from app.agent.loop import AgentLoop

    written = {}

    class _Mem:
        def write_turn_assistant(self, conv_id, text, **kw):
            written["text"] = text
            written.update(kw)
            return object()

        def write_turns(self, *a, **kw):
            written["embedded"] = True

    loop = AgentLoop.__new__(AgentLoop)
    loop._agent_memory = lambda name: _Mem()

    AgentLoop._persist_partial_assistant_turn(
        loop,
        agent_name="peribot",
        conv_id="conv-1",
        user_turn=object(),
        user_text="do the thing",
        user_id=1,
        attachments=[],
        emission_iters=[{"iteration": 0, "assistant_text": "working on it"}],
        final_text=None,
        t0=0.0,
        agent_adapter=type("A", (), {"name": "minimax/MiniMax-M3.1-Flash"})(),
        tool_iters=1,
        retrieved_ids=[],
        request_id="req-1",
        embed=False,
        error_message="HTTPStatusError: 400 Bad Request",
    )

    assert written["text"].startswith("⚠️")
    assert "400 Bad Request" in written["text"]
    traj = written["trajectory"]
    assert traj["error"]["failed"] is True
    assert "400 Bad Request" in traj["error"]["message"]
    assert traj["total_iterations"] == 1
    assert traj["iterations"][0]["assistant_text"] == "working on it"
    assert traj["request_id"] == "req-1"
    # no embedding call on the failure path
    assert "embedded" not in written


def test_disconnect_turn_still_uses_the_partial_text():
    """The disconnect path (no error_message) is unchanged."""
    from app.agent.loop import AgentLoop

    written = {}

    class _Mem:
        def write_turn_assistant(self, conv_id, text, **kw):
            written["text"] = text
            written.update(kw)
            return object()

        def write_turns(self, *a, **kw):
            written["embedded"] = True

    loop = AgentLoop.__new__(AgentLoop)
    loop._agent_memory = lambda name: _Mem()

    AgentLoop._persist_partial_assistant_turn(
        loop,
        agent_name="peribot",
        conv_id="conv-1",
        user_turn=object(),
        user_text="do the thing",
        user_id=1,
        attachments=[],
        emission_iters=[{"iteration": 0, "assistant_text": "working on it"}],
        final_text=None,
        t0=0.0,
        agent_adapter=type("A", (), {"name": "m"})(),
        tool_iters=1,
        retrieved_ids=[],
        request_id="req-2",
        embed=False,
    )

    assert "working on it" in written["text"]
    assert "disconnected mid-turn" in written["text"]
    assert "error" not in written["trajectory"]
