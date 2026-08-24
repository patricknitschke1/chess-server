"""Delivery and move application — the only two writers of a live position (§5.2, §6.1)."""
from dataclasses import dataclass
from typing import Optional

from chess_core import (
    STARTING_FEN,
    Color,
    GameResult,
    GameStatus,
    MatchState,
    MoveResult,
    TerminationReason,
    account_move_and_switch,
    compute_turn_elapsed_ms,
    detect_termination,
    get_legal_moves,
    has_flagged,
    transition_after_move,
    validate_and_apply_move,
)

from chess_server.engine import state
from chess_server.engine.deps import EngineDeps
from chess_server.engine.games import (
    ILLEGAL_STRIKE_LIMIT,
    finalise_game_locked,
    forfeit_game_locked,
    opposite_win,
)
from chess_server.engine.wall import utc_now_iso
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import (
    NON_TERMINAL,
    BotRepo,
    GameRepo,
    MoveRepo,
    _clock_from_game,
)
from chess_server.store.rows import GameRow
from chess_server.store.txn import Txn, critical_section



@dataclass(frozen=True)
class Applied:
    game: GameRow
    san: str
    fen_after: str
    terminal: bool


@dataclass(frozen=True)
class Rejected:
    fen: str
    legal_moves: list[str]
    strikes: int
    forfeited: bool


@dataclass(frozen=True)
class NotDelivered:
    ply: int
    fen: str
    status: str


@dataclass(frozen=True)
class Flagged:
    result: GameResult


@dataclass(frozen=True)
class WrongController:
    controller: str


MoveOutcome = Applied | Rejected | NotDelivered | Flagged | WrongController



async def deliver_position_locked(
    deps: EngineDeps, txn: Txn, game: GameRow, now_mono: int
) -> bool:
    """Returns whether this call delivered. False means already delivered, which
    is free by design — the caller re-reads the row and never sees an error."""
    games = GameRepo(txn.conn, txn.executor)
    started_at = utc_now_iso()
    delivered, started = await games.cas_deliver(game.id, game.ply, now_mono, started_at)
    if started:
        bots = BotRepo(txn.conn, txn.executor)
        white = await bots.get_by_id(game.white_bot_id)
        black = await bots.get_by_id(game.black_bot_id)
        txn.emit("game_started", {
            "game_id": game.id,
            "white_bot_id": white.id,
            "white_bot_name": white.name,
            "black_bot_id": black.id,
            "black_bot_name": black.name,
            "started_at": started_at,
        })
    return delivered


async def deliver_position(deps: EngineDeps, bot_id: int) -> Optional[GameRow]:
    """The outer form. Returns the post-delivery row, or None when the bot holds
    no seat — a game is reachable only through `seats` (§7.1)."""
    async with critical_section(deps.conn, deps.executor, deps.sink) as txn:
        games = GameRepo(txn.conn, txn.executor)
        game = await games.get_for_bot(bot_id)
        if game is None:
            return None
        await deliver_position_locked(deps, txn, game, deps.now_mono())
        return await games.get_by_id(game.id)


def _mover_id(game: GameRow) -> int:
    return game.white_bot_id if game.to_move == Color.WHITE.value else game.black_bot_id


def _history_fens(game: GameRow, fen_after: str) -> list[str]:
    """Interfaces Part 1: the starting position, every fen_after in order, and the
    position just reached. Omitting ply 0 loses the commonest repetition there is."""
    return state.history.get(game.id, [STARTING_FEN]) + [fen_after]


def _record_ply(game_id: int, fen_after: str, san: str) -> None:
    state.history.setdefault(game_id, [STARTING_FEN]).append(fen_after)
    state.history_san.setdefault(game_id, []).append(san)



async def apply_move_locked(
    deps: EngineDeps,
    txn: Txn,
    game_id: int,
    from_ply: int,
    uci: str,
    *,
    controller: str = "client",
    client_reported_ms: Optional[int],
    now_mono: int,
) -> MoveOutcome:
    games = GameRepo(txn.conn, txn.executor)
    bots = BotRepo(txn.conn, txn.executor)

    game = await games.get_by_id(game_id)
    # The from-state predicate, read under BEGIN IMMEDIATE, so no writer can move
    # it between here and the CAS-UPDATE that repeats it in SQL at step 9.
    if game is None or game.ply != from_ply or game.status not in NON_TERMINAL:
        raise CASConflict(f"game {game_id} is not at ply {from_ply} and non-terminal")

    mover = await bots.get_by_id(_mover_id(game))
    if mover.controller != controller:
        return WrongController(controller=mover.controller)

    if game.delivered_to_mover != 1:
        return NotDelivered(ply=game.ply, fen=game.fen, status=game.status)

    clock = _clock_from_game(game)
    if has_flagged(clock, now_mono):
        result = opposite_win(clock.to_move)
        await finalise_game_locked(deps, txn, game, result, TerminationReason.FLAG)
        return Flagged(result=result)

    outcome = validate_and_apply_move(game.fen, uci)
    if not outcome.accepted:
        return await _reject_locked(deps, txn, game, clock.to_move)

    fen_after = outcome.move_result.fen_after
    terminal, termination, result = detect_termination(
        fen_after, _history_fens(game, fen_after)
    )
    # Built from detect_termination, never from validate_and_apply_move: the
    # latter is handed one position and so cannot see threefold.
    move_result = MoveResult(
        fen_after=fen_after,
        san=outcome.move_result.san,
        is_terminal=terminal,
        termination=termination,
        result=result,
    )
    # Constructed here and discarded here; the only place PLY_CAP is applied.
    after_state = transition_after_move(
        MatchState(GameStatus.ACTIVE, game.ply, None, None), move_result
    )

    # The same now_mono step 4 asked with, so `flagged` here is necessarily False.
    accounted = account_move_and_switch(clock, receive_mono=now_mono, now_mono=now_mono)
    new_ply = game.ply + 1

    await MoveRepo(txn.conn, txn.executor).insert_move(
        game.id, new_ply, uci, move_result.san, fen_after,
        accounted.elapsed_ms, client_reported_ms,
    )
    await games.cas_apply_move(
        game.id, game.ply, game.status, fen_after, accounted.new_clock
    )
    mover_id = _mover_id(game)
    txn.defer(lambda: state.mailbox.pop(mover_id, None))
    # One defer for both caches: the SAN is already computed here, and splitting
    # them lets a rollback leave one of the two a ply ahead of the database.
    txn.defer(lambda: _record_ply(game.id, fen_after, move_result.san))

    game_after = await games.get_by_id(game.id)
    txn.emit("move_played", {
        "game_id": game.id,
        "ply": new_ply,
        "uci": uci,
        "san": move_result.san,
        "fen": fen_after,
        "to_move": game_after.to_move,
        "white_ms": game_after.white_ms,
        "black_ms": game_after.black_ms,
        "turn_elapsed_ms": compute_turn_elapsed_ms(clock, now_mono),
        "server_elapsed_ms": accounted.elapsed_ms,
    })

    if after_state.status != GameStatus.ACTIVE:
        await finalise_game_locked(
            deps, txn, game_after, after_state.result, after_state.termination
        )
    return Applied(
        game=game_after,
        san=move_result.san,
        fen_after=fen_after,
        terminal=after_state.status != GameStatus.ACTIVE,
    )


async def _reject_locked(
    deps: EngineDeps, txn: Txn, game: GameRow, mover: Color
) -> Rejected:
    """Commits. Raising here would roll the strike back and §8.3 would not exist."""
    games = GameRepo(txn.conn, txn.executor)
    strikes = await games.cas_add_strike(game.id, game.ply, mover.value)
    forfeited = strikes >= ILLEGAL_STRIKE_LIMIT
    if forfeited:
        await forfeit_game_locked(deps, txn, await games.get_by_id(game.id), mover)
    return Rejected(
        fen=game.fen,
        legal_moves=get_legal_moves(game.fen),
        strikes=strikes,
        forfeited=forfeited,
    )


async def apply_move(
    deps: EngineDeps,
    game_id: int,
    from_ply: int,
    uci: str,
    *,
    controller: str = "client",
    client_reported_ms: Optional[int] = None,
) -> MoveOutcome:
    async with critical_section(deps.conn, deps.executor, deps.sink) as txn:
        return await apply_move_locked(
            deps, txn, game_id, from_ply, uci,
            controller=controller,
            client_reported_ms=client_reported_ms,
            now_mono=deps.now_mono(),
        )


