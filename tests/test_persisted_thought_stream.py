"""A finished turn must still show its thought stream.

THE OPERATOR'S REPORT (2026-11-02): "check on evie's session, I can't see
the thought stream."

WHAT WAS TRUE. The thought stream was LIVE ONLY. The client built it from
the 'thought' event while a turn was streaming, and it lived in the live
bubble's DOM. When the turn ended, the client re-rendered the history
from ``GET /entities/<name>/turns`` -- and that endpoint returns a THIN
row: role, content, model, attachments, created_at. The trajectory is not
in it. So the persisted bubble was the answer and nothing else: no
thought stream, no tool-call trace.

The data was never lost. The loop writes the per-iteration narration and
the tool calls into ``turns.trajectory`` (the 'iterations' array). It was
in the database and on no wire.

THE FIX. Two halves, and both are needed:

  1. The turns endpoint offers the trail. ``?trail=1`` adds a compact
     per-iteration summary of the narration and the tool calls, computed
     in SQL from the stored trajectory -- so the answer text, the tool
     calls and the narration are all on the wire in ONE response.

  2. The client renders it. A persisted assistant turn gets the same
     collapsible thought section and the same tool-call trace the live
     bubble has, collapsed by default, and the summary says how much is
     inside.

WHY NOT SHIP THE RAW TRAJECTORY. The stored trajectory for one busy turn
is ~670 KB (the thinking blocks are the bulk). Fifty of them is ~33 MB
for one page load, and the loop's own measurements put a turn's replayed
thinking at 2.3 MB. The compact form -- one line of narration plus the
tool names per iteration -- is about 20 KB for the same 50 turns.

This is the same lesson the group chat already learned: the persisted
bubble has to CARRY the machinery, or it throws it away at the end of
every turn.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

APP_JS = (REPO / "app" / "static" / "app.js").read_text()
API_PY = (REPO / "app" / "routes" / "api.py").read_text()
MEMORY_PY = (REPO / "app" / "agent" / "memory.py").read_text()


def _fn(source: str, name: str) -> str:
    """The body of one `function <name>(...)` of a client.

    It stops at the function's OWN closing brace, found by counting
    braces. Two cheaper approaches both lie:

      * stopping at the next `function ` declaration misses an
        `async function` and runs 24 KB past the end of the function,
        so an assertion about "thoughts" passes on a LATER function's
        body; and
      * the same is true of the live-bubble code, which is nested
        inside another function and mentions `thoughts` and `trace`.

    A test that passes for the wrong reason is worse than no test, so
    this counts braces.
    """
    marker = "function %s(" % name
    start = source.find(marker)
    assert start >= 0, "the client must have a %s()" % name
    brace = source.find("{", start)
    depth = 0
    end = len(source)
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    return source[brace:end]



def _pyconst(source: str, name: str) -> str:
    # The value of a class-level constant (a triple-quoted SQL
    # fragment, in this case). The literal triple quote never has to
    # appear in this file, so the helper is written with chr(34).
    marker = "%s = " % name
    start = source.find(marker)
    assert start >= 0, "the module must have a %s" % name
    rest = source[start + len(marker):]
    quote = chr(34) * 3
    lines = rest.split(chr(10))
    out = []
    seen_open = False
    for ln in lines:
        if not seen_open:
            if quote in ln:
                seen_open = True
                # the opening line: keep it, and if the value ends
                # on this same line, we are already done
                out.append(ln)
                if ln.count(quote) >= 2:
                    break
            continue
        out.append(ln)
        if quote in ln:
            break
    return chr(10).join(out)


def _strip_docstrings(source: str) -> str:
    """The code of a Python body, with the docstrings removed.

    The docstrings legitimately talk ABOUT the thinking blocks; the
    check is on the code. ast does the removing, so the literal
    triple quote never has to appear in this file.
    """
    import ast
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body.pop(0)
    return ast.unparse(tree)

def _pyfn(source: str, name: str) -> str:
    """The body of one Python `def <name>(...)`, up to the NEXT def.

    Stopping at the next def is the only boundary that holds: a
    docstring's own blank lines and the nested defs inside a function
    both defeat an indentation rule, and the body then runs hundreds of
    lines past the function it was asked about.
    """
    marker = "def %s(" % name
    start = source.find(marker)
    assert start >= 0, "the module must have a %s()" % name
    rest = source[start:]
    nxt = re.search(r"\n(?:    )?(?:async )?def \w+\(", rest[1:])
    return rest if nxt is None else rest[:nxt.start() + 1]


# --- the server half ----------------------------------------------------

def test_the_turns_endpoint_can_deliver_the_trail():
    """The turns endpoint must be able to carry the thought trail."""
    assert "trail" in API_PY, (
        "the turns endpoint must be able to deliver the per-iteration "
        "trail: the narration is in turns.trajectory and on no wire"
    )


def test_the_trail_is_computed_in_sql_not_in_python():
    """The compact form is computed by Postgres, from the stored trajectory.

    Pulling the whole trajectory into Python to slice it up would ship
    megabytes of thinking blocks for every turn on the page. The check
    is inside THIS function: the file already uses jsonb_array_elements
    elsewhere, so a whole-file search would pass for the wrong reason.
    """
    body = _pyfn(MEMORY_PY, "list_turns_for_persistent")
    const = _pyconst(MEMORY_PY, "_TRAIL_EXPR")
    assert "jsonb_path_query_array" in (body + const) or "jsonb_array_elements" in (body + const), (
        "the trail must be computed in SQL from the stored trajectory"
    )


def test_the_trail_does_not_ship_the_thinking_blocks():
    """The compact form must NOT carry the raw thinking blocks.

    They are the bulk of the trajectory (~670 KB for one busy turn) and
    they are the model's private reasoning, not the narration the user
    asked to see.
    """
    # Strip the docstring and the comments: they legitimately talk
    # ABOUT the thinking blocks. The check is on the code.
    body = _pyfn(MEMORY_PY, "list_turns_for_persistent")
    const = _pyconst(MEMORY_PY, "_TRAIL_EXPR")
    code = _strip_docstrings(body) + const
    for bad in ("thinking_blocks", "thinking"):
        assert bad not in code, (
            "the trail must not carry the raw thinking blocks (%s)" % bad
        )


def test_the_trail_holds_only_the_tool_calling_iterations():
    """The answer must not appear twice.

    The last iteration of a turn is the answer. The live stream does not
    put it in the thought stream -- the loop emits a 'thought' event only
    for an interim iteration, one that called a tool -- so the persisted
    trail must not either, or the answer shows up a second time, right
    underneath itself.
    """
    const = _pyconst(MEMORY_PY, "_TRAIL_EXPR")
    assert "jsonb_array_length" in const, (
        "the trail must be filtered to the iterations that called a tool"
    )


def test_the_trail_is_off_by_default():
    """The trail is opt-in, so the ordinary chat load stays thin."""
    assert "include_trail" in MEMORY_PY, (
        "the trail must be opt-in: the default turns response must stay "
        "thin, or every chat page load ships the whole trail"
    )


# --- the client half ----------------------------------------------------

def test_the_client_asks_for_the_trail():
    """The client must request the trail when it loads the history."""
    body = _fn(APP_JS, "loadTurns")
    assert "trail=1" in body, (
        "the client must ask for the trail: without it the persisted "
        "turn has no narration to show"
    )


def test_a_persisted_assistant_turn_gets_a_thought_section():
    """The persisted bubble must carry the collapsible thought section."""
    body = _fn(APP_JS, "appendTurn")
    assert "thoughts" in body, (
        "a persisted assistant turn must render the thought section: "
        "that is what the operator could not see"
    )


def test_a_persisted_assistant_turn_gets_the_tool_trace():
    """The persisted bubble must carry the tool-call trace too."""
    body = _fn(APP_JS, "appendTurn")
    assert "trace" in body, (
        "a persisted assistant turn must render the tool-call trace"
    )


def test_the_persisted_sections_start_collapsed():
    """Collapsed by default, like the live bubble."""
    body = _fn(APP_JS, "appendTurn")
    assert "removeAttribute('open')" in body or "open = false" in body, (
        "the persisted thought section and trace must start CLOSED"
    )


def test_the_sections_say_how_much_is_inside():
    """A collapsed section must still be informative.

    The check is for the ASSIGNMENT, not the words: the phrase appears
    in a comment in this function already, so a bare substring search
    would pass without the summary ever being set.
    """
    body = _fn(APP_JS, "appendTurn")
    code = "\n".join(
        ln for ln in body.split("\n") if not ln.strip().startswith("//")
    )
    assert "thought stream" in code, (
        "the persisted thought section must say how many thoughts it holds"
    )
    assert "tool call" in code, (
        "the persisted trace must say how many tool calls it holds"
    )


def test_a_user_turn_gets_no_thought_section():
    """Only an assistant turn has a narration."""
    body = _fn(APP_JS, "appendTurn")
    assert "role === 'assistant'" in body, (
        "the thought section belongs to the assistant turn only"
    )


def test_the_client_still_parses():
    """The bundle must still be valid JavaScript."""
    node = shutil.which("node")
    if not node:
        return
    r = subprocess.run(
        [node, "--check", str(REPO / "app" / "static" / "app.js")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, "app.js must parse: %s" % r.stderr
