"""The interrupted marker must be written EXACTLY ONCE per turn.

Regression test for the duplicate assistant turn on 2026-10-26 09:12:44:
a service restart during a live turn left TWO identical rows (ids 196
and 197) in peribot.turns -- same content, byte-identical trajectories,
and the second row's computed latency_ms was ONE MILLISECOND SMALLER
than the first's, which is only possible if the two writers interleaved.

The old guard was `if state.interrupted_id() is not None: return` --
a check-then-act across a database write. Two callers can both read
"not written yet" and both write. The fix is an atomic CLAIM, taken
before the write.

These tests drive the real code path with a fake memory, and run the
writers CONCURRENTLY on purpose.
"""
import threading
import time

import pytest

from app.agent import turn_guard
from app.agent.loop import AgentLoop


class FakeTurn:
    """What write_turn_assistant returns."""

    def __init__(self, turn_id: int):
        self.id = turn_id


class FakeMemory:
    """Records the assistant turns written, with a real gap so the
    write is slow enough for a second caller to interleave."""

    def __init__(self, delay: float = 0.02):
        self.writes: list[str] = []
        self._delay = delay
        self._lock = threading.Lock()
        self._next_id = 1000

    def write_turn_assistant(self, conv_id, text, **kwargs):
        # The window between the caller's check and the row existing.
        time.sleep(self._delay)
        with self._lock:
            self._next_id += 1
            self.writes.append(text)
            return FakeTurn(self._next_id)


def _make_loop(memory):
    """An AgentLoop whose agent memory is the fake."""
    loop = AgentLoop.__new__(AgentLoop)
    loop._agent_memory = lambda agent_name: memory
    return loop


def _make_state(memory, conv_id="conv-1", request_id="req-1"):
    state = turn_guard.TurnState(
        conv_id=conv_id,
        agent_name="testbot",
        user_id=1,
        user_turn=None,
        user_text="hello",
        attachments=[],
        t0=time.monotonic(),
        model="test-model",
        request_id=request_id,
    )
    loop = _make_loop(memory)
    state._persist = lambda reason: loop._persist_interrupted_turn(state, reason)
    return state


@pytest.fixture(autouse=True)
def _clear_claims():
    """Each test starts with no claims."""
    with turn_guard._INTERRUPT_CLAIM_LOCK:
        turn_guard._INTERRUPT_CLAIMS.clear()
    yield
    with turn_guard._INTERRUPT_CLAIM_LOCK:
        turn_guard._INTERRUPT_CLAIMS.clear()


def test_one_writer_writes_one_row():
    memory = FakeMemory(delay=0)
    state = _make_state(memory)
    loop = _make_loop(memory)

    turn_id = loop._persist_interrupted_turn(state, "the server is restarting")

    assert turn_id is not None
    assert len(memory.writes) == 1
    assert "interrupted" in memory.writes[0]


def test_two_sequential_writers_write_one_row():
    """The simple case: the second call sees the first's row."""
    memory = FakeMemory(delay=0)
    state = _make_state(memory)
    loop = _make_loop(memory)

    first = loop._persist_interrupted_turn(state, "reason")
    second = loop._persist_interrupted_turn(state, "reason")

    assert first is not None
    assert second == first
    assert len(memory.writes) == 1


def test_concurrent_writers_write_exactly_one_row():
    """The bug: concurrent writers both pass the check and both write.

    This is the shape that produced ids 196 and 197. The fake memory
    sleeps inside the write, so every writer is inside the vulnerable
    window at the same time.
    """
    memory = FakeMemory(delay=0.05)
    state = _make_state(memory)
    loop = _make_loop(memory)

    n = 8
    results: list = [None] * n
    barrier = threading.Barrier(n)

    def writer(i):
        barrier.wait()          # release them all at once
        results[i] = loop._persist_interrupted_turn(state, "reason")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(memory.writes) == 1, (
        f"expected exactly one interrupted row, got {len(memory.writes)}"
    )
    # and every caller got the SAME turn id back
    assert len({r for r in results if r is not None}) == 1


def test_two_state_objects_for_one_turn_write_one_row():
    """Two TurnState objects for the same (conversation, request).

    The registry holds the newest state, but a superseded request
    thread still holds its own. The marker must still be unique --
    which is why the claim is keyed by (conversation, request id) and
    not by the state object.
    """
    memory = FakeMemory(delay=0.05)
    state_a = _make_state(memory, conv_id="conv-9", request_id="req-9")
    state_b = _make_state(memory, conv_id="conv-9", request_id="req-9")
    loop = _make_loop(memory)

    barrier = threading.Barrier(2)
    results: list = [None, None]

    def writer(i, st):
        barrier.wait()
        results[i] = loop._persist_interrupted_turn(st, "reason")

    threads = [
        threading.Thread(target=writer, args=(0, state_a)),
        threading.Thread(target=writer, args=(1, state_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(memory.writes) == 1


def test_a_failed_write_hands_the_claim_back():
    """A failed write must not leave the turn with no marker at all."""
    class ExplodingMemory:
        def write_turn_assistant(self, *a, **k):
            raise RuntimeError("the database is on fire")

    memory = ExplodingMemory()
    state = _make_state(memory)
    loop = _make_loop(memory)

    assert loop._persist_interrupted_turn(state, "reason") is None
    # the claim was released, so a later caller can still write it
    assert not turn_guard.interrupt_already_written("conv-1", "req-1")

    good = FakeMemory(delay=0)
    state2 = _make_state(good)
    loop2 = _make_loop(good)
    assert loop2._persist_interrupted_turn(state2, "reason") is not None
    assert len(good.writes) == 1


def test_the_claim_is_per_request():
    """Two different requests in the same conversation each get a marker."""
    memory = FakeMemory(delay=0)
    loop = _make_loop(memory)

    a = _make_state(memory, conv_id="conv-x", request_id="req-a")
    b = _make_state(memory, conv_id="conv-x", request_id="req-b")

    loop._persist_interrupted_turn(a, "reason")
    loop._persist_interrupted_turn(b, "reason")

    assert len(memory.writes) == 2


def test_the_claim_table_is_bounded():
    """The claim table must not grow without bound."""
    from app.agent.turn_guard import _MAX_INTERRUPT_CLAIMS

    with turn_guard._INTERRUPT_CLAIM_LOCK:
        turn_guard._INTERRUPT_CLAIMS.clear()
        for i in range(_MAX_INTERRUPT_CLAIMS + 50):
            turn_guard.claim_interrupt(f"conv-{i}", f"req-{i}")
        assert len(turn_guard._INTERRUPT_CLAIMS) <= _MAX_INTERRUPT_CLAIMS
