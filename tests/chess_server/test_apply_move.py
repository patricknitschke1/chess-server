"""apply_move steps 5-10 (role spec §6.1, §6.2). Failure paths first."""
import sqlite3

import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    Color,
    GameResult,
    TerminationReason,
    create_clock,
    ns_to_ms,
)
from chess_server.engine import state
from chess_server.engine.games import create_game_locked
from chess_server.engine.runner import Applied, Rejected, apply_move, deliver_position
from chess_server.store.repositories import GameRepo, MoveRepo, SeatRepo
from chess_server.store.txn import critical_section

THREE_SECONDS_NS = 3_000_000_000
LEGAL = "e2e4"
ILLEGAL = "e2e5"
BACK_RANK_MATE_FEN = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"


async def _game(store, deps, seed_bots, *, fen=None, names=("white-bot", "black-bot")):
    white, black = await seed_bots(*names)
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await create_game_locked(
            deps, txn, white, black,
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=deps.now_mono(),
        )
    if fen is not None:
        store.writer.execute("UPDATE games SET fen = ? WHERE id = ?", (fen, game_id))
    await deliver_position(deps, white.id)
    return white, black, await GameRepo(store.writer, store.executor).get_by_id(game_id)


def _games(store):
    return GameRepo(store.writer, store.executor)


async def test_an_illegal_move_commits_its_strike(store, deps, sink, seed_bots):
    """Raising through critical_section would roll the strike back with it, and
    design §8.3's three-strike rule would silently not exist."""
    _, _, game = await _game(store, deps, seed_bots)
    sink.events.clear()

    outcome = await apply_move(deps, game.id, game.ply, ILLEGAL)

    after = await _games(store).get_by_id(game.id)
    assert isinstance(outcome, Rejected)
    assert (outcome.strikes, outcome.forfeited) == (1, False)
    assert LEGAL in outcome.legal_moves
    assert after.white_strikes == 1
    assert (after.ply, after.turn_started_mono) == (game.ply, game.turn_started_mono)
    assert await MoveRepo(store.writer, store.executor).list_moves_for_game(game.id) == []
    assert sink.types() == []


async def test_the_third_strike_forfeits_in_the_same_transaction(
    store, deps, sink, seed_bots
):
    white, black, game = await _game(store, deps, seed_bots)
    sink.events.clear()

    for _ in range(2):
        await apply_move(deps, game.id, game.ply, ILLEGAL)
    outcome = await apply_move(deps, game.id, game.ply, ILLEGAL)

    after = await _games(store).get_by_id(game.id)
    assert isinstance(outcome, Rejected) and outcome.forfeited is True
    assert after.termination == TerminationReason.ILLEGAL_FORFEIT.value
    assert after.result == GameResult.BLACK_WIN.value
    assert after.white_strikes == 3
    assert await SeatRepo(store.writer, store.executor).list_seated_bot_ids() == []
    assert sink.types() == ["game_ended", "rating_changed", "rating_changed"]


async def test_strikes_do_not_follow_a_bot_into_its_next_game(store, deps, seed_bots):
    white, _, first = await _game(store, deps, seed_bots)
    for _ in range(2):
        await apply_move(deps, first.id, first.ply, ILLEGAL)
    assert (await _games(store).get_by_id(first.id)).white_strikes == 2

    _, _, second = await _game(store, deps, seed_bots, names=("w2", "b2"))

    assert (second.white_strikes, second.black_strikes) == (0, 0)


async def test_time_spent_on_illegal_attempts_is_charged_cumulatively(
    store, deps, clock, seed_bots
):
    _, _, game = await _game(store, deps, seed_bots)

    clock.advance(THREE_SECONDS_NS)
    await apply_move(deps, game.id, game.ply, ILLEGAL)
    clock.advance(THREE_SECONDS_NS)
    await apply_move(deps, game.id, game.ply, ILLEGAL)
    await apply_move(deps, game.id, game.ply, LEGAL)

    after = await _games(store).get_by_id(game.id)
    spent = ns_to_ms(RATED_TIME_CONTROL_NS) - after.white_ms + ns_to_ms(RATED_INCREMENT_NS)
    assert spent == ns_to_ms(2 * THREE_SECONDS_NS)


async def test_a_legal_move_persists_the_ply_the_clock_and_the_event(
    store, deps, clock, sink, seed_bots
):
    white, black, game = await _game(store, deps, seed_bots)
    sink.events.clear()
    clock.advance(THREE_SECONDS_NS)

    outcome = await apply_move(deps, game.id, game.ply, LEGAL, client_reported_ms=42)

    after = await _games(store).get_by_id(game.id)
    (move,) = await MoveRepo(store.writer, store.executor).list_moves_for_game(game.id)
    assert isinstance(outcome, Applied) and outcome.san == "e4"
    assert (after.ply, after.to_move) == (game.ply + 1, "black")
    assert (after.delivered_to_mover, after.turn_started_mono) == (0, None)
    assert after.to_move_since_mono == clock()
    assert (move.ply, move.uci, move.san) == (game.ply + 1, LEGAL, "e4")
    assert move.server_elapsed_ms == ns_to_ms(THREE_SECONDS_NS)
    assert move.client_reported_ms == 42
    assert sink.of("move_played") == [{
        "game_id": game.id,
        "ply": game.ply + 1,
        "uci": LEGAL,
        "san": "e4",
        "fen": after.fen,
        "to_move": "black",
        "white_ms": after.white_ms,
        "black_ms": after.black_ms,
        "turn_elapsed_ms": ns_to_ms(THREE_SECONDS_NS),
        "server_elapsed_ms": ns_to_ms(THREE_SECONDS_NS),
    }]


async def test_the_movers_mailbox_is_cleared_only_when_the_move_commits(
    store, deps, seed_bots
):
    white, _, game = await _game(store, deps, seed_bots)
    state.mailbox[white.id] = "stale turn payload"

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            from chess_server.engine.runner import apply_move_locked
            await apply_move_locked(deps, txn, game.id, game.ply, LEGAL,
                                    client_reported_ms=None, now_mono=deps.now_mono())
            raise _Boom
    assert state.mailbox[white.id] == "stale turn payload"

    await apply_move(deps, game.id, game.ply, LEGAL)

    assert white.id not in state.mailbox


async def test_a_mating_move_finalises_in_the_same_transaction(
    store, deps, sink, seed_bots
):
    _, _, game = await _game(store, deps, seed_bots, fen=BACK_RANK_MATE_FEN)
    sink.events.clear()

    outcome = await apply_move(deps, game.id, game.ply, "a1a8")

    after = await _games(store).get_by_id(game.id)
    assert isinstance(outcome, Applied) and outcome.terminal is True
    assert sink.types()[:2] == ["move_played", "game_ended"]
    assert (after.status, after.termination) == ("finished", TerminationReason.CHECKMATE.value)
    assert after.result == GameResult.WHITE_WIN.value
    assert after.ply == game.ply + 1
    assert await SeatRepo(store.writer, store.executor).list_seated_bot_ids() == []


async def test_two_moves_at_the_same_ply_violate_the_primary_key(store, deps, seed_bots):
    _, _, game = await _game(store, deps, seed_bots)
    moves = MoveRepo(store.writer, store.executor)

    with pytest.raises(sqlite3.IntegrityError):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            repo = MoveRepo(txn.conn, txn.executor)
            clock = create_clock(RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, Color.WHITE, 0)
            for _ in range(2):
                await repo.insert_move(game.id, 1, LEGAL, "e4", "fen", 0, None, clock)
    assert await moves.list_moves_for_game(game.id) == []
