"""Role spec §7.6, design §4.6: the supervisor, and the remedy that must not
be worse than the disease. Two tickers contend for write_lock and double every
sweep, so a cancel that does not take must produce no replacement at all.
"""
import asyncio
import logging

import pytest

from chess_core import TICK_INTERVAL_NS
from chess_server.engine.supervisor import (
    TICK_RESTART_NS,
    TICK_WARN_NS,
    Supervisor,
    probe_db_writable,
)
from chess_server.engine.ticker import TickerMetrics
from chess_server.store.txn import critical_section


class Obedient:
    """A ticker wedged on an await: `done()` is False, but it accepts cancellation."""

    def __init__(self):
        self.started = asyncio.Event()

    async def __call__(self) -> None:
        self.started.set()
        await asyncio.Event().wait()


class Defiant:
    """The failure the supervisor must not compound: a task that swallows
    CancelledError and keeps running. Only `stop` ends it, so tests can clean up."""

    def __init__(self):
        self.stop = False
        self.started = asyncio.Event()

    async def __call__(self) -> None:
        self.started.set()
        while not self.stop:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass


class Spawner:
    def __init__(self, body):
        self.body = body
        self.tasks: list[asyncio.Task] = []

    def __call__(self) -> asyncio.Task:
        task = asyncio.create_task(self.body())
        self.tasks.append(task)
        return task

    def live(self) -> int:
        return sum(1 for task in self.tasks if not task.done())


async def _quiet(spawner: Spawner) -> None:
    if isinstance(spawner.body, Defiant):
        spawner.body.stop = True
    for task in spawner.tasks:
        task.cancel()
    if spawner.tasks:
        await asyncio.wait(spawner.tasks, timeout=1)


@pytest.fixture
async def supervised(deps, clock):
    """A supervisor over a live ticker task, with the clock reading 'just ticked'."""
    made = []

    async def _build(body, **kw):
        spawner = Spawner(body)
        metrics = TickerMetrics(tick_number=7, last_tick_mono=clock())
        sup = Supervisor(
            deps=deps, metrics=metrics, spawn=spawner, task=spawner(), **kw
        )
        made.append(spawner)
        # Cancelling a task that has never run cancels it outright, which would
        # make even the defiant ticker look obedient.
        await body.started.wait()
        return sup, spawner, metrics

    yield _build
    for spawner in made:
        await _quiet(spawner)


async def test_a_fresh_tick_warns_nothing_and_restarts_nothing(supervised, caplog):
    sup, spawner, metrics = await supervised(Obedient())
    with caplog.at_level(logging.WARNING):
        await sup.step()
    assert caplog.records == []
    assert metrics.ticker_restarts == 0
    assert spawner.live() == 1


async def test_six_seconds_stale_warns_and_names_the_tick_but_does_not_restart(
    supervised, clock, caplog
):
    sup, spawner, metrics = await supervised(Obedient())
    clock.advance(TICK_WARN_NS + TICK_INTERVAL_NS)
    with caplog.at_level(logging.WARNING):
        await sup.step()
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "7" in caplog.records[0].getMessage()
    assert metrics.ticker_restarts == 0
    assert spawner.live() == 1
    assert len(spawner.tasks) == 1


async def test_sixteen_seconds_stale_cancels_and_starts_exactly_one_replacement(
    supervised, clock
):
    sup, spawner, metrics = await supervised(Obedient())
    original = sup.task
    clock.advance(TICK_RESTART_NS + TICK_INTERVAL_NS)
    await sup.step()
    assert metrics.ticker_restarts == 1
    assert original.cancelled()
    assert sup.task is not original
    assert not sup.task.done()
    assert spawner.live() == 1


async def test_a_cancel_that_does_not_take_creates_no_second_ticker(
    supervised, clock, caplog
):
    sup, spawner, metrics = await supervised(Defiant(), cancel_wait=0.05)
    original = sup.task
    clock.advance(TICK_RESTART_NS + TICK_INTERVAL_NS)
    with caplog.at_level(logging.CRITICAL):
        await sup.step()
    # Asserted before the counter: the count of live tickers is the property that
    # matters, and it must be the assertion that dies.
    assert len(spawner.tasks) == 1
    assert spawner.live() == 1
    assert sup.task is original
    assert [r.levelno for r in caplog.records] == [logging.CRITICAL]
    assert metrics.ticker_restarts == 0


async def test_the_decision_does_not_consult_task_done(supervised, clock):
    """A wedged ticker never completes, so `done()` would never fire the restart."""
    sup, spawner, metrics = await supervised(Obedient())
    assert sup.task.done() is False
    clock.advance(TICK_RESTART_NS + TICK_INTERVAL_NS)
    await sup.step()
    assert metrics.ticker_restarts == 1


async def test_probe_db_writable_is_true_on_a_healthy_store(deps):
    assert await probe_db_writable(deps) is True


async def test_a_cancel_landing_inside_a_transaction_leaves_the_writer_usable(deps, clock):
    """The wedge the review flagged: cancellation lands mid-`critical_section`,
    which is why that context manager catches BaseException and shields its
    rollback. Verified rather than assumed — an unreleased writer wedges the
    process for its lifetime."""
    entered = asyncio.Event()

    async def wedged():
        async with critical_section(deps.conn, deps.executor):
            entered.set()
            await asyncio.Event().wait()

    wedged_spawner = Spawner(wedged)
    task = wedged_spawner()
    await entered.wait()
    replacement = Spawner(Obedient())
    sup = Supervisor(
        deps=deps,
        metrics=TickerMetrics(tick_number=3, last_tick_mono=clock()),
        spawn=replacement,
        task=task,
    )
    clock.advance(TICK_RESTART_NS + TICK_INTERVAL_NS)
    try:
        await sup.step()
        assert sup.metrics.ticker_restarts == 1
        assert await probe_db_writable(deps) is True
    finally:
        await _quiet(wedged_spawner)
        await _quiet(replacement)


async def test_probe_db_writable_is_false_while_the_writer_is_held(deps):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with critical_section(deps.conn, deps.executor):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await entered.wait()
    try:
        assert await probe_db_writable(deps) is False
    finally:
        release.set()
        await holder
    assert await probe_db_writable(deps) is True
