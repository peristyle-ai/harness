"""Unit tests for the agent's three names (2026-11-01).

There are THREE forms of an agent's name, and they are not the same
thing:

    key          'example'   the identifier -- the schema, the config's
                             app.agents, the API path, the agent_name
                             column, the group's roster row.
    display name 'EXAMPLE'   the label -- the chat header, the entity
                             list, the group bubble. The '**Name:**'
                             line in the agent's IDENTITY.md.
    schema form  'example'   the key, folded (Postgres folds an
                             unquoted identifier to lower case).

The bug this covers: an install whose agent was renamed from a
capitalised key to a lower-case one left its bookmarks, its group
roster and its HMI pointing at the old form. The URL carried
'EXAMPLE', the API only knew 'example', and the chat window rendered
EMPTY -- with the data intact and the service healthy. A count query
proves storage, never a screen.

No DB, no app stack -- same style as tests/test_agent_name.py.
"""
import sys
import unittest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.names import (
    canonical_agent_name,
    display_name_from_identity,
    resolve_agent_key,
)


class TestDisplayNameFromIdentity(unittest.TestCase):
    """The '**Name:**' line in an IDENTITY.md is the agent's label."""

    def test_the_name_field_is_read(self):
        content = "# IDENTITY.md - Who Am I?\n\n- **Name:** EXAMPLE\n"
        self.assertEqual(display_name_from_identity(content), "EXAMPLE")

    def test_the_first_line_of_the_file_works_too(self):
        content = "- **Name:** EXAMPLE\n\nI'm **EXAMPLE** -- the operator.\n"
        self.assertEqual(display_name_from_identity(content), "EXAMPLE")

    def test_a_name_that_is_not_the_field_is_not_a_name(self):
        # The prose mentions the name in bold, but that is not the field.
        content = "# IDENTITY.md\n\nI'm **EXAMPLE** -- the operator.\n"
        self.assertIsNone(display_name_from_identity(content))

    def test_a_file_without_a_name_field_has_none(self):
        self.assertIsNone(display_name_from_identity("# IDENTITY.md\n\nJust prose.\n"))

    def test_an_empty_file_has_none(self):
        self.assertIsNone(display_name_from_identity(""))

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(
            display_name_from_identity("- **Name:**   EXAMPLE  \n"), "EXAMPLE"
        )

    def test_quotes_around_the_value_are_stripped(self):
        self.assertEqual(
            display_name_from_identity('- **Name:** "EXAMPLE"\n'), "EXAMPLE"
        )

    def test_the_field_must_be_at_the_start_of_a_line(self):
        # A mention of the field mid-sentence is prose, not the field.
        content = "Set the **Name:** field to EXAMPLE in your identity.\n"
        self.assertIsNone(display_name_from_identity(content))


class TestResolveAgentKey(unittest.TestCase):
    """A name that arrived in a URL resolves to the agent's KEY."""

    KEYS = ["example", "second"]
    DISPLAY = {"example": "EXAMPLE", "second": "Second"}

    def test_an_exact_key_resolves_to_itself(self):
        self.assertEqual(resolve_agent_key("example", self.KEYS, self.DISPLAY), "example")

    def test_the_display_name_resolves_to_the_key(self):
        self.assertEqual(resolve_agent_key("EXAMPLE", self.KEYS, self.DISPLAY), "example")

    def test_the_display_name_resolves_case_insensitively(self):
        self.assertEqual(resolve_agent_key("example", self.KEYS, self.DISPLAY), "example")
        self.assertEqual(resolve_agent_key("ExAmPlE", self.KEYS, self.DISPLAY), "example")

    def test_an_unknown_name_resolves_to_nothing(self):
        self.assertIsNone(resolve_agent_key("nobody", self.KEYS, self.DISPLAY))

    def test_an_empty_name_resolves_to_nothing(self):
        self.assertIsNone(resolve_agent_key("", self.KEYS, self.DISPLAY))

    def test_an_ambiguous_display_name_resolves_to_nothing(self):
        # Two agents claiming the same label: guessing would send the
        # conversation to the wrong one.
        keys = ["a", "b"]
        display = {"a": "Same", "b": "Same"}
        self.assertIsNone(resolve_agent_key("Same", keys, display))

    def test_a_name_with_no_display_entry_falls_back_to_the_folded_key(self):
        # An install whose identity file declares no name: the old
        # capitalised form still has to find the agent.
        self.assertEqual(
            resolve_agent_key("SECOND", ["second"], {"example": "EXAMPLE"}), "second"
        )

    def test_an_exact_key_wins_over_a_folded_match(self):
        # Deterministic: the exact key is the key, whatever else folds
        # onto it.
        self.assertEqual(resolve_agent_key("A", ["a", "A"], {}), "A")
        self.assertEqual(resolve_agent_key("a", ["a", "A"], {}), "a")


class TestTheThreeFormsAreDistinct(unittest.TestCase):
    """The schema form is the fold of the key; the label is neither."""

    def test_the_schema_form_is_the_folded_key(self):
        self.assertEqual(canonical_agent_name("example"), "example")
        self.assertEqual(canonical_agent_name("EXAMPLE"), "example")

    def test_the_label_is_never_the_schema_form(self):
        # This is the whole point: the label is what the agent calls
        # itself, and it is not lower-cased by anything.
        label = display_name_from_identity("- **Name:** EXAMPLE\n")
        self.assertEqual(label, "EXAMPLE")
        self.assertNotEqual(label, canonical_agent_name("example"))


if __name__ == "__main__":
    unittest.main()
