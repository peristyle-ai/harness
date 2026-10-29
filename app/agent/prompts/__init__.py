"""System prompts indexed by adapter_id.

Each adapter can have its own variant (different tool descriptions,
different style instructions, different XML-vs-Markdown preferences). If
a specific adapter has no prompt, we fall back to the minimax_m3 prompt
('agent') as the canonical default — most modern models will read it
without complaint.
"""
from . import minimax_m3

# Adapters without a specific prompt get the canonical agent prompt.
DEFAULT_PROMPT = minimax_m3.SYSTEM_PROMPT

_PROMPTS: dict[str, str] = {
    "minimax_m3": minimax_m3.SYSTEM_PROMPT,
    # 'kimi_k3' falls through to the default (same prompt works fine).
    # Future: "kimi_k3": kimi_k3.SYSTEM_PROMPT, ...
}


# Operator mode (2026-10-29). Appended to the system prompt when the
# install sets `security.allow_sudo: true`. The base prompt says sudo is
# blocked; this clause states that the install overrides that, and what
# is STILL blocked, so the model does not have to guess.
OPERATOR_MODE_CLAUSE = """

## OPERATOR MODE (this install has `security.allow_sudo: true`)

This install runs in **operator mode**. `exec_bash` MAY use `sudo`, and
the tool description above reflects that. Everything else is unchanged:

- `sudo` is bounded by the OPERATOR's sudoers grants on this machine. A
  grant is normally NOPASSWD for a specific command. A `sudo` call that
  would need a password will simply fail — do not try to work around it,
  and do not ask the user for a password.
- Still hard-blocked, in operator mode too: `rm -rf` on root paths,
  `mkfs`, writes to `/etc/passwd`, `/etc/shadow`, `/etc/sudoers`,
  `/etc/systemd/system/`, `chmod` with setuid/setgid bits, `chmod 777`,
  `curl|sh`, `wget|sh`, fork bombs, and `systemctl mask|disable` of a
  SYSTEM unit. Use `systemctl --user` for user units.
- The soft-flag rules are unchanged: recursive `chmod`, stopping or
  restarting the harness's own services, edits to `*.service` files and
  `rm -rf` inside an uploads directory still return
  `requires_confirmation=true` and still need the user's explicit
  approval for THAT command.

Use `sudo` when the task genuinely needs it (postgres superuser work,
installing into another Unix session, reading a root-owned log). Prefer
the unprivileged path when it works.
"""


def get(adapter_id: str, allow_sudo: bool = False) -> str:
    base = _PROMPTS.get(adapter_id, DEFAULT_PROMPT)
    if allow_sudo:
        return base + OPERATOR_MODE_CLAUSE
    return base
