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

import contextlib
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
SCHEMA_OTHER = "trail_sql_probe_other"
USER_ID = 4242

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
    for schema in (SCHEMA, SCHEMA_OTHER):
        cur.execute("DROP SCHEMA IF EXISTS " + schema + " CASCADE")
        cur.execute("CREATE SCHEMA " + schema)
        cur.execute(
            "CREATE TABLE " + schema + ".turns ("
            " id bigserial PRIMARY KEY,"
            " conversation_id uuid NOT NULL,"
            " role text NOT NULL,"
            " content text NOT NULL,"
            " model text,"
            " attachments jsonb NOT NULL DEFAULT '[]'::jsonb,"
            " created_at timestamptz NOT NULL DEFAULT now(),"
            " trajectory jsonb)"
        )
        cur.execute(
            "CREATE TABLE " + schema + ".conversations ("
            " id uuid PRIMARY KEY,"
            " user_id bigint,"
            " agent_name text NOT NULL DEFAULT 'wisp',"
            " title text,"
            " created_at timestamptz NOT NULL DEFAULT now(),"
            " updated_at timestamptz NOT NULL DEFAULT now(),"
            " archived_at timestamptz,"
            " is_group boolean NOT NULL DEFAULT false)"
        )
    cur.close()
    yield conn
    cur = conn.cursor()
    for schema in (SCHEMA, SCHEMA_OTHER):
        cur.execute("DROP SCHEMA IF EXISTS " + schema + " CASCADE")
    cur.close()
    conn.close()


def _sql(agent: str = SCHEMA) -> str:
    mem = MemoryAPI(pool=None, embedding_client=None,
                    agent_name=SCHEMA, config=None)
    return mem._persistent_turns_sql(agent)


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


# ---------------------------------------------------------------------------
# THE REGRESSION. The schema belongs to the agent ASKED FOR.
# ---------------------------------------------------------------------------

class _FakePool:
    """The smallest pool MemoryAPI needs: .connection() as a context
    manager that yields a connection with a .cursor()."""

    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return contextlib.nullcontext(self._conn)


def _seed_other(db, conv_id: str, content: str) -> None:
    """A conversation and a turn in the OTHER agent's schema."""
    cur = db.cursor()
    cur.execute(
        "INSERT INTO " + SCHEMA_OTHER + ".conversations"
        " (id, user_id, agent_name, is_group)"
        " VALUES (%s, %s, %s, FALSE)",
        (conv_id, USER_ID, SCHEMA_OTHER),
    )
    cur.execute(
        "INSERT INTO " + SCHEMA_OTHER + ".turns"
        " (conversation_id, role, content, model, trajectory)"
        " VALUES (%s, 'assistant', %s, 'm', %s::jsonb)",
        (conv_id, content, json.dumps(TRAJECTORY)),
    )
    cur.close()


def test_the_service_reads_the_schema_of_the_agent_ASKED_FOR(db):
    """THE BUG. The route holds the DEFAULT agent's MemoryAPI ("memory":
    default_memory -- back-compat) and asks it for another agent's
    persistent conversation. An earlier version of the statement read
    the INSTANCE's schema, so it asked the default agent's turns table
    for the other agent's conversation id -- and found nothing. The
    chat rendered empty, the database was never in doubt (197 turns),
    and nothing errored.

    The previous test could not have caught it: it asked the instance's
    own agent, so instance-schema and requested-schema were the same
    schema and the statement was always right. This one asks for a
    DIFFERENT agent than the instance's.
    """
    conv_id = "66666666-6666-6666-6666-666666666666"
    _seed_other(db, conv_id, "the other agent's answer")
    mem = MemoryAPI(pool=_FakePool(db), embedding_client=None,
                    agent_name=SCHEMA, config=None)
    turns = mem.list_turns_for_persistent(SCHEMA_OTHER, user_id=USER_ID)
    assert [t["content"] for t in turns] == ["the other agent's answer"], (
        "the turns of the agent asked for must come back; the statement "
        "reads the schema of the agent asked for, not the schema of the "
        "MemoryAPI instance"
    )


def test_the_agent_name_is_canonicalised_and_checked():
    """The schema name travels into SQL, so it is checked once."""
    mem = MemoryAPI(pool=None, embedding_client=None,
                    agent_name=SCHEMA, config=None)
    with pytest.raises(ValueError):
        mem.list_turns_for_persistent("bad name; drop table", user_id=None)
    with pytest.raises(ValueError):
        mem.list_turns_for_persistent("' OR 1=1--", user_id=None)


def test_a_LOGGED_OUT_caller_gets_no_turns_and_no_error(db):
    """The route has no login requirement, so user_id can be None.

    Postgres cannot infer the type of a NULL bind parameter, so the
    lookup raised "could not determine data type of parameter $2" --
    a 500 on the chat page for anyone who is not logged in. The
    parameter is typed, and a caller with no user gets no turns.
    """
    conv_id = "77777777-7777-7777-7777-777777777777"
    _seed_other(db, conv_id, "the other agent's answer")
    mem = MemoryAPI(pool=_FakePool(db), embedding_client=None,
                    agent_name=SCHEMA, config=None)
    assert mem.list_turns_for_persistent(SCHEMA_OTHER, user_id=None) == []
