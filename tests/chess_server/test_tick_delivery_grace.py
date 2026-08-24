"""Test task 12: the delivery-grace sweep (role spec §7.4, §11.3)."""
import pytest

from chess_core import (
    AGENT_DELIVERY_GRACE_NS,
    DELIVERY_GRACE_NS,
    GameResult,
    STARTING_RATING,
    TerminationReason,
)
from chess_server.engine import games as games_module
from chess_server.engine.games import abort_game_locked
from chess_server.engine.runner import apply_move, deliver_position
from chess_server.engine.ticker import TickerMetrics, _tick_once, step_delivery_grace
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import RatingHistoryRepo, SeatRepo
from chess_server.store.txn import critical_section

ONE_MS_NS = 1_000_000
THIRTY_SECONDS_NS = 30_000_000_000


async def tick(deps, metrics=None, steps=(step_delivery_grace,)):
    await _tick_once(deps, metrics or TickerMetrics(), steps=list(steps))


async def test_the_sweep_never_returns_a_finished_game_and_keeps_working(
    store, deps, clock, games, seed_bots, make_game
):
    """§11.3. The wedge this guards is invisible: the CAS just returns rowcount 0."""
    white, black, other = await seed_bots("white-bot", "black-bot", "third-bot")
    done = await make_game(white, black)
    await deliver_position(deps, white.id)
    await apply_move(deps, done.id, 0, "e2e4")
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await abort_game_locked(
            deps, txn, await games.get_by_id(done.id), TerminationReason.ADMIN_ABORT
        )
    finished = await games.get_by_id(done.id)

    metrics = TickerMetrics()
    live = await make_game(other, (await seed_bots("fourth-bot"))[0])
    for _ in range(20):
        assert done.id not in [g.id for g in await games.list_undelivered_non_terminal()]
        await tick(deps, metrics)

    assert metrics.consecutive_tick_errors == 0
    assert (await games.get_by_id(done.id)) == finished     # nothing re-touched it
    clock.advance(DELIVERY_GRACE_NS + ONE_MS_NS)
    await tick(deps, metrics)                                # and the sweep still works
    assert (await games.get_by_id(live.id)).status == "aborted"


async def test_no_show_at_ply_zero_is_unrated_and_frees_both_seats(
    store, deps, clock, games, bot_repo, seed_bots, make_game, sink
):
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    clock.advance(DELIVERY_GRACE_NS + ONE_MS_NS)
    await tick(deps)

    after = await games.get_by_id(game.id)
    assert (after.status, after.termination, after.rated) == (
        "aborted", TerminationReason.NO_SHOW.value, 0
    )
    assert after.result is None
    assert await SeatRepo(store.writer, store.executor).list_seated_bot_ids() == []
    history = RatingHistoryRepo(store.writer, store.executor)
    for bot in (white, black):
        assert await history.sum_deltas_by_bot(bot.id) == 0
        assert (await bot_repo.get_by_id(bot.id)).rating == STARTING_RATING
    assert sink.of("game_ended")[-1]["termination"] == TerminationReason.NO_SHOW.value


async def test_mid_game_undelivered_is_abandonment_and_is_rated(
    store, deps, clock, games, seed_bots, make_game
):
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)
    await apply_move(deps, game.id, 0, "e2e4")   # black is now to move, undelivered

    clock.advance(DELIVERY_GRACE_NS + ONE_MS_NS)
    await tick(deps)

    after = await games.get_by_id(game.id)
    assert (after.status, after.termination) == ("finished", TerminationReason.ABANDONED.value)
    assert after.result == GameResult.WHITE_WIN.value
    assert after.rated == 1
    history = RatingHistoryRepo(store.writer, store.executor)
    assert len(await history.list_points_for_bot(white.id)) == 1
    assert len(await history.list_points_for_bot(black.id)) == 1


@pytest.mark.parametrize(
    "advance,expected", [(THIRTY_SECONDS_NS, "pending"), (AGENT_DELIVERY_GRACE_NS + ONE_MS_NS, "aborted")]
)
async def test_an_agent_controlled_mover_gets_the_longer_grace(
    store, deps, clock, games, bot_repo, seed_bots, make_game, advance, expected
):
    """30 s is the half a hard-coded DELIVERY_GRACE_NS fails."""
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    async with critical_section(store.writer, store.executor):
        await bot_repo.update_controller(white.id, "agent")

    clock.advance(advance)
    await tick(deps)
    assert (await games.get_by_id(game.id)).status == expected


async def test_a_delivered_position_is_never_swept(
    deps, clock, games, seed_bots, make_game
):
    """Abandonment applies only while delivered_to_mover = 0, so it cannot race the clock."""
    white, black = await seed_bots("white-bot", "black-bot")
    game = await make_game(white, black)
    await deliver_position(deps, white.id)

    clock.advance(DELIVERY_GRACE_NS * 10)
    await tick(deps)
    assert (await games.get_by_id(game.id)).status == "active"


async def test_one_conflicted_game_does_not_stop_the_others(
    deps, clock, games, seed_bots, make_game, monkeypatch
):
    a1, a2, b1, b2 = await seed_bots("a1", "a2", "b1", "b2")
    first = await make_game(a1, a2)
    second = await make_game(b1, b2)

    real = games_module.abort_game_locked

    async def flaky(deps_, txn, game, termination):
        if game.id == first.id:
            raise CASConflict("someone else got there")
        return await real(deps_, txn, game, termination)

    monkeypatch.setattr("chess_server.engine.ticker.abort_game_locked", flaky)
    clock.advance(DELIVERY_GRACE_NS + ONE_MS_NS)
    metrics = TickerMetrics()
    await tick(deps, metrics)

    assert (await games.get_by_id(first.id)).status == "pending"
    assert (await games.get_by_id(second.id)).status == "aborted"
    assert metrics.consecutive_tick_errors == 0
