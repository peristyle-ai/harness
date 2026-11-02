"""The trail's SQL must actually run.

THE BUG THIS PINS. The trail's statement was built by splicing a
fragment into an f-string:

    SELECT id, role, content, model, attachments, created_at,
           {trail_expr if include_trail else ""} AS trail
      FROM ...

With the trail OFF that produced a statement ending in a bare
"AS trail" -- a syntax error. Every source-reading test passed, because
the SQL was never executed by a test. It reached production, and the
only thing that caught it was calling the deployed code against the
production database.

THE RULE. A statement that reaches a database has to be run by a test
against a database. Reading the source proves the string is there, not
that Postgres can parse it.

The test runs the EXACT statement the service runs -- the one
MemoryAPI._persistent_turns_sql() returns -- against a throwaway
schema, in both modes: the trail on and the trail off.

It skips when there is no database to talk to. The CI job has one
(the pgvector service); a laptop may not.
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

psycopg = pytest.importorskip("psycopg")

from app.agent.memory import MemoryAPI  # noqa: E402


DATABASE_URL = os.environ.get("DATABASE_URL")

SCHEMA = "trail_sql_probe"

TRAJECTORY = {
    "iterations": [
        {
            "iteration": 0,
            "finish_reason": "tool_use",
            "assistant_text": "Let me look at the thing.",
            "tool_calls": [{"name": "exec_bash", "arguments": {"command": "ls"}}],
            "tool_results": [],
        },
        {
            "iteration": 1,
            "finish_reason": "tool_use",
            "assistant_text": "Now the second step.",
            "tool_calls": [
                {"name": "exec_bash", "arguments": {}},
                {"name": "memory_write", "arguments": {}},
            ],
            "tool_results": [],
        },
        {
            "iteration": 2,
            "finish_reason": "stop",
            "assistant_text": "And that is the answer.",
            "tool_calls": [],
            "tool_results": [],
        },
    ],
    "total_iterations": 3,
    "total_tool_calls": 3,
}


@pytest.fixture(scope="module")
def db():
    if not DATABASE_URL:
        pytest.skip(
            "no DATABASE_URL: the trail's SQL is only proved against a real database"
        )
    try:
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip("cannot reach the database: %s" % exc)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS " + SCHEMA + " CASCADE")
    cur.execute("CREATE SCHEMA " + SCHEMA)
    cur.execute(
        "CREATE TABLE " + SCHEMA + ".turns ("
        " id bigserial PRIMARY KEY,"
        " conversation_id uuid NOT NULL,"
        " role text NOT NULL,"
        " content text NOT NULL,"
        " model text,"
        " attachments jsonb NOT NULL DEFAULT '[]'::jsonb,"
        " created_at timestamptz NOT NULL DEFAULT now(),"
        " trajectory jsonb)"
    )
    cur.close()
    yield conn
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS " + SCHEMA + " CASCADE")
    cur.close()
    conn.close()


def _sql() -> str:
    mem = MemoryAPI(pool=None, embedding_client=None,
                    agent_name=SCHEMA, config=None)
    return mem._persistent_turns_sql()


def _seed(db, conv_id: str) -> None:
    cur = db.cursor()
    cur.execute(
        "INSERT INTO " + SCHEMA + ".turns"
        " (conversation_id, role, content, model, trajectory)"
        " VALUES (%s, 'user', 'do the thing', 'm', NULL),"
        " (%s, 'assistant', 'the answer', 'm', %s::jsonb)",
        (conv_id, conv_id, json.dumps(TRAJECTORY)),
    )
    cur.close()


def _run(db, include_trail: bool, conv_id: str):
    cur = db.cursor()
    cur.execute(_sql(), (include_trail, conv_id, 50))
    rows = cur.fetchall()
    cur.close()
    return rows


def _assistant_trail(rows):
    for r in rows:
        if r[1] == "assistant":
            trail = r[6]
            if isinstance(trail, str):
                trail = json.loads(trail)
            return trail
    return None


def test_the_statement_runs_with_the_trail_ON(db):
    """The service's own statement, executed."""
    conv_id = "11111111-1111-1111-1111-111111111111"
    _seed(db, conv_id)
    rows = _run(db, True, conv_id)
    assert len(rows) == 2, "both turns come back"


def test_the_statement_runs_with_the_trail_OFF(db):
    """The same statement, the trail not asked for.

    This is the mode that was broken: the statement ended in a bare
    "AS trail" and Postgres refused it.
    """
    conv_id = "22222222-2222-2222-2222-222222222222"
    _seed(db, conv_id)
    rows = _run(db, False, conv_id)
    assert len(rows) == 2, "both turns come back"


def test_the_trail_carries_the_narration_and_the_tool_names(db):
    """The trail is the narration and the tool names, per iteration."""
    conv_id = "33333333-3333-3333-3333-333333333333"
    _seed(db, conv_id)
    trail = _assistant_trail(_run(db, True, conv_id))
    assert trail, "the assistant turn must carry a trail"
    texts = [s["text"] for s in trail]
    assert "Let me look at the thing." in texts
    assert "Now the second step." in texts
    names = [n for s in trail for n in s["tools"]]
    assert names == ["exec_bash", "exec_bash", "memory_write"]


def test_the_trail_holds_NO_the_answer_iteration(db):
    """The last iteration is the answer; the thought stream must not
    repeat it underneath itself."""
    conv_id = "44444444-4444-4444-4444-444444444444"
    _seed(db, conv_id)
    trail = _assistant_trail(_run(db, True, conv_id))
    texts = [s["text"] for s in trail]
    assert "And that is the answer." not in texts, (
        "the answer must not be in the thought stream: the live stream "
        "puts only the interim iterations there"
    )


def test_the_trail_is_empty_when_it_is_not_asked_for(db):
    """The ordinary chat load pays nothing for the trail."""
    conv_id = "55555555-5555-5555-5555-555555555555"
    _seed(db, conv_id)
    for r in _run(db, False, conv_id):
        trail = r[6]
        if isinstance(trail, str):
            trail = json.loads(trail)
        assert trail == [], "no trail, no cost"
