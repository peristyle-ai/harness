"""A turn that ends on a promise must not be taken for an answer (2026-11-02).

On 2026-11-02 a production turn ran 50 tool iterations and ended with:

    "... Let me check one last fact before I touch the shared code."

...with no tool call. The user asked for a continuation; the next turn
repeated the same shape and ended the same way.

The harness HAS a guard for this failure mode -- the "intent without
execution" nudge in app/agent/loop.py (_INTENT_RE / _NUDGE_INTENT_MESSAGE)
-- but the guard is suppressed for the WHOLE turn once any earlier
iteration landed a tool result:

    prev_successful_tool_intent = any(
        it.get("tool_results") for it in emission_iters
    )
    intent_narrates = not prev_successful_tool_intent

The suppression exists for a real reason: a wrap-up summary after a
successful tool call mentions "sent", "rendered", "written", and must NOT
be nudged. But it leaves the guard reachable only on the FIRST iteration
of a turn. From iteration 2 onwards the model can end the turn on a
promise and the harness accepts the promise as the answer.

The distinction the fix draws is not "did a tool run" but "is the turn
ENDING on a promise". A report of work already done ("I checked it, it is
correct") is an answer. A promise of work not yet done ("let me check
it") is not.

These tests drive the REAL agent loop over a fake adapter: no database, no
model, and no tool is ever executed (the loop is asked for a tool that
does not exist, so it takes the "Unknown tool" path and runs nothing).
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.llm.base import LLMResponse, Message, ToolCall
from app.agent.loop import AgentLoop


# --- fakes ------------------------------------------------------------------


class FakeTurn:
    def __init__(self, turn_id: int, content: str = ""):
        self.id = turn_id
        self.content = content
        self.attachments: list = []


class FakeMemory:
    """Just enough of MemoryAPI for the loop to run without a database."""

    def __init__(self):
        self.assistant_texts: list[str] = []

    def get_or_create_persistent_conversation(self, agent_name, user_id=None):
        return uuid.uuid4()

    def write_turn_user(self, conv_id, text, attachments=None, trajectory=None):
        return FakeTurn(1, text)

    def write_turn_assistant(self, conv_id, text, model=None, latency_ms=None,
                             attachments=None, trajectory=None):
        self.assistant_texts.append(text)
        return FakeTurn(2, text)

    def write_turns(self, conv_id, user_turn, assistant_turn):
        pass

    def set_conversation_title(self, conv_id, title):
        pass


class FakeAdapter:
    """A model that returns a canned list of responses; the last one
    repeats forever."""

    name = "fake-model"
    adapter_id = "fake"

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = 0
        self.seen_messages: list = []

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        self.seen_messages = list(messages)
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]

    def format_tool_result(self, call, result):
        return Message(role="user", content=[{
            "type": "tool_result",
            "tool_use_id": call.id,
            "content": str(result),
        }])


class _AgentConfig:
    max_tool_iterations = 500
    max_searches_per_turn = 15
    max_pins_per_turn = 10
    max_unpins_per_turn = 10
    max_turn_seconds = 0
    context_window_tokens = 600_000
    context_wrap_tokens = 600_000
    tool_output_head_chars = 4_000
    tool_output_tail_chars = 4_000
    max_concurrent_llm = 2


class _Config:
    agent = _AgentConfig()


def _run_turn(responses: list, max_iters: int = 500):
    """Drive the real agent loop; return (final_text, memory, adapter)."""
    memory = FakeMemory()
    adapter = FakeAdapter(responses)
    loop = AgentLoop(
        adapter=adapter, memory=memory, bootstrap=None, config=_Config(),
    )
    loop.config.agent.max_tool_iterations = max_iters
    loop._compose_messages = lambda *a, **k: [
        Message(role="system", content="SYSTEM"),
        Message(role="user", content="the question"),
    ]
    events = list(loop.handle_chat_streaming(
        "the question", agent_name="testbot", user_id=1,
    ))
    finals = [e for e in events if e.get("type") == "final"]
    assert finals, f"the turn produced no final event: {events}"
    return finals[-1]["response"].assistant, memory, adapter


def _tool_call_response(name: str = "no_such_tool"):
    """A response that keeps the loop going (the tool does not exist, so
    nothing is executed -- the loop takes the 'Unknown tool' path)."""
    return LLMResponse(
        text=None,
        tool_calls=[ToolCall(id="call-1", name=name, arguments={})],
        finish_reason="tool_use",
    )


def _text_response(text: str):
    """Text, no tool call: the model is talking, not acting."""
    return LLMResponse(text=text, tool_calls=[], finish_reason="stop")


def _as_text(content) -> str:
    """A message's content is a string or a one-element tuple of strings."""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return " ".join(str(c) for c in content)
    return ""


def _nudges(adapter) -> list:
    """The nudge messages the model was shown."""
    out = []
    for m in adapter.seen_messages:
        text = _as_text(getattr(m, "content", None))
        if "tool_use" in text and "did not issue" in text:
            out.append(m)
    return out


# The production turn's closing promise, with the business detail
# genericised -- the sentence SHAPE is what the guard keys on.
PROMISE_TEXT = (
    "Both artefacts are correct in substance. But the export exposed three "
    "real defects for one customer. Let me check one last fact before I "
    "touch the shared code."
)

# The earlier turn's closing promise, same shape.
EARLIER_PROMISE_TEXT = (
    "Now let me check the account categories and the deployed export "
    "behaviour for this customer."
)


# --- the guard ---------------------------------------------------------------


def test_a_promise_after_a_tool_call_is_nudged_and_the_turn_continues():
    """THE REGRESSION. The turn has already run a tool, so the old
    suppression made the intent guard unreachable -- and the promise was
    accepted as the final answer."""
    final_text, _memory, adapter = _run_turn([
        _tool_call_response(),
        _text_response(PROMISE_TEXT),
        _text_response("DONE: the three defects are fixed."),
    ])
    assert _nudges(adapter), "the promise was not nudged -- it was taken as the answer"
    assert final_text.strip() == "DONE: the three defects are fixed.", final_text


def test_the_earlier_production_promise_is_nudged_too():
    final_text, _memory, adapter = _run_turn([
        _tool_call_response(),
        _text_response(EARLIER_PROMISE_TEXT),
        _text_response("DONE."),
    ])
    assert _nudges(adapter), "the promise was not nudged"
    assert final_text.strip() == "DONE.", final_text


def test_a_promise_on_the_first_iteration_is_still_nudged():
    """The pre-existing behaviour must not change: before any tool has
    run, a promise was already nudged."""
    final_text, _memory, adapter = _run_turn([
        _text_response(PROMISE_TEXT),
        _text_response("DONE."),
    ])
    assert _nudges(adapter), "the promise was not nudged"
    assert final_text.strip() == "DONE.", final_text


# --- the guard must NOT fire on a report of work already done ---------------


def test_a_wrap_up_after_a_tool_call_is_not_nudged():
    """The case the suppression was built for: the model has just run a
    tool and is reporting the result. That is an answer."""
    wrap_up = ("PDF rendered (3,988 bytes) attached - paperclip on this "
               "message. The invoice is ready for review.")
    final_text, _memory, adapter = _run_turn([
        _tool_call_response(),
        _text_response(wrap_up),
    ])
    assert not _nudges(adapter), "a wrap-up summary must not be nudged"
    assert final_text == wrap_up, final_text


def test_a_promise_that_is_not_the_last_thing_said_is_not_nudged():
    """The turn ends on a report, not on the promise: the promise is in an
    earlier sentence. That is the model thinking out loud, and the answer
    follows it."""
    text = ("Let me check the totals. I found three defects and fixed them "
            "in the renderer; the PDF now renders the address block "
            "correctly and the invoice is attached.")
    final_text, _memory, adapter = _run_turn([
        _tool_call_response(),
        _text_response(text),
    ])
    assert not _nudges(adapter), "a promise that is not the last thing said must not nudge"
    assert final_text == text, final_text


def test_an_offer_of_later_work_is_not_a_promise():
    """"Let me know if you want the other format" is a closing, not a
    promise to act now."""
    text = ("Done - the invoice is attached and the totals match. Let me "
            "know if you want it in the other currency.")
    final_text, _memory, adapter = _run_turn([
        _tool_call_response(),
        _text_response(text),
    ])
    assert not _nudges(adapter), "an offer of later work must not nudge"
    assert final_text == text, final_text


# --- the diagnostic ---------------------------------------------------------


def test_the_turn_says_it_ended_on_an_unfulfilled_promise():
    """When the model keeps promising and the nudges run out, the turn must
    SAY that -- not present the promise as the answer."""
    final_text, _memory, adapter = _run_turn([
        _tool_call_response(),
        _text_response(PROMISE_TEXT),
    ])
    assert "unfulfilled promise" in final_text, final_text
    assert "tool-iteration cap" not in final_text, final_text
    assert "of the 500 allowed tool iterations" in final_text, final_text
    # The model's own words are kept -- the work is not thrown away.
    assert PROMISE_TEXT in final_text, final_text


def test_a_normal_answer_is_untouched():
    final_text, _memory, _adapter = _run_turn([
        _tool_call_response(),
        _text_response("All done - the three defects are fixed and the "
                       "invoice PDF renders the address block correctly."),
    ])
    assert final_text.startswith("All done"), final_text
