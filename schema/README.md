# The harness's schema

**One file: `schema/init.sql`.**

```bash
psql -d <db> -v agent_name=<agent> -f schema/init.sql
```

It creates the whole schema — the shared public tables (the users table,
the groups, the app settings) and the agent's schema (conversations,
memories, memory_audit, memory_chunks, turns) with their indexes and
constraints.

It is **idempotent**: every object is created only if it is missing, and
the statements that would otherwise fail on a second run are guarded.
Re-running it against a live database changes nothing.

Run it once per agent:

```bash
bash tools/migrate.sh wisp evie clawbie
```

## Why there is no migrations/ directory

This project was private for its whole life, and it carried its history
with it: fifteen numbered migrations, a separate schema file per agent,
and a `migrate.sh` that had to apply them in an order that only ever
worked on the machine where they were written.

A published product should ship the schema it **has**, not the path it
took to get there. So the migrations were replaced by a single init
generated from the live database — the authoritative target state — and
verified against it.

## Regenerating it

`scripts/build_init.py` generates `schema/init.sql` from a running
database:

```bash
# the public schema (needs postgres -- the runtime role cannot read it all)
sudo -u postgres pg_dump --schema-only --no-owner --no-privileges \
     -d <db> -n public > /tmp/public_dump.sql

# one agent's schema (the three are identical modulo the schema name)
pg_dump --schema-only --no-owner --no-privileges \
     -d <db> -n wisp > /tmp/agent_dump.sql

PUBLIC_DUMP=/tmp/public_dump.sql AGENT_DUMP=/tmp/agent_dump.sql \
     python3 scripts/build_init.py
```

Edit the live database, regenerate, and commit the result. Do not hand-edit
`schema/init.sql` — it will be overwritten.

## Verifying it

`scripts/compare_schema.py` compares a database built by the init against
the live one, structurally (columns, constraints, indexes, foreign keys):

```bash
sudo -u postgres psql -c "CREATE DATABASE harness_init_test"
for agent in wisp evie clawbie; do
  sudo -u postgres psql -d harness_init_test -v agent_name=$agent \
       -f schema/init.sql
done
python3 scripts/compare_schema.py
```

It must print `IDENTICAL`.

## A note on the psql variable

psql substitutes a variable in ordinary SQL, but **not** inside a
dollar-quoted string and **not** inside a quoted literal. So:

- the schema-qualified table names use the `:"agent_name"` form, which psql
  substitutes correctly;
- a name that has to appear inside a string — a regclass literal, or a name
  used by a `DO` block — is built at run time from a session setting that
  the init sets once at the top:

```sql
SELECT set_config('harness.agent_name', :'agent_name', false);
```

```sql
DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    ...
    EXECUTE format('ALTER TABLE %I.%I ...', t, 'memories');
END
$guard$;
```

That is why the init has guard blocks and `format()` calls in it rather
than plain DDL.
