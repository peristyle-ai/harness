# agent_template/ — canonical per-agent bootstrap template

> **Source of truth for "what an agent's `identity/` should contain."**
> Captures only the critical `should.md` elements — the must-have
> sections that have proven, across the 2026-10-19 sweeps, to make a
> harness agent work reliably on complex tasks.

## What's in here

This folder is a **template**, not a live config. The harness's per-agent
content lives in `pie/<agent>/identity/` (per-agent repos at
`peristyle-ai/<agent>`, gitignored from the harness repo). When you add a
new agent or sweep an existing one, copy these templates into the agent's
`identity/` directory and fill in the per-agent details (name, role, host,
working agreements).

| File | What it is | Critical sections |
|---|---|---|
| `IDENTITY.md.template` | Who am I, what do I do | Name, Creature, Vibe, Emoji, Avatar; the "What I do" + "How I work" + "Origin" sections |
| `SOUL.md.template` | Personality + standing rules | Core Truths, Boundaries, Vibe, Continuity, Operational Rules (Stay In This Session, Schedule Don't Bank, Memory Is Your Path To Persistence) |
| `USER.md.template` | Who you're helping + their context | Identity, operating principles, business context, workspace context, what they care about |
| `AGENTS.md.template` | The workspace + how to work in it | Session startup, memory sources, role description (three-duties pattern), red lines, ask-vs-do, execution closure rule, promise closure rule, group chat etiquette |
| `HEARTBEAT.md.template` | Polling + recurring tasks | Default behaviour (no heartbeat / minimal), recurring-task pattern, promise closure |
| `MEMORY.md.template` | Long-term curated memory | Identity, role, runtime, working agreements, standing references, ask-vs-do |

## Why a template, not a generator

The harness has a `BootstrapLoader` (`app/agent/bootstrap.py`) that
**reads** the per-agent identity files — it doesn't generate them. The
operator's job is to keep the files under the size caps in
`config.bootstrap.files` and content-correct for the agent's role. A
checked-in template (this folder) is the way to make that operator's
job consistent across agents.

## How to use this template

### For a new agent

1. Create the per-agent repo: `gh repo create peristyle-ai/<agent> --private`
2. Add the per-repo deploy key for `peristyle@localhost` (the existing pattern)
3. Clone to `pie/<agent>/` on `peristyle@localhost`
4. Copy this template's files into `pie/<agent>/identity/`, removing the `.template` suffix
5. Fill in the per-agent details (name, creature, role, host, working agreements)
6. Add `agents: [..., <agent>]` to `/opt/harness/harness/config.yaml`
7. Apply the DB schema (`psql -d <db> -v agent_name=<agent> -f schema/init.sql`)
8. Restart the harness

### For an existing agent (sweep)

The 2026-10-19 sweeps (Evie, the agent, Wisp) followed this pattern: copy
the template into the agent's `identity/`, customize for the role, and
commit. The "OpenClaw-era" wording in older agents should be replaced
with harness language. See `memory/2026-10-19.md` for the
worked-example sweep log.

## What "critical should.md elements" means

After the Evie + the agent + Wisp sweeps (Oct 15 / Oct 18 / Oct 19), the
elements that consistently made the agent more reliable on complex
tasks were:

- **A clear "What I do" section in IDENTITY.md** — the agent needs to
  know its role without having to infer it from AGENTS.md.
- **Standing operational rules in SOUL.md** (Stay In This Session,
  Schedule Don't Bank, Memory Is Your Path To Persistence) — these
  three rules consistently shaped the agent toward the "use tools in
  the main session, schedule follow-ups, write things down" behaviour
  that makes complex tasks survivable.
- **The execution closure rule in AGENTS.md** — `DONE:` / `FAILED:`
  with evidence. Without this, the agent silently swallows failures
  and the operator has to chase them.
- **The promise closure rule in AGENTS.md** — every promise to follow
  up gets a `create_timer` wakeup. Without this, promises evaporate
  across context compaction.
- **The "red lines" + ask-vs-do split in AGENTS.md** — explicit red
  lines (no exfiltration, no destructive ops without asking) and an
  explicit ask-vs-do list for the agent's role. The agent that
  internalizes these doesn't have to re-derive them every turn.
- **A thin MEMORY.md (<25 KB) with a "Standing references" section** —
  the agent needs to know where the canonical state lives (creds
  files, infra tables, runbooks) without rediscovering them every
  session.

What we **deliberately did not** keep in the templates:

- OpenClaw-specific runtime references (the harness replaced the
  OpenClaw runtime in 2026-10-15; anything that says "openclaw
  memory search" or "~/.openclaw/..." is a sweep target, not a
  template element)
- Per-agent role-specific scripts (those live in `pie/<agent>/scripts/`
  or `pie/<agent>/skills/`, not in the bootstrap)
- The 33 KB of historical context that used to live in peribot's
  MEMORY.md (archived to `pie/<agent>/docs/MEMORY_ARCHIVE_<date>.md`
  if needed, not in the hot file)
