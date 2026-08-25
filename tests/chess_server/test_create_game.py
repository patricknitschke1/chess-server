"""Game creation, its two seats, and the work deferred past the commit (role spec §7.2)."""
import sqlite3

import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_FEN,
    ns_to_ms,
)
from chess_server.engine import state
from chess_server.engine.games import create_game_locked
from chess_server.store.repositories import GameRepo, SeatRepo
from chess_server.store.txn import critical_section


async def _create(deps, txn, white, black, *, time_control_ns=RATED_TIME_CONTROL_NS,
                  increment_ns=RATED_INCREMENT_NS, source="matchmaker"):
    return await create_game_locked(
        deps, txn, white, black,
        time_control_ns=time_control_ns,
        increment_ns=increment_ns,
        source=source,
        now_mono=deps.now_mono(),
    )


def _games(store):
    return GameRepo(store.writer, store.executor)


async def test_a_seat_collision_rolls_back_only_its_own_pairing(store, deps, seed_bots):
    a, b, c, d = await seed_bots("a", "b", "c", "d")

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        async with txn.savepoint("sp1"):
            first = await _create(deps, txn, a, b)
        with pytest.raises(sqlite3.IntegrityError):
            async with txn.savepoint("sp2"):
                await _create(deps, txn, b, c)
        async with txn.savepoint("sp3"):
            third = await _create(deps, txn, c, d)

    seats = SeatRepo(store.writer, store.executor)
    assert sorted(await seats.list_seated_bot_ids()) == sorted([a.id, b.id, c.id, d.id])
    rows = store.reader.execute("SELECT id FROM games ORDER BY id").fetchall()
    assert [row["id"] for row in rows] == [first, third]


async def test_a_rolled_back_creation_emits_nothing_and_consumes_no_seq(
    store, deps, sink, seed_bots
):
    a, b, c, d = await seed_bots("a", "b", "c", "d")

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        with pytest.raises(sqlite3.IntegrityError):
            async with txn.savepoint("sp1"):
                await _create(deps, txn, a, b)
                await _create(deps, txn, a, c)   # a is already seated
        async with txn.savepoint("sp2"):
            survivor = await _create(deps, txn, c, d)

    assert [(seq, name) for seq, name, _ in sink.events] == [(0, "game_created")]
    assert sink.of("game_created")[0]["game_id"] == survivor


async def test_a_new_game_is_pending_unstarted_and_seated(store, deps, seed_bots):
    a, b = await seed_bots("a", "b")

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await _create(deps, txn, a, b)

    game = await _games(store).get_by_id(game_id)
    seats = SeatRepo(store.writer, store.executor)
    assert (game.status, game.ply, game.to_move) == ("pending", 0, "white")
    assert (game.delivered_to_mover, game.turn_started_mono) == (0, None)
    assert game.fen == STARTING_FEN
    assert sorted(await seats.list_seated_bot_ids()) == sorted([a.id, b.id])


async def test_game_created_carries_the_whole_payload(store, deps, sink, seed_bots):
    a, b = await seed_bots("a", "b")

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await _create(deps, txn, a, b)

    assert sink.of("game_created") == [{
        "game_id": game_id,
        "white_bot_id": a.id,
        "white_bot_name": "a",
        "black_bot_id": b.id,
        "black_bot_name": "b",
        "status": "pending",
        "rated": True,
        "source": "matchmaker",
        "time_control_ms": ns_to_ms(RATED_TIME_CONTROL_NS),
        "increment_ms": ns_to_ms(RATED_INCREMENT_NS),
    }]


async def test_both_clocks_start_at_three_minutes_in_milliseconds(store, deps, seed_bots):
    """Stated twice on purpose: a game with 5.7 years on the clock satisfies
    every other assertion in this file (role spec §11.8)."""
    a, b = await seed_bots("a", "b")

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await _create(deps, txn, a, b)

    game = await _games(store).get_by_id(game_id)
    assert game.white_ms == game.black_ms == ns_to_ms(RATED_TIME_CONTROL_NS)
    assert game.white_ms == 180_000
    assert game.time_control_ms == 180_000
    assert game.increment_ms == ns_to_ms(RATED_INCREMENT_NS)


@pytest.mark.parametrize(
    "names,role,owner,expected",
    [
        (("a", "b"), "competitor", None, 1),
        (("a", "b"), "competitor", "same", 0),
    ],
)
async def test_rated_is_settled_at_creation(
    store, deps, seed_bots, names, role, owner, expected
):
    a, b = await seed_bots(*names, role=role, owner=owner)

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await _create(deps, txn, a, b)

    assert (await _games(store).get_by_id(game_id)).rated == expected


async def test_the_history_seed_and_the_wakes_wait_for_the_commit(
    store, deps, wake, seed_bots
):
    a, b = await seed_bots("a", "b")

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await _create(deps, txn, a, b)
        assert state.history == {}
        assert wake.woken == []

    assert state.history[game_id] == [STARTING_FEN]
    assert sorted(wake.woken) == sorted([a.id, b.id])
