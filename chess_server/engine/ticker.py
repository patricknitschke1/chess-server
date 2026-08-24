"""The one background ticker (role spec §7). The only creator of games."""
import asyncio
import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional, Sequence

from chess_core import (
    AGENT_DELIVERY_GRACE_NS,
    DELIVERY_GRACE_NS,
    TICK_INTERVAL_NS,
    TerminationReason,
    check_delivery_timeout,
    elapsed_ms,
)

from chess_server.engine.deps import EngineDeps
from chess_server.engine.games import abort_game_locked, finalise_game_locked, opposite_win
from chess_server.engine.runner import _mover_id
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import BotRepo, GameRepo, _clock_from_game
from chess_server.store.txn import Txn, critical_section

logger = logging.getLogger(__name__)

# The literal is a float so it cannot collide with TICK_INTERVAL_NS's own value.
TICK_INTERVAL_SECONDS = TICK_INTERVAL_NS / 1e9

Step = Callable[[EngineDeps, Txn, int], Awaitable[None]]

_SAFE_SAVEPOINT = re.compile(r"\A[A-Za-z][A-Za-z0-9_]*\Z")


@dataclass
class TickerMetrics:
    """Written by the ticker; read by the supervisor and, in 3c, by /health."""
    tick_number: int = 0
    last_tick_mono: int = 0
    last_tick_duration_ms: int = 0
    consecutive_tick_errors: int = 0
    ticker_restarts: int = 0


@asynccontextmanager
async def _unit(txn: Txn, name: str) -> AsyncIterator[None]:
    """One unit of work: one savepoint, and the only place the swallow is written.

    Design §4.3 argued this for pairing; it holds identically for every other
    per-game action. Without it one conflict at the flag step silently discards
    every pairing and challenge the same tick made, because a rolled-back CAS
    is not an error.
    """
    if not _SAFE_SAVEPOINT.match(name):
        raise ValueError(f"savepoint name reaches SQL text verbatim: {name!r}")
    try:
        async with txn.savepoint(name):
            yield
    except (CASConflict, sqlite3.IntegrityError) as exc:
        logger.info("unit %s rolled back: %s", name, exc)


STEPS: list[Step] = []


async def step_delivery_grace(deps: EngineDeps, txn: Txn, now_mono: int) -> None:
    """Role spec §7.4. `list_undelivered_non_terminal` carries the status filter;
    broadening it re-admits every finished game forever, silently, because the
    finalisation CAS then returns rowcount 0 rather than an error."""
    games = GameRepo(txn.conn, txn.executor)
    bots = BotRepo(txn.conn, txn.executor)
    for game in await games.list_undelivered_non_terminal():
        async with _unit(txn, f"grace_{game.id}"):
            mover = await bots.get_by_id(_mover_id(game))
            # The grace belongs to the bot to move; `games` has no controller column.
            grace_ns = (
                AGENT_DELIVERY_GRACE_NS if mover.controller == "agent" else DELIVERY_GRACE_NS
            )
            if not check_delivery_timeout(_clock_from_game(game), now_mono, grace_ns):
                continue
            if game.ply == 0:
                await abort_game_locked(deps, txn, game, TerminationReason.NO_SHOW)
            else:
                # The server never writes `crash` (design §22).
                await finalise_game_locked(
                    deps, txn, game, opposite_win(game.to_move), TerminationReason.ABANDONED
                )



async def _tick_once(
    deps: EngineDeps, metrics: TickerMetrics, steps: Optional[Sequence[Step]] = None
) -> None:
    """The single-step entry point. Every ticker test drives this, never a sleep."""
    started = deps.now_mono()
    metrics.tick_number += 1          # before the body, so an error log names the tick
    async with critical_section(deps.conn, deps.executor, deps.sink) as txn:
        for step in (STEPS if steps is None else steps):
            await step(deps, txn, started)
    metrics.last_tick_mono = started
    metrics.last_tick_duration_ms = elapsed_ms(started, deps.now_mono())


async def run_ticker(deps: EngineDeps, metrics: TickerMetrics) -> None:
    """Never exits. A tick that raises is logged and the next one still runs."""
    while True:
        try:
            await _tick_once(deps, metrics)
        except asyncio.CancelledError:
            raise
        except Exception:
            metrics.consecutive_tick_errors += 1
            logger.exception("tick %d failed", metrics.tick_number)
        else:
            metrics.consecutive_tick_errors = 0
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
