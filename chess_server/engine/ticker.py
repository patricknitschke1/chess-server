"""The one background ticker (role spec §7). The only creator of games."""
import asyncio
import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional, Sequence

import chess

from chess_core import (
    DELIVERY_GRACE_NS,
    EXHIBITION_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    TICK_INTERVAL_NS,
    ClockView,
    Color,
    TerminationReason,
    check_delivery_timeout,
    has_flagged,
    elapsed_ms,
    is_within,
    ms_to_ns,
    ns_to_ms,
    pair_bots,
    window_start_mono,
)

from chess_server.engine import state
from chess_server.engine.deps import EngineDeps
from chess_server.engine.games import (
    abort_game_locked,
    create_game_locked,
    finalise_game_locked,
    opposite_win,
)
from chess_server.engine.pool import offer_anchors, snapshot_pool
from chess_server.engine.reference_bots import bot_for
from chess_server.engine.runner import _mover_id, apply_move_locked, deliver_position_locked
from chess_server.engine.wall import utc_now_iso
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import (
    BotRepo,
    GameRepo,
    SeatRepo,
    _clock_from_game,
)
from chess_server.store.rows import GameRow
from chess_server.store.txn import Txn, critical_section

logger = logging.getLogger(__name__)

# The literal is a float so it cannot collide with TICK_INTERVAL_NS's own value.
TICK_INTERVAL_SECONDS = TICK_INTERVAL_NS / 1e9

# Server-local (role spec §2.1): presence is a dashboard concern, so chess_core
# has no opinion on it and there is nothing to import.
DISCONNECT_AFTER_NS = 30_000_000_000

Step = Callable[[EngineDeps, Txn, int], Awaitable[None]]

_SAFE_SAVEPOINT = re.compile(r"\A[A-Za-z][A-Za-z0-9_]*\Z")


class AnchorMoveFailed(Exception):
    """Our own code failed, not an attendee's. Rolls back one unit, never the tick."""


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
    every pairing the same tick made, because a rolled-back CAS is not an error.
    """
    if not _SAFE_SAVEPOINT.match(name):
        raise ValueError(f"savepoint name reaches SQL text verbatim: {name!r}")
    try:
        async with txn.savepoint(name):
            yield
    except (CASConflict, sqlite3.IntegrityError, AnchorMoveFailed) as exc:
        logger.info("unit %s rolled back: %s", name, exc)


EXHIBITION_TIME_CONTROL_MS = ns_to_ms(EXHIBITION_TIME_CONTROL_NS)


async def step_matchmaking(deps: EngineDeps, txn: Txn, now_mono: int) -> None:
    """Role spec §7.2 (matchmaking half). Steps 1-7 of that list, in order."""
    if deps.is_paused():
        return
    pool = await snapshot_pool(txn, now_mono)
    competitors = [entry for entry in pool if not entry.is_anchor]
    anchors = [entry for entry in pool if entry.is_anchor]

    # Anchors are deliberately not passed in, so pair_bots never sees an
    # anchor-versus-anchor option and the §9.3 gate cannot be bypassed.
    pairings = pair_bots(competitors)
    paired_ids = {p.white_bot_id for p in pairings} | {p.black_bot_id for p in pairings}
    for competitor, anchor in offer_anchors(competitors, anchors, paired_ids):
        # §9.2's colour precedence stays in one place.
        pairings += pair_bots([competitor, anchor])

    bots = BotRepo(txn.conn, txn.executor)
    seated: set[int] = set()
    for pairing in pairings:
        white_id, black_id = pairing.white_bot_id, pairing.black_bot_id
        async with _unit(txn, f"pair_{white_id}_{black_id}"):
            await create_game_locked(
                deps, txn,
                await bots.get_by_id(white_id),
                await bots.get_by_id(black_id),
                time_control_ns=RATED_TIME_CONTROL_NS,
                increment_ns=RATED_INCREMENT_NS,
                source="matchmaker",
                now_mono=now_mono,
            )
            seated |= {white_id, black_id}

    # Deferred with everything else the tick did: a rolled-back tick must not
    # leave the relaxation counter a tick ahead of the database.
    idle = [entry.bot_id for entry in pool if entry.bot_id not in seated]
    txn.defer(lambda: _record_pairing_outcome(seated, idle))


def _record_pairing_outcome(seated: set[int], idle: list[int]) -> None:
    """Design §9.4. Popping is the reset: a seated bot has left the pool, and its
    next spell starts from 0 either way."""
    for bot_id in seated:
        state.unpaired_ticks.pop(bot_id, None)
    for bot_id in idle:
        state.unpaired_ticks[bot_id] = state.unpaired_ticks.get(bot_id, 0) + 1


def _clock_view(game: GameRow) -> ClockView:
    """`my_ms` is always the mover's, which is what removes colour-indexing as a
    class of bug for every bot, ours included."""
    mine, theirs = (
        (game.white_ms, game.black_ms)
        if game.to_move == Color.WHITE.value
        else (game.black_ms, game.white_ms)
    )
    return ClockView(
        my_ms=mine, opponent_ms=theirs, increment_ms=game.increment_ms, ply=game.ply
    )


async def step_anchor_moves(deps: EngineDeps, txn: Txn, now_mono: int) -> None:
    """Role spec §7.3. An anchor has no HTTP client, so the in-process call is
    itself the delivery — nothing else will ever deliver to it or move for it."""
    games = GameRepo(txn.conn, txn.executor)
    bots = BotRepo(txn.conn, txn.executor)
    for game in await games.list_anchor_to_move():
        async with _unit(txn, f"anchor_{game.id}"):
            mover = await bots.get_by_id(_mover_id(game))
            player = bot_for(mover.name)
            if player is None:
                logger.error("no reference bot named %r for game %d", mover.name, game.id)
                continue
            # Starts the clock and moves pending -> active. Inside the unit, so a
            # failure below un-delivers and step 4 abandons rather than wedging.
            await deliver_position_locked(deps, txn, game, now_mono)
            game = await games.get_by_id(game.id)
            try:
                move = player.choose_move(chess.Board(game.fen), _clock_view(game))
            except Exception as exc:
                logger.exception(
                    "anchor %s failed in game %d at fen %s", mover.name, game.id, game.fen
                )
                raise AnchorMoveFailed(f"{mover.name} in game {game.id}") from exc
            # The same locked path a client's move takes: the anchor is charged
            # real time, and there is no second move implementation to drift.
            await apply_move_locked(
                deps, txn, game.id, game.ply, move.uci(),
                client_reported_ms=None,
                now_mono=deps.now_mono(),
            )



async def step_delivery_grace(deps: EngineDeps, txn: Txn, now_mono: int) -> None:
    """Role spec §7.4. `list_undelivered_non_terminal` carries the status filter;
    broadening it re-admits every finished game forever, silently, because the
    finalisation CAS then returns rowcount 0 rather than an error."""
    games = GameRepo(txn.conn, txn.executor)
    for game in await games.list_undelivered_non_terminal():
        async with _unit(txn, f"grace_{game.id}"):
            if not check_delivery_timeout(_clock_from_game(game), now_mono, DELIVERY_GRACE_NS):
                continue
            if game.ply == 0:
                await abort_game_locked(deps, txn, game, TerminationReason.NO_SHOW)
            else:
                # The server never writes `crash` (design §22).
                await finalise_game_locked(
                    deps, txn, game, opposite_win(game.to_move), TerminationReason.ABANDONED
                )


async def step_flag(deps: EngineDeps, txn: Txn, now_mono: int) -> None:
    """Role spec §7.5. `has_flagged` is the single declaration of design §6.4's
    flag-fall predicate; chess_server never derives time left of its own."""
    games = GameRepo(txn.conn, txn.executor)
    for game in await games.list_delivered_active():
        async with _unit(txn, f"flag_{game.id}"):
            if has_flagged(_clock_from_game(game), now_mono):
                await finalise_game_locked(
                    deps, txn, game, opposite_win(game.to_move), TerminationReason.FLAG
                )


async def step_presence(deps: EngineDeps, txn: Txn, now_mono: int) -> None:
    """Role spec §7.5. Edge-triggered against `state.connected`, so each event fires
    once per transition rather than once per tick. Writes no rows at all.
    """
    for bot in await BotRepo(txn.conn, txn.executor).list_presence_candidates():
        recent = bot.last_poll_mono is not None and is_within(
            bot.last_poll_mono, now_mono, DISCONNECT_AFTER_NS
        )
        if recent == (bot.id in state.connected):
            continue
        txn.emit(
            "bot_connected" if recent else "bot_disconnected",
            {"bot_id": bot.id, "bot_name": bot.name},
        )
        # Deferred, so a rolled-back tick does not consume the edge that its
        # discarded event was the only record of.
        if recent:
            txn.defer(lambda bot_id=bot.id: state.connected.add(bot_id))
        else:
            txn.defer(lambda bot_id=bot.id: state.connected.discard(bot_id))


# Role spec §7: the order is a production fact, not a test argument.
STEPS: list[Step] = [
    step_matchmaking,
    step_anchor_moves,
    step_delivery_grace,
    step_flag,
    step_presence,
]


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
    # Seeded before the loop: 0 reads as infinitely stale, so a supervisor step
    # landing before the first completed tick would restart a healthy ticker.
    metrics.last_tick_mono = deps.now_mono()
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
