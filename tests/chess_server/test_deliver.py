"""Idempotent delivery and the pending -> active transition (role spec §5.2)."""
from chess_core import RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS, TerminationReason
from chess_server.engine.games import abort_game_locked, create_game_locked
from chess_server.engine.runner import deliver_position, deliver_position_locked
from chess_server.store.repositories import GameRepo
from chess_server.store.txn import critical_section

FIVE_SECONDS_NS = 5_000_000_000


async def _seated_game(store, deps, seed_bots):
    white, black = await seed_bots("white-bot", "black-bot")
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await create_game_locked(
            deps, txn, white, black,
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=deps.now_mono(),
        )
    return white, black, await GameRepo(store.writer, store.executor).get_by_id(game_id)


async def test_redelivery_never_restarts_the_clock(store, deps, clock, sink, seed_bots):
    """Otherwise a bot re-polls while thinking and resets its own clock."""
    white, _, game = await _seated_game(store, deps, seed_bots)
    await deliver_position(deps, white.id)
    first = (await GameRepo(store.writer, store.executor).get_by_id(game.id))
    sink.events.clear()

    clock.advance(FIVE_SECONDS_NS)
    await deliver_position(deps, white.id)

    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    assert after.turn_started_mono == first.turn_started_mono
    assert after.started_at == first.started_at
    assert sink.types() == []


async def test_delivery_on_a_terminal_game_is_a_silent_no_op(store, deps, sink, seed_bots):
    _, _, game = await _seated_game(store, deps, seed_bots)
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await abort_game_locked(deps, txn, game, TerminationReason.ADMIN_ABORT)
    stale = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    sink.events.clear()

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        delivered = await deliver_position_locked(deps, txn, stale, deps.now_mono())

    assert delivered is False
    assert sink.events == []
    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    assert (after.status, after.delivered_to_mover, after.turn_started_mono) == (
        "aborted", 0, None)


async def test_first_delivery_activates_the_game_in_one_statement(
    store, deps, clock, sink, seed_bots
):
    white, black, game = await _seated_game(store, deps, seed_bots)
    sink.events.clear()

    await deliver_position(deps, white.id)

    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    # Together, because a split implementation leaves delivered_to_mover=1 on 'pending'.
    assert (after.status, after.delivered_to_mover) == ("active", 1)
    assert after.turn_started_mono == clock()
    assert after.started_at is not None
    assert sink.of("game_started") == [{
        "game_id": game.id,
        "white_bot_id": white.id,
        "white_bot_name": "white-bot",
        "black_bot_id": black.id,
        "black_bot_name": "black-bot",
        "started_at": after.started_at,
    }]


async def test_delivery_to_a_bot_with_no_seat_reports_no_game(store, deps, sink, seed_bots):
    (loner,) = await seed_bots("loner")

    assert await deliver_position(deps, loner.id) is None
    assert sink.events == []
