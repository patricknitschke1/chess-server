"""Everything a request needs, in one object hung off the app.

Routes reach it through a single dependency and never import a module global:
a test builds its own `AppState` and the process-wide names stay unshared.
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from fastapi import Request

from chess_server.api.rate_limit import RateLimiter, register_limiter
from chess_server.api.settings import Settings
from chess_server.engine.deps import EngineDeps
from chess_server.engine.mailbox import WaiterRegistry
from chess_server.engine.ticker import TickerMetrics
from chess_server.store.db import Store
from chess_server.store.txn import EventSink, _drop


@dataclass
class AppState:
    store: Store
    settings: Settings
    sink: EventSink = _drop
    now_mono: Callable[[], int] = time.monotonic_ns
    metrics: TickerMetrics = field(default_factory=TickerMetrics)
    matchmaking_paused: bool = False
    ticker_task: Optional[asyncio.Task] = None
    supervisor_task: Optional[asyncio.Task] = None
    limiter: RateLimiter = field(default_factory=RateLimiter)
    register_limiter: RateLimiter = field(default_factory=register_limiter)
    waiters: WaiterRegistry = field(default_factory=WaiterRegistry)
    deps: EngineDeps = field(init=False)

    def __post_init__(self) -> None:
        self.deps = EngineDeps(
            conn=self.store.writer,
            executor=self.store.executor,
            sink=self.sink,
            wake=self.waiters.wake,
            is_paused=lambda: self.matchmaking_paused,
            now_mono=self.now_mono,
        )


def get_state(request: Request) -> AppState:
    """The single seam between a handler and the process. Routes never import a
    module global, so a test builds its own AppState and shares nothing."""
    return request.app.state.arena
