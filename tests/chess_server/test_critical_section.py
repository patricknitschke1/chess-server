import asyncio
import sqlite3
import time

import pytest

from chess_server.store.txn import critical_section

_INSERT = (
    "INSERT INTO bots (name, owner, token_hash, role, created_at)"
    " VALUES ('{}', 'ada', 'h', 'competitor', '2026-08-24T00:00:00Z')"
)


async def _exec(store, sql):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(store.executor, store.writer.execute, sql)


def _names(store):
    return {row[0] for row in store.reader.execute("SELECT name FROM bots")}


async def _writer_still_works(store):
    async with critical_section(store.writer, store.executor):
        await _exec(store, _INSERT.format("survivor"))
    return "survivor" in _names(store)


class _CancelDuringRollback:
    """Delegating executor that fires a second cancel as ROLLBACK is submitted, and
    holds the statement open long enough that releasing the lock early is observable."""

    def __init__(self, inner):
        self.inner = inner
        self.task = None

    def submit(self, fn, *args, **kwargs):
        if not (args and args[0] == "ROLLBACK"):
            return self.inner.submit(fn, *args, **kwargs)
        if self.task is not None:
            asyncio.get_running_loop().call_soon(self.task.cancel)

        def slow(*a, **k):
            time.sleep(0.05)
            return fn(*a, **k)

        return self.inner.submit(slow, *args, **kwargs)


async def test_cancellation_rolls_back_and_leaves_the_writer_usable(store):
    entered = asyncio.Event()
    never = asyncio.Event()

    async def _stuck():
        async with critical_section(store.writer, store.executor):
            await _exec(store, _INSERT.format("ghost"))
            entered.set()
            await never.wait()

    task = asyncio.create_task(_stuck())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.writer.in_transaction is False
    assert "ghost" not in _names(store)
    assert await _writer_still_works(store)


async def test_cancellation_during_the_rollback_still_rolls_back(store):
    entered = asyncio.Event()
    never = asyncio.Event()
    executor = _CancelDuringRollback(store.executor)

    async def _stuck():
        async with critical_section(store.writer, executor):
            await _exec(store, _INSERT.format("ghost"))
            entered.set()
            await never.wait()

    task = asyncio.create_task(_stuck())
    executor.task = task
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Must already be closed: _finish holds the section open until ROLLBACK lands.
    assert store.writer.in_transaction is False
    assert "ghost" not in _names(store)
    assert await _writer_still_works(store)


async def test_exception_rolls_back_and_re_raises(store):
    with pytest.raises(RuntimeError, match="boom"):
        async with critical_section(store.writer, store.executor):
            await _exec(store, _INSERT.format("ghost"))
            raise RuntimeError("boom")

    assert store.writer.in_transaction is False
    assert "ghost" not in _names(store)
    assert await _writer_still_works(store)


async def test_begin_failure_surfaces_to_the_caller(store):
    await _exec(store, "BEGIN")
    try:
        with pytest.raises(sqlite3.OperationalError):
            async with critical_section(store.writer, store.executor):
                pass
    finally:
        await _exec(store, "ROLLBACK")


async def test_concurrent_sections_serialise(store):
    order = []

    async def worker(tag):
        async with critical_section(store.writer, store.executor):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-out")

    await asyncio.wait_for(asyncio.gather(worker("a"), worker("b")), timeout=5)
    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )


async def test_commit_persists(store):
    async with critical_section(store.writer, store.executor):
        await _exec(store, _INSERT.format("alpha"))
    assert "alpha" in _names(store)
    assert store.writer.in_transaction is False
