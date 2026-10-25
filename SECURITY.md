# Security

The security model of the Peristyle Harness: what it trusts, what it does
not, what an attacker can and cannot reach, and where the boundaries are
thin enough that you should know about them before you deploy it.

This document describes **the code as it is**. If a claim here does not
match what you read in `app/`, the code is right and this file is wrong —
please say so.

- [Reporting a vulnerability](#reporting-a-vulnerability)
- [Threat model](#threat-model)
- [The tool roster](#the-tool-roster)
- [Authentication and sessions](#authentication-and-sessions)
- [Authorisation and data isolation](#authorisation-and-data-isolation)
- [Input handling](#input-handling)
- [Rate limiting](#rate-limiting)
- [Response headers](#response-headers)
- [Secrets](#secrets)
- [Logging and audit trails](#logging-and-audit-trails)
- [Backups](#backups)
- [OWASP Top 10 mapping](#owasp-top-10-mapping)
- [Known limitations](#known-limitations)
- [Audit tests](#audit-tests)

---

## Reporting a vulnerability

**Do not open a public issue for a vulnerability.**

Email **operator@example.com**, or use GitHub's private advisory form once
the repository is public:

    https://github.com/peristyle-ai/harness/security/advisories/new

Please include:

- what an attacker can do, and what they need in order to do it;
- the affected file or route;
- a reproduction — a request, a prompt, or a set of steps;
- what you believe the impact is.

What to expect: an acknowledgement within a few days, and a fix or an
assessment of severity. If the report is accepted, the fix ships as a
commit and the advisory is published after a release has gone out.

**Please do not test against anyone else's install.** The harness is
single-tenant: every user of an install shares one Postgres database and
one set of agent schemas. If you find a way to read another user's
conversation, that is a real finding — report it, do not exercise it
further than you need to.

### Supported versions

| Version | Supported |
|---|---|
| `main` | yes |
| any tagged release | security fixes only |

There is no LTS branch. Fixes land on `main`.

---

## Threat model

### Trusted

- **The host machine.** The harness runs as an ordinary Unix user with no
  `sudo`. Everything it can do, an operator logged in as that user could
  do too.
- **Postgres.** Peer/scram auth on localhost; the runtime role is
  DML-only.
- **The embedding endpoint.** Configured by the operator; treated as
  trusted infrastructure.
- **The model API.** Authenticated with the operator's key. The *model* is
  **not** trusted — its output is treated as untrusted input, and it is the
  thing most likely to be attacked through prompt injection.
- **The operator.** They hold the secrets and they own the machine.

### Not trusted

- **Chat messages.** Every user message is untrusted input and the primary
  prompt-injection surface.
- **Model output.** Assistant text is rendered as HTML in the browser, so
  it is sanitised (see [Input handling](#input-handling)) and never
  interpolated into a SQL string or a shell command.
- **Tool output.** Tool results go back to the model, so a compromised tool
  can inject context. The memory API is the boundary: the agent loop never
  sees SQL.
- **Uploaded files.** User-supplied content with a size cap and a MIME
  allowlist — but it is still user content.
- **The config file** — `config.yaml` holds the model key, the database
  URL and password, the session secret and the internal token. Mode 600,
  enforced at startup.

### What an attacker can do

- Sign in as a user (if they have credentials or steal a session).
- Send any message to any agent they can reach.
- Read and write their own conversations and memories.
- Upload files.
- Try prompt injection: talk the agent into revealing another user's
  memory, into calling a tool it should not, or into running a command it
  should ask about first.
- Read anything that is served without authentication — see
  [the public surface](#the-public-surface).

### What an attacker cannot do (by design)

- **Escalate to root.** `exec_bash` runs in the harness process's own
  user context, and `sudo` / `su` are hard-blocked by the command
  classifier.
- **Write to the database through the agent.** All SQL is parametric
  (`%s` placeholders) and the read-only `sql_query` tool refuses anything
  that is not a `SELECT`.
- **Write to the database through the web app.** The runtime role has DML
  only — no `CREATE`, `DROP` or `ALTER`.
- **Read another agent's memories** — each agent has its own Postgres
  schema and its own tables. The one shared table is `public.users`.
- **Read the API keys out of the logs** — the redaction filter masks the
  two patterns that matter (see the limitation below).
- **Read the secrets out of `exec_bash`'s environment** — the tool runs
  with a scrubbed environment; variables whose names look secret-shaped
  are removed before the subprocess starts.
- **Run an unbounded tool loop.** The agent loop caps the iterations per
  turn, and there is a concurrency cap on in-flight model calls.

### The public surface

These endpoints are reachable without a session:

| Route | Why |
|---|---|
| `GET /health` | liveness |
| `GET /`, `GET /c/<entity>`, `GET /g/<group>` | the HTML shells — the data behind them is fetched from authenticated APIs |
| `GET /api/v1/entities/<name>/icon` | so an `<img>` tag can load an agent's avatar without credentials |
| `POST /api/v1/internal/*` | service-to-service, gated on a shared token, not on a session |

Everything else needs a session, and the admin views additionally need the
`admin` role.

---

## The tool roster

**This is the part to read carefully.** The harness gives the model real
tools, including ones that touch the machine and the database. A prompt
injection is a way of *asking* the model to use them, so the controls
below are the actual security boundary — not the model.

There are ten tools:

| Tool | What it does |
|---|---|
| `memory_recall` | search this agent's memories (vector + trigram) |
| `memory_recent` | list this agent's recent long-form memories |
| `memory_pin` | pin a memory so it is injected into every prompt |
| `memory_unpin` | unpin a memory |
| `memory_write` | write a long-form memory |
| `memory_update` | amend a long-form memory in place |
| `memory_delete` | delete a memory (hard delete; cascades to its chunks) |
| `sql_query` | run a **read-only** `SELECT` against the harness database |
| `exec_bash` | run a **shell command** as the harness's Unix user |
| `attach_file` | copy a file from the operator's home into the uploads directory |

Every memory operation is written to the agent's `memory_audit` table.
Every shell command is written to `exec_audit.jsonl`.

### `exec_bash`

Three controls, in order:

1. **The environment is scrubbed.** The subprocess gets a whitelist of
   ordinary environment variables (`PATH`, `HOME`, `TZ`, …) plus
   `HARNESS_BIND` and `HARNESS_CONFIG`. Anything whose name contains
   `KEY`, `SECRET`, `PASSWORD`, `TOKEN`, `CREDENTIAL` or `AUTH` is
   dropped, so the model cannot `printenv` its way to the model key.
2. **The command is classified.**
   - *Hard block* — refused outright: `sudo`, `su`, `rm -rf /`, `mkfs`,
     `dd of=/dev/…`, writes to `/dev/sd*`, fork bombs, `curl|sh` and
     `wget|sh`, anything touching `/etc/passwd`, `/etc/shadow`,
     `/etc/sudoers` or `/etc/systemd/system/`, `chmod` with setuid/setgid
     bits, `chmod 777`, and `systemctl mask|disable` of a *system* unit.
   - *Soft flag* — refused **unless the operator confirms**: recursive
     `chmod`, `rm -rf` inside an uploads directory, and stopping,
     restarting or disabling the harness's own services (which would kill
     the chat surface the operator is talking to). The tool returns
     `requires_confirmation: true`, and the agent is instructed to ask in
     chat and re-call with `confirm=true` only after the operator agrees.
3. **Everything is logged.** The full command, the working directory, the
   exit code, the byte counts and the outcome land in
   `exec_audit.jsonl`. Output is capped at 50 KB per stream and the
   timeout is 30 s by default, 120 s at most.

### `sql_query`

Read-only, enforced by a pattern matcher: any statement containing a write
verb (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`,
`GRANT`, `REVOKE`, `COPY`, `VACUUM`, `REINDEX`, `CLUSTER`, `LOCK`, `CALL`,
`DO`) is refused, as is a multi-statement string. A 30 s
`statement_timeout` is applied, the result is capped at 1000 rows and
50 KB.

**The matcher is a regex, not a SQL parser.** It refuses the obvious
write verbs at the top level; it is not proof that a statement is
read-only. It also reads from the whole database, not from one schema —
see [Known limitations](#known-limitations).

### `attach_file`

Copies a file from under the operator's home into the uploads directory so
the chat UI can render an attachment icon. It is the one tool that reads
the filesystem, and it is bounded to the operator's home.

---

## Authentication and sessions

- Sessions are Flask-Login sessions backed by `public.users`.
- Passwords are hashed with Werkzeug's `pbkdf2:sha256` — 600 000
  iterations, 16-byte salt. The hash is never serialised into a JSON
  response.
- The session cookie is `Secure`, `HttpOnly` and `SameSite=Lax`, and lives
  for 30 days.
- API routes use `@login_required_json`, which returns `401` with a JSON
  body rather than a redirect.
- **CSRF**: `SameSite=Lax` stops cross-origin form posts, and every
  state-changing request (`POST`, `PUT`, `DELETE`, `PATCH`) is additionally
  checked against the `Origin` header. A request whose `Origin` host does
  not match the `Host` is refused with `403`. Requests with no `Origin`
  header are allowed — that is what `curl` and server-to-server clients
  send.
- The service-to-service routes (`/api/v1/internal/*`) authenticate with
  the `X-Harness-Token` header, which must equal `HARNESS_INTERNAL_TOKEN`.
- Admin views check the user's role as well as their session.

---

## Authorisation and data isolation

- **One schema per agent.** An agent's conversations, turns, memories,
  chunks and audit rows live in `<agent>.*`. Adding an agent is
  `CREATE SCHEMA <agent>` plus a schema seed — not a new user, a new
  process or a new deployment.
- **`public.users` is the only shared table.** It holds the accounts.
- **The runtime role is DML-only.** The operator role owns the schema and
  does the DDL; the role the app connects as cannot create, drop or
  alter anything. `ALTER DEFAULT PRIVILEGES` grants the runtime role
  rights on tables the operator creates later.
- **Conversations are user-scoped.** A conversation lookup filters on the
  owning user, so two users never see each other's conversations with the
  same agent.
- **Group chats are shared on purpose.** In a group, every participating
  agent sees the group's transcript — that is the feature. Group
  conversations are flagged `is_group = TRUE` and are excluded from the
  DM lookup paths, so a DM can never load a group's transcript.

---

## Input handling

- **SQL**: every statement in `app/` uses `psycopg`'s `%s` placeholders.
  This is enforced by a test, not by convention — see
  [Audit tests](#audit-tests).
- **Uploads**: capped at `HARNESS_MAX_UPLOAD_BYTES` (10 MiB by default),
  restricted to an allowlist of MIME types, stored under a UUID-prefixed
  name, and served back through a route that only accepts a
  `<32-hex>.<allowed-ext>` filename.
- **Agent avatars**: uploaded through `PUT /api/v1/entities/<name>/icon`,
  capped at 2 MiB, restricted to image MIME types, and stored outside the
  tracked tree.
- **Model output → HTML**: assistant text is parsed by `marked` and then
  sanitised by `DOMPurify` before it reaches the page. If `DOMPurify` is
  unavailable the UI refuses to render the unsanitised HTML rather than
  falling back to it. Both libraries are vendored in `app/static/` (see
  `app/static/THIRD_PARTY_NOTICES.md`) so the page needs no third-party
  origin and the CSP stays `default-src 'self'`.
- **Shell commands and SQL**: see [the tool roster](#the-tool-roster).

---

## Rate limiting

Flask-Limiter, in-memory, fixed-window, keyed on the remote address.

| Scope | Default |
|---|---|
| every route | 100/minute |
| chat routes | 30/minute |
| memory routes | 60/minute |

`RATE_LIMIT_DEFAULT`, `RATE_LIMIT_CHAT` and `RATE_LIMIT_MEMORY` set them.

Two consequences worth knowing: the storage is **per process**, so a
multi-worker deployment allows *N* times the limit, and a restart resets
every counter. If you run more than one worker, move the limiter to
Redis (`storage_uri`).

---

## Response headers

Every response carries:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Strict-Transport-Security` | `max-age=15552000; includeSubDomains` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

There are **no inline scripts** — `script-src 'self'` with no
`unsafe-inline` — which is why the vendored libraries are served from
this origin rather than a CDN. Inline *styles* are allowed, because the
chat UI sets them.

---

## Secrets

- `config.yaml` holds the model key, the database URL and password, the
  session secret, the internal token and the TLS paths. It is git-ignored
  and must be mode `0600`. There is no `.env`: one file, one thing to
  protect.
- **The app refuses to start** if `config.yaml` is readable by anyone
  else, if `app.secret_key`, `db.url` or `llm.api_key` is empty, or if
  one of them still holds a placeholder. That check is
  `security.sanity_check_config()` and it runs at startup.
- The tracked `config.yaml.example` holds safe values with
  `YOUR_..._HERE` where a secret belongs. The install's own `config.yaml`
  is not tracked.
- A prepared install's `config.yaml` carries real keys and is git-ignored.
- The redaction filter masks `Authorization: Bearer <token>` and
  `LLM_API_KEY=…` in every log record, in the message and in the
  arguments.

---

## Logging and audit trails

| What | Where |
|---|---|
| HTTP access + a per-request id | the service log; `X-Request-Id` on every response |
| every memory operation | the agent's `memory_audit` table |
| every shell command | `exec_audit.jsonl` next to the install |
| tool calls generally | `tool_audit.jsonl` |

`X-Request-Id` is taken from the request if the client supplies one and
generated otherwise, so a single id ties a CF edge request to a tool
result.

---

## Backups

`tools/backup.sh` takes a `pg_dump` of the harness database, writes a
sha256 manifest beside it, and prunes anything older than 14 days. Point
it at a directory of your choosing:

    bash tools/backup.sh /path/to/backup/dir

It is not scheduled by the repo — wire it to a `systemd --user` timer
yourself. A backup that is not restored from is not a backup.

---

## OWASP Top 10 mapping

| Risk | Mitigation |
|---|---|
| **A01 Broken Access Control** | Session auth on every data route (`@login_required_json`), role checks on the admin views, user-scoped conversation lookups, per-agent schema isolation, a shared-token check on the internal routes. |
| **A02 Cryptographic Failures** | TLS at the edge, HSTS, `pbkdf2:sha256` password hashing, `Secure`/`HttpOnly`/`SameSite` cookies, `SECRET_KEY` from the environment. |
| **A03 Injection (SQL)** | Parametric SQL everywhere in `app/`; enforced by `tests/test_security.py::test_no_fstring_sql`. The read-only tool refuses write verbs. |
| **A03 Injection (prompt)** | The model is not a boundary. The tools are: command classification, read-only SQL, a scrubbed environment, a scrubbed model output path, and the soft-flag confirmation flow. |
| **A04 Insecure Design** | Single-purpose design; a documented and deliberately limited tool roster; a documented threat model; an explicit public surface. |
| **A05 Security Misconfiguration** | `sanity_check_config()` refuses to start on a config readable by others, an empty required value or a leftover placeholder. The security headers are applied to every response. |
| **A06 Vulnerable Components** | A hand-curated `requirements.txt` with upper bounds; two vendored browser libraries with their licences recorded in `app/static/THIRD_PARTY_NOTICES.md`. |
| **A07 Identification & Auth Failures** | `pbkdf2:sha256` with a per-user salt, 30-day sessions, an `Origin` check plus `SameSite=Lax` for CSRF, 401 JSON for the API. |
| **A08 Software & Data Integrity** | Idempotent schema installation; bootstrap identity files are re-read per turn so an operator edit takes effect on the next turn; the audit trails above. |
| **A09 Security Logging** | `X-Request-Id` on every request, the `memory_audit` table, `exec_audit.jsonl`, and the log redaction filter. |
| **A10 SSRF** | The agent loop makes no outbound HTTP of its own. The embedding and model endpoints are operator-configured singletons. |

---

## Known limitations

These are real, and none of them is fixed today.

1. **`sql_query` is not sandboxed by a real SQL parser.** It refuses
   write verbs with a regex. A statement crafted to hide a write from
   that regex is the obvious attack, and the regex is the only thing
   standing in the way. It also reads from *any* table in the database,
   including `public.users` — which holds password hashes. If that
   matters for your install, remove the tool or scope it to a schema.
2. **`exec_bash` is a command filter, not a sandbox.** It runs as the
   harness's Unix user. The hard-block list is a set of regexes; a
   dangerous command that is not on the list will run. The real
   containment is the Unix user's own permissions — so **run the harness
   as a user with no `sudo` and no secrets of its own**, and treat the
   agent as being able to do anything that user can do.
3. **The confirmation flow is a prompt-level control.** When a command is
   soft-flagged, the tool returns `requires_confirmation: true` and the
   *agent* is expected to ask the operator before re-calling with
   `confirm=true`. Nothing enforces that the operator actually said yes.
   Treat it as a speed bump, not a gate.
4. **Log redaction covers two patterns.** `Authorization: Bearer …` and
   `LLM_API_KEY=…`. A credential in a differently-named variable, or one
   that has been base64-encoded, will reach the log. Nothing logs request
   bodies, which is what keeps this survivable.
5. **Rate limiting is per process and in memory.** Multiple workers mean
   multiple times the limit; a restart clears the counters.
6. **The internal token is compared with `!=`,** not a constant-time
   comparison. The practical risk is low, but it is not free of a timing
   side channel.
7. **Everything shares one database.** Users, agents and groups live in
   one Postgres instance with one set of schemas. The isolation between
   users is enforced in the application layer, not by the database.
8. **The `Origin` check allows requests with no `Origin` header,** which
   is what a non-browser client sends. That is deliberate — the API is
   meant to be callable — but it means CSRF protection rests on
   `SameSite=Lax` for browser-originated requests.
9. **Avatar and upload endpoints are unauthenticated on read.** The icon
   endpoint is public on purpose so an `<img>` tag works; an upload is
   served only to a signed-in user, but the icon is not.

---

## Audit tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/test_security.py -v
```

The suite needs a Postgres instance and the environment variables in
the config. It covers:

- the redaction filter (message and arguments, model key and bearer token);
- `sanity_check_env()` — world-readable file, placeholder value, empty
  value, and the happy path;
- that no file under `app/` contains f-string SQL;
- that the security headers are present on every response;
- that `X-Request-Id` round-trips;
- that the tool roster matches what this document says it is.

The live tests skip themselves when the harness is not running:

```bash
HARNESS_RUN_LIVE_TESTS=1 python -m pytest tests/test_security.py -v
```
