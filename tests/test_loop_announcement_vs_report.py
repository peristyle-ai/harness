"""An announcement of work and a report of work already done (2026-11-02).

The "intent without execution" guard exists to catch the model that
describes an action it never performs ("Resending the email.") and
leaves it there as the answer. That is a real failure mode and the
guard is worth keeping.

But the guard keyed on a bare action verb ANYWHERE in the text
(_INTENT_RE), and ordinary English is full of action verbs. Measured
against one production conversation's 89 assistant answers, it fired on
86 of them.

The failure it caused was concrete. An agent was asked to stop working
and report where it had got to. It did exactly that, in twelve
seconds:

    "I'm stopping. Here's exactly where I got to.

     ## Where I got to

     **Done and verified:**

     1. **Checked the live state first** - ..."

The guard matched the word "got" ("where I GOT to"), told the agent it
had narrated an action without performing it, and pushed it back into
the work loop for another 45 iterations and forty minutes.

So the guard has to tell two things apart:

  * an ANNOUNCEMENT of work that is not done - a forward frame ("let
    me check", "I'll send") or a bare progressive ("Resending the
    email."), which is the LAST thing the turn says, and which is
    neither negated nor gated on a consent-conditional;

  * a REPORT of work already done - "here is where I got to", "done
    and verified", "read-only, nothing changed" - which is an answer.

And the claim guard ("PDF rendered (3,988 bytes)") has to look at the
CONVERSATION, not just the current turn: a claim of work done is only
a fabrication if the work was never done.

These tests drive the real agent loop over a fake adapter: no
database, no model, and no tool is ever executed.
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

    def __init__(self, conversation_ran_tool: bool = False):
        self.assistant_texts: list[str] = []
        # What the conversation-scoped evidence says: has this
        # conversation run a tool in an earlier turn?
        self.conversation_ran_tool_flag = conversation_ran_tool

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

    def conversation_ran_tool(self, conv_id, tool_name=None):
        return self.conversation_ran_tool_flag


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


def _run_turn(responses: list, max_iters: int = 500,
              conversation_ran_tool: bool = False):
    """Drive the real agent loop; return (final_text, memory, adapter)."""
    memory = FakeMemory(conversation_ran_tool=conversation_ran_tool)
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


# --- the production shape, with the business detail genericised ------------

STATUS_REPORT = (
    "I'm stopping. Here's exactly where I got to.\n\n"
    "## Where I got to\n\n"
    "**Done and verified:**\n\n"
    "1. **Checked the live state first** - the record and how the report "
    "renders.\n"
    "2. **Confirmed the totals** - checked against the live service, not "
    "against the code.\n\n"
    "Nothing was changed; this was a read-only pass."
)


# --- the regression ---------------------------------------------------------


def test_a_status_report_is_not_nudged():
    """THE REGRESSION. The agent was asked to stop and say where it had
    got to; it did, and the guard told it off for narrating instead of
    acting -- because "where I GOT to" matched an action verb."""
    final_text, _memory, adapter = _run_turn([_text_response(STATUS_REPORT)])
    assert not _nudges(adapter), (
        "a status report is an answer, not an announcement of work"
    )
    assert final_text == STATUS_REPORT, final_text


def test_a_status_report_after_a_tool_call_is_not_nudged():
    final_text, _memory, adapter = _run_turn([
        _tool_call_response(),
        _text_response(STATUS_REPORT),
    ])
    assert not _nudges(adapter), "a status report must not be nudged"
    assert final_text == STATUS_REPORT, final_text


def test_a_read_only_answer_is_not_nudged():
    """The other shape the guard used to catch: "read-only" is an action
    verb in the list, and "changed" is not."""
    text = ("Read-only, nothing changed. The totals match the live "
            "service.")
    final_text, _memory, adapter = _run_turn([_text_response(text)])
    assert not _nudges(adapter), "a plain answer must not be nudged"
    assert final_text == text, final_text


# --- the guard must still catch an announcement ------------------------------


def test_a_narration_of_work_is_nudged():
    """The failure mode the guard was built for: the model announces an
    action and does not perform it."""
    final_text, _memory, adapter = _run_turn([
        _text_response("Resending the invoice now."),
        _text_response("DONE: the invoice is attached."),
    ])
    assert _nudges(adapter), "a narration of work must be nudged"
    assert final_text.strip() == "DONE: the invoice is attached.", final_text


def test_a_promise_at_the_end_is_nudged():
    final_text, _memory, adapter = _run_turn([
        _text_response("Both artefacts are correct. Let me check one last "
                       "fact before I touch the shared code."),
        _text_response("DONE."),
    ])
    assert _nudges(adapter), "a promise must be nudged"
    assert final_text.strip() == "DONE.", final_text


# --- and must not catch an offer, or a negation ----------------------------


def test_an_offer_of_later_work_is_not_nudged():
    """"Say the word and I'll build it" is an offer, not a promise to
    act now."""
    text = ("Done - the report renders correctly and the totals match. "
            "Say the word and I'll build the other format.")
    final_text, _memory, adapter = _run_turn([_text_response(text)])
    assert not _nudges(adapter), "an offer of later work must not nudge"
    assert final_text == text, final_text


def test_a_negated_intent_is_not_nudged():
    """"I will NOT push" is a refusal, not an announcement."""
    text = ("I will NOT touch the shared code without explicit "
            "confirmation.")
    final_text, _memory, adapter = _run_turn([_text_response(text)])
    assert not _nudges(adapter), "a negated intent must not nudge"
    assert final_text == text, final_text


# --- the claim guard looks at the CONVERSATION -----------------------------


def test_a_prose_claim_is_nudged_when_the_conversation_ran_no_tool():
    """A conversation where nothing ever ran: "PDF rendered (3,988
    bytes)" is a fabrication."""
    text = ("PDF rendered (3,988 bytes) attached - paperclip on this "
            "message.")
    final_text, _memory, adapter = _run_turn(
        [_text_response(text)], conversation_ran_tool=False,
    )
    assert _nudges(adapter), "a fabricated claim must be nudged"
    assert final_text.strip() == text.strip(), final_text


def test_a_prose_claim_is_not_nudged_when_the_conversation_ran_a_tool():
    """The same words in a conversation where the work WAS done are a
    report."""
    text = ("PDF rendered (3,988 bytes) attached - paperclip on this "
            "message.")
    final_text, _memory, adapter = _run_turn(
        [_text_response(text)], conversation_ran_tool=True,
    )
    assert not _nudges(adapter), "a claim backed by the conversation's own work must not nudge"
    assert final_text == text, final_text


# --- the detector itself ----------------------------------------------------


def test_the_detector_tells_an_announcement_from_a_report():
    from app.agent.loop import _announces_unfinished_work as announces

    # announcements
    assert announces("Resending the invoice now.")
    assert announces("Let me check one last fact before I touch the code.")
    assert announces("Both are correct. Let me confirm the totals.")

    # reports
    assert not announces(STATUS_REPORT)
    assert not announces("Read-only, nothing changed.")
    assert not announces("Done - the report renders correctly.")
    assert not announces("Say the word and I'll build the other format.")
    assert not announces("I will NOT touch the shared code.")
    assert not announces("")
    assert not announces(None)
