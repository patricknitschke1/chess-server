"""Harness for the SDK.

The SDK is synchronous, because attendees write synchronous `choose_move`. The
server is ASGI. `SyncASGITransport` bridges the two by owning one event loop on
one thread, so the tests exercise the real routes rather than a hand-rolled
mock of them.
"""
import asyncio
import sys
import threading
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "chess-bot-starter-kit"))

from chess_core import RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS  # noqa: E402
from chess_server.api.app import create_app  # noqa: E402
from chess_server.api.auth import hash_token  # noqa: E402
from chess_server.api.settings import Settings  # noqa: E402
from chess_server.api.state import AppState  # noqa: E402
from chess_server.engine import state as engine_state  # noqa: E402
from chess_server.engine.games import create_game_locked  # noqa: E402
from chess_server.store import txn as txn_module  # noqa: E402
from chess_server.store.db import open_store  # noqa: E402
from chess_server.store.repositories import BotRepo  # noqa: E402
from chess_server.store.txn import critical_section, reset_seq  # noqa: E402

JOIN_CODE = "workshop-2026"
ADMIN_TOKEN = "admin-secret"
WALL = "2026-08-25T00:00:00Z"
START_MONO = 1_000_000_000_000


class LoopThread:
    """One event loop on one background thread, driven from the test thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=30)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


class SyncASGITransport(httpx.BaseTransport):
    def __init__(self, app, loop_thread: LoopThread):
        self._inner = httpx.ASGITransport(app=app)
        self._loop_thread = loop_thread

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        content = request.read()
        outgoing = httpx.Request(
            request.method, request.url, headers=request.headers, content=content
        )
        response = self._loop_thread.run(self._send(outgoing))
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    async def _send(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        await response.aread()
        return response


class FakeClock:
    def __init__(self, value: int = START_MONO):
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, ns: int) -> int:
        self.value += ns
        return self.value


class RecordingSink:
    def __init__(self):
        self.events = []

    def __call__(self, seq: int, event_type: str, data: dict) -> None:
        self.events.append((seq, event_type, data))


@pytest.fixture(autouse=True)
def _fresh_globals(monkeypatch):
    monkeypatch.setattr(txn_module, "write_lock", asyncio.Lock())
    reset_seq()
    for container in (
        engine_state.mailbox,
        engine_state.history,
        engine_state.history_san,
        engine_state.unpaired_ticks,
        engine_state.connected,
    ):
        container.clear()


@pytest.fixture
def loop_thread():
    lt = LoopThread()
    try:
        yield lt
    finally:
        lt.stop()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(tmp_path):
    """File-backed, never ':memory:' — two ':memory:' connections are two databases."""
    s = open_store(str(tmp_path / "arena.db"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def api_state(store, clock):
    return AppState(
        store=store,
        settings=Settings(
            db_path=store.path,
            join_code=JOIN_CODE,
            admin_token=ADMIN_TOKEN,
            # The hold length is the server's business; tests should not sit through it.
            poll_hold_seconds=0.05,
        ),
        sink=RecordingSink(),
        now_mono=clock,
    )


@pytest.fixture
def transport(api_state, loop_thread):
    return SyncASGITransport(create_app(api_state), loop_thread)


@pytest.fixture
def seed_bot(store, loop_thread):
    """Insert a bot with a known plaintext token, through the real repository."""

    def _seed(name: str, token: str, *, owner: str = "tester"):
        async def _insert():
            bots = BotRepo(store.writer, store.executor)
            async with critical_section(store.writer, store.executor):
                bot_id = await bots.insert_bot(
                    name=name,
                    owner=owner,
                    token_hash=hash_token(token),
                    role="competitor",
                    rating=1200,
                    is_anchor=0,
                    created_at=WALL,
                )
            return await bots.get_by_id(bot_id)

        return loop_thread.run(_insert())

    return _seed


@pytest.fixture
def make_game(store, api_state, loop_thread, clock):
    def _make(white, black):
        async def _create():
            async with critical_section(
                store.writer, store.executor, api_state.deps.sink
            ) as txn:
                return await create_game_locked(
                    api_state.deps,
                    txn,
                    white,
                    black,
                    time_control_ns=RATED_TIME_CONTROL_NS,
                    increment_ns=RATED_INCREMENT_NS,
                    source="matchmaker",
                    now_mono=clock(),
                )

        return loop_thread.run(_create())

    return _make


class ScriptedTransport(httpx.BaseTransport):
    """Returns canned responses in order and records every request.

    Used only for the wire faults the real server cannot be made to produce on
    demand (network drop, 5xx, rate limits).
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if not self._responses:
            raise AssertionError(f"Unscripted request: {request.method} {request.url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        nxt.request = request
        return nxt
