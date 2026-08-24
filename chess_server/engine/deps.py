"""The seams the engine takes from phase 3c, and the one clock it is allowed to read.

Every default here is inert, so every engine test runs with no HTTP server. The
monotonic clock is injected because tests drive elapsed time; wall clock is not,
because it is display-only and never decides anything.
"""
import sqlite3
import time
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Callable

from chess_server.store.txn import EventSink, _drop


def _no_wake(bot_id: int) -> None:
    pass


def _never_paused() -> bool:
    return False


@dataclass
class EngineDeps:
    conn: sqlite3.Connection
    executor: Executor
    sink: EventSink = _drop
    wake: Callable[[int], None] = _no_wake
    is_paused: Callable[[], bool] = _never_paused
    now_mono: Callable[[], int] = time.monotonic_ns
