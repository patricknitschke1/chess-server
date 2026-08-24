"""Startup and shutdown (role spec §8.6). The lifespan is exercised only here."""
import asyncio
import contextlib

import pytest

from chess_core import RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS, TerminationReason
from chess_server.api import app as app_module
from chess_server.api.settings import Settings
from chess_server.engine.games import create_game_locked
from chess_server.store.repositories import BotRepo, GameRepo, SeatRepo
from chess_server.store.txn import critical_section

WALL = "2026-08-24T00:00:00Z"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / "arena.db"), join_code="join", admin_token="admin"
    )


@pytest.fixture
def app_state(settings):
    built = app_module.build_state(settings)
    try:
        yield built
    finally:
        with contextlib.suppress(Exception):
            built.store.close()


def _idle_starter(attr, log=None, label=None):
    """A stand-in for the real ticker: it parks forever and records that it began."""

    def _start(app_state):
        async def _idle():
            await asyncio.Event().wait()

        task = asyncio.create_task(_idle(), name=label or attr)
        setattr(app_state, attr, task)
        if log is not None:
            log.append(label)
        return task

    return _start


@pytest.fixture
def inert_tasks(monkeypatch):
    monkeypatch.setattr(app_module, "start_ticker", _idle_starter("ticker_task"))
    monkeypatch.setattr(app_module, "start_supervisor", _idle_starter("supervisor_task"))


async def _seat_a_pending_game(app_state):
    bots = BotRepo(app_state.store.writer, app_state.store.executor)
    async with critical_section(app_state.store.writer, app_state.store.executor) as txn:
        made = [
            await bots.get_by_id(
                await bots.insert_bot(
                    name=name, owner=name, token_hash=f"hash-{name}",
                    role="competitor", rating=1200, is_anchor=0, created_at=WALL,
                )
            )
            for name in ("alpha", "beta")
        ]
        return await create_game_locked(
            app_state.deps, txn, made[0], made[1],
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=app_state.deps.now_mono(),
        )


async def test_startup_aborts_a_live_game_and_frees_its_seats(app_state, inert_tasks):
    game_id = await _seat_a_pending_game(app_state)
    app = app_module.create_app(app_state)

    async with app.router.lifespan_context(app):
        game = await GameRepo(app_state.store.writer, app_state.store.executor).get_by_id(
            game_id
        )
        seats = SeatRepo(app_state.store.writer, app_state.store.executor)
        assert game.status == "aborted"
        assert game.termination == TerminationReason.SERVER_RESTART.value
        assert await seats.list_seated_bot_ids() == []


async def test_startup_seeds_then_recovers_then_starts_the_two_tasks(
    app_state, monkeypatch
):
    """Recovery must be the last write before the socket accepts: a ticker started
    above it can pair a bot into a game recovery is about to abort."""
    log: list[str] = []

    async def _seed(conn, executor):
        log.append("seed")

    async def _recover(conn, executor, now_wall, clear_process_state, sink):
        log.append("recover")

    monkeypatch.setattr(app_module, "seed_anchors", _seed)
    monkeypatch.setattr(app_module, "recover", _recover)
    monkeypatch.setattr(
        app_module, "start_ticker", _idle_starter("ticker_task", log, "ticker")
    )
    monkeypatch.setattr(
        app_module, "start_supervisor", _idle_starter("supervisor_task", log, "supervisor")
    )
    app = app_module.create_app(app_state)

    async with app.router.lifespan_context(app):
        pass

    assert log == ["seed", "recover", "ticker", "supervisor"]


async def test_shutdown_cancels_both_tasks_and_closes_the_store(app_state, inert_tasks):
    app = app_module.create_app(app_state)

    async with app.router.lifespan_context(app):
        ticker, supervisor = app_state.ticker_task, app_state.supervisor_task
        assert not ticker.done() and not supervisor.done()

    assert ticker.cancelled() and supervisor.cancelled()
    with pytest.raises(RuntimeError):
        app_state.store.executor.submit(int)


@pytest.mark.parametrize("field", ["join_code", "admin_token"])
def test_settings_refuse_to_construct_with_an_empty_secret(tmp_path, field):
    values = {"db_path": str(tmp_path / "a.db"), "join_code": "join", "admin_token": "admin"}
    values[field] = ""
    with pytest.raises(ValueError):
        Settings(**values)
