"""Mid-turn steering + the LIVE-ONLY thought stream (2026-10-25).

Two features Mal asked for:

1. "in openclaw i can message you within your turn, and it interrupts you
   between iterations ... please examine how it is implemented in your
   openclaw version, and enable this feature also in our harness webchat"
   -> OpenClaw's `Agent.steer()` queues a user message and the agent loop
      polls `getSteeringMessages()` at the END of each iteration, pushing
      the message into the live message list so the model sees it on its
      NEXT call, inside the same turn. The harness now does the same.

2. The thought stream. It WAS persisted as one turn per iteration
   (model='thought-stream'), which Mal rejected on 2026-10-25 14:02:
   "the thought streams did come through, but as separate turns which has
   consumed the chat history in the webchat and also polluted the
   peribot.turns table with erroneous turns ... fix it so the thought stream
   appears as a collapsible section like the tool call summary. thought
   stream doesn't need to persist after refresh (eg. like the iter
   summary)".

   So the narration is now LIVE ONLY: streamed as a 'thought' event and
   rendered in a collapsible section, gone after a refresh. It is still
   recorded -- in the turn's own trajectory (iterations[].assistant_text)
   -- so the trail survives a turn that never finishes.

What these tests pin down:
  - the steering message lands in the live message list with the right
    shape, and never orphans a tool_result (the API 400s on that)
  - a tool_result message keeps its tool_result blocks FIRST
  - the steering queue drains in order and empties
  - the loop writes NO narration row (the turns table holds only turns)
  - the narration is still streamed to the client as a 'thought' event
  - the history query and the model replay both exclude a narration row
  - the web chat renders the narration in a COLLAPSIBLE section, and a
    narration row never becomes a bubble
"""
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

# The repo this test lives in -- not an install path. A hardcoded absolute
# path only works on the machine the test was written on.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.agent.llm.base import Message
from app.agent.loop import AgentLoop
from app.agent.turn_guard import TurnState

APP_JS = (REPO / "app" / "static" / "app.js").read_text()


# --- steering message shape -------------------------------------------------

def _tool_result_msg(call_id="t1"):
    return Message(role="user", content=[
        {"type": "tool_result", "tool_use_id": call_id, "content": "ok"},
    ])


def test_steering_message_becomes_a_user_message():
    messages = [
        Message(role="system", content="SYSTEM"),
        Message(role="user", content="the question"),
        Message(role="assistant", content=[{"type": "tool_use", "id": "t1", "name": "exec_bash", "input": {}}]),
    ]
    AgentLoop._append_steering_message(messages, "actually, use the other branch")
    assert messages[-1].role == "user"
    assert messages[-1].content == "actually, use the other branch"


def test_steering_message_rides_along_with_the_tool_result():
    """A tool_result must stay the FIRST block of its message.

    The Anthropic-compatible endpoints merge consecutive user messages
    anyway, so the steering text goes into the tool_result message as a
    text block after it -- that keeps the tool_result shape valid.
    """
    messages = [
        Message(role="system", content="SYSTEM"),
        Message(role="user", content="the question"),
        Message(role="assistant", content=[{"type": "tool_use", "id": "t1", "name": "exec_bash", "input": {}}]),
        _tool_result_msg("t1"),
    ]
    AgentLoop._append_steering_message(messages, "also attach the screenshot")
    assert len(messages) == 4, "no extra message -- the text joins the tool_result"
    blocks = messages[-1].content
    assert blocks[0]["type"] == "tool_result", "the tool_result stays first"
    assert blocks[-1] == {"type": "text", "text": "also attach the screenshot"}
    # the tool_result is still answerable
    assert blocks[0]["tool_use_id"] == "t1"


def test_steering_message_with_an_image_block():
    messages = [
        Message(role="system", content="SYSTEM"),
        Message(role="user", content="q"),
        Message(role="assistant", content=[{"type": "tool_use", "id": "t1", "name": "exec_bash", "input": {}}]),
        _tool_result_msg("t1"),
    ]
    vision = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
        {"type": "text", "text": "here is the screenshot"},
    ]
    AgentLoop._append_steering_message(messages, vision)
    blocks = messages[-1].content
    assert blocks[0]["type"] == "tool_result"
    assert blocks[1]["type"] == "image"
    assert blocks[2]["type"] == "text"


# --- the steering queue -----------------------------------------------------

def _state():
    return TurnState(
        conv_id="conv-1", agent_name="peribot", user_id=1,
        user_turn=object(), user_text="q", attachments=[],
        t0=0.0, model="m",
    )


def test_steering_queue_drains_in_order_and_empties():
    st = _state()
    st.enqueue_steering({"text": "first"})
    st.enqueue_steering({"text": "second"})
    out = st.drain_steering()
    assert [m["text"] for m in out] == ["first", "second"]
    assert st.drain_steering() == [], "the queue is empty after a drain"
    assert st.enqueue_steering({"text": "third"}) == 1, "the depth restarts"


def test_steering_queue_is_thread_safe():
    """The queue is written by the request thread and read by the loop
    thread, so it has to be locked."""
    import threading

    st = _state()
    def push():
        for i in range(50):
            st.enqueue_steering({"text": str(i)})

    t = threading.Thread(target=push)
    t.start()
    t.join()
    assert len(st.drain_steering()) == 50


# --- the loop really drains it ---------------------------------------------

def test_loop_drains_the_steering_queue_at_the_iteration_boundary():
    src = inspect.getsource(AgentLoop.handle_chat_streaming)
    assert "drain_steering" in src, "the loop must drain the steering queue"
    # the drain has to happen INSIDE the iteration loop, i.e. before the
    # next model call -- that is what "interrupts between iterations" means
    loop_at = src.index("while tool_iters < max_iters:")
    drain_at = src.index("drain_steering")
    assert drain_at > loop_at, "the drain must be inside the iteration loop"
    # and the drained message has to go into the live message list
    assert "_append_steering_message" in src


# --- the thought stream is LIVE ONLY ---------------------------------------

def test_the_loop_writes_NO_narration_row():
    """A turn row per iteration put 132 rows into one turn's history and
    filled the turns table with rows that were not turns (Mal,
    2026-10-25 14:02). The loop must not write one."""
    src = inspect.getsource(AgentLoop.handle_chat_streaming)
    assert 'model="thought-stream"' not in src, (
        "the narration must NOT be persisted as a turn")
    assert "persist_thought_stream" not in src, (
        "the setting is gone -- the narration is live-only")
    # and no narration row is written anywhere in the loop
    assert "thought_row" not in src, (
        "there is no narration row to write or update")


def test_the_narration_is_still_streamed_to_the_client():
    """Live-only does NOT mean invisible: the client still gets the
    narration, as a 'thought' event, and renders it in the collapsible
    section."""
    src = inspect.getsource(AgentLoop.handle_chat_streaming)
    assert '"type": "thought"' in src, (
        "the narration must still be streamed as a 'thought' event")
    assert "_one_line_thought" in src, "the thought is still derived"


def test_the_narration_is_still_recorded_in_the_trajectory():
    """The trail must survive a turn that never finishes. It lives in the
    turn's own trajectory now (iterations[].assistant_text), not in rows
    of its own."""
    src = inspect.getsource(AgentLoop.handle_chat_streaming)
    assert '"assistant_text": response.text' in src, (
        "each iteration records the model's text in the emission record")
    assert '"iterations": state.emission_iters' in inspect.getsource(
        AgentLoop._persist_interrupted_turn
    ), "an interrupted turn persists the emission trail"


# --- the history and the replay exclude a narration row --------------------

def _capture_sql():
    from app.agent.memory import MemoryAPI

    captured = {}

    class _Cur:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            # the first query (in list_turns_for_persistent) looks the
            # conversation up; it must return a row or the method returns
            # before the turns query is ever issued
            captured["query_count"] = captured.get("query_count", 0) + 1
        def fetchall(self):
            if "FROM peribot.conversations" in captured.get("sql", ""):
                return [("00000000-0000-0000-0000-000000000000",)]
            return []
        def fetchone(self):
            if "FROM peribot.conversations" in captured.get("sql", ""):
                return ("00000000-0000-0000-0000-000000000000",)
            return None
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    mem = MemoryAPI.__new__(MemoryAPI)
    mem.pool = _Pool()
    mem.agent_name = "peribot"
    return mem, captured


def test_the_model_replay_excludes_the_narration_rows():
    """The narration rows are for the human; replaying them would fill the
    51-turn context window with old narration. The rows no longer exist,
    so this is a guard against a legacy row."""
    mem, captured = _capture_sql()
    mem.load_recent_turns("00000000-0000-0000-0000-000000000000", limit=5,
                          include_thought_stream=False)
    assert "model IS DISTINCT FROM 'thought-stream'" in captured["sql"]


def test_the_CHAT_HISTORY_excludes_the_narration_rows():
    """The web chat's history must not contain a narration row either --
    that is what ate the 50-row window (Mal: "consumed the chat history
    in the webchat")."""
    mem, captured = _capture_sql()
    mem.list_turns_for_persistent("peribot", user_id=1, limit=50)
    assert "model IS DISTINCT FROM 'thought-stream'" in captured["sql"], (
        "the history query must filter the narration rows")


# --- the web chat renders the narration in a COLLAPSIBLE section ------------

def test_the_thought_section_is_a_collapsible_details():
    """Mal: 'fix it so the thought stream appears as a collapsible section
    like the tool call summary'."""
    assert "liveThoughts = document.createElement('details')" in APP_JS, (
        "the thought section must be a <details> (collapsible), like the trace")
    assert "liveThoughts.className = 'thoughts'" in APP_JS
    assert "liveThoughtsSummary = document.createElement('summary')" in APP_JS, (
        "a <details> needs a <summary> to be collapsible")


def test_the_thought_lines_go_inside_the_section():
    assert "liveThoughtLines.appendChild(line)" in APP_JS, (
        "the narration lines go inside the collapsible section")
    assert "liveThoughts.appendChild(marker)" not in APP_JS, (
        "the markers go inside the section too, not onto the bubble")


def test_a_narration_row_never_becomes_a_bubble():
    """The second line of defence: even if a legacy row reaches the
    client, it is dropped instead of rendered as its own bubble."""
    m = re.search(
        r"if \(t\.model === 'thought-stream'\) \{\s*return;\s*\}", APP_JS)
    assert m, "a narration row must be dropped, not rendered"


def test_the_persist_toggle_is_gone():
    """The burger-menu toggle controlled the narration rows. Those rows
    no longer exist, so the toggle would be a dead control."""
    assert "menuPersistThoughtStream" not in APP_JS
    assert "persist_thought_stream" not in APP_JS


# --- the one-line thought ---------------------------------------------------

def test_one_line_thought_prefers_the_models_own_text():
    class _R:
        text = "Let me pick up the thread and find out exactly where the tree is."
        thinking_blocks = [{"thinking": "something else entirely"}]
    assert AgentLoop._one_line_thought(_R()).startswith("Let me pick up the thread")


def test_one_line_thought_falls_back_to_the_first_sentence_of_the_thinking():
    """MiniMax returns NO text when it calls a tool -- the reasoning is
    then the only source of a one-line thought."""
    class _R:
        text = ""
        thinking_blocks = [{
            "thinking": "So state: the accounts repo has 2 modified files "
                        "uncommitted. Now I should verify the three hazards.",
        }]
    t = AgentLoop._one_line_thought(_R())
    assert t.startswith("So state:")
    assert "\n" not in t, "it has to be ONE line"
    assert len(t) <= 300


def test_one_line_thought_is_empty_when_there_is_nothing():
    class _R:
        text = ""
        thinking_blocks = []
    assert AgentLoop._one_line_thought(_R()) == ""
