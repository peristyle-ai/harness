"""Resolving an agent name that arrived in a URL.

There are three forms of an agent's name, and they are not the same
thing:

    key          'example'     the identifier -- the schema, the config's
                              app.agents, the API path, the agent_name
                              column. Everything that touches SQL or a
                              route uses this.
    display name 'EXAMPLE'     the label -- the chat header, the entity
                              list. The '**Name:**' line in the agent's
                              IDENTITY.md.
    schema form  'example'     the key, folded (Postgres folds an
                              unquoted identifier to lower case).

A URL can carry either the key or the display name: a bookmark from
before an agent was renamed carries the old form, and a person typing
the name the agent answers to expects that to work too. Both resolve to
the same agent.
"""
from __future__ import annotations

from flask import current_app, g, request

from app.agent.names import resolve_agent_key


def _registry():
    ext = current_app.extensions.get("harness") or {}
    return ext.get("bootstrap_registry")


def agent_keys() -> list[str]:
    """The agent keys this install has."""
    registry = _registry()
    if registry is not None:
        return list(registry.list_agents())
    cfg = current_app.config.get("HARNESS_CONFIG_OBJ")
    agents = getattr(getattr(cfg, "app", None), "agents", ()) if cfg else ()
    return list(agents or ())


def agent_display_names() -> dict[str, str]:
    """agent key -> the name it displays in the UI."""
    registry = _registry()
    if registry is None:
        return {}
    try:
        return registry.display_names()
    except Exception:
        return {}


def resolve_agent(name: str) -> str | None:
    """The KEY for a user-supplied agent name, or None if there is none."""
    return resolve_agent_key(name, agent_keys(), agent_display_names())


def agent_display_name(key: str) -> str:
    """The label to show for an agent key. Falls back to the key."""
    return agent_display_names().get(key) or key


def canonicalise_agent_name() -> None:
    """Rewrite an entity path segment from the display name to the key.

    The entity routes interpolate the segment into SQL and compare it
    with the agent_name column, so it has to be the KEY. Accepting the
    display name as well means an old bookmark -- or a person typing the
    name the agent answers to -- keeps working instead of getting a 400
    (which is what an empty chat window looks like).

    The name the caller sent is kept in ``g.original_entity_name``: the
    icon routes file an icon under the name it was uploaded with, and
    that is not necessarily the current key.
    """
    view_args = request.view_args or {}
    name = view_args.get("name")
    if not name:
        return
    key = resolve_agent(name)
    if key and key != name:
        view_args["name"] = key
        g.original_entity_name = name
