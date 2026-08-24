"""Delivery and move application — the only two writers of a live position (§5.2, §6.1)."""
from dataclasses import dataclass
from typing import Optional

from chess_core import Color, GameResult, TerminationReason, has_flagged

from chess_server.engine.deps import EngineDeps
from chess_server.engine.games import finalise_game_locked, opposite_win
from chess_server.engine.wall import utc_now_iso
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import NON_TERMINAL, BotRepo, GameRepo, _clock_from_game
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

    raise NotImplementedError("apply_move steps 5-10 land in task 8")

