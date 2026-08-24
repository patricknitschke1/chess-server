"""Typed SQL wrappers. Transaction control belongs to the caller, never here (§3.6)."""
import asyncio
import sqlite3
from concurrent.futures import Executor
from typing import Optional, Sequence

from chess_core import ClockState, Color, ms_to_ns, ns_to_ms

from chess_server.store.rows import BotRow, GameRow, from_row


def _clock_from_game(game: GameRow) -> ClockState:
    """The single ms -> ns boundary. Nothing else in chess_server/ calls ms_to_ns."""
    return ClockState(
        white_ns=ms_to_ns(game.white_ms),
        black_ns=ms_to_ns(game.black_ms),
        time_control_ns=ms_to_ns(game.time_control_ms),
        increment_ns=ms_to_ns(game.increment_ms),
        to_move=Color(game.to_move),
        to_move_since_mono=game.to_move_since_mono,
        turn_started_mono=game.turn_started_mono,
        delivered_to_mover=game.delivered_to_mover,
    )


def _clock_to_game_fields(clock: ClockState) -> dict:
    """The single ns -> ms boundary. Nothing else in chess_server/ calls ns_to_ms."""
    return {
        "white_ms": ns_to_ms(clock.white_ns),
        "black_ms": ns_to_ms(clock.black_ns),
        "to_move": clock.to_move.value,
        "to_move_since_mono": clock.to_move_since_mono,
        "turn_started_mono": clock.turn_started_mono,
        "delivered_to_mover": clock.delivered_to_mover,
    }


class _Repo:
    """Every sqlite3 call runs on the connection's own thread; nothing else does."""

    def __init__(self, conn: sqlite3.Connection, executor: Executor):
        self.conn = conn
        self.executor = executor

    async def _run(self, fn):
        return await asyncio.get_running_loop().run_in_executor(self.executor, fn)

    async def _write(self, sql: str, params: Sequence = ()) -> sqlite3.Cursor:
        return await self._run(lambda: self.conn.execute(sql, params))

    async def _one(self, sql: str, params: Sequence = ()) -> Optional[sqlite3.Row]:
        return await self._run(lambda: self.conn.execute(sql, params).fetchone())

    async def _all(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        return await self._run(lambda: self.conn.execute(sql, params).fetchall())


_COUNTER_COLUMN = {"win": "wins", "loss": "losses", "draw": "draws"}


class BotRepo(_Repo):
    async def insert_bot(
        self,
        name: str,
        owner: str,
        token_hash: str,
        role: str,
        rating: int,
        is_anchor: int,
        created_at: str,
        controller: str = "client",
    ) -> int:
        cursor = await self._write(
            "INSERT INTO bots (name, owner, token_hash, role, rating, is_anchor,"
            " controller, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, owner, token_hash, role, rating, is_anchor, controller, created_at),
        )
        return cursor.lastrowid

    async def get_by_id(self, bot_id: int) -> Optional[BotRow]:
        return from_row(BotRow, await self._one("SELECT * FROM bots WHERE id = ?", (bot_id,)))

    async def get_by_name(self, name: str) -> Optional[BotRow]:
        return from_row(BotRow, await self._one("SELECT * FROM bots WHERE name = ?", (name,)))

    async def get_by_token_hash(self, token_hash: str) -> Optional[BotRow]:
        return from_row(
            BotRow,
            await self._one("SELECT * FROM bots WHERE token_hash = ?", (token_hash,)),
        )

    async def get_competitor_for_owner(self, owner: str) -> Optional[BotRow]:
        return from_row(
            BotRow,
            await self._one(
                "SELECT * FROM bots WHERE owner = ? AND role = 'competitor' ORDER BY id",
                (owner,),
            ),
        )

    async def update_controller(self, bot_id: int, controller: str) -> None:
        await self._write("UPDATE bots SET controller = ? WHERE id = ?", (controller, bot_id))

    async def update_rating_and_counters(self, bot_id: int, rating: int, outcome: str) -> None:
        column = _COUNTER_COLUMN[outcome]
        await self._write(
            f"UPDATE bots SET rating = ?, {column} = {column} + 1,"
            " games_played = games_played + 1 WHERE id = ?",
            (rating, bot_id),
        )

    async def update_pool_history(
        self,
        bot_id: int,
        last_color: str,
        last_opponent_id: Optional[int],
        increment_white: bool,
    ) -> None:
        await self._write(
            "UPDATE bots SET last_color = ?, last_opponent_id = ?,"
            " white_count = white_count + ? WHERE id = ?",
            (last_color, last_opponent_id, int(increment_white), bot_id),
        )

    async def update_last_poll(self, bot_id: int, poll_at: str, poll_mono: int) -> None:
        await self._write(
            "UPDATE bots SET last_poll_at = ?, last_poll_mono = ? WHERE id = ?",
            (poll_at, poll_mono, bot_id),
        )

    async def update_last_agent_action(self, bot_id: int, action_mono: int) -> None:
        await self._write(
            "UPDATE bots SET last_agent_action_mono = ? WHERE id = ?", (action_mono, bot_id)
        )

    async def list_leaderboard(self) -> list[BotRow]:
        rows = await self._all(
            "SELECT * FROM bots WHERE role = 'competitor' ORDER BY rating DESC, name"
        )
        return [from_row(BotRow, row) for row in rows]

    async def list_anchors(self) -> list[BotRow]:
        return [from_row(BotRow, row) for row in await self._all(
            "SELECT * FROM bots WHERE is_anchor = 1 ORDER BY id"
        )]

    async def list_agent_controlled(self) -> list[BotRow]:
        return [from_row(BotRow, row) for row in await self._all(
            "SELECT * FROM bots WHERE controller = 'agent' ORDER BY id"
        )]

    async def list_pool_candidates(self, cutoff_mono: int) -> list[BotRow]:
        """§9.1. Recency applies to competitors only — an anchor never polls."""
        rows = await self._all(
            "SELECT b.* FROM bots b"
            " WHERE b.role IN ('competitor', 'anchor')"
            "   AND b.controller = 'client'"
            "   AND NOT EXISTS (SELECT 1 FROM seats s WHERE s.bot_id = b.id)"
            "   AND (b.role <> 'competitor'"
            "        OR (b.last_poll_mono IS NOT NULL AND b.last_poll_mono >= ?))"
            " ORDER BY b.id",
            (cutoff_mono,),
        )
        return [from_row(BotRow, row) for row in rows]
