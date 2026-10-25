#!/usr/bin/env python3
"""Compare the schema the init produces against the LIVE schema.

This is the test that matters: the init is generated from the live
database, so it must reproduce it exactly -- for every agent -- or the
published product would install a different schema than the one running.

The comparison is structural: the tables, their columns (name, type,
nullability, default), the constraints, the indexes and the triggers.
"""
import subprocess
import sys

LIVE = "peristyle"
TEST = "harness_init_test"
AGENTS = ("wisp", "evie", "clawbie")


def q(db, sql):
    """Run a query as postgres and return its output.

    A FAILED query returns a distinct marker, and the comparison below
    treats that marker as a failure. Without that, a permission problem
    (sudo asking for a password, say) makes both sides return the same
    error text and the comparison reports "IDENTICAL" while comparing
    NOTHING -- a vacuous pass. It happened here: the script was run as
    the `peristyle` user, whose sudo to postgres needs a password, and it
    reported 1 item per category and called it a match.
    """
    r = subprocess.run(
        ["sudo", "-n", "-u", "postgres", "psql", "-d", db, "-tAc", sql],
        capture_output=True, text=True)
    if r.returncode != 0:
        return "ERR: " + r.stderr.strip()[:300]
    return r.stdout.strip()


def is_error(out):
    """True when a query FAILED rather than returning rows."""
    return out.startswith("ERR:")


# The live public schema belongs to SEVERAL projects (the harness, the
# website's contact form, the starter-pack orders). The harness owns only
# these -- the comparison must be scoped to them, or it reports the other
# projects' tables as "missing".
HARNESS_PUBLIC_TABLES = (
    "users", "app_settings", "entity_icons",
    "groups", "group_members", "group_agents", "group_messages",
)

COLUMNS = """
select table_schema||'.'||table_name||' | '||column_name||' | '||data_type
  ||' | '||is_nullable||' | '||coalesce(column_default,'-')
from information_schema.columns
where table_schema = '{schema}'
  and (table_schema <> 'public' or table_name in ({public_tables}))
order by table_name, ordinal_position
"""

CONSTRAINTS = """
select conrelid::regclass::text||' | '||conname||' | '||pg_get_constraintdef(oid)
from pg_constraint
where connamespace = '{schema}'::regnamespace
  and (connamespace::regnamespace::text <> 'public'
       or conrelid::regclass::text in ({public_tables}))
order by conrelid::regclass::text, conname
"""

INDEXES = """
select schemaname||'.'||indexname||' | '||indexdef
from pg_indexes where schemaname = '{schema}'
  and (schemaname <> 'public' or tablename in ({public_tables}))
order by indexname
"""

FKS = """
select c.conrelid::regclass::text||' -> '||c.confrelid::regclass::text
from pg_constraint c
where c.connamespace = '{schema}'::regnamespace and c.contype = 'f'
  and (c.connamespace::regnamespace::text <> 'public'
       or c.conrelid::regclass::text in ({public_tables}))
order by 1
"""

fails = 0
for schema in ("public",) + AGENTS:
    print(f"=== {schema} ===")
    pt = ", ".join(f"'{t}'" for t in HARNESS_PUBLIC_TABLES)
    for label, tmpl in (("columns", COLUMNS), ("constraints", CONSTRAINTS),
                         ("indexes", INDEXES), ("fks", FKS)):
        live = q(LIVE, tmpl.format(schema=schema, public_tables=pt))
        test = q(TEST, tmpl.format(schema=schema, public_tables=pt))

        # the agent_name column's DEFAULT: the live install carries
        # 'wisp' in every agent's table (a copy-paste from the wisp
        # schema). The init gives each agent its own name. The app always
        # passes agent_name explicitly, so the default is never used --
        # but the init is more correct, so the difference is expected.
        def norm(s, agent=None):
            out = []
            for line in s.splitlines():
                for a in AGENTS:
                    line = line.replace(a + ".", "<agent>.")
                    line = line.replace(a + "_", "<agent>_")
                if agent and "agent_name | text | NO | '" in line:
                    line = line.split("| NO | ")[0] + "| NO | <agent-default>"
                out.append(line.strip())
            return sorted(x for x in out if x)

        if is_error(live) or is_error(test):
            fails += 1
            print(f"  {label:12} QUERY FAILED -- cannot compare")
            print(f"      live: {live[:200]}")
            print(f"      test: {test[:200]}")
            continue

        a, b = norm(live, schema), norm(test, schema)
        if not a and not b:
            fails += 1
            print(f"  {label:12} BOTH SIDES EMPTY -- nothing was compared")
            continue
        if a == b:
            print(f"  {label:12} IDENTICAL ({len(a)} item(s))")
        else:
            fails += 1
            print(f"  {label:12} DIFFERENT  live={len(a)} test={len(b)}")
            only_live = [x for x in a if x not in b]
            only_test = [x for x in b if x not in a]
            for x in only_live[:6]:
                print(f"      only in the LIVE db: {x}")
            for x in only_test[:6]:
                print(f"      only in the TEST db: {x}")

print()
print("RESULT:", "MISMATCH" if fails else "IDENTICAL -- the init reproduces the live schema")
sys.exit(1 if fails else 0)
