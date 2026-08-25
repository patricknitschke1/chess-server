"""Test task 17: edge-triggered presence (§7.5)."""
import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
)
from chess_server.engine import state
from chess_server.engine.ticker import (
    DISCONNECT_AFTER_NS,
    TickerMetrics,
    _tick_once,
    step_matchmaking,
    step_presence,
)
from chess_server.store.txn import critical_section

WALL = "2026-08-24T00:00:00Z"
ONE_SECOND_NS = 1_000_000_000


async def tick(deps, steps, metrics=None):
    await _tick_once(deps, metrics or TickerMetrics(), steps=list(steps))


async def test_presence_is_edge_triggered_in_both_directions(
    deps, clock, sink, seed_bots, poll
):
    """Ten ticks with a freshly polling bot are one event, not ten."""
    (bot,) = await seed_bots("bot-a")
    await poll(bot.id)

    for _ in range(10):
        await tick(deps, [step_presence])
    assert sink.of("bot_connected") == [{"bot_id": bot.id, "bot_name": "bot-a"}]
    assert state.connected == {bot.id}

    clock.advance(DISCONNECT_AFTER_NS + ONE_SECOND_NS)
    for _ in range(10):
        await tick(deps, [step_presence])
    assert sink.of("bot_disconnected") == [{"bot_id": bot.id, "bot_name": "bot-a"}]
    assert state.connected == set()


async def test_a_benchmark_bot_that_polls_is_present(deps, sink, seed_bots, poll):
    """A benchmark bot is an HTTP client like any other. Reading the leaderboard
    query here would silently exclude it from presence entirely."""
    (bot,) = await seed_bots("spar-partner", role="benchmark")
    await poll(bot.id)

    await tick(deps, [step_presence])

    assert sink.of("bot_connected") == [{"bot_id": bot.id, "bot_name": "spar-partner"}]


async def test_the_presence_step_writes_nothing(store, deps, seed_bots, poll):
    (bot,) = await seed_bots("bot-a")
    await poll(bot.id)
    before = _snapshot(store)

    await tick(deps, [step_presence])

    assert _snapshot(store) == before


def _snapshot(store) -> dict[str, list[tuple]]:
    tables = [
        row["name"]
        for row in store.reader.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    return {
        name: store.reader.execute(f"SELECT * FROM {name}").fetchall()
        for name in tables
    }
