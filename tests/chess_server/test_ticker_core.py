"""Test task 11: the ticker frame — one transaction, a savepoint per unit."""
import asyncio
import sqlite3

import pytest

from chess_core import TICK_INTERVAL_NS
from chess_server.engine.ticker import TickerMetrics, _tick_once, _unit, run_ticker
from chess_server.store import txn as txn_module
from chess_server.store.cas import CASConflict


def emitting_step(name: str, *, raises: BaseException | None = None):
    async def step(deps, txn, now_mono):
        async with _unit(txn, name):
            txn.emit("game_created", {"unit": name})
            if raises is not None:
                raise raises

    return step


async def test_a_conflicted_unit_discards_only_its_own_work(deps, sink):
    metrics = TickerMetrics()
    steps = [
        emitting_step("first"),
        emitting_step("second", raises=CASConflict("clash")),
        emitting_step("third"),
    ]
    await _tick_once(deps, metrics, steps=steps)

    assert [data["unit"] for _, _, data in sink.events] == ["first", "third"]
    assert [seq for seq, _, _ in sink.events] == [0, 1]  # the middle unit took no seq


async def test_an_integrity_error_in_one_unit_behaves_the_same(deps, sink):
    metrics = TickerMetrics()
    steps = [
        emitting_step("first"),
        emitting_step("second", raises=sqlite3.IntegrityError("UNIQUE failed: seats.bot_id")),
        emitting_step("third"),
    ]
    await _tick_once(deps, metrics, steps=steps)

    assert [data["unit"] for _, _, data in sink.events] == ["first", "third"]
    assert [seq for seq, _, _ in sink.events] == [0, 1]


async def test_a_conflicted_unit_rolls_its_rows_back_but_not_its_neighbours(deps, seed_bots):
    await seed_bots("alpha")
    metrics = TickerMetrics()

    async def insert(name, boom):
        async def step(deps, txn, now_mono):
            async with _unit(txn, name):
                await asyncio.get_running_loop().run_in_executor(
                    txn.executor,
                    lambda: txn.conn.execute(
                        "INSERT INTO bots (name, owner, token_hash, role, rating, is_anchor,"
                        " created_at) VALUES (?, ?, ?, 'competitor', 1000, 0,"
                        " '2026-08-24T00:00:00Z')",
                        (name, name, f"hash-{name}"),
                    ),
                )
                if boom:
                    raise CASConflict("clash")

        return step

    await _tick_once(deps, metrics, steps=[
        await insert("kept", False),
        await insert("rolled", True),
        await insert("also_kept", False),
    ])

    rows = deps.conn.execute("SELECT name FROM bots ORDER BY id").fetchall()
    assert [r["name"] for r in rows] == ["alpha", "kept", "also_kept"]


async def test_a_non_cas_error_propagates_and_run_ticker_survives_it(deps, monkeypatch):
    metrics = TickerMetrics()
    boom = emitting_step("boom", raises=RuntimeError("not a conflict"))

    with pytest.raises(RuntimeError):
        await _tick_once(deps, metrics, steps=[boom])

    ticks = []

    async def one_step(deps, txn, now_mono):
        ticks.append(metrics.tick_number)
        if len(ticks) == 1:
            raise RuntimeError("not a conflict")

    monkeypatch.setattr("chess_server.engine.ticker.STEPS", [one_step])

    async def no_sleep(_seconds):
        if len(ticks) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    metrics = TickerMetrics()
    with pytest.raises(asyncio.CancelledError):
        await run_ticker(deps, metrics)

    assert len(ticks) == 2                      # the second tick ran after the first died
    assert metrics.consecutive_tick_errors == 0  # and the clean tick reset the counter


async def test_consecutive_tick_errors_counts_up_then_resets(deps, monkeypatch):
    metrics = TickerMetrics()
    outcomes = [RuntimeError("a"), RuntimeError("b"), None]

    async def scripted(deps, txn, now_mono):
        failure = outcomes[metrics.tick_number - 1]
        if failure is not None:
            raise failure

    monkeypatch.setattr("chess_server.engine.ticker.STEPS", [scripted])

    seen = []

    async def no_sleep(_seconds):
        seen.append(metrics.consecutive_tick_errors)
        if len(seen) == len(outcomes):
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        await run_ticker(deps, metrics)

    assert seen == [1, 2, 0]


async def test_a_tick_acquires_the_write_lock_exactly_once(deps, monkeypatch):
    class CountingLock(asyncio.Lock):
        acquires = 0

        async def acquire(self):
            CountingLock.acquires += 1
            return await super().acquire()

    monkeypatch.setattr(txn_module, "write_lock", CountingLock())
    await _tick_once(deps, TickerMetrics(), steps=[emitting_step("only")])
    assert CountingLock.acquires == 1


async def test_metrics_move_every_tick(deps, clock):
    metrics = TickerMetrics()
    assert metrics.tick_number == 0

    clock.set(4_000_000_000)
    await _tick_once(deps, metrics, steps=[])
    assert metrics.tick_number == 1
    assert metrics.last_tick_mono == 4_000_000_000

    clock.set(4_000_000_000 + TICK_INTERVAL_NS)
    await _tick_once(deps, metrics, steps=[])
    assert metrics.tick_number == 2
    assert metrics.last_tick_mono == 4_000_000_000 + TICK_INTERVAL_NS


async def test_a_units_duration_is_measured_from_the_injected_clock(deps, clock):
    metrics = TickerMetrics()

    async def slow(deps, txn, now_mono):
        clock.advance(250_000_000)

    await _tick_once(deps, metrics, steps=[slow])
    assert metrics.last_tick_duration_ms == 250


def test_the_step_list_is_ordered_and_owned_by_the_module():
    from chess_server.engine import ticker

    assert isinstance(ticker.STEPS, list)
