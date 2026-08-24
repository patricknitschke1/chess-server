import asyncio

import pytest

from chess_core import STARTING_RATING
from chess_server.engine.deps import EngineDeps
from chess_server.store import txn
from chess_server.store.db import open_store
from chess_server.store.repositories import BotRepo
from chess_server.store.txn import critical_section, reset_seq

WALL = "2026-08-24T00:00:00Z"
START_MONO = 1_000_000_000_000  # any baseline; only differences carry meaning


@pytest.fixture(autouse=True)
def _fresh_write_lock(monkeypatch):
    """asyncio.Lock binds to the first loop that *contends* it and raises in any
    other. The server has one loop for its lifetime; pytest-asyncio gives each test
    its own, so without this only the first contending test can ever deadlock."""
    monkeypatch.setattr(txn, "write_lock", asyncio.Lock())


@pytest.fixture(autouse=True)
def _fresh_seq():
    reset_seq()


@pytest.fixture
def store(tmp_path):
    """File-backed, never ':memory:' — two ':memory:' connections are two databases,
    so the reader/writer split, WAL and BEGIN IMMEDIATE contention are unobservable."""
    s = open_store(str(tmp_path / "arena.db"))
    try:
        yield s
    finally:
        s.close()


class FakeClock:
    """Monotonic time a test moves by hand. An engine test that sleeps to observe
    behaviour is testing the event loop, not the engine."""

    def __init__(self, value: int = START_MONO):
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, ns: int) -> int:
        self.value += ns
        return self.value

    def set(self, ns: int) -> None:
        self.value = ns


class RecordingSink:
    def __init__(self):
        self.events: list[tuple[int, str, dict]] = []

    def __call__(self, seq: int, event_type: str, data: dict) -> None:
        self.events.append((seq, event_type, data))

    def types(self) -> list[str]:
        return [event_type for _, event_type, _ in self.events]

    def of(self, event_type: str) -> list[dict]:
        return [data for _, name, data in self.events if name == event_type]


class RecordingWake:
    def __init__(self):
        self.woken: list[int] = []

    def __call__(self, bot_id: int) -> None:
        self.woken.append(bot_id)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def sink():
    return RecordingSink()


@pytest.fixture
def wake():
    return RecordingWake()


@pytest.fixture
def deps(store, clock, sink, wake):
    return EngineDeps(
        conn=store.writer,
        executor=store.executor,
        sink=sink,
        wake=wake,
        now_mono=clock,
    )


@pytest.fixture
def seed_bots(store):
    """Insert bot rows through the real repository, inside a real transaction."""

    async def _seed(*names, role="competitor", rating=STARTING_RATING, owner=None,
                    is_anchor=0, controller="client"):
        bots = BotRepo(store.writer, store.executor)
        made = []
        async with critical_section(store.writer, store.executor):
            for name in names:
                bot_id = await bots.insert_bot(
                    name=name,
                    owner=owner if owner is not None else name,
                    token_hash=f"hash-{name}",
                    role=role,
                    rating=rating,
                    is_anchor=is_anchor,
                    created_at=WALL,
                    controller=controller,
                )
                made.append(await bots.get_by_id(bot_id))
        return made

    return _seed
