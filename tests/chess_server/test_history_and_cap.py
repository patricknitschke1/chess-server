"""history_fens, threefold and the ply cap (role spec §6.4, §6.1 steps 6-7)."""
import pytest

from chess_core import (
    PLY_CAP,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_FEN,
    GameResult,
    TerminationReason,
    detect_termination,
)
from chess_server.engine import state
from chess_server.engine.games import abort_game_locked, create_game_locked
from chess_server.engine.runner import apply_move, apply_move_locked, deliver_position
from chess_server.store.repositories import GameRepo, MoveRepo
from chess_server.store.txn import critical_section

SHUFFLE = ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8"]
FIFTY_MOVE_FEN = "8/8/4k3/8/8/4K3/8/R7 w - - 99 60"
QUIET_FEN = "8/8/4k3/8/8/4K3/8/R7 w - - 10 60"
BACK_RANK_MATE_FEN = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"


async def _game(store, deps, seed_bots, *, fen=None, ply=None):
    white, black = await seed_bots("white-bot", "black-bot")
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
    if ply is not None:
        store.writer.execute("UPDATE games SET ply = ? WHERE id = ?", (ply, game_id))
    return game_id


async def _play(store, deps, game_id, uci):
    games = GameRepo(store.writer, store.executor)
    game = await games.get_by_id(game_id)
    mover = game.white_bot_id if game.to_move == "white" else game.black_bot_id
    await deliver_position(deps, mover)
    game = await games.get_by_id(game_id)
    return await apply_move(deps, game_id, game.ply, uci)


async def test_threefold_from_the_starting_position_is_claimed(store, deps, seed_bots):
    game_id = await _game(store, deps, seed_bots)

    for uci in SHUFFLE:
        await _play(store, deps, game_id, uci)

    game = await GameRepo(store.writer, store.executor).get_by_id(game_id)
    assert game.termination == TerminationReason.THREEFOLD.value
    assert game.result == GameResult.DRAW.value


async def test_a_history_built_from_the_moves_table_alone_misses_it(
    store, deps, seed_bots
):
    """The off-by-one that makes the first assertion about the contract rather
    than about luck: `SELECT fen_after ORDER BY ply` omits ply 0."""
    game_id = await _game(store, deps, seed_bots)
    for uci in SHUFFLE:
        await _play(store, deps, game_id, uci)

    rows = await MoveRepo(store.writer, store.executor).list_moves_for_game(game_id)
    moves_only = [row.fen_after for row in rows]
    final = rows[-1].fen_after

    assert detect_termination(final, moves_only) == (False, None, None)
    assert detect_termination(final, [STARTING_FEN] + moves_only)[1] == (
        TerminationReason.THREEFOLD)


async def test_a_rolled_back_move_leaves_the_cache_where_it_was(store, deps, seed_bots):
    game_id = await _game(store, deps, seed_bots)
    game = await GameRepo(store.writer, store.executor).get_by_id(game_id)
    await deliver_position(deps, game.white_bot_id)
    before = list(state.history[game_id])

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            await apply_move_locked(deps, txn, game_id, 0, "e2e4",
                                    client_reported_ms=None, now_mono=deps.now_mono())
            raise _Boom

    assert state.history[game_id] == before


async def test_the_fifty_move_rule_is_claimed_on_the_hundredth_halfmove(
    store, deps, seed_bots
):
    game_id = await _game(store, deps, seed_bots, fen=FIFTY_MOVE_FEN)

    await _play(store, deps, game_id, "a1a2")

    game = await GameRepo(store.writer, store.executor).get_by_id(game_id)
    assert game.termination == TerminationReason.FIFTY_MOVE.value
    assert game.result == GameResult.DRAW.value


async def test_a_mate_on_the_capping_ply_is_a_mate(store, deps, seed_bots):
    game_id = await _game(store, deps, seed_bots, fen=BACK_RANK_MATE_FEN, ply=PLY_CAP - 1)

    await _play(store, deps, game_id, "a1a8")

    game = await GameRepo(store.writer, store.executor).get_by_id(game_id)
    assert game.termination == TerminationReason.CHECKMATE.value
    assert game.result == GameResult.WHITE_WIN.value
    assert game.ply == PLY_CAP


async def test_a_quiet_move_on_the_capping_ply_is_adjudicated(store, deps, seed_bots):
    game_id = await _game(store, deps, seed_bots, fen=QUIET_FEN, ply=PLY_CAP - 1)

    await _play(store, deps, game_id, "a1a2")

    game = await GameRepo(store.writer, store.executor).get_by_id(game_id)
    assert game.termination == TerminationReason.ADJUDICATED.value
    assert game.result == GameResult.DRAW.value


async def test_the_cache_is_dropped_by_a_mate_and_by_an_abort(store, deps, seed_bots):
    mated = await _game(store, deps, seed_bots, fen=BACK_RANK_MATE_FEN)
    await _play(store, deps, mated, "a1a8")
    assert mated not in state.history

    aborted = await _game(store, deps, seed_bots, names_unused := None) if False else None
    aborted_id = await _game_second(store, deps, seed_bots)
    game = await GameRepo(store.writer, store.executor).get_by_id(aborted_id)
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await abort_game_locked(deps, txn, game, TerminationReason.NO_SHOW)

    assert aborted_id not in state.history


async def _game_second(store, deps, seed_bots):
    white, black = await seed_bots("w2", "b2")
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        return await create_game_locked(
            deps, txn, white, black,
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=deps.now_mono(),
        )
