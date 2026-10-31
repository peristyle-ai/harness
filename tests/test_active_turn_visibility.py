"""The live-turn lookup must not be blind to the turn another user started.

``GET /api/v1/entities/<name>/turns/active`` exists so the web chat can
rebuild its live bubble after a refresh: the bubble is client-side DOM,
so a refresh loses it while the turn keeps running server-side.  The
endpoint replays the turn's buffered events.

It looked the turn up with the user who STARTED it::

    turn_guard.find(agent_name=name, user_id=user_id)

That is right for steering -- one user's message must never be queued
into another user's turn -- but it is wrong for the read.  The browser
watching an agent is not always the browser session that started the
turn: a second tab, a second browser, or a colleague watching the same
agent are all ordinary.

The result was that the read answered "no active turn" while the agent
was deep inside one.  The bubble could not be rebuilt, the chat looked
dead, and the turn finished -- with its answer written to the database
and shown to nobody.  That is the "the agent is stuck" symptom the
endpoint exists to prevent.

These tests pin the fix: an admin sees the agent's turn whichever
session started it; a member still only sees their own.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.routes import api as api_module
from app.routes.api import _visible_active_turn


class _FakeRegistry:
    """Stands in for app.agent.turn_guard, recording every lookup."""

    def __init__(self, by_user=None, by_agent=None):
        self.by_user = by_user or {}
        self.by_agent = by_agent
        self.calls = []

    def find(self, agent_name=None, user_id=None, request_id=None):
        self.calls.append({"agent_name": agent_name, "user_id": user_id})
        if user_id is not None:
            return self.by_user.get(user_id)
        return self.by_agent


def _user(uid, role="member"):
    return SimpleNamespace(id=uid, role=role, email=f"u{uid}@example.com")


def _patch(monkeypatch, registry):
    monkeypatch.setattr(api_module.turn_guard, "find", registry.find)


def test_the_users_own_turn_is_found(monkeypatch):
    registry = _FakeRegistry(by_user={7: "OWN"})
    _patch(monkeypatch, registry)
    assert _visible_active_turn("example", _user(7)) == "OWN"


def test_an_admin_sees_the_turn_another_user_started(monkeypatch):
    registry = _FakeRegistry(by_user={7: None}, by_agent="OTHER")
    _patch(monkeypatch, registry)
    assert _visible_active_turn("example", _user(9, role="admin")) == "OTHER"
    assert registry.calls[0] == {"agent_name": "example", "user_id": 9}, (
        "the user's own turn must be looked up first"
    )
    assert registry.calls[1] == {"agent_name": "example", "user_id": None}, (
        "the fallback must be the agent-wide lookup -- that is the one "
        "that finds a turn another user started"
    )


def test_a_member_does_not_see_the_turn_another_user_started(monkeypatch):
    registry = _FakeRegistry(by_user={7: None}, by_agent="OTHER")
    _patch(monkeypatch, registry)
    assert _visible_active_turn("example", _user(9, role="member")) is None
    assert len(registry.calls) == 1, (
        "a member must not get the agent-wide lookup at all"
    )


def test_a_member_who_started_the_turn_still_sees_it(monkeypatch):
    registry = _FakeRegistry(by_user={9: "OWN"}, by_agent="OTHER")
    _patch(monkeypatch, registry)
    assert _visible_active_turn("example", _user(9, role="member")) == "OWN"
    assert len(registry.calls) == 1, (
        "no fallback is needed when the user-scoped lookup answered"
    )


def test_no_user_at_all_keeps_the_original_agent_wide_lookup(monkeypatch):
    """A missing user is the original behaviour, unchanged.

    ``turn_guard.find`` only filters on the user when it is given one,
    so ``user_id=None`` was always the agent-wide lookup.  The route is
    behind the login decorator, so this is not reachable in practice --
    but the fallback must not change what it does.
    """
    registry = _FakeRegistry(by_agent="OTHER")
    _patch(monkeypatch, registry)
    assert _visible_active_turn("example", None) == "OTHER"
    assert registry.calls == [{"agent_name": "example", "user_id": None}]


def test_the_route_uses_the_helper(monkeypatch):
    """The route must call the helper, not the user-scoped lookup."""
    source = (REPO / "app" / "routes" / "api.py").read_text()
    body = source.split("def entity_turn_active", 1)[1].split("\ndef ", 1)[0]
    assert "_visible_active_turn(" in body, (
        "the route must use _visible_active_turn, or the admin fallback "
        "never happens"
    )
    assert "turn_guard.find(" not in body, (
        "the route must not do its own user-scoped lookup -- that is the "
        "lookup that hid the running turn"
    )
