"""The §6.6 rating derivation, as it lands in the database."""
import sqlite3

import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_RATING,
    GameResult,
    TerminationReason,
)
from chess_server.engine.games import create_game_locked, finalise_game_locked, rate_game_locked
from chess_server.store.cas import InvariantViolation
from chess_server.store.repositories import BotRepo, GameRepo, RatingHistoryRepo
from chess_server.store.txn import critical_section

CHECKMATE = TerminationReason.CHECKMATE


async def _pair(store, deps, white, black):
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await create_game_locked(
            deps, txn, white, black,
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=deps.now_mono(),
        )
    store.writer.execute("UPDATE games SET status = 'active' WHERE id = ?", (game_id,))
    return await GameRepo(store.writer, store.executor).get_by_id(game_id)


async def _finalise(store, deps, game, result, termination=CHECKMATE):
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await finalise_game_locked(deps, txn, game, result, termination)


async def _rows(store, bot_id):
    return await RatingHistoryRepo(store.writer, store.executor).list_points_for_bot(bot_id)


async def test_two_anchors_are_an_invariant_violation_not_a_rating_case(
    store, deps, seed_bots
):
    (a,) = await seed_bots("anchor-a", role="anchor", is_anchor=1)
    (b,) = await seed_bots("anchor-b", role="anchor", is_anchor=1)
    game = await _pair(store, deps, a, b)
    assert game.rated == 1

    with pytest.raises(InvariantViolation):
        await _finalise(store, deps, game, GameResult.WHITE_WIN)


@pytest.mark.parametrize(
    "result", [GameResult.WHITE_WIN, GameResult.BLACK_WIN, GameResult.DRAW]
)
async def test_an_unrated_game_moves_no_rating_at_all(store, deps, sink, seed_bots, result):
    a, b = await seed_bots("a", "b", owner="same")
    game = await _pair(store, deps, a, b)
    sink.events.clear()

    await _finalise(store, deps, game, result)

    assert await _rows(store, a.id) == [] and await _rows(store, b.id) == []
    assert "rating_changed" not in sink.types()


@pytest.mark.parametrize("anchor_is_white", [True, False])
async def test_only_the_competitor_is_rated_against_an_anchor(
    store, deps, sink, seed_bots, anchor_is_white
):
    (anchor,) = await seed_bots("ref", role="anchor", is_anchor=1)
    (competitor,) = await seed_bots("comp")
    white, black = (anchor, competitor) if anchor_is_white else (competitor, anchor)
    game = await _pair(store, deps, white, black)
    sink.events.clear()

    anchor_wins = GameResult.WHITE_WIN if anchor_is_white else GameResult.BLACK_WIN
    await _finalise(store, deps, game, anchor_wins)

    bots = BotRepo(store.writer, store.executor)
    (row,) = await _rows(store, competitor.id)
    assert row.delta < 0
    assert await _rows(store, anchor.id) == []
    assert (await bots.get_by_id(anchor.id)).rating == anchor.rating
    assert (await bots.get_by_id(competitor.id)).rating == row.rating_after
    assert [d["bot_id"] for d in sink.of("rating_changed")] == [competitor.id]


@pytest.mark.parametrize("anchor_rating,sign", [(1400, 1), (800, -1)])
async def test_a_draw_against_an_anchor_follows_the_rating_gap(
    store, deps, seed_bots, anchor_rating, sign
):
    (anchor,) = await seed_bots("ref", role="anchor", is_anchor=1, rating=anchor_rating)
    (competitor,) = await seed_bots("comp", rating=STARTING_RATING)
    game = await _pair(store, deps, competitor, anchor)

    await _finalise(store, deps, game, GameResult.DRAW, TerminationReason.STALEMATE)

    (row,) = await _rows(store, competitor.id)
    assert row.delta != 0
    assert (row.delta > 0) == (sign > 0)
    assert await _rows(store, anchor.id) == []


@pytest.mark.parametrize(
    "result,termination",
    [(GameResult.WHITE_WIN, CHECKMATE), (GameResult.DRAW, TerminationReason.STALEMATE)],
)
async def test_competitor_versus_competitor_is_two_rows_summing_to_zero(
    store, deps, sink, seed_bots, result, termination
):
    (a,) = await seed_bots("a", rating=1300)
    (b,) = await seed_bots("b", rating=1100)
    game = await _pair(store, deps, a, b)
    sink.events.clear()

    await _finalise(store, deps, game, result, termination)

    rows = await _rows(store, a.id) + await _rows(store, b.id)
    assert len(rows) == 2
    assert sum(row.delta for row in rows) == 0
    for row in rows:
        assert row.rating_before + row.delta == row.rating_after
    assert len(sink.of("rating_changed")) == 2


async def test_rating_the_same_game_twice_violates_the_unique_constraint(
    store, deps, seed_bots
):
    a, b = await seed_bots("a", "b")
    game = await _pair(store, deps, a, b)
    await _finalise(store, deps, game, GameResult.WHITE_WIN)

    bots = BotRepo(store.writer, store.executor)
    with pytest.raises(sqlite3.IntegrityError):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            await rate_game_locked(
                txn, game,
                await bots.get_by_id(a.id), await bots.get_by_id(b.id),
                GameResult.WHITE_WIN,
            )


async def test_every_competitor_rating_equals_its_delta_sum(store, deps, seed_bots):
    """The §8.5 identity, asserted here rather than waiting for /admin/consistency."""
    (anchor,) = await seed_bots("ref", role="anchor", is_anchor=1, rating=1000)
    competitors = []
    for index in range(4):
        (bot,) = await seed_bots(f"c{index}")
        competitors.append(bot)

    schedule = [
        (0, 1, GameResult.WHITE_WIN), (1, 2, GameResult.DRAW),
        (2, 3, GameResult.BLACK_WIN), (3, 0, GameResult.WHITE_WIN),
        (0, 2, GameResult.DRAW), (1, 3, GameResult.BLACK_WIN),
        (2, 0, GameResult.WHITE_WIN), (3, 1, GameResult.DRAW),
    ]
    bots = BotRepo(store.writer, store.executor)
    for white_index, black_index, result in schedule:
        white = await bots.get_by_id(competitors[white_index].id)
        black = await bots.get_by_id(competitors[black_index].id)
        game = await _pair(store, deps, white, black)
        await _finalise(store, deps, game, result,
                        CHECKMATE if result != GameResult.DRAW
                        else TerminationReason.THREEFOLD)
    for index, result in ((0, GameResult.WHITE_WIN), (1, GameResult.DRAW)):
        game = await _pair(store, deps, await bots.get_by_id(competitors[index].id),
                           await bots.get_by_id(anchor.id))
        await _finalise(store, deps, game, result,
                        CHECKMATE if result != GameResult.DRAW
                        else TerminationReason.THREEFOLD)

    history = RatingHistoryRepo(store.writer, store.executor)
    for bot in competitors:
        current = (await bots.get_by_id(bot.id)).rating
        assert current == STARTING_RATING + await history.sum_deltas_by_bot(bot.id)
    assert (await bots.get_by_id(anchor.id)).rating == 1000
    assert await history.sum_deltas_by_bot(anchor.id) == 0
