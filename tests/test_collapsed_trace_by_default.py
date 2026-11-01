"""The thought stream and the tool-call trace are COLLAPSED by default.

The 1:1 chat has two collapsible sections under an answer:

  * the THOUGHT STREAM -- the model's per-iteration narration
  * the TOOL-CALL TRACE -- one line per tool call

Both are ``<details>`` elements, so both are closed by default: the
answer is the answer, and the machinery behind it is one click away.

The one exception was the admin view.  ``app/static/app.js`` opened
BOTH sections for an admin -- at the start of the turn and again at the
end -- so an admin's chat was a wall of interim narration and tool
calls with the answer buried under it.  The operator asked for the
opposite:

    "i would like the thought stream and tool call summarys to be
     collapsed by default"

So the default is the default for EVERYONE.  The information is not
lost: the trace still carries its static summary ("N iter - M tool
calls - Zs") and the thought section still states how many thoughts it
holds, so a collapsed section still says what is in it.

These tests pin the default state.  They read the source, because the
defect is in the source: a branch that opens a <details> by role.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_JS = (REPO / "app" / "static" / "app.js").read_text()


def _send_streaming() -> str:
    """The body of sendStreaming() in app.js."""
    start = APP_JS.find("async function sendStreaming(")
    assert start >= 0, "app.js must have sendStreaming()"
    rest = APP_JS[start:]
    nxt = rest.find("async function sendBuffered(")
    return rest if nxt < 0 else rest[:nxt]


def test_the_live_trace_does_not_open_itself():
    assert "liveTrace.setAttribute('open'" not in APP_JS, (
        "the live tool-call trace must not open itself. The operator "
        "asked for the thought stream and the tool-call summary to be "
        "COLLAPSED BY DEFAULT, for everyone."
    )


def test_the_live_thoughts_do_not_open_themselves():
    assert "liveThoughts.setAttribute('open'" not in APP_JS, (
        "the live thought stream must not open itself -- same reason."
    )


def test_the_streaming_path_has_no_admin_branch():
    body = _send_streaming()
    assert "role === 'admin'" not in body, (
        "the streaming path must not branch on the user's role: the "
        "collapsed default is the default for everyone, admin or not"
    )


def test_the_trace_still_collapses_at_the_end_of_the_turn():
    assert "liveTrace.removeAttribute('open')" in APP_JS, (
        "when the turn ends the trace must be collapsed, not left open"
    )
    assert "liveThoughts.removeAttribute('open')" in APP_JS, (
        "when the turn ends the thought stream must be collapsed too"
    )


def test_a_collapsed_section_still_says_what_is_in_it():
    """Collapsing must not lose the summary."""
    body = _send_streaming()
    assert re.search(r"liveSummary\.textContent\s*=\s*iters\s*\+\s*' iter", body), (
        "the collapsed trace must keep its static summary (N iter - M "
        "tool calls - Zs), otherwise a collapsed section says nothing "
        "about the work it holds"
    )
    assert "liveThoughtsSummary.textContent = 'thought stream" in APP_JS, (
        "the collapsed thought section must still state how many "
        "thoughts it holds"
    )
