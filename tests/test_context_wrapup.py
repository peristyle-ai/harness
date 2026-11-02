"""Tests for the context WRAP-UP threshold (2026-11-02).

The bug this exists to prevent: an agent's turn grew until the harness
started dropping the OLDEST iterations of the turn out of the model's
context. The model kept working -- 50+ iterations, two hours -- with a
trajectory it could no longer see, and the turn ended with a promise
instead of an answer.

The budget now has two thresholds:

  * a HARD ceiling (`limit_tokens`) -- past it, the oldest iterations
    are dropped from under the model. Lossy, and the model does not
    know it happened until the truncation notice tells it;
  * a SOFT threshold (`wrap_tokens`) -- at it, the model is told, in the
    system prompt, to WRAP THE TURN UP: write (or update) a pinned
    memory carrying the handoff, report where it got to, and emit the
    ordered plan for finishing the task, which is what the next turn
    resumes from.

What these tests pin down:
  - the soft threshold fires, and only at or above it
  - the notice says the three things a wrap-up has to do
  - the hard ceiling still drops the oldest steps (the soft threshold
    does not weaken it)
  - the wrap-up notice rides LAST in the system prompt, after the
    truncation notice
  - a turn that was told to wrap up gets the WRAP-UP nudge, not the
    "you said you would do X, now do it" nudge, which would push it
    straight back into the work loop
  - the shipped defaults are the ones the operator asked for
"""
import pathlib

from app.agent.context_window import ContextWindow


# --- the threshold --------------------------------------------------------

def test_no_notice_below_the_threshold():
    w = ContextWindow(limit_tokens=800_000, wrap_tokens=600_000)
    assert w.wrap_notice(0) == ""
    assert w.wrap_notice(599_999) == ""


def test_the_notice_fires_at_the_threshold():
    w = ContextWindow(limit_tokens=800_000, wrap_tokens=600_000)
    assert w.wrap_notice(600_000) != ""
    assert w.wrap_notice(700_000) != ""


def test_zero_disables_the_wrap_up():
    w = ContextWindow(limit_tokens=800_000, wrap_tokens=0)
    assert w.wrap_notice(10_000_000) == ""


def test_the_notice_asks_for_a_pinned_memory():
    """A pinned memory is what survives the window; the context does not."""
    w = ContextWindow(limit_tokens=800_000, wrap_tokens=600_000)
    notice = w.wrap_notice(650_000)
    assert "pinned" in notice.lower()
    assert "memory_write" in notice
    assert "pin=true" in notice


def test_the_notice_asks_for_a_report_and_a_resume_plan():
    w = ContextWindow(limit_tokens=800_000, wrap_tokens=600_000)
    notice = w.wrap_notice(650_000)
    assert "where you got to" in notice.lower()
    assert "plan" in notice.lower()
    assert "next step" in notice.lower()


def test_the_token_count_is_rounded_so_the_prompt_does_not_churn():
    """The system prompt is rewritten when the notice changes; it must not
    change on every single iteration."""
    w = ContextWindow(limit_tokens=800_000, wrap_tokens=600_000)
    a = w.wrap_notice(650_000)
    b = w.wrap_notice(650_400)
    assert a == b
    c = w.wrap_notice(660_000)
    assert c != a


def test_the_notice_states_the_budget_it_is_talking_about():
    w = ContextWindow(limit_tokens=800_000, wrap_tokens=600_000)
    notice = w.wrap_notice(650_000)
    assert "800,000" in notice
    assert "650,000" in notice


# --- the hard ceiling is not weakened -------------------------------------

def test_the_hard_ceiling_still_drops_the_oldest_steps():
    from app.agent.llm.base import Message

    w = ContextWindow(limit_tokens=1000, wrap_tokens=500, head_chars=10, tail_chars=10)
    messages = [Message(role="user", content="q")]
    groups = []
    live_tokens = 0
    for i in range(3):
        start = len(messages)
        messages.append(Message(role="assistant", content=[
            {"type": "tool_use", "id": f"c{i}", "name": "exec_bash", "input": {}},
        ]))
        messages.append(Message(role="user", content=[
            {"type": "tool_result", "tool_use_id": f"c{i}", "content": "y" * 2400},
        ]))
        groups.append((start, len(messages), w.tokens_of(messages[start:])))
        live_tokens += groups[-1][2]

    assert w.wrap_notice(live_tokens) != "", "the soft threshold is crossed too"
    dropped, live_tokens = w.enforce(messages, 1, groups, live_tokens)
    assert dropped.groups == 2, "the hard ceiling still drops the oldest"
    assert live_tokens <= w.limit_tokens


# --- the loop's seams -----------------------------------------------------

def test_the_system_text_carries_the_wrap_notice_last():
    from app.agent.loop import AgentLoop

    text = AgentLoop._window_system_text(
        "SYSTEM", "THE TRUNCATION NOTICE", "THE WRAP-UP NOTICE",
    )
    assert text.startswith("SYSTEM\n\n")
    assert "THE TRUNCATION NOTICE" in text
    assert "THE WRAP-UP NOTICE" in text
    assert text.index("THE TRUNCATION NOTICE") < text.index("THE WRAP-UP NOTICE")


def test_the_system_text_is_unchanged_with_no_notice():
    from app.agent.loop import AgentLoop

    assert AgentLoop._window_system_text("SYSTEM", "", "") == "SYSTEM"


def test_a_wrapped_up_turn_gets_the_wrap_up_nudge():
    """The intent nudge ("now do it") would push the model straight back
    into the work loop it was told to leave."""
    from app.agent.loop import AgentLoop

    nudge = str(AgentLoop._nudge_content(
        wrap_up=True, intent_matches=True, next_nudge_idx=1,
    ))
    assert "wrap" in nudge.lower()
    assert "pinned" in nudge.lower()
    assert "plan" in nudge.lower()


def test_a_normal_turn_still_gets_the_intent_nudge():
    from app.agent.loop import AgentLoop

    nudge = str(AgentLoop._nudge_content(
        wrap_up=False, intent_matches=True, next_nudge_idx=1,
    ))
    assert "tool_use" in nudge


def test_a_normal_turn_still_gets_the_standard_nudges():
    from app.agent.loop import AgentLoop

    first = str(AgentLoop._nudge_content(
        wrap_up=False, intent_matches=False, next_nudge_idx=1,
    ))
    later = str(AgentLoop._nudge_content(
        wrap_up=False, intent_matches=False, next_nudge_idx=3,
    ))
    assert first != later


# --- the shipped configuration -------------------------------------------

def test_the_shipped_defaults_are_the_ones_the_operator_asked_for(config_path):
    from app.config import load_config

    cfg = load_config(str(config_path))
    assert cfg.agent.context_window_tokens == 800_000
    assert cfg.agent.context_wrap_tokens == 600_000


def test_an_explicit_threshold_is_honoured(config_path):
    from app.config import load_config

    path = pathlib.Path(config_path)
    text = path.read_text()
    text = text.replace(
        "agent:\n  max_tool_iterations: 5",
        "agent:\n  max_tool_iterations: 5\n"
        "  context_window_tokens: 900000\n"
        "  context_wrap_tokens: 450000",
    )
    path.write_text(text)
    cfg = load_config(str(config_path))
    assert cfg.agent.context_window_tokens == 900_000
    assert cfg.agent.context_wrap_tokens == 450_000


# --- the wiring: the notice reaches the model ----------------------------
#
# The tests above prove what the notice SAYS. This one proves it is
# actually put in front of the model: the real agent loop, over a fake
# adapter that remembers the messages every call was given. No database,
# no model, no tool executed (the tool does not exist, so the loop takes
# the "Unknown tool" path).

import uuid

from app.agent.llm.base import LLMResponse, Message, ToolCall
from app.agent.loop import AgentLoop


class _FakeTurn:
    def __init__(self, turn_id: int, content: str = ""):
        self.id = turn_id
        self.content = content
        self.attachments: list = []


class _FakeMemory:
    def __init__(self):
        self.assistant_texts: list[str] = []

    def get_or_create_persistent_conversation(self, agent_name, user_id=None):
        return uuid.uuid4()

    def write_turn_user(self, conv_id, text, attachments=None, trajectory=None):
        return _FakeTurn(1, text)

    def write_turn_assistant(self, conv_id, text, model=None, latency_ms=None,
                             attachments=None, trajectory=None):
        self.assistant_texts.append(text)
        return _FakeTurn(2, text)

    def write_turns(self, conv_id, user_turn, assistant_turn):
        pass

    def set_conversation_title(self, conv_id, title):
        pass


class _CapturingAdapter:
    """A model that returns canned responses and remembers what each
    call was given."""

    name = "fake-model"
    adapter_id = "fake"

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = 0
        self.seen: list[list] = []

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        self.seen.append(list(messages))
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]

    def format_tool_result(self, call, result):
        return Message(role="user", content=[{
            "type": "tool_result",
            "tool_use_id": call.id,
            "content": str(result),
        }])


class _AgentConfig:
    max_tool_iterations = 3
    max_searches_per_turn = 15
    max_pins_per_turn = 10
    max_unpins_per_turn = 10
    max_turn_seconds = 0
    context_window_tokens = 600_000
    # crossed after the FIRST iteration, so the notice is observable
    context_wrap_tokens = 5
    tool_output_head_chars = 4_000
    tool_output_tail_chars = 4_000
    max_concurrent_llm = 2


class _Config:
    agent = _AgentConfig()


def _run(responses: list):
    memory = _FakeMemory()
    adapter = _CapturingAdapter(responses)
    loop = AgentLoop(adapter=adapter, memory=memory, bootstrap=None, config=_Config())
    # The prompt is irrelevant here -- skip the DB-backed history.
    loop._compose_messages = lambda *a, **k: [
        Message(role="system", content="SYSTEM"),
        Message(role="user", content="the question"),
    ]
    events = list(loop.handle_chat_streaming(
        "the question", agent_name="testbot", user_id=1,
    ))
    finals = [e for e in events if e.get("type") == "final"]
    assert finals, f"the turn produced no final event: {events}"
    return adapter, finals[-1]["response"]


def test_the_notice_reaches_the_model_on_the_next_call():
    tool_call = LLMResponse(
        text=None,
        tool_calls=[ToolCall(id="call-1", name="no_such_tool", arguments={})],
        finish_reason="tool_use",
    )
    answer = LLMResponse(text="All done.", tool_calls=[], finish_reason="stop")
    adapter, _response = _run([tool_call, answer])

    assert len(adapter.seen) >= 2, "the turn did not take two model calls"
    first_system = adapter.seen[0][0].content
    second_system = adapter.seen[1][0].content
    assert "wrap this turn up" not in first_system, "the notice fired too early"
    assert "wrap this turn up" in second_system, "the notice never reached the model"
    assert second_system.startswith("SYSTEM\n\n"), "the base prompt is kept"


def test_the_notice_tells_the_model_to_pin_and_to_plan():
    tool_call = LLMResponse(
        text=None,
        tool_calls=[ToolCall(id="call-1", name="no_such_tool", arguments={})],
        finish_reason="tool_use",
    )
    answer = LLMResponse(text="All done.", tool_calls=[], finish_reason="stop")
    adapter, _response = _run([tool_call, answer])

    system = adapter.seen[1][0].content
    assert "pin=true" in system
    assert "memory_write" in system
    assert "where you got to" in system.lower()
    assert "plan" in system.lower()


def test_a_turn_that_never_crosses_the_threshold_gets_no_notice():
    class _HighConfig(_AgentConfig):
        context_wrap_tokens = 600_000

    class _HighCfg:
        agent = _HighConfig()

    memory = _FakeMemory()
    adapter = _CapturingAdapter([LLMResponse(
        text="All done.", tool_calls=[], finish_reason="stop",
    )])
    loop = AgentLoop(adapter=adapter, memory=memory, bootstrap=None, config=_HighCfg())
    loop._compose_messages = lambda *a, **k: [
        Message(role="system", content="SYSTEM"),
        Message(role="user", content="the question"),
    ]
    list(loop.handle_chat_streaming(
        "the question", agent_name="testbot", user_id=1,
    ))
    assert "wrap this turn up" not in adapter.seen[0][0].content
