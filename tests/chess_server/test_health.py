"""GET /health, the health_tick, and the seeded tick clock (role spec §7.6, §8.1;
design §4.6; Part 5 `HealthResponse`, Part 2 `health_tick`).

`health_tick` is the one deliberately unbuffered event: it reports process state,
belongs to no transaction, and would otherwise never be emitted at all.
"""
import asyncio

import pytest

from chess_core import ns_to_ms
from chess_server.api.health import (
    HEALTH_TICK_FIELDS,
    build_health,
    health_tick_data,
    publish_health_tick,
)
from chess_server.engine import ticker as ticker_module
from chess_server.engine.mailbox import deliver_for_poll
from chess_server.engine.supervisor import Supervisor, probe_db_writable
from chess_server.engine.ticker import TickerMetrics, run_ticker
from chess_server.store.txn import critical_section

SECOND_NS = 1_000_000_000


@pytest.fixture
async def two_bots(seed_bots, poll):
    white, black = await seed_bots("alpha", "beta")
    await poll(white.id, black.id)
    return white, black


# --- 1. the seed --------------------------------------------------------------

async def test_a_supervisor_step_before_the_first_tick_restarts_nothing(
    api_state, monkeypatch
):
    """`0` reads as infinitely stale, so an unseeded metric restarts a healthy
    ticker on the very first supervisor step."""
    reached = asyncio.Event()

    async def never_completes(deps, metrics):
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ticker_module, "_tick_once", never_completes)
    metrics = TickerMetrics()
    task = asyncio.create_task(run_ticker(api_state.deps, metrics))
    await reached.wait()

    supervisor = Supervisor(
        deps=api_state.deps, metrics=metrics, spawn=lambda: task, task=task
    )
    await supervisor.step()

    assert metrics.ticker_restarts == 0
    task.cancel()


# --- 2. the write probe -------------------------------------------------------

async def test_the_probe_is_false_while_the_writer_is_inside_a_transaction(
    api_state, store
):
    """A `SELECT 1` passes both halves of this test, which is why it is not one."""

    async def hold() -> bool:
        async with critical_section(store.writer, store.executor):
            return await probe_db_writable(api_state.deps)

    assert await hold() is False
    assert await probe_db_writable(api_state.deps) is True


async def test_health_reports_the_probe_result(client, monkeypatch):
    from chess_server.api import health as health_module

    async def wedged(deps):
        return False

    monkeypatch.setattr(health_module, "probe_db_writable", wedged)

    assert (await client.get("/health")).json()["db_writable"] is False


# --- 3. the snapshot ----------------------------------------------------------

async def test_every_part5_field_is_present_and_typed(client):
    body = (await client.get("/health")).json()

    for field, kind in (
        ("last_tick_age_ms", int), ("last_tick_duration_ms", int),
        ("active_games", int), ("pending_games", int), ("stalled_games", int),
        ("pooled_bots", int), ("held_polls", int), ("sse_clients", int),
        ("db_writable", bool), ("consecutive_tick_errors", int),
        ("ticker_restarts", int),
    ):
        assert isinstance(body[field], kind), field


async def test_stalled_counts_an_undelivered_game_and_stops_once_delivered(
    client, api_state, make_game, two_bots
):
    white, _ = two_bots
    await make_game(*two_bots)

    before = (await client.get("/health")).json()
    assert [before["stalled_games"], before["pending_games"],
            before["active_games"]] == [1, 1, 0]

    await deliver_for_poll(api_state.deps, white.id)
    after = (await client.get("/health")).json()

    assert [after["stalled_games"], after["pending_games"],
            after["active_games"]] == [0, 0, 1]


async def test_pooled_bots_counts_recent_pollers_only(client, two_bots, clock):
    assert (await client.get("/health")).json()["pooled_bots"] == 2

    clock.advance(60 * SECOND_NS)
    assert (await client.get("/health")).json()["pooled_bots"] == 0


async def test_held_polls_follows_register_and_discard(client, api_state, two_bots):
    white, _ = two_bots
    waiter = api_state.waiters.register(white.id)
    assert (await client.get("/health")).json()["held_polls"] == 1

    api_state.waiters.discard(white.id, waiter)
    assert (await client.get("/health")).json()["held_polls"] == 0


async def test_sse_clients_follows_subscription(client, api_state):
    api_state.hub.subscribe()

    assert (await client.get("/health")).json()["sse_clients"] == 1


async def test_last_tick_age_grows_with_the_clock(api_state, clock):
    api_state.metrics.last_tick_mono = clock()
    clock.advance(4 * SECOND_NS)

    health = await build_health(api_state, db_writable=True)

    assert health.last_tick_age_ms == ns_to_ms(4 * SECOND_NS)


# --- 4. the health_tick -------------------------------------------------------

async def test_the_supervisor_publishes_one_health_tick_per_step(api_state, clock):
    published: list[int] = []

    async def on_health() -> None:
        published.append(len(published) + 1)

    api_state.metrics.last_tick_mono = clock()
    supervisor = Supervisor(
        deps=api_state.deps,
        metrics=api_state.metrics,
        spawn=lambda: None,
        task=None,
        on_health=on_health,
    )

    await supervisor.step()
    await supervisor.step()

    assert published == [1, 2]


async def test_health_tick_carries_exactly_the_part2_fields(api_state):
    data = health_tick_data(await build_health(api_state, db_writable=True))

    assert set(data) == HEALTH_TICK_FIELDS
    assert "db_writable" not in data


async def test_health_tick_is_published_while_a_critical_section_is_open(
    api_state, store, sink
):
    """Buffered through a Txn it would be invisible until an unrelated transaction
    committed — the opposite of a liveness signal."""
    async with critical_section(store.writer, store.executor, api_state.deps.sink):
        await publish_health_tick(api_state)
        assert sink.types() == ["health_tick"]


async def test_health_tick_consumes_a_seq_of_its_own(api_state, sink):
    await publish_health_tick(api_state)
    await publish_health_tick(api_state)

    assert [seq for seq, _, _ in sink.events] == [0, 1]
