"""Agent name helpers — three forms, kept apart.

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

On top of those there is a third form, added 2026-11-01: the DISPLAY
NAME -- the name the agent calls itself, the '**Name:**' line in its
IDENTITY.md ('EXAMPLE'). That one is a LABEL and nothing else. It never
touches SQL, never appears in a URL, and never has to be unique.

    key          example      the identifier: schema, URL, API path,
                             config's app.agents, the agent_name column
    display name EXAMPLE      the label: the chat header, the entity list
    schema form  example      the key, folded (Postgres does this)

Before this module existed, an install whose agent name was not lower
case (app.agents: [EXAMPLE]) booted cleanly and worked in 1:1 chat --
Postgres folded the identifier for it -- but every group-chat mention
failed with "invalid agent name: 'EXAMPLE'", because the group path was
the only place with a hard guard on the name.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

# The schema-safe form of an agent name: lower case, and restricted to
# characters that cannot break out of an identifier in SQL.
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# The '**Name:** EXAMPLE' line in an IDENTITY.md. The list marker is
# optional, so both of these match:
#
#     - **Name:** EXAMPLE
#       **Name:** EXAMPLE
#
DISPLAY_NAME_RE = re.compile(
    r"^\s*(?:[-*]\s*)?\*\*Name:\*\*\s*(.+?)\s*$", re.MULTILINE
)


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


def display_name_from_identity(content: str) -> str | None:
    """The name an agent calls itself, read from its IDENTITY.md.

    '**Name:** EXAMPLE' -> 'EXAMPLE'. None when the file does not declare
    one -- the caller then falls back to the key.

    This is a LABEL. It is never interpolated into SQL, never used as
    an identifier, and never has to be unique.
    """
    if not content:
        return None
    match = DISPLAY_NAME_RE.search(content)
    if not match:
        return None
    name = match.group(1).strip().strip('"').strip("'").strip()
    return name or None


def resolve_agent_key(
    name: str,
    keys: Iterable[str],
    display_names: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a user-supplied agent name to the agent's KEY.

    The key is the identifier (schema, URL, API path, config). The
    display name is the label. A URL can carry either: a bookmark from
    before a rename carries the old form, and a person typing the name
    the agent answers to expects that to work too. So both resolve.

    The order is: an exact key, then the display name, then a
    case-insensitive key. The last one is what keeps a bookmark that
    predates the display-name feature working on an install whose
    identity file does not declare a name.

    The display name is matched case-insensitively, and only when it is
    unambiguous -- two agents claiming the same label resolve to
    neither, because guessing would send the conversation to the wrong
    agent.
    """
    if not name:
        return None
    key_list = list(keys or ())
    if name in key_list:
        return name
    wanted = name.strip().lower()
    if not wanted:
        return None
    hits = [
        key
        for key, display in (display_names or {}).items()
        if (display or "").strip().lower() == wanted
    ]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return None
    # No display name matched. Fall back to a case-insensitive key
    # match, so 'EXAMPLE' still finds the agent whose key is 'example'.
    folded = [key for key in key_list if key.lower() == wanted]
    if len(folded) == 1:
        return folded[0]
    return None
