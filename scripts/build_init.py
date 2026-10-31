#!/usr/bin/env python3
"""Build the harness's single init: schema/init.sql.

The harness carried 15 numbered migrations plus 3 per-agent schema files --
the accreted history of a project that was never published. A published
product should ship the SCHEMA it has, not the path it took to it.

So the init is GENERATED from the live database (the authoritative target
state), then made idempotent and templated on the agent's schema name.

It creates:
  * the extensions,
  * the shared public tables the harness owns,
  * the agent's schema with its tables, indexes and constraints.

Two properties it must have:
  1. IDEMPOTENT -- a second run changes nothing, so an operator can re-run
     it against a live database without destroying data.
  2. SELF-CONTAINED -- a fresh database gets the whole thing from this one
     file.

THE psql VARIABLE PROBLEM
  psql substitutes a variable in ordinary SQL, but NOT inside a
  dollar-quoted string, and NOT inside a quoted literal. So:
    * the schema-qualified table names use the :"agent_name" form, which
      psql substitutes correctly;
    * a name that must appear INSIDE a string -- a regclass literal, or a
      name used by a DO block -- is built at run time from a session
      setting: set_config('harness.agent_name', ...) is written once at the
      top, and the DO blocks read current_setting('harness.agent_name')
      and build their names with format().
"""
import os
import re
import sys

PUBLIC_DUMP = os.environ.get("PUBLIC_DUMP", "/tmp/public_dump.sql")
AGENT_DUMP = os.environ.get("AGENT_DUMP", "/tmp/agent_dump.sql")
OUT = os.environ.get("OUT", "/tmp/harness_audit/schema/init.sql")
# The schema the dump was taken from. The generator rewrites it to the
# agent's name, so it only matters when re-generating from a dump.
_SRC_RAW = os.environ.get("HARNESS_SOURCE_SCHEMA", "peribot")
_SRC = re.escape(_SRC_RAW)

# The live public schema belongs to SEVERAL projects (the harness, the
# website's contact form, the starter-pack orders). These are the harness's.
HARNESS_PUBLIC_TABLES = {
    "users", "app_settings", "entity_icons",
    "groups", "group_members", "group_agents", "group_messages",
}


# ------------------------------------------------------------- the parsing

def split_statements(text):
    """Split a pg_dump into complete statements.

    A statement may span many lines (a CREATE TABLE, an ALTER TABLE with a
    constraint). So: accumulate lines until the accumulated text ends with
    ';', and do NOT split inside a dollar-quoted body.
    """
    out, buf, in_dollar = [], [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("--") or s.startswith("SET ") or \
           s.startswith("SELECT pg_catalog") or s.startswith("\\restrict") or \
           s.startswith("\\unrestrict") or not s:
            continue
        buf.append(line)
        if len(re.findall(r"\$[a-zA-Z_]*\$", line)) % 2 == 1:
            in_dollar = not in_dollar
        if not in_dollar and s.endswith(";"):
            out.append("\n".join(buf).strip())
            buf = []
    if buf:
        out.append("\n".join(buf).strip())
    return out


def statement_kind(stmt):
    """The leading keyword of a statement."""
    m = re.match(r"([A-Z]+(?:\s+[A-Z]+)?)", stmt)
    kind = m.group(1) if m else ""
    if kind == "CREATE UNIQUE" and stmt.startswith("CREATE UNIQUE INDEX"):
        kind = "CREATE UNIQUE INDEX"
    return kind


def table_of(stmt):
    """The table a statement belongs to, if any."""
    for pat in (r"^CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?\"?([a-z_]+)\"?",
                r"^ALTER TABLE (?:ONLY )?(?:public\.)?\"?([a-z_]+)\"?",
                r"^COMMENT ON TABLE (?:public\.)?\"?([a-z_]+)\"?",
                # an index belongs to the TABLE it is on, not to itself
                r"^CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?(?:public\.)?\"?\w+\"? ON (?:public\.)?\"?([a-z_]+)\"?",
                r"^CREATE SEQUENCE (?:IF NOT EXISTS )?(?:public\.)?\"?([a-z_]+?)_id_seq\"?",
                r"^ALTER SEQUENCE (?:public\.)?\"?([a-z_]+?)_id_seq\"? OWNED BY"):
        m = re.match(pat, stmt)
        if m:
            return m.group(1)
    return None


# ------------------------------------------------------ the idempotency

def guard_public(table, constraint, body):
    """A conditional ADD CONSTRAINT on a public table.

    The names are interpolated with format()'s %L, so they are QUOTED as
    SQL literals -- a table name can never become SQL.
    """
    template = (
        "DO $guard$\n"
        "BEGIN\n"
        "    IF NOT EXISTS (SELECT 1 FROM pg_constraint\n"
        "        WHERE conname = %L AND conrelid =\n"
        "              %L::regclass) THEN\n"
        "        {body}\n"
        "    END IF;\n"
        "END\n"
        "$guard$;"
    )
    # the body is substituted with .replace(), not with %, because it may
    # itself contain a % (a format() call, for instance)
    return (template
            .replace("{body}", body)
            .replace("%L", "'" + constraint.replace("'", "''") + "'", 1)
            .replace("%L", "'" + table.replace("'", "''") + "'", 1))


def guard_agent(table, constraint, body):
    """A conditional ADD CONSTRAINT on an agent table.

    The table is addressed through the session setting, because psql does
    not substitute a variable inside a dollar-quoted string.
    """
    # The body becomes the TEMPLATE of the EXECUTE's format() call, so
    # its own %I placeholders are filled with the agent's schema name at
    # run time. It is therefore spliced in as-is (not quoted) -- but it is
    # our own generated DDL, never request data.
    #
    # The body may have ONE %I (the table itself) or TWO (a foreign key
    # also names the table it references), so the number of arguments
    # follows the number of placeholders.
    n_placeholders = body.count("%I")
    args = ", ".join(["t"] * n_placeholders)
    template = (
        "DO $guard$\n"
        "DECLARE\n"
        "    t text := current_setting('harness.agent_name');\n"
        "BEGIN\n"
        "    IF NOT EXISTS (SELECT 1 FROM pg_constraint\n"
        "        WHERE conname = %L AND conrelid =\n"
        "              format('%I.%I', t, %L)::regclass) THEN\n"
        "        EXECUTE format(%L" + (", " + args if args else "") + ");\n"
        "    END IF;\n"
        "END\n"
        "$guard$;"
    )
    return (template
            .replace("%L", "'" + constraint.replace("'", "''") + "'", 1)
            .replace("%L", "'" + table.replace("'", "''") + "'", 1)
            .replace("%L", "'" + body.replace("'", "''") + "'", 1))


# The types and operator classes that belong to an EXTENSION. The dump
# renders them qualified by the extension's real schema (public.halfvec,
# public.gin_trgm_ops, ...), which is only correct on the machine the
# dump came from. The generated file puts the extension's namespace on
# the search_path instead, so these are emitted UNQUALIFIED.
EXTENSION_OBJECTS = (
    "halfvec", "vector", "hnsw",
    "gin_trgm_ops", "gist_trgm_ops",
    "halfvec_cosine_ops", "halfvec_l2_ops", "halfvec_ip_ops",
    "vector_cosine_ops", "vector_l2_ops", "vector_ip_ops",
    "citext",
)


def unqualify_extension_objects(stmt):
    """Drop the schema qualifier from an extension-owned type/opclass."""
    for name in EXTENSION_OBJECTS:
        stmt = re.sub(r"\bpublic\.%s\b" % name, name, stmt)
    return stmt


def make_idempotent(stmt, agent=False):
    """Return an idempotent form of a statement, or None to drop it.

    When `agent` is set, the statement is still in its SOURCE form (it
    refers to the literal source schema), so the table reference can be
    read. The result is emitted in the templated form.
    """
    kind = statement_kind(stmt)

    if kind.startswith("CREATE TABLE"):
        if "IF NOT EXISTS" not in stmt:
            stmt = stmt.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
        return stmt

    if kind.startswith("CREATE SEQUENCE"):
        if "IF NOT EXISTS" not in stmt:
            stmt = stmt.replace("CREATE SEQUENCE ",
                                "CREATE SEQUENCE IF NOT EXISTS ", 1)
        return stmt

    if kind.startswith("CREATE UNIQUE INDEX"):
        if "IF NOT EXISTS" not in stmt:
            stmt = stmt.replace("CREATE UNIQUE INDEX ",
                                "CREATE UNIQUE INDEX IF NOT EXISTS ", 1)
        return stmt

    if kind.startswith("CREATE INDEX"):
        if "IF NOT EXISTS" not in stmt:
            stmt = stmt.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
        return stmt

    if kind.startswith("CREATE TYPE"):
        m = re.match(r"CREATE TYPE (public\.\w+) AS ENUM", stmt)
        if not m:
            return None
        typename = m.group(1)
        schema, name = typename.split(".")
        template = (
            "DO $guard$\n"
            "BEGIN\n"
            "    IF NOT EXISTS (SELECT 1 FROM pg_type t\n"
            "        JOIN pg_namespace n ON n.oid = t.typnamespace\n"
            "        WHERE t.typname = %L AND n.nspname = %L) THEN\n"
            "        {stmt}\n"
            "    END IF;\n"
            "END\n"
            "$guard$;"
        )
        return (template
                .replace("{stmt}", stmt)
                .replace("%L", "'" + name.replace("'", "''") + "'", 1)
                .replace("%L", "'" + schema.replace("'", "''") + "'", 1))

    if kind.startswith("ALTER SEQUENCE"):
        return stmt

    if kind.startswith("ALTER TABLE"):
        # ADD CONSTRAINT -- guard it. The statement may span two lines
        # (pg_dump puts the constraint on its own line).
        m = re.match(
            r"ALTER TABLE (?:ONLY )?(\S+)\s+ADD CONSTRAINT (\w+)",
            stmt, re.S)
        if m:
            table, constraint = m.group(1), m.group(2)
            body = re.sub(
                r"^ALTER TABLE (?:ONLY )?\S+\s+ADD CONSTRAINT \w+ ",
                "", stmt, count=1)
            if agent:
                # the agent's tables -- the names are built at run time
                # from the session setting, because psql will not
                # interpolate a variable inside a dollar-quoted string
                agent_table = table.split(".")[-1]
                m2 = re.search(rf"REFERENCES (?:{_SRC}\.)?(\w+)", body)
                if m2:
                    body = re.sub(rf"REFERENCES (?:{_SRC}\.)?\w+",
                                  "REFERENCES %I." + m2.group(1), body)
                # The body becomes the format() TEMPLATE of the EXECUTE,
                # so its %I placeholders take the agent's schema name. The
                # template is the whole statement.
                return guard_agent(
                    agent_table, constraint,
                    f"ALTER TABLE %I.{agent_table} ADD CONSTRAINT "
                    f"{constraint} {body}")
            # a public table
            return guard_public(table, constraint,
                                f"ALTER TABLE {table} ADD CONSTRAINT "
                                f"{constraint} {body}")

        # SET DEFAULT -- repeatable, but the regclass literal is inside a
        # quoted string, where psql will not substitute a variable. It is
        # built at run time from the session setting.
        m = re.match(
            rf"^ALTER TABLE ONLY {_SRC}\.(\w+) ALTER COLUMN (\w+) "
            r"SET DEFAULT nextval\('{_SRC}\.(\w+_id_seq)'::regclass\);$",
            stmt)
        if m:
            table, column, seq = m.groups()
            return (
                "DO $default$\n"
                "DECLARE\n"
                "    t text := current_setting('harness.agent_name');\n"
                "BEGIN\n"
                "    EXECUTE format(\n"
                "        'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT "
                "nextval(%L::regclass)',\n"
                f"        t, '{table}', '{column}',\n"
                f"        t || '.{seq}');\n"
                "END\n"
                "$default$;"
            )
        return stmt

    if kind.startswith("COMMENT"):
        return stmt

    return stmt


# ---------------------------------------------------------------- the build

def order(stmts):
    """Order the statements so they can be applied.

    pg_dump emits them in a readable order, not an appliable one: the
    ALTER COLUMN ... SET DEFAULT nextval('..._id_seq') comes BEFORE the
    CREATE SEQUENCE it names. The order that works:

      1. CREATE TYPE
      2. CREATE TABLE          (a table before anything references it)
      3. CREATE SEQUENCE       (the sequences the defaults need)
      4. ALTER SEQUENCE OWNED BY
      5. ALTER COLUMN SET DEFAULT
      6. CREATE INDEX
      7. ADD CONSTRAINT        (the FKs reference other tables)
      8. COMMENT ON
    """
    def rank(stmt):
        k = statement_kind(stmt)
        if k.startswith("CREATE TYPE"):
            return 1
        if k.startswith("CREATE TABLE"):
            return 2
        if k.startswith("CREATE SEQUENCE"):
            return 3
        if k.startswith("ALTER SEQUENCE"):
            return 4
        if "SET DEFAULT" in stmt:
            return 5
        if k.startswith("CREATE UNIQUE INDEX") or k.startswith("CREATE INDEX"):
            return 6
        if "ADD CONSTRAINT" in stmt:
            return 7
        if k.startswith("COMMENT"):
            return 8
        return 9

    return sorted(stmts, key=rank)


HEADER = """-- The Peristyle Harness -- the complete schema, in one file.
--
-- Generated from the live database (the authoritative target state) by
-- scripts/build_init.py. It replaces the numbered migrations and the
-- per-agent schema files this project accreted while it was private: a
-- published product ships the schema it HAS, not the path it took to it.
--
-- WHAT IT CREATES
--   * the extensions: pgcrypto, pg_trgm, vector, citext
--   * the shared public tables the harness owns: users, app_settings,
--     entity_icons, groups, group_members, group_agents, group_messages
--   * the agent's schema (templated on :agent_name) with conversations,
--     memories, memory_audit, memory_chunks and turns, their indexes and
--     their constraints
--
-- HOW TO RUN IT
--   psql -d <db> -v agent_name=<agent> -f schema/init.sql
--
-- It is IDEMPOTENT: every object is created only if it is missing, and the
-- statements that would otherwise fail on a second run are guarded. Re-
-- running it against a live database changes nothing.
--
-- THE psql VARIABLE
-- psql substitutes a variable in ordinary SQL, but NOT inside a
-- dollar-quoted string and NOT inside a quoted literal. So the
-- schema-qualified table names use the :"agent_name" form, and the names
-- that must appear inside a string -- a regclass literal, a name used by a
-- DO block -- are built at run time from a session setting that this file
-- sets once, at the top.

\\set ON_ERROR_STOP on

BEGIN;

-- The agent's schema name, as a session setting, so the DO blocks below
-- can build qualified names (psql will not interpolate inside them).
SELECT set_config('harness.agent_name', :'agent_name', false);

-- ---------------------------------------------------------------- extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "citext";

-- The extensions do not always live in `public`. On a host where another
-- project installed them first, their types and operator classes are in
-- THAT project's schema -- pgvector's halfvec, pg_trgm's gin_trgm_ops.
--
-- The dump rendered them qualified by their real schema, so the file
-- below says e.g. public.halfvec(1024). That is right on the machine the
-- dump came from and WRONG everywhere else, so the schema is resolved at
-- run time instead: the extension's own namespace is put on the
-- search_path, and the references are unqualified.
--
-- (This is the same thing migrations/007_add_memories_table.sql did,
-- and the reason it carried the comment about it.)
SELECT format('SET search_path TO public, %s',
              (SELECT string_agg(DISTINCT s.nspname, ', ')
                 FROM (SELECT 'public' AS nspname
                       UNION
                       SELECT n2.nspname
                         FROM pg_extension e2
                         JOIN pg_namespace n2 ON n2.oid = e2.extnamespace
                        WHERE e2.extname IN ('vector', 'pg_trgm', 'citext')) s))
\gexec
"""


def build():
    out = [HEADER]

    out.append("""
-- ============================================================================
-- The shared public schema
--
-- The agents' conversations reference public.users, and the harness's login
-- IS the users table -- so it is shared, not per-agent. It lives in public
-- because another project (the accounts service) may own it too; every
-- statement here is conditional, so it is safe to run either way.
-- ============================================================================
""")

    pub = [s for s in split_statements(open(PUBLIC_DUMP, encoding="utf-8").read())
           if (table_of(s) in HARNESS_PUBLIC_TABLES)
           or s.startswith("CREATE TYPE")]

    for s in order(pub):
        st = make_idempotent(s)
        if st:
            out.append(unqualify_extension_objects(st) + "\n")

    out.append("""
-- ============================================================================
-- The agent's schema
--
-- One schema per agent: the isolation boundary. The agent's tables are
-- addressed through :"agent_name".<table>.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS :"agent_name";
""")

    agent = [s for s in split_statements(
        open(AGENT_DUMP, encoding="utf-8").read())
        if not s.startswith("CREATE SCHEMA ")]

    for s in order(agent):
        # make the statement idempotent FIRST, on the source form -- the
        # guard blocks and the run-time name builders have to read the
        # literal source schema to know what they are guarding.
        st = make_idempotent(s, agent=True)
        if st is None:
            continue
        st = unqualify_extension_objects(st)

        # The INDEX NAMES that carry the agent name. psql's :"agent_name"
        # form quotes the WHOLE identifier, so it cannot be concatenated
        # with a suffix the way the schema-qualified names can. These are
        # emitted as a \\gexec pair: a query that builds the statement
        # with format(), and \\gexec to run its output. This is detected
        # on the SOURCE form, before the templating.
        m = re.match(
            rf"^CREATE (UNIQUE )?INDEX (?:IF NOT EXISTS )?({_SRC}_\w+) "
            rf"ON {_SRC}\.(\w+) (.*);$",
            st)
        if m:
            unique, index_name, table, rest = m.groups()
            # the live index names are "<agent>_memories_created_at_idx" --
            # the agent name, an underscore, then the suffix
            suffix = index_name[len(_SRC_RAW):]
            # The index body may contain a single quote -- WITH (m='16')
            # -- which would TERMINATE the format string. So the body is
            # passed as a %s ARGUMENT (not %L, which would quote it) and
            # the quote is doubled for the surrounding SQL literal.
            st = (
                "SELECT format(\n"
                f"    'CREATE {unique or ''}INDEX IF NOT EXISTS %I ON %I.{table} %s',\n"
                "    :'agent_name' || '" + suffix + "',\n"
                "    :'agent_name',\n"
                "    '" + rest.replace("'", "''") + "')\n"
                "\\gexec"
            )
            out.append(st + "\n")
            continue

        # now template the statements that psql can interpolate
        st = re.sub(rf"\b{_SRC}\.", ':"agent_name".', st)
        st = st.replace(f"DEFAULT '{_SRC}'::text", "DEFAULT :'agent_name'::text")
        # the sequence names
        st = re.sub(rf"\b{_SRC}\.(\w+_id_seq)\b", ':"agent_name".\1', st)
        out.append(st + "\n")

    out.append("\nCOMMIT;\n")

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


if __name__ == "__main__":
    t = build()
    print(f"  wrote {OUT}  ({len(t)} bytes, {t.count(chr(10))} lines)")
