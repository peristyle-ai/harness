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

## Proposing a change

`main` is protected. Nobody pushes to it: a change lands on `main` only as a
merged pull request, with one approval from a code owner and both CI jobs
green.

If you are an agent working in an installation of this repository, the path
is:

```bash
scripts/agent_propose.sh start <slug>                  # make a worktree
# ... make the change in the worktree it prints ...
scripts/agent_propose.sh push <slug> "<title>" ["<body>"]
```

`start` creates a **git worktree** rather than checking a branch out in the
working tree. The working checkout may be a live installation whose service
is running from it; checking a branch out there would change the code under
the running service.

`push` commits the change, bumps the patch version in `pyproject.toml`, and
pushes the branch. Pushing a branch named `agent/**` (or `fix/**`) makes the
`agent-pr` workflow open a pull request for it.

### Why the workflow opens the pull request

The agents here hold a **deploy key**, which is a git credential: it can push
a branch but it cannot talk to the API at all. If an agent were given an API
token instead, it could open a pull request as an account that could also
approve it.

So the pull request is opened by a workflow using the repository's own
`GITHUB_TOKEN`, which makes its author `github-actions[bot]`. The bot is not
a code owner and cannot approve its own pull request, so the pull request
waits for a code owner — and only a code owner can merge it. **An agent
cannot merge its own work.**

### The version is the release

The version in `pyproject.toml` is the released version, and every pull
request that lands on `main` is a release — so the patch version is bumped as
part of the change (`0.1.0` → `0.1.1`). The diff shows the bump rather than
hiding it, and a merge into `main` *is* the release.

### If your pull request changes a workflow file

GitHub holds the workflow run for approval rather than running it, because a
pull request that changes a workflow is changing the thing that runs it. A
maintainer approves the run and the checks continue.
