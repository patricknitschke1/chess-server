"""Terminal transitions (role spec §6.5). Rating is task 5 and is stubbed here."""
import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    Color,
    GameResult,
    TerminationReason,
)
from chess_server.engine import state
from chess_server.engine.games import abort_game_locked, create_game_locked, finalise_game_locked
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import BotRepo, GameRepo, SeatRepo
from chess_server.store.txn import critical_section

CHECKMATE = TerminationReason.CHECKMATE


async def _seed_game(store, deps, seed_bots, *, owner=None, active=True, ply=0):
    white, black = await seed_bots("white-bot", "black-bot", owner=owner)
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await create_game_locked(
            deps, txn, white, black,
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=deps.now_mono(),
        )
    if active or ply:
        store.writer.execute(
            "UPDATE games SET status = ?, ply = ? WHERE id = ?",
            ("active" if active else "pending", ply, game_id),
        )
    games = GameRepo(store.writer, store.executor)
    return white, black, await games.get_by_id(game_id)


async def _finalise(store, deps, game, result=GameResult.WHITE_WIN, termination=CHECKMATE):
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await finalise_game_locked(deps, txn, game, result, termination)


async def test_finalising_a_game_whose_ply_moved_conflicts_and_changes_nothing(
    store, deps, sink, seed_bots
):
    white, black, game = await _seed_game(store, deps, seed_bots)
    store.writer.execute("UPDATE games SET ply = ply + 1 WHERE id = ?", (game.id,))
    sink.events.clear()

    with pytest.raises(CASConflict):
        await _finalise(store, deps, game)

    bots = BotRepo(store.writer, store.executor)
    seats = SeatRepo(store.writer, store.executor)
    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    assert after.status == "active" and after.ended_at is None
    assert sorted(await seats.list_seated_bot_ids()) == sorted([white.id, black.id])
    assert (await bots.get_by_id(white.id)).games_played == 0
    assert sink.events == []


async def test_a_second_finalisation_conflicts_and_leaves_one_game_ended(
    store, deps, sink, seed_bots
):
    _, _, game = await _seed_game(store, deps, seed_bots)

    await _finalise(store, deps, game)
    with pytest.raises(CASConflict):
        await _finalise(store, deps, game)

    assert sink.types().count("game_ended") == 1


@pytest.mark.parametrize(
    "termination,expected",
    [
        (TerminationReason.NO_SHOW, 0),
        (TerminationReason.SERVER_RESTART, 0),
        (TerminationReason.ADMIN_ABORT, 0),
        (TerminationReason.CHECKMATE, 1),
        (TerminationReason.FLAG, 1),
    ],
)
async def test_rule_one_unrates_exactly_three_terminations(
    store, deps, seed_bots, termination, expected
):
    _, _, game = await _seed_game(store, deps, seed_bots)
    assert game.rated == 1

    await _finalise(store, deps, game, termination=termination)

    assert (await GameRepo(store.writer, store.executor).get_by_id(game.id)).rated == expected


async def test_rated_never_returns_to_one(store, deps, seed_bots):
    """Rules 2-6 settled `rated` at creation; rule 1 can only take it away."""
    _, _, game = await _seed_game(store, deps, seed_bots, owner="same")
    assert game.rated == 0

    await _finalise(store, deps, game, termination=CHECKMATE)

    assert (await GameRepo(store.writer, store.executor).get_by_id(game.id)).rated == 0


async def test_finalisation_frees_the_seats_and_moves_both_bots_counters(
    store, deps, seed_bots
):
    white, black, game = await _seed_game(store, deps, seed_bots, owner="same")

    await _finalise(store, deps, game, result=GameResult.WHITE_WIN)

    games = GameRepo(store.writer, store.executor)
    bots = BotRepo(store.writer, store.executor)
    seats = SeatRepo(store.writer, store.executor)
    after = await games.get_by_id(game.id)
    assert (after.status, after.result, after.termination) == (
        "finished", GameResult.WHITE_WIN.value, CHECKMATE.value)
    assert after.ended_at is not None
    assert (after.delivered_to_mover, after.turn_started_mono) == (0, None)
    assert await seats.list_seated_bot_ids() == []

    w, b = await bots.get_by_id(white.id), await bots.get_by_id(black.id)
    assert (w.games_played, w.wins, w.losses, w.draws) == (1, 1, 0, 0)
    assert (b.games_played, b.wins, b.losses, b.draws) == (1, 0, 1, 0)
    assert (w.last_color, w.last_opponent_id, w.white_count) == ("white", black.id, 1)
    assert (b.last_color, b.last_opponent_id, b.white_count) == ("black", white.id, 0)


async def test_the_deferred_cleanup_waits_for_the_commit(store, deps, wake, seed_bots):
    white, black, game = await _seed_game(store, deps, seed_bots, owner="same")
    state.mailbox[white.id] = "stale"
    state.mailbox[black.id] = "stale"
    state.unpaired_ticks[white.id] = 4
    state.unpaired_ticks[black.id] = 2
    wake.woken.clear()

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await finalise_game_locked(deps, txn, game, GameResult.DRAW,
                                   TerminationReason.STALEMATE)
        assert white.id in state.mailbox and black.id in state.mailbox
        assert game.id in state.history
        assert state.unpaired_ticks and wake.woken == []

    assert state.mailbox == {}
    assert game.id not in state.history
    assert state.unpaired_ticks == {}
    assert sorted(wake.woken) == sorted([white.id, black.id])


async def test_game_ended_carries_the_post_rule_one_rated_flag(store, deps, sink, seed_bots):
    white, black, game = await _seed_game(store, deps, seed_bots)
    sink.events.clear()

    await _finalise(store, deps, game, result=None, termination=TerminationReason.NO_SHOW)

    assert sink.of("game_ended") == [{
        "game_id": game.id,
        "white_bot_id": white.id,
        "white_bot_name": "white-bot",
        "white_bot_display_name": "white-bot",
        "black_bot_id": black.id,
        "black_bot_name": "black-bot",
        "black_bot_display_name": "black-bot",
        "status": "aborted",
        "result": None,
        "termination": TerminationReason.NO_SHOW.value,
        "rated": False,
        "final_ply": game.ply,
        "ended_at": (await GameRepo(store.writer, store.executor)
                     .get_by_id(game.id)).ended_at,
    }]


async def test_abort_is_unrated_resultless_and_still_writes_pool_history(
    store, deps, sink, seed_bots
):
    white, black, game = await _seed_game(store, deps, seed_bots)
    sink.events.clear()

    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await abort_game_locked(deps, txn, game, TerminationReason.NO_SHOW)

    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    bots = BotRepo(store.writer, store.executor)
    assert (after.status, after.result, after.rated) == ("aborted", None, 0)
    assert (await bots.get_by_id(white.id)).last_opponent_id == black.id
    assert (await bots.get_by_id(black.id)).last_color == Color.BLACK.value
    assert sink.types() == ["game_ended"]
