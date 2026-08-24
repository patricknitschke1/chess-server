import sqlite3

import pytest

from chess_core import RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS, STARTING_RATING
from chess_server.store.repositories import BotRepo, GameRepo, MoveRepo, RatingHistoryRepo

NOW = 10_000_000_000_000
WALL = "2026-08-24T00:00:00Z"
FEN_AFTER = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


@pytest.fixture
def repos(store):
    return (
        BotRepo(store.writer, store.executor),
        GameRepo(store.writer, store.executor),
        MoveRepo(store.writer, store.executor),
        RatingHistoryRepo(store.writer, store.executor),
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


async def _game(bots, games, suffix=""):
    white = await bots.get_by_name(f"white{suffix}") or await _bot(bots, f"white{suffix}")
    black = await bots.get_by_name(f"black{suffix}") or await _bot(bots, f"black{suffix}")
    game_id = await games.insert_game(
        white=white,
        black=black,
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        source="matchmaker",
        now_mono=NOW,
        created_at=WALL,
    )
    return white, black, game_id


async def test_a_duplicate_ply_is_refused(repos):
    bots, games, moves, _ = repos
    _, _, game_id = await _game(bots, games)
    await moves.insert_move(game_id, 0, "e2e4", "e4", FEN_AFTER, 1234, None)

    with pytest.raises(sqlite3.IntegrityError):
        await moves.insert_move(game_id, 0, "d2d4", "d4", FEN_AFTER, 1234, None)


async def test_a_game_cannot_be_rated_twice_for_one_bot(repos):
    bots, games, _, ratings = repos
    white, _, game_id = await _game(bots, games)
    await ratings.insert_rating_change(white.id, game_id, STARTING_RATING, 1212, 12, WALL)

    with pytest.raises(sqlite3.IntegrityError):
        await ratings.insert_rating_change(white.id, game_id, STARTING_RATING, 1188, -12, WALL)


async def test_client_reported_ms_is_optional_and_server_elapsed_ms_is_not(repos):
    bots, games, moves, _ = repos
    _, _, game_id = await _game(bots, games)

    await moves.insert_move(game_id, 0, "e2e4", "e4", FEN_AFTER, 1234, None)
    (row,) = await moves.list_moves_for_game(game_id)
    assert row.client_reported_ms is None
    assert row.server_elapsed_ms == 1234

    with pytest.raises(sqlite3.IntegrityError):
        await moves.insert_move(game_id, 1, "e7e5", "e5", FEN_AFTER, None, 40)


async def test_moves_come_back_in_ply_order_however_they_went_in(repos):
    bots, games, moves, _ = repos
    _, _, game_id = await _game(bots, games)
    for ply in (2, 0, 1):
        await moves.insert_move(game_id, ply, "e2e4", "e4", FEN_AFTER, 1, None)

    assert [row.ply for row in await moves.list_moves_for_game(game_id)] == [0, 1, 2]


async def test_an_empty_history_sums_to_zero_not_none(repos):
    bots, _, _, ratings = repos
    bot = await _bot(bots, "lonely")

    assert await ratings.sum_deltas_by_bot(bot.id) == 0


async def test_the_delta_sum_reconstructs_the_current_rating(repos):
    bots, games, _, ratings = repos
    white, _, _ = await _game(bots, games)
    rating = STARTING_RATING
    for index, delta in enumerate((12, -8, 20)):
        _, _, game_id = await _game(bots, games, suffix=f"-{index}")
        await ratings.insert_rating_change(white.id, game_id, rating, rating + delta, delta, WALL)
        rating += delta

    assert await ratings.sum_deltas_by_bot(white.id) == rating - STARTING_RATING
    assert [row.rating_after for row in await ratings.list_points_for_bot(white.id)][-1] == rating
