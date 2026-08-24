import sqlite3

import pytest

from chess_core import RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS, STARTING_RATING
from chess_server.store.repositories import BotRepo, GameRepo, SeatRepo
from chess_server.store.txn import critical_section, reset_seq

NOW = 10_000_000_000_000
WALL = "2026-08-24T00:00:00Z"


@pytest.fixture(autouse=True)
def _fresh_seq():
    reset_seq()


@pytest.fixture
def repos(store):
    return (
        BotRepo(store.writer, store.executor),
        GameRepo(store.writer, store.executor),
        SeatRepo(store.writer, store.executor),
    )


async def _bot(bots, name):
    bot_id = await bots.insert_bot(
        name=name,
        owner=name,
        token_hash=f"hash-{name}",
        role="competitor",
        rating=STARTING_RATING,
        is_anchor=0,
        created_at=WALL,
    )
    return await bots.get_by_id(bot_id)


async def _pair(games, seats, white, black):
    game_id = await games.insert_game(
        white=white,
        black=black,
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        source="matchmaker",
        now_mono=NOW,
        created_at=WALL,
    )
    await seats.insert_seat(white.id, game_id)
    await seats.insert_seat(black.id, game_id)
    return game_id


async def test_a_bot_cannot_hold_two_seats(repos):
    bots, games, seats = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")
    game_id = await _pair(games, seats, white, black)

    with pytest.raises(sqlite3.IntegrityError):
        await seats.insert_seat(white.id, game_id)


async def test_a_seat_for_an_unknown_bot_is_refused(repos):
    bots, games, seats = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")
    game_id = await _pair(games, seats, white, black)

    with pytest.raises(sqlite3.IntegrityError):
        await seats.insert_seat(9999, game_id)


async def test_a_seat_collision_rolls_back_only_its_own_pairing(store, repos):
    """Without the savepoint this leaves an orphan game and a stray seat: a
    statement-level IntegrityError aborts the statement, not the transaction."""
    bots, games, seats = repos
    a_white = await _bot(bots, "a_white")
    a_black = await _bot(bots, "a_black")
    b_white = await _bot(bots, "b_white")
    received = []

    async with critical_section(
        store.writer, store.executor, lambda seq, kind, data: received.append(kind)
    ) as txn:
        async with txn.savepoint("pairing_a"):
            game_a = await _pair(games, seats, a_white, a_black)
            txn.emit("game_created", {"game_id": game_a})
        with pytest.raises(sqlite3.IntegrityError):
            async with txn.savepoint("pairing_b"):
                await _pair(games, seats, b_white, a_black)  # a_black is already seated
                txn.emit("game_created", {"game_id": -1})

    assert [row.id for row in await games.list_undelivered_non_terminal()] == [game_a]
    assert await seats.list_seated_bot_ids() == [a_white.id, a_black.id]
    assert (await seats.get_seat(b_white.id)) is None
    assert received == ["game_created"]


async def test_delete_seats_for_game_frees_both_and_is_a_no_op_when_repeated(repos):
    bots, games, seats = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")
    game_id = await _pair(games, seats, white, black)
    assert len(await seats.list_seated_bot_ids()) == 2

    await seats.delete_seats_for_game(game_id)
    after_first = await seats.list_seated_bot_ids()
    await seats.delete_seats_for_game(game_id)

    assert after_first == []
    assert await seats.list_seated_bot_ids() == []
