# install/

**The install configs in this directory are NOT tracked in git.**

Each one is a per-customer file: it carries that customer's API keys (the
LLM key, the embedding key), so it must never be committed. The pattern
`install/*.yaml` is in `.gitignore` for exactly that reason.

## What an install config is

The single file a customer receives by email, alongside the GitHub URL. It
says everything that differs from a stock install:

```yaml
install:
  agent_name: agent             # the agent's name (the default agent)
  operator_name: the operator
  operator_email: operator@example.com
  operator_timezone: UTC
  database: harness
  schema: agent
  operator_role: agent
  runtime_role: agent_app
  bind: 127.0.0.1:5010
  pie_root: harness/pie
  llm:
    adapter: minimax_m31_flash
    api_key: sk-...             # <- a secret: this file is never committed
    base_url: https://api.minimax.io/anthropic
    model: MiniMax-M3.1-Flash
    ...
  embedding:
    base_url: https://embed.example.com/v1
    api_key: ep_...              # <- a secret: this file is never committed
    model: Qwen/Qwen3-Embedding-4B
    dimensions: 1024
```

## How it is applied

```bash
python3 tools/apply_install_config.py install/<name>.yaml
```

which writes `config.yaml`, `.env`, the agent's identity files and the
agent's schema SQL, and prints the remaining steps. See `README_INSTALL.md`.

## Writing a new one

Copy the shape above, fill it in, and **keep it out of git**:

```bash
cp install/example.yaml install/<name>.yaml
```

`install/example.yaml` (the version WITHOUT the secrets) is the tracked
example.
