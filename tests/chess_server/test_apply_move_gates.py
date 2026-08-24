"""apply_move steps 1-4. The order is normative (role spec §6.1)."""
import pathlib
import re

import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    GameResult,
    TerminationReason,
)
from chess_server.engine.games import create_game_locked
from chess_server.engine.runner import (
    Flagged,
    NotDelivered,
    WrongController,
    apply_move_locked,
    deliver_position,
)
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import BotRepo, GameRepo, MoveRepo
from chess_server.store.txn import critical_section

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[2] / "chess_server"
LEGAL = "e2e4"
ILLEGAL = "e2e5"


async def _game(store, deps, seed_bots, *, deliver=True):
    white, black = await seed_bots("white-bot", "black-bot")
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        game_id = await create_game_locked(
            deps, txn, white, black,
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=deps.now_mono(),
        )
    if deliver:
        await deliver_position(deps, white.id)
    return white, black, await GameRepo(store.writer, store.executor).get_by_id(game_id)


async def _apply(store, deps, game, uci, *, from_ply=None, controller="client"):
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        return await apply_move_locked(
            deps, txn, game.id,
            from_ply if from_ply is not None else game.ply,
            uci,
            controller=controller,
            client_reported_ms=None,
            now_mono=deps.now_mono(),
        )


async def test_a_stale_ply_conflicts_and_leaves_the_game_alone(store, deps, seed_bots):
    _, _, game = await _game(store, deps, seed_bots)

    with pytest.raises(CASConflict):
        await _apply(store, deps, game, LEGAL, from_ply=game.ply + 1)

    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    assert (after.ply, after.fen, after.status) == (game.ply, game.fen, game.status)


async def test_a_controller_change_committed_after_the_read_still_refuses(
    store, deps, seed_bots
):
    """Authorisation is checked in this transaction, never as a pre-check."""
    white, _, game = await _game(store, deps, seed_bots)
    async with critical_section(store.writer, store.executor, deps.sink) as txn:
        await BotRepo(txn.conn, txn.executor).update_controller(white.id, "agent")

    outcome = await _apply(store, deps, game, LEGAL, controller="client")

    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    assert isinstance(outcome, WrongController)
    assert outcome.controller == "agent"
    assert (after.ply, after.fen) == (game.ply, game.fen)


async def test_an_undelivered_position_is_refused_and_never_delivered(
    store, deps, seed_bots
):
    """Delivering here would let a bot start its own clock by submitting a move."""
    _, _, game = await _game(store, deps, seed_bots, deliver=False)

    outcome = await _apply(store, deps, game, LEGAL)

    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    assert isinstance(outcome, NotDelivered)
    assert (after.delivered_to_mover, after.turn_started_mono) == (0, None)
    assert (after.status, after.ply) == ("pending", 0)


@pytest.mark.parametrize("uci", [ILLEGAL, LEGAL])
async def test_the_flag_falls_before_the_move_is_ever_validated(
    store, deps, clock, seed_bots, uci
):
    _, _, game = await _game(store, deps, seed_bots)
    clock.advance(RATED_TIME_CONTROL_NS + 1)

    outcome = await _apply(store, deps, game, uci)

    after = await GameRepo(store.writer, store.executor).get_by_id(game.id)
    assert isinstance(outcome, Flagged)
    assert after.termination == TerminationReason.FLAG.value
    assert after.result == GameResult.BLACK_WIN.value
    assert (after.white_strikes, after.black_strikes) == (0, 0)
    assert after.termination != TerminationReason.ILLEGAL_FORFEIT.value
    assert await MoveRepo(store.writer, store.executor).list_moves_for_game(game.id) == []


def test_has_flagged_is_the_only_flag_predicate_in_chess_server():
    """§6.4's `<= 0` is declared once, in chess_core, or it drifts between the
    ticker and the move endpoint."""
    offenders = []
    for path in sorted(SERVER_ROOT.rglob("*.py")):
        text = path.read_text()
        for pattern in (r"\bremaining\b", r"<=\s*0\b", r"turn_started_mono\s*-",
                        r"-\s*\w*turn_started_mono"):
            if re.search(pattern, text):
                offenders.append(f"{path.name}: {pattern}")
    assert offenders == []
