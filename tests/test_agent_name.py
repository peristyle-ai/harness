"""Unit tests for the agent name helpers (2026-10-31).

The bug these cover: an install whose agent name was not lower case
(app.agents: [EXAMPLE]) booted fine and worked in 1:1 chat -- Postgres
folds an unquoted identifier -- but every group-chat mention failed with

    could not open the conversation for EXAMPLE: invalid agent name: 'EXAMPLE'

because the group path validated the name against a lower-case-only
pattern and interpolated it as the schema name.

The rule under test: the SCHEMA form is lower case; the DISPLAY form is
kept as it is, because the conversations.agent_name column is filtered
with an exact match.

No DB, no app stack -- same style as tests/test_groups.py.
"""
import sys
import unittest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.group_chat import parse_mentions
from app.agent.names import canonical_agent_name
from app.models.group import GroupStore


class TestCanonicalAgentName(unittest.TestCase):
    def test_lowercase_name_is_unchanged(self):
        self.assertEqual(canonical_agent_name("example"), "example")
        self.assertEqual(canonical_agent_name("wisp"), "wisp")

    def test_display_name_folds_to_the_schema_form(self):
        # The case that broke the group chat.
        self.assertEqual(canonical_agent_name("EXAMPLE"), "example")
        self.assertEqual(canonical_agent_name("Wisp"), "wisp")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(canonical_agent_name("  EXAMPLE  "), "example")

    def test_invalid_names_are_refused(self):
        for bad in (
            "",
            None,
            "1example",       # must start with a letter
            "exa-mple",       # no dashes
            "exa mple",       # no spaces
            "example;drop",   # no statement separator
            "example\";--",   # no quotes
            "' or 1=1",      # no quotes
            "example; drop table turns",  # no statement separator
            "a" * 41,        # too long
        ):
            with self.assertRaises(ValueError, msg=f"{bad!r} should be refused"):
                canonical_agent_name(bad)


class TestMentionToSchema(unittest.TestCase):
    """The mention resolves to the DISPLAY name; the schema is the fold."""

    ROSTER = ["EXAMPLE"]

    def test_mention_of_an_upper_case_agent_is_matched(self):
        matched, unknown = parse_mentions("@EXAMPLE please do the thing", self.ROSTER)
        self.assertEqual(matched, ["EXAMPLE"])
        self.assertEqual(unknown, [])

    def test_a_lower_case_mention_of_the_same_agent_is_matched(self):
        # parse_mentions is case-insensitive, so this resolves to the
        # roster's own (display) spelling, not to a second agent.
        matched, unknown = parse_mentions("@example please do the thing", self.ROSTER)
        self.assertEqual(matched, ["EXAMPLE"])
        self.assertEqual(unknown, [])

    def test_the_schema_of_the_matched_agent_is_the_fold(self):
        matched, _ = parse_mentions("@EXAMPLE hello", self.ROSTER)
        self.assertEqual(canonical_agent_name(matched[0]), "example")


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, params))

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._sink)

    def commit(self):
        pass


class _FakePool:
    def __init__(self):
        self.calls = []

    def connection(self):
        return _FakeConn(self.calls)


class TestEnsureAgentConversationUsesTheSchemaForm(unittest.TestCase):
    """The INSERT must name the lower-case schema and keep the display
    name in the agent_name COLUMN."""

    def _run(self, agent_name):
        pool = _FakePool()
        store = GroupStore(pool)
        store.ensure_agent_conversation(agent_name, "conv-123", "GROUP CHAT")
        return pool.calls

    def test_upper_case_agent_inserts_into_the_lower_case_schema(self):
        calls = self._run("EXAMPLE")
        self.assertTrue(calls, "no SQL was executed")
        sql, params = calls[0]
        self.assertIn("example.conversations", sql)
        self.assertNotIn("EXAMPLE.conversations", sql)
        # the column keeps the display form
        self.assertIn("EXAMPLE", params)

    def test_lower_case_agent_is_unchanged(self):
        calls = self._run("example")
        sql, params = calls[0]
        self.assertIn("example.conversations", sql)
        self.assertIn("example", params)

    def test_an_injection_attempt_is_refused_before_any_sql(self):
        pool = _FakePool()
        store = GroupStore(pool)
        with self.assertRaises(ValueError):
            store.ensure_agent_conversation(
                "example; drop table turns", "conv-123", "GROUP CHAT"
            )
        self.assertEqual(pool.calls, [], "no SQL may run for an invalid name")


class TestReverieActivityCountUsesTheSchemaForm(unittest.TestCase):
    """The reverie path interpolates the agent name into <agent>.turns too.

    It carried the same lower-case-only guard, so an install with an
    upper-case display name would have had its nightly turn killed with
    ValueError the moment reverie.enabled was turned on -- and nothing
    in the suite covered it.
    """

    def _run(self, agent_name):
        from app.agent.reverie import activity_count

        class _Cur:
            def __init__(self):
                self.sql = ""
                self.args = ()

            def execute(self, sql, args=()):
                self.sql = sql
                self.args = args

            def fetchone(self):
                return (7,)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        cur = _Cur()

        class _Conn:
            def cursor(self):
                return cur

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Pool:
            def connection(self):
                return _Conn()

        count = activity_count(_Pool(), agent_name, 24)
        return count, cur.sql

    def test_an_upper_case_agent_reads_the_folded_schema(self):
        count, sql = self._run("EXAMPLE")
        self.assertEqual(count, 7)
        self.assertIn("FROM example.turns", sql)
        self.assertNotIn("EXAMPLE.turns", sql)

    def test_a_lower_case_agent_is_unchanged(self):
        count, sql = self._run("example")
        self.assertEqual(count, 7)
        self.assertIn("FROM example.turns", sql)

    def test_an_injection_attempt_is_still_refused(self):
        from app.agent.reverie import activity_count

        class _Pool:
            def connection(self):
                raise AssertionError("no SQL may run for an invalid name")

        with self.assertRaises(ValueError):
            activity_count(_Pool(), "example; drop table turns", 24)



if __name__ == "__main__":
    unittest.main()
