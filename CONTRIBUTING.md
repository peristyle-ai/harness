# Contributing

Thanks for considering a contribution.

## The shape of the code

- `app/agent/` is the core: the agent loop (`loop.py`), the memory layer
  (`memory.py`), the tool registry (`tools/`), the LLM adapters (`llm/`), the
  context window (`context_window.py`) and the identity bootstrap
  (`bootstrap.py`).
- `app/routes/` is the REST API, `app/static/` is the chat UI, and
  `app/templates/` holds the server-rendered pages.
- `schema/init.sql` is the SQL -- the whole schema, in one file. It is
  generated from the live database by `scripts/build_init.py`; edit the
  live database and regenerate, rather than hand-editing the file.
- `tests/` is pytest. The four `tests_*.py` files in the repository root are
  **not** pytest modules — they are runnable scripts that need a live model
  (see the "Run:" line at the top of each).

## Getting set up

```bash
pip install -r requirements.txt -r requirements-dev.txt
cp config.yaml.example config.yaml
python run.py --init-db
python -m pytest tests/ -q
```

The suite needs Postgres. The tests that need a live model or a live database
skip themselves when it is not there, so a partial environment still gives a
meaningful run.

## The rules that matter

- **An identity file is read at the start of every turn.** Changing
  `SOUL.md` changes the next turn — no restart, no redeploy.
- **Memories are rows.** If a behaviour belongs in a memory, write it with the
  memory tools; if it belongs in the code, change the code. Do not create a
  second convention.
- **Never commit a secret.** `config.yaml` is git-ignored — it holds the
  API keys and the database password. `config.yaml.example` is committed
  and holds safe values with `YOUR_..._HERE` where a secret belongs.
- **A schema change is a migration.** Add a numbered file; do not edit an
  existing one that has shipped.

## A pull request

1. One concern per pull request.
2. The suite passes (`python -m pytest tests/ -q`).
3. If the change touches the agent loop, the steering or the thought stream,
   say so in the description — those paths have behavioural tests that a
   reader will want to look at.

## Security

Do not open a public issue for a vulnerability. See
[SECURITY.md](./SECURITY.md) for the reporting route.

## License

By contributing you agree that your contribution is licensed under the MIT
license of this project.
