"""The end-of-turn diagnostic must tell the truth (2026-10-27).

A turn can end with no final text for two very different reasons, and
the old code reported BOTH as the same thing -- "I wasn't able to finish
reasoning within the allowed N tool iterations":

  CASE A -- the iteration cap was reached while the model was still
            making tool calls. The diagnostic has to say that, and has
            to say how many iterations the turn used.

  CASE B -- the model ended the turn with NO text and NO tool call. The
            minimax adapter drops thinking blocks from `.text`, so a
            reasoning-only response arrives with text=None. The
            diagnostic must NOT claim a cap hit (the cap was barely
            touched), and the model's last output must not be thrown
            away.

On 2026-10-27 at 17:26 a pull-request turn whose work was DONE reported
"hit the 25-iteration cap" -- with 477 iterations still unused -- and the
operator went hunting for the wrong bug.

These tests drive the REAL agent loop over a fake adapter: no database,
no model, and no tool is ever executed (the cap case asks for a tool
that does not exist, so the loop takes the "Unknown tool" path and runs
nothing).
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.llm.base import LLMResponse, Message, ToolCall
from app.agent.loop import AgentLoop

# The wording the loop used for BOTH cases before 2026-10-27.
OLD_CANNED = "I wasn't able to finish reasoning within the allowed"


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
    """A model that returns a canned list of responses."""

    name = "fake-model"
    adapter_id = "fake"

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
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
    tool_output_head_chars = 4_000
    tool_output_tail_chars = 4_000
    max_concurrent_llm = 2


class _Config:
    agent = _AgentConfig()


def _run_turn(responses: list, max_iters: int = 3):
    """Drive the real agent loop; return (final_text, memory)."""
    memory = FakeMemory()
    adapter = FakeAdapter(responses)
    loop = AgentLoop(
        adapter=adapter, memory=memory, bootstrap=None, config=_Config(),
    )
    loop.config.agent.max_tool_iterations = max_iters
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
    return finals[-1]["response"].assistant, memory


def _tool_call_response(name: str = "no_such_tool"):
    """A response that keeps the loop going (the tool does not exist,
    so nothing is executed -- the loop takes the 'Unknown tool' path)."""
    return LLMResponse(
        text=None,
        tool_calls=[ToolCall(id="call-1", name=name, arguments={})],
        finish_reason="tool_use",
    )


def _thinking_only_response(thinking: str = "The pull request is open."):
    """What the minimax adapter produces for a reasoning-only response:
    no text, no tool call, the reasoning in `thinking_blocks`."""
    return LLMResponse(
        text=None,
        tool_calls=[],
        finish_reason="stop",
        thinking_blocks=[{"type": "thinking", "thinking": thinking}],
    )


# --- CASE A: the iteration cap was really hit --------------------------------


def test_cap_hit_names_the_cap_and_the_iterations_used():
    final_text, _ = _run_turn([_tool_call_response()], max_iters=3)
    assert "tool-iteration cap" in final_text, final_text
    assert "3 of the 3 allowed tool iterations" in final_text, final_text
    # The old wording is gone: the message now says WHICH end it was.
    assert OLD_CANNED not in final_text, final_text


def test_cap_hit_is_not_reported_as_a_no_answer():
    final_text, _ = _run_turn([_tool_call_response()], max_iters=2)
    assert "ended this turn without an answer" not in final_text, final_text


# --- CASE B: the model ended the turn with no answer -------------------------


def test_no_answer_is_not_reported_as_a_cap_hit():
    """The regression: the model ended the turn with a reasoning-only
    response while the cap had barely been touched."""
    final_text, _ = _run_turn([_thinking_only_response()], max_iters=500)
    assert "tool-iteration cap" not in final_text, final_text
    assert OLD_CANNED not in final_text, final_text
    assert "ended this turn without an answer" in final_text, final_text


def test_no_answer_reports_the_iterations_actually_used():
    final_text, _ = _run_turn([_thinking_only_response()], max_iters=500)
    assert "1 of the 500 allowed tool iterations" in final_text, final_text


def test_no_answer_keeps_the_models_last_output():
    """The model's last output must not be thrown away."""
    thinking = "I opened the pull request and the checks went green."
    final_text, memory = _run_turn([_thinking_only_response(thinking)],
                                   max_iters=500)
    assert thinking in final_text, final_text
    # ...and it is what gets persisted as the assistant turn.
    assert thinking in memory.assistant_texts[0], memory.assistant_texts


def test_no_answer_without_thinking_says_so_plainly():
    """An adapter that produces nothing at all: the message must still
    say the turn ended without an answer, not blame the cap."""
    final_text, _ = _run_turn(
        [LLMResponse(text=None, tool_calls=[], finish_reason="stop")],
        max_iters=500,
    )
    assert "ended this turn without an answer" in final_text, final_text
    assert "tool-iteration cap" not in final_text, final_text
    assert "1 of the 500 allowed tool iterations" in final_text, final_text


def test_a_normal_answer_is_untouched():
    final_text, _ = _run_turn(
        [LLMResponse(text="All done.", tool_calls=[], finish_reason="stop")],
        max_iters=500,
    )
    assert final_text == "All done."


# --- the admin view's fabrication detector follows the new wording ---------
#
# app/routes/web.py watches for the model FABRICATING the harness's own
# failure message (the "the model gave up early" failure mode). The
# loop's wording changed, so the detector has to follow it -- and it
# still has to match the OLD wording, which the model can produce from
# its own context.


def test_fabrication_detector_matches_the_new_cap_wording():
    from app.routes.web import _detect_fabrication
    cap_text = AgentLoop._iteration_cap_text(iterations_used=5, max_iters=500)
    # A genuine cap hit is not a fabrication.
    assert _detect_fabrication(cap_text, 5, 5) is None
    # The model claiming the cap when the loop only ran 2 iterations is.
    assert _detect_fabrication(cap_text, 500, 2) == (5, 500, 2)


def test_fabrication_detector_still_matches_the_old_wording():
    from app.routes.web import _detect_fabrication
    old = ("I wasn't able to finish reasoning within the allowed "
           "40 tool iterations.")
    assert _detect_fabrication(old, 500, 2) == (40, 500, 2)
