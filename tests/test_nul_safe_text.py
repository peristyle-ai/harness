"""A turn must survive whatever a tool printed.

THE FAILURE
-----------
Postgres cannot store a NUL -- not in `text`, not in `jsonb`:

    psycopg.errors.UntranslatableCharacter: unsupported Unicode escape
    sequence
    DETAIL:  \\u0000 cannot be converted to text.

and psycopg refuses a raw NUL in a text parameter outright:

    DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes

A NUL gets into a turn the moment a tool reads a BINARY file -- an ELF
header, a `.pyc`, a core dump, a JPEG.  The bytes travel through the
tool result into the trajectory, and then the turn cannot be persisted
AT ALL.  The reply is lost, the turn is lost, and the error message
that replaces it carries the same bytes, so the failure repeats while
it is being reported.

That is not hypothetical.  On 2026-11-03 it hit two agents on one
install: an agent asked to debug a rendering problem lost its own
answer to the rendering problem, because the answer contained the very
bytes it was reporting on.

TWO FAILURES, DIFFERENT IN KIND
-------------------------------
* **Invalid UTF-8** (a byte like 0xa0) breaks the DECODE.
  `subprocess.run(text=True)` decodes strictly, so the tool raised
  `UnicodeDecodeError` before it could return anything at all.
* **A NUL** is perfectly valid UTF-8, so it survives any decode and
  only explodes at the database, much later and somewhere else entirely.

So the rule these tests pin is: nothing enters the harness carrying a
NUL, and every tool output is decoded leniently.

THE ORDER MATTERS
-----------------
`json.dumps` turns a NUL into the six characters `\\u0000`.  That is
legal JSON -- and Postgres's jsonb parser then rejects it with
`UntranslatableCharacter`.  So sanitising AFTER `json.dumps` is too
late: the escape is already baked into the string.  The sanitising has
to happen BEFORE the serialisation, which is what `nul_safe_deep` does.
"""
import asyncio
import json

import pytest

from app.agent.tools import exec_bash as exec_bash_mod

# The helpers live in their own module so the behaviour can be tested
# against the code as it was BEFORE the fix.  A module-level import
# would make the whole file a collection error on the old code, and a
# collection error is a weaker demonstration than a behaviour that
# fails: it proves the module is missing, not that the bug was real.
try:
    from app.agent.text import NUL, nul_safe, nul_safe_deep
except ImportError:  # the pre-fix tree
    NUL = "\x00"
    nul_safe = None
    nul_safe_deep = None


# --- the helpers ----------------------------------------------------------

def test_the_helper_module_exists():
    """The fix is a module, not a patch in place."""
    assert nul_safe is not None, "app.agent.text does not exist"
    assert nul_safe_deep is not None, "app.agent.text does not exist"


def test_nul_safe_replaces_the_nul_with_a_visible_escape():
    out = nul_safe("before" + NUL + "after")
    assert NUL not in out
    # Visible and greppable: a bare NUL is invisible in a terminal AND
    # unstorable, so a reader would never know something was dropped.
    assert out == "before\\0after"


def test_nul_safe_leaves_ordinary_text_alone():
    text = "a normal answer with unicode: caf\u00e9 \u2014 \u2713"
    assert nul_safe(text) is text


def test_nul_safe_passes_none_through():
    assert nul_safe(None) is None


def test_nul_safe_deep_reaches_into_nested_structures():
    payload = {
        "tool": "exec_bash",
        "stdout": "head" + NUL + "tail",
        "iterations": [{"content": "x" + NUL + "y"}],
    }
    clean = nul_safe_deep(payload)
    assert NUL not in json.dumps(clean)
    assert clean["stdout"] == "head\\0tail"
    assert clean["iterations"][0]["content"] == "x\\0y"


def test_nul_safe_deep_survives_json_serialisation():
    """The escape must not become a `\\u0000` again.

    This is the regression in one assertion: if the NUL were escaped
    AFTER json.dumps, the serialised string would carry `\\u0000` and
    Postgres would refuse the row.
    """
    payload = {"stdout": "a" + NUL + "b"}
    serialised = json.dumps(nul_safe_deep(payload))
    assert "\\u0000" not in serialised
    assert NUL not in serialised


# --- the tool boundary ----------------------------------------------------

def test_exec_bash_decodes_leniently(monkeypatch):
    """Invalid bytes must not raise UnicodeDecodeError out of the tool.

    `text=True` alone decodes STRICTLY, so a single byte like 0xa0 in
    a tool's output killed the whole turn with a decode error before
    the tool could return anything.  This is the assertion that fails
    on the pre-fix code, and it fails for the RIGHT reason: the
    subprocess call did not ask for a lenient decode.
    """
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(exec_bash_mod.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        asyncio.run(exec_bash_mod.run({"command": "true"}, _fake_ctx()))

    assert captured.get("text") is True
    assert captured.get("errors") == "replace", (
        "the subprocess call decodes strictly: invalid bytes in a tool's "
        "output raise UnicodeDecodeError and kill the turn"
    )


def test_exec_bash_sanitises_a_nul_in_the_output(monkeypatch):
    """A NUL in a tool's output must not reach the caller."""
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = "before" + NUL + "after"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(exec_bash_mod.subprocess, "run", fake_run)
    result = asyncio.run(exec_bash_mod.run({"command": "true"}, _fake_ctx()))

    assert NUL not in result["stdout"]
    assert result["stdout"] == "before\\0after"


def _fake_ctx():
    """The smallest context the tool reads."""
    class Ctx:
        user_id = 1
        conversation_id = "00000000-0000-0000-0000-000000000000"
        # The repository's own gate pins the agent names a shipped file
        # may use: the template's agent, or an obvious placeholder.
        agent_name = "testbot"

    return Ctx()
