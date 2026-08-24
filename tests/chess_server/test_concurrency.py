"""Role spec §11.1: a move and a flag fired at the same instant.

The definition-of-done test for the phase. The single writer serialises the two
tasks; which one wins is a scheduling fact the test pins by argument order, but
neither ordering may produce two terminal transitions, two rating rows, or a
move at a ply the game never reached.
"""
import asyncio

import pytest

from chess_core import STARTING_RATING, TerminationReason, ms_to_ns
from chess_server.engine.games import finalise_game_locked, opposite_win
from chess_server.engine.runner import apply_move, deliver_position
from chess_server.engine.ticker import TickerMetrics, _tick_once, step_flag
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import MoveRepo, RatingHistoryRepo, SeatRepo
from chess_server.store.txn import critical_section

ITERATIONS = 25
ORDERS = ("move_first", "tick_first")


async def _game_at_the_flag_boundary(deps, clock, seed_bots, make_game, games):
    """A rated, delivered game whose mover has exactly zero left: §6.4's `<= 0`
    makes both the mover's own move and the ticker's sweep live at once."""
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)
    clock.advance(ms_to_ns((await games.get_by_id(game.id)).white_ms))
    return white, black, game


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("iteration", range(ITERATIONS))
async def test_a_move_and_a_flag_at_the_same_instant_end_the_game_once(
    store, deps, clock, games, bot_repo, seed_bots, make_game, sink, order, iteration
):
    white, black, game = await _game_at_the_flag_boundary(
        deps, clock, seed_bots, make_game, games
    )
    move = apply_move(deps, game.id, 0, "e2e4")
    tick = _tick_once(deps, TickerMetrics(), steps=[step_flag])
    pair = (move, tick) if order == "move_first" else (tick, move)
    results = await asyncio.gather(*pair, return_exceptions=True)

    after = await games.get_by_id(game.id)
    assert after.status == "finished"
    assert after.termination == TerminationReason.FLAG.value
    assert after.result == opposite_win(after.to_move).value
    assert after.ended_at is not None

    # One terminal transition, however the two landed.
    assert len(sink.of("game_ended")) == 1
    assert len(sink.of("rating_changed")) == 2

    history = RatingHistoryRepo(store.writer, store.executor)
    for bot in (white, black):
        points = await history.list_points_for_bot(bot.id)
        assert len(points) == 1
        assert points[0].rating_before == STARTING_RATING
        assert (await bot_repo.get_by_id(bot.id)).rating == STARTING_RATING + points[0].delta

    assert await SeatRepo(store.writer, store.executor).list_seated_bot_ids() == []
    # The mover was already flagged, so its move was never applied: a row here
    # would be a ply the game never reached.
    assert await MoveRepo(store.writer, store.executor).list_moves_for_game(game.id) == []

    raised = [r for r in results if isinstance(r, BaseException)]
    assert all(isinstance(r, CASConflict) for r in raised)
    if order == "tick_first":
        # The mover lost: it found the game already terminal and mutated nothing.
        assert len(raised) == 1


@pytest.mark.parametrize("iteration", range(ITERATIONS))
async def test_two_moves_for_the_same_ply_leave_exactly_one(
    store, deps, games, seed_bots, make_game, sink, iteration
):
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)

    results = await asyncio.gather(
        apply_move(deps, game.id, 0, "e2e4"),
        apply_move(deps, game.id, 0, "d2d4"),
        return_exceptions=True,
    )

    raised = [r for r in results if isinstance(r, BaseException)]
    assert len(raised) == 1 and isinstance(raised[0], CASConflict)
    moves = await MoveRepo(store.writer, store.executor).list_moves_for_game(game.id)
    assert len(moves) == 1
    assert (await games.get_by_id(game.id)).ply == 1
    assert len(sink.of("move_played")) == 1


async def test_a_stale_row_cannot_terminate_an_already_terminated_game(
    store, deps, clock, games, seed_bots, make_game, sink
):
    """What the compare-and-swap is actually for: a caller holding a row it read
    before someone else's transition. The outer forms re-read under BEGIN
    IMMEDIATE and so never reach the CAS with a stale row — this does."""
    white, black, game = await _game_at_the_flag_boundary(
        deps, clock, seed_bots, make_game, games
    )
    stale = await games.get_by_id(game.id)

    await _tick_once(deps, TickerMetrics(), steps=[step_flag])
    assert (await games.get_by_id(game.id)).status == "finished"

    with pytest.raises(CASConflict):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            await finalise_game_locked(
                deps, txn, stale, opposite_win(stale.to_move), TerminationReason.FLAG
            )

    assert len(sink.of("game_ended")) == 1
    assert len(sink.of("rating_changed")) == 2
    history = RatingHistoryRepo(store.writer, store.executor)
    for bot in (white, black):
        assert len(await history.list_points_for_bot(bot.id)) == 1


async def test_a_stale_row_on_an_unrated_game_still_cannot_end_it_twice(
    store, deps, clock, games, seed_bots, make_game, sink
):
    """The same race with nothing else to catch it. Rated games are also guarded
    by UNIQUE (game_id, bot_id) on rating_history, which would mask a missing CAS
    as an IntegrityError; here the compare-and-swap is the only defence."""
    white, black = await seed_bots("white-bot", "black-bot", owner="one-owner")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)
    clock.advance(ms_to_ns((await games.get_by_id(game.id)).white_ms))
    stale = await games.get_by_id(game.id)
    assert stale.rated == 0

    await _tick_once(deps, TickerMetrics(), steps=[step_flag])

    with pytest.raises(CASConflict):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            await finalise_game_locked(
                deps, txn, stale, opposite_win(stale.to_move), TerminationReason.FLAG
            )
    assert len(sink.of("game_ended")) == 1
