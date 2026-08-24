"""Test task 13: the flag sweep (role spec §7.5)."""
from chess_core import (
    GameResult,
    RATED_TIME_CONTROL_NS,
    STARTING_RATING,
    TerminationReason,
    ms_to_ns,
)
from chess_server.engine.runner import apply_move, deliver_position
from chess_server.engine.ticker import TickerMetrics, _tick_once, step_flag
from chess_server.store.repositories import RatingHistoryRepo, SeatRepo

ONE_MS_NS = 1_000_000


async def tick(deps):
    await _tick_once(deps, TickerMetrics(), steps=[step_flag])


async def test_an_exhausted_clock_flags_and_pays_out(
    store, deps, clock, games, bot_repo, seed_bots, make_game, sink
):
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)

    clock.advance(RATED_TIME_CONTROL_NS + ONE_MS_NS)
    await tick(deps)

    after = await games.get_by_id(game.id)
    assert (after.status, after.termination) == ("finished", TerminationReason.FLAG.value)
    assert after.result == GameResult.BLACK_WIN.value
    assert after.rated == 1
    assert await SeatRepo(store.writer, store.executor).list_seated_bot_ids() == []

    history = RatingHistoryRepo(store.writer, store.executor)
    assert len(await history.list_points_for_bot(white.id)) == 1
    assert len(await history.list_points_for_bot(black.id)) == 1
    assert (await bot_repo.get_by_id(white.id)).rating < STARTING_RATING
    assert sink.of("game_ended")[-1]["termination"] == TerminationReason.FLAG.value


async def test_a_position_delivered_a_millisecond_ago_does_not_flag(
    deps, clock, games, seed_bots, make_game
):
    """Expected outcome: no change. Only meaningful paired with the test above."""
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)

    clock.advance(ONE_MS_NS)
    await tick(deps)
    assert (await games.get_by_id(game.id)).status == "active"


async def test_exactly_zero_remaining_flags(deps, clock, games, seed_bots, make_game):
    """§6.4 is `<= 0`, not `< 0`."""
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)

    clock.advance(ms_to_ns((await games.get_by_id(game.id)).white_ms))
    await tick(deps)
    assert (await games.get_by_id(game.id)).status == "finished"


async def test_an_undelivered_active_game_is_not_a_flag_candidate(
    deps, clock, games, seed_bots, make_game
):
    """Task 12's sweep owns this game; the two must never both fire."""
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)
    await apply_move(deps, game.id, 0, "e2e4")
    assert (await games.get_by_id(game.id)).delivered_to_mover == 0

    clock.advance(RATED_TIME_CONTROL_NS * 2)
    await tick(deps)
    assert (await games.get_by_id(game.id)).status == "active"
    # Asserted on the candidate set, not only the outcome: remaining_ns returns the
    # stored time when turn_started_mono is NULL, so dropping the delivery filter
    # leaves the outcome right and the sweep wrong.
    assert game.id not in [g.id for g in await games.list_delivered_active()]


async def test_black_flags_too(deps, clock, games, seed_bots, make_game):
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)
    await apply_move(deps, game.id, 0, "e2e4")
    await deliver_position(deps, black.id)

    clock.advance(RATED_TIME_CONTROL_NS + ONE_MS_NS)
    await tick(deps)

    after = await games.get_by_id(game.id)
    assert (after.status, after.termination) == ("finished", TerminationReason.FLAG.value)
    assert after.result == GameResult.WHITE_WIN.value
