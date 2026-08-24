"""The single write lock, the transaction, and the event buffer (role spec §3.7, §3.8)."""
import asyncio
import sqlite3
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

write_lock = asyncio.Lock()

EventSink = Callable[[int, str, dict], None]

# Per-run, module-owned: interfaces Part 2 pins server_run_started at seq 0, and a
# client's gap check is only meaningful if seq restarts with the run.
_next_seq = 0


def _take_seq() -> int:
    global _next_seq
    seq = _next_seq
    _next_seq += 1
    return seq


def reset_seq() -> None:
    global _next_seq
    _next_seq = 0


def current_seq() -> int:
    """The last seq assigned; -1 when nothing has been emitted in this run. This is
    what `/state.event_id` is, and what a client's gap check compares against."""
    return _next_seq - 1


def _drop(seq: int, event_type: str, data: dict) -> None:
    pass


@dataclass
class Txn:
    conn: sqlite3.Connection
    executor: Executor
    sink: EventSink = _drop
    events: list[tuple[str, dict]] = field(default_factory=list)
    deferred: list[Callable[[], None]] = field(default_factory=list)

    def emit(self, event_type: str, data: dict) -> None:
        """Buffer an SSE event. Nothing leaves the process until flush()."""
        self.events.append((event_type, data))

    def defer(self, fn: Callable[[], None]) -> None:
        """Register an in-process mutation to apply only if this transaction commits."""
        self.deferred.append(fn)

    @asynccontextmanager
    async def savepoint(self, name: str) -> AsyncIterator[None]:
        """One unit of work. On failure, roll the rows back and truncate the buffers."""
        events_at_entry = len(self.events)
        deferred_at_entry = len(self.deferred)
        await _finish(self.conn, f"SAVEPOINT {name}", self.executor)
        try:
            yield
        except BaseException:
            await _finish(self.conn, f"ROLLBACK TO {name}", self.executor)
            await _finish(self.conn, f"RELEASE {name}", self.executor)
            del self.events[events_at_entry:]
            del self.deferred[deferred_at_entry:]
            raise
        else:
            await _finish(self.conn, f"RELEASE {name}", self.executor)

    def flush(self) -> None:
        """Assign seq in commit order, fan out, then run the deferred work."""
        for event_type, data in self.events:
            self.sink(_take_seq(), event_type, data)
        self.events.clear()
        for fn in self.deferred:
            fn()
        self.deferred.clear()

    def discard(self) -> None:
        """A rolled-back unit must consume no seq — that is what the gap check reads."""
        self.events.clear()
        self.deferred.clear()


def _execute(conn: sqlite3.Connection, sql: str, executor: Executor):
    return asyncio.get_running_loop().run_in_executor(executor, conn.execute, sql)


async def _finish(conn: sqlite3.Connection, sql: str, executor: Executor) -> None:
    """Run BEGIN / COMMIT / ROLLBACK to completion, even under cancellation.

    A bare `await` here is cancellable. A cancelled ROLLBACK releases write_lock with
    the single writer connection still inside a transaction, after which every later
    BEGIN IMMEDIATE raises "cannot start a transaction within a transaction" — for the
    life of the process.
    """
    fut = asyncio.ensure_future(_execute(conn, sql, executor))
    cancelled: BaseException | None = None
    while not fut.done():
        try:
            await asyncio.shield(fut)
        except asyncio.CancelledError as exc:
            cancelled = exc      # ours, not the statement's: hold it and keep waiting
    fut.result()                 # surface a genuine SQL error
    if cancelled is not None:
        raise cancelled


@asynccontextmanager
async def critical_section(
    conn: sqlite3.Connection, executor: Executor, sink: EventSink = _drop
) -> AsyncIterator[Txn]:
    """Acquire the single writer, open a transaction, yield a Txn, commit or roll back.

    Exactly one COMMIT or ROLLBACK runs before the lock is released, and both run
    to completion even if this task is being cancelled.
    """
    async with write_lock:
        await _finish(conn, "BEGIN IMMEDIATE", executor)
        txn = Txn(conn=conn, executor=executor, sink=sink)
        try:
            yield txn
        except BaseException:
            await _finish(conn, "ROLLBACK", executor)
            txn.discard()
            raise
        else:
            await _finish(conn, "COMMIT", executor)
            txn.flush()
