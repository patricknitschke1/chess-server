"""Everything a request needs, in one object hung off the app.

Routes reach it through a single dependency and never import a module global:
a test builds its own `AppState` and the process-wide names stay unshared.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Optional

from chess_server.api.settings import Settings
from chess_server.engine.deps import EngineDeps
from chess_server.engine.ticker import TickerMetrics
from chess_server.store.db import Store


@dataclass
class AppState:
    store: Store
    settings: Settings
    metrics: TickerMetrics = field(default_factory=TickerMetrics)
    matchmaking_paused: bool = False
    ticker_task: Optional[asyncio.Task] = None
    supervisor_task: Optional[asyncio.Task] = None
    deps: EngineDeps = field(init=False)

    def __post_init__(self) -> None:
        self.deps = EngineDeps(
            conn=self.store.writer,
            executor=self.store.executor,
            is_paused=lambda: self.matchmaking_paused,
        )
