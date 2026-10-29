## What

Some agents are not assistants but **operators**: the sysadmin of an install, driving installs, databases and services on a machine it is responsible for. Such an agent cannot finish the job when every privileged step needs a human present.

This adds **operator mode**: one per-install config option.

```yaml
security:
  allow_sudo: true      # default: false
```

## What it changes

- The `sudo`/`su` hard blocks in the command classifier are lifted, so `exec_bash` may use sudo. It can only escalate as far as the **operator's sudoers grants** allow — in practice `NOPASSWD` grants for specific commands (e.g. `psql` as the postgres superuser). A grant for `psql` is not a grant for `bash`.
- The system prompt gains an **OPERATOR MODE clause**, so the model knows it may use sudo and what is *still* blocked. A silent change would leave the model believing sudo is blocked, and it would never try.
- `install.sh` drops `NoNewPrivileges=true` from the unit for an operator-mode install. This is not cosmetic: under that flag sudo refuses to run **at all** (`sudo: The "no new privileges" flag is set, which prevents sudo from running as root`), so sudo calls would fail no matter what the sudoers grants say.

## What it does NOT change

- Every other hard block stands: `rm -rf` on root paths, `mkfs`, writes to `/etc/{passwd,shadow,sudoers}` and `/etc/systemd/system/`, setuid bits, `chmod 777`, `curl|sh`, fork bombs, `systemctl mask|disable` of a *system* unit. The soft-flag/confirmation rules are unchanged.
- Every command is still audited to `exec_audit.jsonl`.

## The trade-off, stated plainly

Operator mode **widens the blast radius of a prompt injection**: by default, a hostile instruction in content the agent reads can at most run commands as the install's Unix user; in operator mode it can additionally use whatever the operator has granted. That is an acceptable trade when the grants are narrowly scoped and the agent's inputs are the operator's own — and it is not acceptable if the agent reads attacker-controlled content. The decision belongs to the operator of the install, per install.

`SECURITY.md` gains an **Operator mode** section that says exactly this.

## Also fixes a pre-existing classifier gap

The new test exposed that the **symbolic** setuid form was not blocked: `chmod 4755 f` was refused, but `chmod u+s f` was allowed. Both are refused now.

## Tests

8 new cases in `tests/test_security.py`: default posture (sudo/su blocked), operator mode (allowed), the blocks that must survive operator mode, the soft flags, the prompt clause (present only in operator mode), the shipped example config defaulting to off, and the installer stripping the flag.

Full suite: **211 passed, 9 skipped** (the skips need a live model/DB).

## Notes for the reviewer

- The config option is read defensively (`getattr` chain), so an install whose config predates the option behaves exactly as before.
- The classifier's privilege patterns were split into their own list so the two postures cannot drift apart.
- Per the repo's workflow this lands on `main` only after validation — @durthu is the approver of record.
