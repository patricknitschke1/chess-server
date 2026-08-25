"""Ticker supervision (role spec §7.6, design §4.6).

Watches `last_tick_mono`, never `task.done()`: a ticker wedged on an await is the
likelier failure and `done()` never fires for it. The restart is a sequence rather
than a bare cancel, because two tickers are strictly worse than none — the ticker
is the only creator of games, and a second one doubles every sweep while
contending for the same write lock.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from chess_core import is_within

from chess_server.engine.deps import EngineDeps
from chess_server.engine.ticker import TickerMetrics
from chess_server.store.txn import critical_section

logger = logging.getLogger(__name__)

# Ops constants: they affect no game outcome, so chess_core has no opinion on
# them and role spec §2.1's server-local table should gain them.
TICK_WARN_NS = 5_000_000_000
TICK_RESTART_NS = 15_000_000_000
SUPERVISOR_PERIOD_SECONDS = 2.0
CANCEL_WAIT_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 1.0


@dataclass
class Supervisor:
    deps: EngineDeps
    metrics: TickerMetrics
    spawn: Callable[[], asyncio.Task]
    task: asyncio.Task
    cancel_wait: float = field(default=CANCEL_WAIT_SECONDS)
    # Supplied by the app; the emission site stays here because §8.4 pins it to
    # the supervisor's cadence and nothing else runs on that cadence.
    on_health: Optional[Callable[[], Awaitable[None]]] = None

    async def step(self) -> None:
        """One decision. The loop is elsewhere so tests never sleep to observe it."""
        await self._decide()
        if self.on_health is not None:
            await self.on_health()

    async def _decide(self) -> None:
        now_mono = self.deps.now_mono()
        if is_within(self.metrics.last_tick_mono, now_mono, TICK_WARN_NS):
            return
        if is_within(self.metrics.last_tick_mono, now_mono, TICK_RESTART_NS):
            logger.warning("ticker is stale; last completed tick %d", self.metrics.tick_number)
            return
        logger.error(
            "ticker stale past the restart threshold; last completed tick %d",
            self.metrics.tick_number,
        )
        await self._restart()

    async def _restart(self) -> None:
        self.task.cancel()
        done, _ = await asyncio.wait({self.task}, timeout=self.cancel_wait)
        if not done:
            logger.critical(
                "ticker did not stop when cancelled; starting no replacement, because"
                " two tickers would contend for write_lock and double every sweep"
            )
            return
        self.metrics.ticker_restarts += 1
        logger.error("ticker restarted (restart %d)", self.metrics.ticker_restarts)
        self.task = self.spawn()


async def run_supervisor(supervisor: Supervisor) -> None:
    while True:
        await asyncio.sleep(SUPERVISOR_PERIOD_SECONDS)
        try:
            await supervisor.step()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("supervisor step failed")


async def probe_db_writable(deps: EngineDeps) -> bool:
    """Whether BEGIN IMMEDIATE still succeeds — the exact property that fails when
    a cancelled rollback leaves the single writer inside a transaction. 3c's
    /health calls this; it lives here because it is a store property, not a route."""

    async def _probe() -> None:
        async with critical_section(deps.conn, deps.executor):
            pass

    try:
        await asyncio.wait_for(_probe(), timeout=PROBE_TIMEOUT_SECONDS)
    except Exception:  # TimeoutError included; CancelledError deliberately is not
        return False
    return True
