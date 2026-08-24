"""Game creation and the terminal transitions (role spec §6.5, §7.2)."""
from typing import Optional

from chess_core import STARTING_FEN, Color, GameResult, TerminationReason, ns_to_ms

from chess_server.engine import state
from chess_server.engine.deps import EngineDeps
from chess_server.engine.wall import utc_now_iso
from chess_server.store.repositories import BotRepo, GameRepo, SeatRepo
from chess_server.store.rows import BotRow, GameRow
from chess_server.store.txn import Txn

# Design §5.3 rule 1, and only rule 1. Everything else leaves `rated` alone.
_RULE_1_UNRATES = frozenset({
    TerminationReason.NO_SHOW,
    TerminationReason.SERVER_RESTART,
    TerminationReason.ADMIN_ABORT,
})

_ABORTS = _RULE_1_UNRATES


def opposite_win(to_move: str | Color) -> GameResult:
    """The side to move loses. Flag, abandonment and illegal forfeit all read this."""
    color = to_move if isinstance(to_move, Color) else Color(to_move)
    return GameResult.BLACK_WIN if color == Color.WHITE else GameResult.WHITE_WIN


def _outcomes(result: GameResult) -> tuple[str, str]:
    if result == GameResult.DRAW:
        return "draw", "draw"
    if result == GameResult.WHITE_WIN:
        return "win", "loss"
    return "loss", "win"



async def create_game_locked(
    deps: EngineDeps,
    txn: Txn,
    white: BotRow,
    black: BotRow,
    *,
    time_control_ns: int,
    increment_ns: int,
    source: str,
    now_mono: int,
) -> int:
    """Insert a game and its two seats. The caller supplies the savepoint.

    Takes whole rows, not ids: `rated_at_creation` and the event payload both
    need the owner, role and name.
    """
    games = GameRepo(txn.conn, txn.executor)
    seats = SeatRepo(txn.conn, txn.executor)

    game_id = await games.insert_game(
        white=white,
        black=black,
        time_control_ns=time_control_ns,
        increment_ns=increment_ns,
        source=source,
        now_mono=now_mono,
        created_at=utc_now_iso(),
    )
    await seats.insert_seat(white.id, game_id)
    await seats.insert_seat(black.id, game_id)

    game = await games.get_by_id(game_id)
    txn.defer(lambda: state.history.setdefault(game_id, [STARTING_FEN]))
    txn.emit("game_created", {
        "game_id": game_id,
        "white_bot_id": white.id,
        "white_bot_name": white.name,
        "black_bot_id": black.id,
        "black_bot_name": black.name,
        "status": game.status,
        "rated": bool(game.rated),
        "source": source,
        "time_control_ms": ns_to_ms(time_control_ns),
        "increment_ms": ns_to_ms(increment_ns),
    })
    txn.defer(lambda: deps.wake(white.id))
    txn.defer(lambda: deps.wake(black.id))
    return game_id


async def rate_game_locked(
    txn: Txn, game: GameRow, white: BotRow, black: BotRow, result: GameResult
) -> list[tuple[BotRow, object]]:
    """Placeholder until the rating derivation lands. Returns the rows persisted."""
    return []


async def _end_game_locked(
    deps: EngineDeps,
    txn: Txn,
    game: GameRow,
    status: str,
    result: Optional[GameResult],
    termination: TerminationReason,
) -> None:
    games = GameRepo(txn.conn, txn.executor)
    bots = BotRepo(txn.conn, txn.executor)
    seats = SeatRepo(txn.conn, txn.executor)

    unrate = 0 if termination in _RULE_1_UNRATES else None
    ended_at = utc_now_iso()
    await games.cas_terminate(
        game.id, game.status, game.ply, status,
        result.value if result is not None else None,
        termination.value, ended_at, unrate,
    )
    rated = 0 if unrate == 0 else game.rated

    white = await bots.get_by_id(game.white_bot_id)
    black = await bots.get_by_id(game.black_bot_id)

    rated_rows = (
        await rate_game_locked(txn, game, white, black, result) if rated == 1 and result
        else []
    )
    new_rating = {bot.id: update.rating_after for bot, update in rated_rows}

    if result is not None:
        white_outcome, black_outcome = _outcomes(result)
        for bot, outcome in ((white, white_outcome), (black, black_outcome)):
            await bots.update_rating_and_counters(
                bot.id, new_rating.get(bot.id, bot.rating), outcome
            )
    await bots.update_pool_history(white.id, Color.WHITE.value, black.id, True)
    await bots.update_pool_history(black.id, Color.BLACK.value, white.id, False)
    await seats.delete_seats_for_game(game.id)

    txn.defer(lambda: state.mailbox.pop(white.id, None))
    txn.defer(lambda: state.mailbox.pop(black.id, None))
    txn.defer(lambda: state.history.pop(game.id, None))
    txn.defer(lambda: state.unpaired_ticks.pop(white.id, None))
    txn.defer(lambda: state.unpaired_ticks.pop(black.id, None))
    txn.defer(lambda: deps.wake(white.id))
    txn.defer(lambda: deps.wake(black.id))

    txn.emit("game_ended", {
        "game_id": game.id,
        "white_bot_id": white.id,
        "white_bot_name": white.name,
        "black_bot_id": black.id,
        "black_bot_name": black.name,
        "status": status,
        "result": result.value if result is not None else None,
        "termination": termination.value,
        "rated": bool(rated),
        "final_ply": game.ply,
        "ended_at": ended_at,
    })
    for bot, update in rated_rows:
        txn.emit("rating_changed", {
            "bot_id": bot.id,
            "bot_name": bot.name,
            "rating_before": update.rating_before,
            "rating_after": update.rating_after,
            "delta": update.delta,
            "game_id": game.id,
        })


async def finalise_game_locked(
    deps: EngineDeps,
    txn: Txn,
    game: GameRow,
    result: Optional[GameResult],
    termination: TerminationReason,
) -> None:
    status = "aborted" if termination in _ABORTS else "finished"
    await _end_game_locked(deps, txn, game, status, result, termination)


async def abort_game_locked(
    deps: EngineDeps, txn: Txn, game: GameRow, termination: TerminationReason
) -> None:
    await _end_game_locked(deps, txn, game, "aborted", None, termination)

