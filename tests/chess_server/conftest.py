import asyncio

import httpx
import pytest

from chess_core import RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS, STARTING_RATING
from chess_server.api.app import create_app
from chess_server.api.settings import Settings
from chess_server.api.state import AppState
from chess_server.engine import state
from chess_server.engine.deps import EngineDeps
from chess_server.engine.games import create_game_locked
from chess_server.store import txn
from chess_server.store.db import open_store
from chess_server.store.repositories import BotRepo, GameRepo
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


@pytest.fixture(autouse=True)
def _fresh_engine_state():
    """Emptied field by field rather than through clear_all, which is under test."""
    for container in (state.mailbox, state.history, state.history_san,
                      state.unpaired_ticks, state.connected):
        container.clear()


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
def games(store):
    return GameRepo(store.writer, store.executor)


@pytest.fixture
def bot_repo(store):
    return BotRepo(store.writer, store.executor)


@pytest.fixture
def make_game(store, deps, games):
    """A seated game through the real creation path, committed."""

    async def _make(white, black, *, time_control_ns=RATED_TIME_CONTROL_NS,
                    increment_ns=RATED_INCREMENT_NS, source="matchmaker"):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            game_id = await create_game_locked(
                deps, txn, white, black,
                time_control_ns=time_control_ns,
                increment_ns=increment_ns,
                source=source,
                now_mono=deps.now_mono(),
            )
        return await games.get_by_id(game_id)

    return _make


@pytest.fixture
def poll(store, clock, bot_repo):
    """Make a bot pool-eligible by giving it a poll at the current fake time."""

    async def _poll(*bot_ids, at=None):
        async with critical_section(store.writer, store.executor):
            for bot_id in bot_ids:
                await bot_repo.update_last_poll(bot_id, WALL, clock() if at is None else at)

    return _poll


@pytest.fixture
def seed_bots(store):
    """Insert bot rows through the real repository, inside a real transaction."""

    async def _seed(*names, role="competitor", rating=STARTING_RATING, owner=None,
                    is_anchor=0):
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
                )
                made.append(await bots.get_by_id(bot_id))
        return made

    return _seed


JOIN_CODE = "workshop-2026"
ADMIN_TOKEN = "admin-secret"


@pytest.fixture
def api_state(store, clock, sink):
    """A pre-built AppState, so route tests never run the lifespan."""
    return AppState(
        store=store,
        settings=Settings(
            db_path=store.path, join_code=JOIN_CODE, admin_token=ADMIN_TOKEN
        ),
        sink=sink,
        now_mono=clock,
    )


@pytest.fixture
def api_app(api_state):
    return create_app(api_state)


@pytest.fixture
async def client(api_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app), base_url="http://arena.test"
    ) as http:
        yield http
