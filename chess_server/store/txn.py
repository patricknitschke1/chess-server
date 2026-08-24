"""The single write lock, the transaction, and the event buffer (role spec §3.7, §3.8)."""
import asyncio
import sqlite3
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

write_lock = asyncio.Lock()


@dataclass
class Txn:
    conn: sqlite3.Connection
    executor: Executor

    def flush(self) -> None:
        pass

    def discard(self) -> None:
        pass


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
    conn: sqlite3.Connection, executor: Executor
) -> AsyncIterator[Txn]:
    """Acquire the single writer, open a transaction, yield a Txn, commit or roll back.

    Exactly one COMMIT or ROLLBACK runs before the lock is released, and both run
    to completion even if this task is being cancelled.
    """
    async with write_lock:
        await _finish(conn, "BEGIN IMMEDIATE", executor)
        txn = Txn(conn=conn, executor=executor)
        try:
            yield txn
        except BaseException:
            await _finish(conn, "ROLLBACK", executor)
            txn.discard()
            raise
        else:
            await _finish(conn, "COMMIT", executor)
            txn.flush()
