"""Typed SQL wrappers. Transaction control belongs to the caller, never here (§3.6)."""
import asyncio
import sqlite3
from concurrent.futures import Executor
from typing import Optional, Sequence

from chess_core import (
    RATED_TIME_CONTROL_NS,
    STARTING_FEN,
    ClockState,
    Color,
    create_clock,
    ms_to_ns,
    ns_to_ms,
)

from chess_server.store.cas import assert_cas
from chess_server.store.rows import BotRow, GameRow, from_row

NON_TERMINAL = ("pending", "active")

_FEN_SIDE = {"w": Color.WHITE.value, "b": Color.BLACK.value}


def to_move_from_fen(fen: str) -> str:
    """§3.2: the FEN's second field is authoritative. Never ply parity."""
    return _FEN_SIDE[fen.split()[1]]


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


def rated_at_creation(white: BotRow, black: BotRow, time_control_ns: int) -> int:
    """Design §5.3 rules 2-6, first match wins. Rule 1 belongs to finalisation."""
    if "benchmark" in (white.role, black.role):
        return 0
    if white.owner == black.owner:
        return 0
    if time_control_ns != RATED_TIME_CONTROL_NS:
        return 0
    return 1


_SUMMARY_SELECT = """
SELECT g.id AS game_id, g.white_bot_id, g.black_bot_id, g.status, g.fen, g.to_move,
       g.ply, g.white_ms, g.black_ms, g.rated, g.turn_started_mono,
       g.to_move_since_mono, g.delivered_to_mover,
       w.name AS white_bot_name, w.rating AS white_rating,
       b.name AS black_bot_name, b.rating AS black_rating
  FROM games g
  JOIN bots w ON w.id = g.white_bot_id
  JOIN bots b ON b.id = g.black_bot_id
"""

_MOVER_JOIN = (
    " JOIN bots m ON m.id = CASE g.to_move WHEN 'white' THEN g.white_bot_id"
    "                                      ELSE g.black_bot_id END"
)


class GameRepo(_Repo):
    async def insert_game(
        self,
        white: BotRow,
        black: BotRow,
        time_control_ns: int,
        increment_ns: int,
        source: str,
        now_mono: int,
        created_at: str,
        fen: str = STARTING_FEN,
    ) -> int:
        clock = create_clock(time_control_ns, increment_ns, Color(to_move_from_fen(fen)), now_mono)
        fields = _clock_to_game_fields(clock)
        cursor = await self._write(
            "INSERT INTO games (white_bot_id, black_bot_id, status, fen, ply, to_move,"
            " white_ms, black_ms, time_control_ms, increment_ms, to_move_since_mono,"
            " turn_started_mono, delivered_to_mover, rated, source, white_strikes,"
            " black_strikes, created_at)"
            " VALUES (?, ?, 'pending', ?, 0, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, 0, 0, ?)",
            (
                white.id,
                black.id,
                fen,
                fields["to_move"],
                fields["white_ms"],
                fields["black_ms"],
                ns_to_ms(time_control_ns),
                ns_to_ms(increment_ns),
                fields["to_move_since_mono"],
                rated_at_creation(white, black, time_control_ns),
                source,
                created_at,
            ),
        )
        return cursor.lastrowid

    async def get_by_id(self, game_id: int) -> Optional[GameRow]:
        return from_row(GameRow, await self._one("SELECT * FROM games WHERE id = ?", (game_id,)))

    async def get_for_bot(self, bot_id: int) -> Optional[GameRow]:
        """§7.1: a game is reachable only through seats, never by scanning games."""
        return from_row(
            GameRow,
            await self._one(
                "SELECT g.* FROM games g JOIN seats s ON s.game_id = g.id WHERE s.bot_id = ?",
                (bot_id,),
            ),
        )

    async def list_undelivered_non_terminal(self) -> list[GameRow]:
        rows = await self._all(
            "SELECT * FROM games WHERE delivered_to_mover = 0"
            " AND status IN (?, ?) ORDER BY id",
            NON_TERMINAL,
        )
        return [from_row(GameRow, row) for row in rows]

    async def list_delivered_active(self) -> list[GameRow]:
        rows = await self._all(
            "SELECT * FROM games WHERE delivered_to_mover = 1 AND status = 'active' ORDER BY id"
        )
        return [from_row(GameRow, row) for row in rows]

    async def list_anchor_to_move(self) -> list[GameRow]:
        rows = await self._all(
            "SELECT g.* FROM games g" + _MOVER_JOIN
            + " WHERE g.status IN (?, ?) AND m.is_anchor = 1 ORDER BY g.id",
            NON_TERMINAL,
        )
        return [from_row(GameRow, row) for row in rows]

    async def list_active_summaries(self) -> list[dict]:
        rows = await self._all(
            _SUMMARY_SELECT + " WHERE g.status IN (?, ?) ORDER BY g.id", NON_TERMINAL
        )
        return [dict(row) for row in rows]

    async def cas_terminate(
        self,
        game_id: int,
        from_status: str,
        from_ply: int,
        status: str,
        result: Optional[str],
        termination: str,
        ended_at: str,
        rated: Optional[int] = None,
    ) -> None:
        """Clearing delivery here keeps a terminal game out of the sweep (§3.10)."""
        cursor = await self._write(
            "UPDATE games"
            "   SET status = ?, result = ?, termination = ?, ended_at = ?,"
            "       rated = COALESCE(?, rated),"
            "       delivered_to_mover = 0, turn_started_mono = NULL"
            " WHERE id = ? AND status = ? AND ply = ?",
            (status, result, termination, ended_at, rated, game_id, from_status, from_ply),
        )
        assert_cas(cursor)

    async def cas_apply_move(
        self, game_id: int, from_ply: int, from_status: str, fen_after: str, clock: ClockState
    ) -> None:
        fields = _clock_to_game_fields(clock)
        cursor = await self._write(
            "UPDATE games"
            "   SET fen = ?, ply = ply + 1, to_move = ?, white_ms = ?, black_ms = ?,"
            "       to_move_since_mono = ?, turn_started_mono = ?, delivered_to_mover = ?"
            " WHERE id = ? AND ply = ? AND status = ?",
            (
                fen_after,
                to_move_from_fen(fen_after),   # the FEN is authoritative, not the clock
                fields["white_ms"],
                fields["black_ms"],
                fields["to_move_since_mono"],
                fields["turn_started_mono"],
                fields["delivered_to_mover"],
                game_id,
                from_ply,
                from_status,
            ),
        )
        assert_cas(cursor)

    async def cas_deliver(
        self, game_id: int, ply: int, now_mono: int, now_wall: str
    ) -> tuple[bool, bool]:
        """§5.2. rowcount 0 means already delivered, which is free by design, not a
        conflict — so this is the one transition that never calls assert_cas."""
        before = await self._one("SELECT status FROM games WHERE id = ?", (game_id,))
        cursor = await self._write(
            "UPDATE games"
            "   SET turn_started_mono = ?, delivered_to_mover = 1,"
            "       status = CASE WHEN status = 'pending' THEN 'active' ELSE status END,"
            "       started_at = CASE WHEN status = 'pending' THEN ? ELSE started_at END"
            " WHERE id = ? AND ply = ? AND delivered_to_mover = 0 AND status IN (?, ?)",
            (now_mono, now_wall, game_id, ply, *NON_TERMINAL),
        )
        delivered = cursor.rowcount == 1
        return delivered, delivered and before["status"] == "pending"


class SeatRepo(_Repo):
    async def insert_seat(self, bot_id: int, game_id: int) -> None:
        await self._write("INSERT INTO seats (bot_id, game_id) VALUES (?, ?)", (bot_id, game_id))
