"""The live status line: the model's LATEST thought, at the top of the bubble.

What the operator asked for (2026-11-01, from a screenshot of the group
chat): with the thought stream and the tool-call trace collapsed by
default, the bubble showed an EMPTY coloured bar where the answer goes.
The one line that says "what is this agent doing RIGHT NOW" was only
visible if you opened the thought stream.

So the bubble carries a STATUS LINE: the latest thought, at the top, live.
It is a separate element from the answer, because:

  * a thought must never overwrite the answer, and
  * the answer must never wipe the thought -- which is exactly what
    happens if both are the same element (the old code wrote the
    streamed text straight into ``.gmsg-body``).

Both surfaces get it -- the group chat (``group.js``) and the 1:1 chat
(``app.js``) -- because a feature that exists in one and not the other is
exactly the "the group chat lags behind the DM" complaint.

The repo's convention for the front end is to read the source (as in
tests/test_group_chat_stream_parity.py), plus a node parse check.
"""

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

GROUP_JS = (REPO / "app" / "static" / "group.js").read_text()
APP_JS = (REPO / "app" / "static" / "app.js").read_text()
APP_CSS = (REPO / "app" / "static" / "app.css").read_text()


def _fn(source: str, name: str) -> str:
    """The body of one `function <name>(...)` of a client."""
    marker = "function %s(" % name
    start = source.find(marker)
    assert start >= 0, "the client must have a %s()" % name
    rest = source[start + len(marker):]
    nxt = rest.find("\n  function ")
    return rest if nxt < 0 else rest[:nxt]


def _case(source: str, name: str) -> str:
    """The body of one `case '<name>':` branch of a client."""
    marker = "case '%s':" % name
    start = source.find(marker)
    assert start >= 0, "the client must handle the '%s' event" % name
    rest = source[start + len(marker):]
    nxt = rest.find("case '")
    return rest if nxt < 0 else rest[:nxt]


def _branch(source: str, name: str) -> str:
    """The body of one event branch, whichever form the client uses.

    The group client switches on `case 'thought':`; the 1:1 client
    chains `else if (ev.type === 'thought')`. Both are the same branch.
    """
    marker = "case '%s':" % name
    start = source.find(marker)
    if start >= 0:
        rest = source[start + len(marker):]
        nxt = rest.find("case '")
        return rest if nxt < 0 else rest[:nxt]
    marker = "ev.type === '%s'" % name
    start = source.find(marker)
    assert start >= 0, "the client must handle the '%s' event" % name
    rest = source[start + len(marker):]
    nxt = rest.find("ev.type === '")
    return rest if nxt < 0 else rest[:nxt]


# --- the group chat --------------------------------------------------------

def test_the_group_bubble_carries_a_status_line():
    assert 'class="gmsg-status"' in GROUP_JS, (
        "the group bubble must carry a .gmsg-status element -- that is "
        "where the model's latest thought is shown"
    )
    assert 'class="gmsg-answer"' in GROUP_JS, (
        "the group bubble must carry a .gmsg-answer element, so the "
        "answer and the status line are separate things"
    )


def test_the_group_status_line_is_at_the_top_of_the_bubble():
    live = _fn(GROUP_JS, "liveBubbleFor")
    body_at = live.find('class="gmsg-body"')
    status_at = live.find('class="gmsg-status"')
    answer_at = live.find('class="gmsg-answer"')
    assert -1 not in (body_at, status_at, answer_at), (
        "the live group bubble must carry a body, a status line and an "
        "answer"
    )
    assert body_at < status_at < answer_at, (
        "the status line must sit AT THE TOP of the bubble, above the "
        "answer (operator: 'the latest thought stream emission should be "
        "showing at the top in the green bar just below the name')"
    )


def test_the_group_thought_event_writes_the_status_line():
    body = _case(GROUP_JS, "thought")
    assert ".gmsg-status" in body, (
        "the 'thought' branch must write into the status line -- that "
        "event carries the model's latest thought"
    )
    assert "ev.text" in body, (
        "the status line must show the thought's text"
    )


def test_the_group_answer_never_wipes_the_status_line():
    """The streamed answer and the status line are different elements."""
    for name in ("delta", "iter_text"):
        body = _case(GROUP_JS, name)
        assert ".gmsg-answer" in body, (
            "the '%s' branch must write the answer into .gmsg-answer. "
            "Writing it into .gmsg-body would wipe the status line out of "
            "the bubble." % name
        )
        assert "querySelector('.gmsg-body')" not in body, (
            "the '%s' branch must not touch .gmsg-body: it holds the "
            "status line, and writing the answer there would delete it"
            % name
        )


def test_the_group_status_line_goes_when_the_answer_arrives():
    finalize = _fn(GROUP_JS, "finalizeBubble")
    assert ".gmsg-status" in finalize and "remove()" in finalize, (
        "when the answer arrives the status line goes: it said what the "
        "agent was doing RIGHT NOW, and the answer is what it did"
    )


# --- the 1:1 chat ----------------------------------------------------------

def test_the_dm_has_the_same_status_line():
    assert "liveStatus" in APP_JS, (
        "the 1:1 chat must have the same status line, or the group chat "
        "is ahead of it instead of level with it"
    )
    assert "className = 'live-status'" in APP_JS, (
        "the 1:1 status line must carry the .live-status class"
    )


def test_the_dm_status_line_is_inside_the_bubble_and_above_the_answer():
    at_status = APP_JS.find("liveDiv.appendChild(liveStatus);")
    at_body = APP_JS.find("liveDiv.appendChild(liveBody);")
    assert -1 not in (at_status, at_body), (
        "the 1:1 status line must be appended to the bubble"
    )
    assert at_status < at_body, (
        "the 1:1 status line must be appended BEFORE the answer, so it "
        "sits at the top of the bubble"
    )


def test_the_dm_thought_event_writes_the_status_line():
    body = _branch(APP_JS, "thought")
    assert "liveStatus.textContent" in body, (
        "the 'thought' branch must write the latest thought into the "
        "status line"
    )
    assert "ev.text" in body, (
        "the status line must show the thought's text"
    )


def test_the_dm_status_line_goes_when_the_answer_arrives():
    assert "liveStatus.remove()" in APP_JS, (
        "when the answer arrives the status line goes"
    )


# --- the styles ------------------------------------------------------------

def test_the_status_line_is_hidden_until_there_is_a_thought():
    assert ".gmsg .gmsg-status:empty" in APP_CSS, (
        "an empty status line must take up no space -- it is only there "
        "when the agent has said something"
    )
    assert ".msg .live-status:empty" in APP_CSS, (
        "the 1:1 status line must be hidden while it is empty too"
    )


def test_the_status_line_is_styled_like_a_thought():
    for selector in (".gmsg .gmsg-status", ".msg .live-status"):
        assert selector in APP_CSS, "the status line (%s) must be styled" % selector
    rule = APP_CSS[APP_CSS.find(".gmsg .gmsg-status {"):][:400]
    assert "font-style: italic" in rule, (
        "the status line is a thought, so it is styled like one: quieter "
        "and italic, not like the answer"
    )


# --- the clients must parse ------------------------------------------------

def test_the_clients_are_valid_javascript():
    """A syntax error in either client kills the whole chat."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        import pytest
        pytest.skip("node is not installed")
    for name in ("group.js", "app.js"):
        proc = subprocess.run(
            [node, "--check", str(REPO / "app" / "static" / name)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            "%s must parse: %s"
            % (name, proc.stderr.strip() or proc.stdout.strip())
        )
