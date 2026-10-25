# Peristyle Harness

A multi-agent harness for language models: Postgres-backed agent memory, one
Flask chat surface, and several agents isolated by database schema instead of
by process.

An **agent** here is a loop with its own memory, its own tools, its own
identity files and its own conversation history. The harness gives each agent
a schema (`peribot`, `peribot`, `peribot`), serves them all from one process on one
port, and lets them share a single users table and a single chat UI.

```
browser ──► Flask (one process)
              ├── /c/<agent>          the chat surface (HMI + PWA)
              ├── /api/v1/...         the REST API
              ├── /g/<group>          group chats (several agents, one transcript)
              └── agent loop ──► LLM ──► tools
                                    └──► memory ──► Postgres (per-agent schema)
```

## What it does

- **Agent memory in Postgres, not markdown files.** Memories are rows. Retrieval
  is hybrid: vector similarity (pgvector) plus trigram text search, with the
  schema, indexes and constraints in the migration rather than in a convention.
- **One process, one chat surface.** No Telegram, no Discord, no Slack
  bindings. A browser chat UI, a REST API and a mobile PWA for receipt capture.
- **Multi-agent via Postgres schemas, not processes.** Adding an agent is
  `CREATE SCHEMA <name>` plus a schema seed — not a new Unix user, a new
  systemd unit and a new tunnel.
- **Group chats.** Several agents share one transcript; only the agents that
  are `@mentioned` answer, and each one sees the others' replies.
- **Mid-turn steering.** A message sent while a turn is running is queued and
  injected at the next iteration boundary, so the agent sees it inside the same
  turn rather than after it.
- **A context window that degrades gracefully.** The turn's live context is
  budgeted; when it is over, the oldest iterations are dropped *whole* and the
  agent is told, in the system prompt, that its tail was truncated — so it
  recovers the trajectory from memory and its commits.
- **Tool output is trimmed head+tail** at the LLM boundary (the command echo
  at the head, the result and the error at the tail) while the trajectory keeps
  the full output.
- **A nightly reverie** that re-reads the agent's own identity files and keeps
  them current.
- **One install file.** `install/<name>.yaml` says everything that differs
  about an install; `tools/apply_install_config.py` turns it into a config, an
  environment and an identity.

## Install

```bash
git clone https://github.com/peristyle-ai/harness.git
cd harness
pip install -r requirements.txt

cp .env.example .env          # DB, LLM, secrets — never commit it
bash tools/migrate.sh         # the schema (needs the operator role)
bash run.sh                   # serve on 127.0.0.1:5010
```

`config.yaml` is committed and entirely `${VAR}`-based; every real value
comes from the environment (see `.env.example`).

`tools/migrate.sh` applies `schema/init.sql` — one file that creates the
whole schema: the shared public tables (the users table) and the agent's
schema. It is templated on the agent's schema name, so an agent called
anything works. It is idempotent: re-running it on a database that already has
the schema is a no-op.

A full per-install setup — the agent's name, its operator, its schema and its
embedding provider — can be captured in one file and applied with:

```bash
python tools/apply_install_config.py install/<name>.yaml
```

See [`install/README.md`](./install/README.md).

### Embeddings

Point `embedding.base_url` at any OpenAI-compatible `/v1/embeddings`
endpoint, local or hosted. With a local GPU server:

```yaml
embedding:
  base_url: http://127.0.0.1:8080/v1
  api_key: ""            # the local server takes no key
```

With a hosted service, give it a key — it is sent as `Authorization: Bearer`:

```yaml
embedding:
  base_url: https://embed.example.com/v1
  api_key: ${EMBEDDING_API_KEY}
```

A machine with no GPU can still run the harness: point it at a hosted
embedding service and leave the dimensions at the service's own.

## Repo layout

```
harness/
├── app/
│   ├── agent/            the agent loop, memory, tools, the LLM adapters
│   ├── models/           the shared user + group models
│   ├── routes/           the REST API
│   ├── static/           the chat UI (HMI + PWA) and the service worker
│   ├── templates/        the server-rendered pages
│   ├── auth.py           sessions, password hashing, the API-key path
│   └── security.py       the security headers and the request filters
├── agent_template/       the default agent identity (see below)
├── schema/init.sql       the whole schema, in one file (idempotent)
├── install/              the install-config format and an example
├── tools/                apply_install_config.py, migrate.sh and the ops scripts
├── scripts/              the smoke tests and the reverie timer
├── tests/                pytest
├── config.yaml           non-secret configuration (committed)
├── .env.example          every environment variable, documented
├── run.sh                the gunicorn entrypoint
└── LICENSE               MIT
```

## The agent identity

Each agent's identity is a directory of Markdown files — `IDENTITY.md`,
`SOUL.md`, `AGENTS.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md` — read at the
start of every turn. Change one and the change lands on the next turn; no
restart.

`agent_template/` holds the **default** identity, and it is a **sysadmin**:
the operator of the machine. The first agent anyone installs is the one best
equipped to set up the tunnels, the DNS and the database, because those are
the things that need credentials and judgement. An install config only
*assigns a name* to it.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q
```

The suite needs a Postgres instance and the environment variables in
`.env.example`; the tests that need a live model or a live database skip
themselves when it is not there.

## Documentation

- [`REQUIREMENTS_AND_SPECIFICATIONS.md`](./REQUIREMENTS_AND_SPECIFICATIONS.md) —
  the design and the reasoning behind it
- [`SECURITY.md`](./SECURITY.md) — the security model, the headers and the
  reporting policy
- [`docs/`](./docs) — the plans that are still in flight

## License

MIT — see [LICENSE](./LICENSE). Copyright (c) 2026 Peristyle.
