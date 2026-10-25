## Installing on a new machine

The harness installs from a clone plus **one install config file**. The
install config names the agent and says what this install differs by (which
embedding provider, which port, which schema); everything else is the
stock repo.

```bash
git clone https://github.com/peristyle-ai/harness.git
cd harness
python3 tools/apply_install_config.py install/<name>.yaml
```

That writes:

| file | what it is |
|---|---|
| `config.yaml` | the harness config for THIS install (one agent, the embedding provider) |
| `.env` | the environment: the install's values filled in, the rest left as visible placeholders |
| `pie/<agent>/identity/` | the agent's identity files — the **default sysadmin identity** with the name filled in |
| `schema/init.sql` | the whole schema — the shared public tables and the agent's schema |

Then the operator finishes the job (the applier prints these):

1. create the database + the roles, and apply the agent's schema
2. fill in the `REPLACE_ME` placeholders in `.env` (the database password,
   the LLM key + base URL, the app secret, the TLS paths)
3. apply the shared migrations (`bash tools/migrate.sh`)
4. start the service (the unit is in `systemd/`)
5. open `http://127.0.0.1:5010` and sign in

### The default identity is a sysadmin

`agent_template/` holds the **default agent identity**, and it is a
**sysadmin**: the operator of the machine. That is deliberate — the first
agent anyone installs is the one best equipped to set up the plumbing
(tunnels, DNS, certificates, the database), because those are the things
that need credentials and judgement.

The install config only **assigns a name** to it. The `identity/` files are
read at the start of every turn, so the operator can edit them (the
`SOUL.md` rules are the protected ones) and the change lands on the next
turn — no restart.

### Embeddings without a GPU

The `embedding.base_url` in `config.yaml` decides where the vectors come
from. Point it at a hosted service and the machine needs no GPU at all:

```yaml
embedding:
  base_url: https://embed.example.com/v1
  api_key: ${EMBEDDING_API_KEY}      # sent as "Authorization: Bearer <key>"
  model: Qwen/Qwen3-Embedding-4B
  dimensions: 1024
```

The request body is OpenAI-compatible either way, so a local router and a
hosted service are interchangeable.
