import asyncio
import sqlite3

import pytest

from chess_server.store.txn import critical_section, reset_seq

_INSERT = (
    "INSERT INTO bots (name, owner, token_hash, role, created_at)"
    " VALUES ('{}', 'ada', 'h', 'competitor', '2026-08-24T00:00:00Z')"
)


@pytest.fixture(autouse=True)
def _fresh_seq():
    reset_seq()


class _Sink:
    def __init__(self, conn):
        self.conn = conn
        self.received = []

    def __call__(self, seq, event_type, data):
        self.received.append((seq, event_type, data, self.conn.in_transaction))

    @property
    def types(self):
        return [row[1] for row in self.received]

    @property
    def seqs(self):
        return [row[0] for row in self.received]


async def _exec(store, sql):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(store.executor, store.writer.execute, sql)


def _names(store):
    return {row[0] for row in store.reader.execute("SELECT name FROM bots")}


async def test_rollback_discards_events_and_consumes_no_seq(store):
    sink = _Sink(store.writer)

    with pytest.raises(RuntimeError):
        async with critical_section(store.writer, store.executor, sink) as txn:
            txn.emit("doomed", {})
            raise RuntimeError("boom")

    assert sink.received == []

    async with critical_section(store.writer, store.executor, sink) as txn:
        txn.emit("survivor", {})

    assert sink.seqs == [0]  # the rolled-back event burned nothing


async def test_deferred_work_runs_only_after_a_commit(store):
    sink = _Sink(store.writer)
    ran = []

    with pytest.raises(RuntimeError):
        async with critical_section(store.writer, store.executor, sink) as txn:
            txn.defer(lambda: ran.append("doomed"))
            raise RuntimeError("boom")

    assert ran == []

    async with critical_section(store.writer, store.executor, sink) as txn:
        txn.defer(lambda: ran.append("committed"))
        assert ran == []

    assert ran == ["committed"]


async def test_savepoint_rollback_truncates_rows_events_and_deferred(store):
    sink = _Sink(store.writer)
    ran = []

    async with critical_section(store.writer, store.executor, sink) as txn:
        async with txn.savepoint("unit_a"):
            await _exec(store, _INSERT.format("alpha"))
            txn.emit("unit_a_done", {})
            txn.defer(lambda: ran.append("a"))

        with pytest.raises(sqlite3.IntegrityError):
            async with txn.savepoint("unit_b"):
                await _exec(store, _INSERT.format("beta"))
                txn.emit("unit_b_done", {})
                txn.defer(lambda: ran.append("b"))
                await _exec(store, _INSERT.format("beta"))

    assert _names(store) == {"alpha"}
    assert sink.types == ["unit_a_done"]
    assert ran == ["a"]


async def test_nested_savepoints_release(store):
    sink = _Sink(store.writer)

    async with critical_section(store.writer, store.executor, sink) as txn:
        async with txn.savepoint("outer"):
            await _exec(store, _INSERT.format("alpha"))
            async with txn.savepoint("inner"):
                await _exec(store, _INSERT.format("beta"))

    assert _names(store) == {"alpha", "beta"}
    assert store.writer.in_transaction is False


async def test_no_event_reaches_the_sink_before_commit_returns(store):
    sink = _Sink(store.writer)

    async with critical_section(store.writer, store.executor, sink) as txn:
        txn.emit("game_started", {"game_id": 1})
        assert sink.received == []

    assert [row[3] for row in sink.received] == [False]


async def test_events_flush_in_emit_order_with_contiguous_seq(store):
    sink = _Sink(store.writer)

    async with critical_section(store.writer, store.executor, sink) as txn:
        txn.emit("first", {})
        txn.emit("second", {})
    async with critical_section(store.writer, store.executor, sink) as txn:
        txn.emit("third", {})

    assert sink.types == ["first", "second", "third"]
    assert sink.seqs == [0, 1, 2]
