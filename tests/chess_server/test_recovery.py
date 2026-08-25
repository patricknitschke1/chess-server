import asyncio
from concurrent.futures import Executor

import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_RATING,
    TerminationReason,
)
from chess_server.engine import state
from chess_server.store.recovery import recover, recover_locked
from chess_server.store.repositories import BotRepo, GameRepo, SeatRepo
from chess_server.store.run import current_run_id
from chess_server.store.txn import critical_section, reset_seq

OLD_MONO = 900_000_000_000_000  # a baseline from a process that no longer exists
WALL = "2026-08-24T08:00:00Z"
RESTART_WALL = "2026-08-24T09:00:00Z"


@pytest.fixture(autouse=True)
def _fresh_seq():
    reset_seq()


class _RecordingExecutor(Executor):
    """Counts BEGINs so 'one critical section' is asserted, not assumed."""

    def __init__(self, inner):
        self.inner = inner
        self.statements = []

    def submit(self, fn, /, *args, **kwargs):
        if args and isinstance(args[0], str):
            self.statements.append(args[0])
        return self.inner.submit(fn, *args, **kwargs)


class _Sink:
    def __init__(self, conn):
        self.conn = conn
        self.received = []

    def __call__(self, seq, event_type, data):
        self.received.append((seq, event_type, data, self.conn.in_transaction))


@pytest.fixture
def repos(store):
    return (
        BotRepo(store.writer, store.executor),
        GameRepo(store.writer, store.executor),
        SeatRepo(store.writer, store.executor),
    )


async def _dirty_database(repos):
    bots, games, seats = repos
    made = {}
    for name in ("a", "b", "c", "d", "agent_bot"):
        bot_id = await bots.insert_bot(
            name=name,
            owner=name,
            token_hash=f"hash-{name}",
            role="competitor",
            rating=STARTING_RATING,
            is_anchor=0,
            created_at=WALL,
            controller="agent" if name == "agent_bot" else "client",
        )
        await bots.update_last_poll(bot_id, WALL, OLD_MONO)
        await bots.update_last_agent_action(bot_id, OLD_MONO)
        made[name] = await bots.get_by_id(bot_id)

    game_ids = {}
    for label, (white, black) in {
        "pending": ("a", "b"),
        "active": ("c", "d"),
        "finished": ("a", "c"),
    }.items():
        game_ids[label] = await games.insert_game(
            white=made[white],
            black=made[black],
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=OLD_MONO,
            created_at=WALL,
        )
    await games.cas_deliver(game_ids["active"], ply=0, now_mono=OLD_MONO, now_wall=WALL)
    await games.cas_terminate(
        game_ids["finished"], "pending", 0, "finished", "white_win", "checkmate", WALL
    )
    for label in ("pending", "active"):
        for name in ({"pending": ("a", "b"), "active": ("c", "d")}[label]):
            await seats.insert_seat(made[name].id, game_ids[label])

    return made, game_ids


async def _recover(store, sink=None, cleared=None):
    return await recover(
        store.writer,
        store.executor,
        now_wall=RESTART_WALL,
        clear_process_state=(lambda: None) if cleared is None else cleared,
        sink=sink or (lambda *args: None),
    )


@pytest.fixture
async def recovered(store, repos):
    seeded = await _dirty_database(repos)
    sink = _Sink(store.writer)
    cleared = []
    previous_run = current_run_id()
    report = await _recover(store, sink, lambda: cleared.append(store.writer.in_transaction))
    return seeded, report, sink, cleared, previous_run


async def test_live_games_are_aborted_unrated_with_an_end_time(repos, recovered):
    _, games, _ = repos
    (_, game_ids), _, _, _, _ = recovered

    for label in ("pending", "active"):
        row = await games.get_by_id(game_ids[label])
        assert row.status == "aborted"
        assert row.termination == TerminationReason.SERVER_RESTART.value
        assert row.rated == 0
        assert row.ended_at == RESTART_WALL


async def test_a_finished_game_is_left_exactly_as_it_was(store, repos):
    _, games, _ = repos
    _, game_ids = await _dirty_database(repos)
    before = dict(await games._one("SELECT * FROM games WHERE id = ?", (game_ids["finished"],)))

    await _recover(store)

    after = dict(await games._one("SELECT * FROM games WHERE id = ?", (game_ids["finished"],)))
    assert after == before


async def test_every_seat_is_freed(repos, recovered):
    _, _, seats = repos
    assert await seats.list_seated_bot_ids() == []


async def test_last_poll_mono_is_null_for_every_bot(store, recovered):
    rows = store.reader.execute("SELECT last_poll_mono FROM bots").fetchall()
    assert rows and all(row["last_poll_mono"] is None for row in rows)


async def test_last_agent_action_mono_is_null_for_every_bot(store, recovered):
    rows = store.reader.execute("SELECT last_agent_action_mono FROM bots").fetchall()
    assert rows and all(row["last_agent_action_mono"] is None for row in rows)


async def test_control_returns_to_the_client_for_every_bot(store, recovered):
    rows = store.reader.execute("SELECT controller FROM bots").fetchall()
    assert rows and all(row["controller"] == "client" for row in rows)


async def test_a_new_run_starts_and_is_announced_after_the_commit(recovered):
    _, report, sink, _, previous_run = recovered

    assert report.run != previous_run
    assert report.run == current_run_id()
    assert sink.received == [
        (0, "server_run_started", {"run_id": report.run, "started_at": RESTART_WALL}, False)
    ]


async def test_process_state_is_cleared_once_and_only_after_the_commit(recovered):
    _, _, _, cleared, _ = recovered

    assert cleared == [False]


async def test_a_second_recovery_is_a_no_op(store, repos, recovered):
    _, first, _, _, _ = recovered

    second = await _recover(store)

    assert first.games_aborted == 2
    assert second.games_aborted == 0
    assert second.run != first.run


async def test_a_lower_monotonic_baseline_cannot_resurrect_the_whole_pool(
    store, repos, recovered
):
    """Recovery nulled the column, so no comparison against a dead baseline happens."""
    bots, _, _ = repos
    seated = store.reader.execute(
        "SELECT COUNT(*) AS n FROM bots WHERE last_poll_mono IS NOT NULL"
    ).fetchone()

    candidates = await bots.list_pool_candidates(cutoff_mono=1)

    assert seated["n"] == 0
    assert candidates == []


async def test_recovery_opens_exactly_one_transaction(store, repos):
    await _dirty_database(repos)
    recorder = _RecordingExecutor(store.executor)

    await recover(
        store.writer,
        recorder,
        now_wall=RESTART_WALL,
        clear_process_state=lambda: None,
        sink=lambda *args: None,
    )

    assert [s for s in recorder.statements if s.startswith("BEGIN")] == ["BEGIN IMMEDIATE"]
    assert [s for s in recorder.statements if s == "COMMIT"] == ["COMMIT"]


class _Boom(Exception):
    pass


def _populate_engine_state():
    state.mailbox[1] = "payload"
    state.history[1] = ["fen"]
    state.unpaired_ticks[1] = 3
    state.connected.add(1)


def _engine_state_sizes():
    return [len(state.mailbox), len(state.history), len(state.unpaired_ticks),
            len(state.connected)]


async def test_recovery_clears_every_engine_container(store, repos):
    await _dirty_database(repos)
    _populate_engine_state()

    await _recover(store, cleared=state.clear_all)

    assert _engine_state_sizes() == [0, 0, 0, 0]


async def test_a_rolled_back_recovery_leaves_engine_state_alone(store, repos):
    """The clear is deferred past the commit, so a failed recovery must not wipe
    in-process state that the database still matches."""
    await _dirty_database(repos)
    _populate_engine_state()

    with pytest.raises(_Boom):
        async with critical_section(store.writer, store.executor) as txn:
            await recover_locked(txn, RESTART_WALL, state.clear_all)
            raise _Boom

    assert _engine_state_sizes() == [1, 1, 1, 1]
