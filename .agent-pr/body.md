## What

The de-identification sweep in #22 was driven by a scan that was
**truncated at 30 lines**. Every hit past that point was missed — and the
misses were not all in tests.

Six files still carried one install's agent roster, and one of them was
shipped tooling:

| Where | What |
|---|---|
| `scripts/build_init.py` | three regexes hardcoded one install's agent name, while the rest of the file parameterised it |
| `tests/test_install_config.py` | the roster, as a literal tuple |
| `tests/test_memory_delete.py`, `tests/test_memory_recent_rework.py` | the roster, in an `agent_name=` position |
| `tests/test_reverie.py`, `tests/test_loop_narrate_guard.py`, `tests/test_steering_and_thought_stream.py` | the roster, in comments and in call arguments |

## Why the build_init.py one is not just a leak

`build_init.py` turns a `pg_dump` of one install's agent schema into the
TEMPLATED `schema/init.sql` every install ships: the source schema is
replaced by `:"agent_name"`, and the index names — which carry the agent's
name as a prefix — are rewritten into a `\gexec` pair that builds them at
run time.

Three of those rewrites used a hardcoded name while the rest of the file
used `_SRC`, the source schema read from `HARNESS_SOURCE_SCHEMA`. **Nothing
in the repository sets that variable** — not the script, not the tests, not
a wrapper. So for every invocation that did not happen to set it, those
three rewrites matched nothing, and the generated SQL kept the source
schema's own name in it. The generator silently produced a file for one
schema only.

They now follow the parameter, and the parameterisation is pinned by a test
that drives the generator with a source schema that is neither the
template's agent nor the hardcoded name.

## The guard, extended

`tests/test_no_install_identifiers.py` gains a rule for the roster: a name
in an **agent-name position** must be the template's agent (read from
`config.yaml.example`) or an obvious placeholder. The positions are the
schema-qualified form (`<agent>.turns`), the three forms the code passes a
name in (`agent_name=`, `"agent":`, `HARNESS_AGENT`), and an agent name as
the prefix of a generated identifier.

The rule names no agent: it reads the template's roster out of the config
and compares. Against the tree before this change it reports six of the
seven leaks, including the one in the shipped tooling.

## The lesson, written down

A scan with `| head -30` in it is a scan that stops where the output stops.
The first sweep trusted one and shipped a partial result. The complete scan
now returns zero hits across the whole tree, with no truncation.

The version is bumped to 0.1.13: a merge into `main` is a release.
