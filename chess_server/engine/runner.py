"""Delivery and move application — the only two writers of a live position (§5.2, §6.1)."""
from typing import Optional

from chess_server.engine.deps import EngineDeps
from chess_server.engine.wall import utc_now_iso
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.rows import GameRow
from chess_server.store.txn import Txn, critical_section


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
