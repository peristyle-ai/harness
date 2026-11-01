"""The group chat stream must render what the 1:1 stream renders.

The 1:1 chat (``app/static/app.js``) has had the streaming features
since 2026-10-25: the live thought stream, the collapsible tool-call
trace, and one line per tool call.  The group chat client
(``app/static/group.js``) never got them.  The server was never the
problem -- ``app/agent/group_chat.py`` forwards EVERY event the agent
loop emits (iter_start / delta / iter_text / thought / tool_start /
tool_end / nudge / final), each tagged with the agent it belongs to --
so the data reached the browser and the browser threw it away.

What the operator saw in the group chat:

  * no thought stream at all.  The client has no ``case 'thought':``
    branch and the live bubble has no thought section, so the model's
    per-iteration narration was invisible.
  * every tool call TWICE.  ``tool_start`` appends a "🔧 name(args)"
    line and ``tool_end`` appends a SECOND "✓ name" line, instead of
    updating the pending line in place the way the 1:1 client does.
  * poor formatting.  The trace and thought styles are scoped to
    ``.msg`` (the 1:1 bubble class); the group bubble is ``.gmsg``, so
    the group's ``<details class="trace">`` renders with no styling at
    all -- no muted summary, no coloured items, no scroll cap.
  * "thinking…" in the ANSWER slot.  The group's ``iter_start``
    branch overwrites the answer body with a placeholder, which is
    exactly what the operator rejected in the 1:1 chat ("i don't want
    the thinking ... (iter 10) to appear in the answer slot").
  * the trace was LOST at the end of the turn: the persisted
    transcript row replaces the live bubble wholesale, and the
    persisted bubble carries no trace.

These tests pin the client behaviour (the repo's convention for the
front end is to read the source, as in
tests/test_streaming_error_declaration.py) and pin the server
behaviour that the client's fix depends on.
"""

import re
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

GROUP_JS = (REPO / "app" / "static" / "group.js").read_text()
APP_CSS = (REPO / "app" / "static" / "app.css").read_text()


def _case(source: str, name: str) -> str:
    """The body of one `case '<name>':` branch of group.js."""
    marker = "case '%s':" % name
    start = source.find(marker)
    assert start >= 0, "group.js must handle the '%s' event" % name
    rest = source[start + len(marker):]
    nxt = rest.find("case '")
    return rest if nxt < 0 else rest[:nxt]


# --- the client ------------------------------------------------------------

def test_the_group_client_handles_the_thought_event():
    assert "case 'thought':" in GROUP_JS, (
        "group.js must handle the 'thought' event. The server sends it "
        "(group_chat.py forwards every loop event); the client ignored "
        "it, so the thought stream never rendered in a group chat."
    )


def test_the_group_live_bubble_carries_a_thought_section():
    assert 'class="thoughts"' in GROUP_JS, (
        "the live group bubble must carry a <details class=\"thoughts\"> "
        "section -- there is nowhere to render the thought stream "
        "without it"
    )
    assert 'class="thought-lines"' in GROUP_JS, (
        "the thought section needs a .thought-lines container to hold "
        "the per-iteration lines"
    )


def test_a_tool_call_is_one_line_and_not_two():
    """tool_end must update the pending line, not append a second one."""
    body = _case(GROUP_JS, "tool_end")
    assert "trace-item.pending" in body, (
        "tool_end must find the pending line the tool_start branch "
        "wrote (the 1:1 client does this with a data-tool attribute) "
        "and update it in place"
    )
    assert "classList.remove('pending')" in body, (
        "tool_end must mark the pending line as done in place. "
        "Appending a second line is what made every tool call appear "
        "twice in the group chat."
    )


def test_the_tool_line_remembers_which_tool_it_is():
    body = _case(GROUP_JS, "tool_start")
    assert "dataset.tool" in body, (
        "tool_start must record the tool name on the line (the 1:1 "
        "client uses data-tool) so tool_end can match the right line"
    )


def test_iter_start_does_not_write_into_the_answer_slot():
    body = _case(GROUP_JS, "iter_start")
    assert "gmsg-body" not in body, (
        "iter_start must NOT write the 'thinking…' placeholder into "
        ".gmsg-body. The body is the answer; the marker belongs in the "
        "thought section. The operator rejected exactly this in the 1:1 "
        "chat."
    )
    assert "thought-lines" in body, (
        "the iteration marker belongs in the thought section"
    )


def test_the_group_client_handles_the_nudge_event():
    assert "case 'nudge':" in GROUP_JS, (
        "group.js must handle the 'nudge' event: the 1:1 chat shows it "
        "in the trace, so the operator can see that the model narrated "
        "a tool call instead of making one"
    )


def test_the_tool_trace_survives_the_persisted_transcript_row():
    body = _case(GROUP_JS, "group_message")
    assert "trace" in body and "thoughts" in body, (
        "when the persisted transcript row replaces the live bubble, "
        "the trace and the thought stream must be carried over. They "
        "exist only in the live DOM, so replacing the bubble wholesale "
        "silently threw the tool-call summary away at the end of every "
        "turn."
    )


def test_the_trace_is_collapsed_by_default_in_the_group_chat():
    bubble = GROUP_JS
    m = re.search(r"liveBubbleFor[\s\S]{0,2000}?appendChild\(div\)", bubble)
    assert m, "liveBubbleFor must build the live bubble"
    body = m.group(0)
    for details in re.findall(r"<details[^>]*>", body):
        assert "open" not in details, (
            "the group's trace and thought sections must start "
            "collapsed: %s" % details
        )


# --- the stylesheet --------------------------------------------------------

def test_the_trace_and_thought_styles_reach_the_group_bubbles():
    for selector in (
        ".gmsg .thoughts",
        ".gmsg .thought-lines",
        ".gmsg .trace",
        ".gmsg .trace-list",
        ".gmsg .trace-item",
    ):
        assert selector in APP_CSS, (
            "app.css must style %s. The trace and thought styles were "
            "scoped to .msg (the 1:1 bubble class) while the group "
            "bubble is .gmsg, so the group's trace rendered with no "
            "styling at all." % selector
        )


# --- the server ------------------------------------------------------------

class _FakeStore:
    def __init__(self):
        self.added = []

    def add_message(self, group_id, author_type, content, author_id=None,
                    author_name="", attachments=None, mentions=None):
        self.added.append({"author_type": author_type, "content": content})
        return "m%d" % len(self.added)

    def ensure_agent_conversation(self, agent, conversation_id, group_name):
        return None

    def list_messages(self, group_id, limit=200):
        return []


class _FakeLoop:
    """A loop that emits the events the real one emits."""

    def handle_chat_streaming(self, **kwargs):
        yield {"type": "iter_start", "iter": 1}
        yield {"type": "thought", "iter": 1, "text": "let me look"}
        yield {"type": "tool_start", "name": "exec_bash", "args": {"command": "ls"}}
        yield {"type": "tool_end", "name": "exec_bash", "ok": True}
        yield {"type": "nudge", "text": "narrated"}
        yield {
            "type": "final",
            "response": SimpleNamespace(
                assistant="done", conversation_id="c", model="m",
                latency_ms=12, tool_iterations=1,
                retrieved_chunk_ids=[], attachments=[],
            ),
        }


def test_the_server_forwards_the_thought_event_to_the_group_client():
    """The server side already worked -- pin it, so the client fix is
    the whole gap and nobody 'fixes' the forwarding instead."""
    from app.agent.group_chat import run_group_message

    events = list(run_group_message(
        store=_FakeStore(),
        group=SimpleNamespace(id="g1", conversation_id="c1", name="G",
                              agents=["alpha"]),
        user=SimpleNamespace(id=1, display_name="you", email="you@x"),
        user_text="@alpha hello",
        allowed_agents=["alpha"],
        loop=_FakeLoop(),
    ))
    types = [e.get("type") for e in events]
    assert "thought" in types, (
        "the group server must forward the loop's 'thought' event; the "
        "client fix depends on it"
    )
    for e in events:
        if e.get("type") in ("thought", "tool_start", "tool_end", "nudge"):
            assert e.get("agent") == "alpha", (
                "every forwarded event must carry the agent it belongs "
                "to, or the client cannot tell whose turn it is"
            )


# --- the client must parse -------------------------------------------------

def test_the_group_client_is_valid_javascript():
    """A syntax error in group.js kills the whole group chat.

    The client is one IIFE: if it does not parse, nothing in the group
    chat works -- not the messages, not the stream, not the trace. The
    check is skipped where node is not installed (the CI image does not
    have it), so it never turns into a false failure.
    """
    import shutil
    import subprocess

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        import pytest
        pytest.skip("node is not installed")
    proc = subprocess.run(
        [node, "--check", str(REPO / "app" / "static" / "group.js")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "group.js must parse: %s" % (proc.stderr.strip() or proc.stdout.strip())
    )
