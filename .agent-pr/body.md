An adapter holds one `httpx.Client`, with one connection pool. On most
installs **every agent shared that one client** — the registry was
populated only for the agents listed in `llm.per_agent`, which was added
for a per-agent *model* test and never for isolation.

**A shared client is a shared failure domain.** When the upstream resets
that connection, every stream in flight on it dies at the same moment. On
2026-11-01 two agents on one install, sharing one client, both lost their
turn on the same upstream reset — three milliseconds apart.

## What changes

- **Every agent gets its own adapter** — and therefore its own client,
  its own pool and its own connection. An upstream reset kills one
  agent's turn, not all of them.
- **`llm.per_agent` becomes a partial override.** It used to be a
  complete `llm` block — every field repeated, `api_key` included — so
  getting an isolated client meant copying the key once per agent, and
  most installs just shared instead. Now an entry names only what
  changes and inherits the rest:

  ```yaml
  llm:
    per_agent:
      example:
        model: some-other-model
  ```

  No duplication, and no key copied per agent.

- **A full block still loads unchanged.** Merging a complete block over
  the global one yields the same values, so existing configs keep
  working untouched.

The cost is one socket and a pool per agent.

## Tests

- two agents on one install get two different clients and two different
  pools — with or without a config entry;
- the partial override merges as documented, and does not mutate the
  global block;
- a full block still builds.

## For the reviewer

The change is generic and names no install. The installer drops
`per_agent` for a single-agent install, which is still correct — and
now for a different reason: every agent gets its own client either way.
