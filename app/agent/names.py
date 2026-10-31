"""Agent name helpers — the schema form and the display form, kept apart.

The agent name travels into SQL: it names the per-agent schema
(<agent>.conversations, <agent>.turns, <agent>.memories). Two things
follow from that, and they are not the same thing:

  * the SCHEMA form is lower case. Postgres folds an unquoted identifier
    to lower case, so 'EXAMPLE' and 'example' address the same schema. It
    is restricted to [a-z][a-z0-9_]{0,39} because it is interpolated
    into SQL -- that restriction is the injection guard.
  * the DISPLAY form is whatever the install chose ('EXAMPLE'). It is what
    the HMI shows, what public.group_agents stores, and what the
    conversations.agent_name column holds. It is NOT lower-cased:
    list_conversations filters on that column with an exact match
    (c.agent_name = %s), so the stored value has to be the one the
    query passes.

Before this module existed, an install whose agent name was not lower
case (app.agents: [EXAMPLE]) booted cleanly and worked in 1:1 chat --
Postgres folded the identifier for it -- but every group-chat mention
failed with "invalid agent name: 'EXAMPLE'", because the group path was
the only place with a hard guard on the name.
"""
from __future__ import annotations

import re

# The schema-safe form of an agent name: lower case, and restricted to
# characters that cannot break out of an identifier in SQL.
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def canonical_agent_name(agent_name: str) -> str:
    """The schema-safe (lower case) form of an agent name.

    Raises ValueError for anything that is not a valid identifier --
    including the injection attempts the guard exists to stop. The
    display form is left alone by the callers; only the schema is
    canonicalised.
    """
    canonical = (agent_name or "").strip().lower()
    if not AGENT_NAME_RE.match(canonical):
        raise ValueError(f"invalid agent name: {agent_name!r}")
    return canonical
