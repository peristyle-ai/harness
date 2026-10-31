"""The schema generator must rewrite the source schema, whatever it is.

`scripts/build_init.py` turns a `pg_dump` of one install's agent schema
into the TEMPLATED `schema/init.sql` that every install ships: the
source schema is replaced by `:"agent_name"`, and the index names --
which carry the agent's name as a prefix -- are rewritten by a
`\\gexec` pair that builds them at run time.

Three of those rewrites used to hardcode one install's agent name
instead of using the parameter the rest of the file uses.  The file
reads the source schema from `HARNESS_SOURCE_SCHEMA` (default: the
template's agent), and NOTHING sets that variable -- so for every
invocation that did not happen to set it, those three rewrites silently
matched nothing and the generated SQL kept the source schema's own name
in it.

The behaviour change is pinned here: the rewrites follow the
parameter, whatever the source schema is called.

The test drives the generator through its own module-level constants
(the paths are read at import time), so it sets the environment BEFORE
importing it.
"""

import importlib.util
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "build_init.py"

# A source schema that is NOT the template's agent and NOT the name the
# bug hardcoded -- so a fix that hardcodes anything fails this test.
SOURCE_SCHEMA = "someagentschema"


def _load_build_init(tmp_path, monkeypatch):
    """Import scripts/build_init.py with the environment it reads at import."""
    public_dump = tmp_path / "public.sql"
    public_dump.write_text("")
    agent_dump = tmp_path / "agent.sql"
    agent_dump.write_text(
        f"CREATE INDEX {SOURCE_SCHEMA}_memories_created_at_idx "
        f"ON {SOURCE_SCHEMA}.memories (created_at);\n"
        f"CREATE INDEX {SOURCE_SCHEMA}_memories_id_seq "
        f"ON {SOURCE_SCHEMA}.memories (id);\n"
        f"CREATE TABLE {SOURCE_SCHEMA}.memories ("
        "id bigserial primary key, created_at timestamptz);\n"
    )
    monkeypatch.setenv("PUBLIC_DUMP", str(public_dump))
    monkeypatch.setenv("AGENT_DUMP", str(agent_dump))
    monkeypatch.setenv("OUT", str(tmp_path / "init.sql"))
    monkeypatch.setenv("HARNESS_SOURCE_SCHEMA", SOURCE_SCHEMA)

    spec = importlib.util.spec_from_file_location("build_init_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_init_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generated(tmp_path, monkeypatch):
    module = _load_build_init(tmp_path, monkeypatch)
    return module.build()


def test_the_generated_schema_carries_no_source_schema_name(generated):
    """The source schema must not survive into the generated file.

    The whole point of the generator is that the shipped schema is
    TEMPLATED: one file, every install, whatever the source schema was
    called.  A literal source schema in the output means the rewrite
    missed it.
    """
    assert SOURCE_SCHEMA not in generated, (
        f"the generated schema still contains {SOURCE_SCHEMA!r}: the "
        "rewrite did not follow the source schema. The generator must "
        "template the source schema, not hardcode a name."
    )


def test_the_index_names_are_rewritten_to_the_agent_name(generated):
    """The index names carry the agent's name, so they are built at run time.

    psql's `:"agent_name"` form quotes the WHOLE identifier, so it
    cannot be concatenated with a suffix the way a schema-qualified
    name can.  The generator emits a `\\gexec` pair instead.
    """
    assert "CREATE INDEX IF NOT EXISTS" in generated, (
        "the index whose name carries the agent's name was not rewritten "
        "into its run-time form"
    )
    assert ":'agent_name' ||" in generated, (
        "the index name must be built at run time from the agent's name, "
        "not baked into the file"
    )


def test_the_schema_is_addressed_through_the_agent_name(generated):
    """A schema-qualified reference becomes `:"agent_name".<table>`."""
    assert ':"agent_name".memories' in generated, (
        "the schema-qualified references were not templated"
    )


def test_the_generator_uses_the_parameterised_source_schema(tmp_path, monkeypatch):
    """The generator's own source-schema parameter is what it rewrites with."""
    module = _load_build_init(tmp_path, monkeypatch)
    assert module._SRC_RAW == SOURCE_SCHEMA
    assert SOURCE_SCHEMA in module._SRC, (
        "the escaped form must still match the source schema"
    )
