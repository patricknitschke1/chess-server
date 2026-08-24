"""Test task 17: agent auto-release, challenge TTL and edge-triggered presence (§7.5)."""
import pytest

from chess_core import (
    AGENT_AUTO_RELEASE_NS,
    CHALLENGE_TTL_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
)
from chess_server.engine import state
from chess_server.engine.ticker import (
    DISCONNECT_AFTER_NS,
    TickerMetrics,
    _tick_once,
    step_agent_release,
    step_challenge_ttl,
    step_matchmaking,
    step_presence,
)
from chess_server.store.repositories import ChallengeRepo
from chess_server.store.txn import critical_section

WALL = "2026-08-24T00:00:00Z"
ONE_SECOND_NS = 1_000_000_000


@pytest.fixture
def challenges(store):
    return ChallengeRepo(store.writer, store.executor)


@pytest.fixture
def open_challenge(store, deps, challenges):
    async def _open(challenger, opponent, *, status="open", at=None):
        async with critical_section(store.writer, store.executor):
            return await challenges.insert_challenge(
                challenger_bot_id=challenger.id,
                opponent_bot_id=opponent.id,
                status=status,
                time_control_ns=RATED_TIME_CONTROL_NS,
                increment_ns=RATED_INCREMENT_NS,
                created_at=WALL,
                created_mono=deps.now_mono() if at is None else at,
            )

    return _open


@pytest.fixture
def take_control(store, bot_repo):
    async def _take(bot_id, action_mono):
        async with critical_section(store.writer, store.executor):
            await bot_repo.update_controller(bot_id, "agent")
            if action_mono is not None:
                await bot_repo.update_last_agent_action(bot_id, action_mono)

    return _take


async def tick(deps, steps, metrics=None):
    await _tick_once(deps, metrics or TickerMetrics(), steps=list(steps))


async def test_an_agent_is_released_only_once_the_window_has_passed(
    deps, clock, bot_repo, wake, seed_bots, take_control
):
    """45 s sits deliberately below the 60 s agent delivery grace; reversed, the
    grace always fires first and this branch is unreachable."""
    (bot,) = await seed_bots("bot-a")
    await take_control(bot.id, clock())

    clock.advance(AGENT_AUTO_RELEASE_NS - ONE_SECOND_NS)
    await tick(deps, [step_agent_release])
    assert (await bot_repo.get_by_id(bot.id)).controller == "agent"
    assert wake.woken == []

    clock.advance(2 * ONE_SECOND_NS)
    await tick(deps, [step_agent_release])
    assert (await bot_repo.get_by_id(bot.id)).controller == "client"
    assert wake.woken == [bot.id]


async def test_the_release_happens_after_matchmaking_so_the_bot_pairs_next_tick(
    deps, clock, games, seed_bots, poll, take_control
):
    """Step 6 follows step 2: the snapshot this tick was taken before the release."""
    a, b = await seed_bots("bot-a", "bot-b")
    await poll(a.id, b.id)
    await take_control(a.id, clock() - AGENT_AUTO_RELEASE_NS - ONE_SECOND_NS)

    await tick(deps, [step_matchmaking, step_agent_release])
    assert await games.list_active_summaries() == []

    await tick(deps, [step_matchmaking, step_agent_release])
    assert len(await games.list_active_summaries()) == 1


async def test_an_agent_with_no_recorded_action_is_released_immediately(
    deps, bot_repo, seed_bots, take_control
):
    """The safe direction. 3c writes the field in the same transaction as `take`."""
    (bot,) = await seed_bots("bot-a")
    await take_control(bot.id, None)

    await tick(deps, [step_agent_release])
    assert (await bot_repo.get_by_id(bot.id)).controller == "client"


async def test_an_open_challenge_times_out_and_a_queued_one_is_untouched(
    deps, clock, sink, challenges, seed_bots, open_challenge
):
    a, b, c, d = await seed_bots("bot-a", "bot-b", "bot-c", "bot-d")
    stale = await open_challenge(a, b, at=clock() - CHALLENGE_TTL_NS - ONE_SECOND_NS)
    fresh = await open_challenge(c, d)
    queued = await open_challenge(a, d, status="queued",
                                  at=clock() - CHALLENGE_TTL_NS - ONE_SECOND_NS)

    await tick(deps, [step_challenge_ttl])

    expired = await challenges.get_by_id(stale)
    assert (expired.status, expired.reason) == ("expired", "timeout")
    assert (await challenges.get_by_id(fresh)).status == "open"
    assert (await challenges.get_by_id(queued)).status == "queued"
    updates = sink.of("challenge_updated")
    assert len(updates) == 1
    assert (updates[0]["challenge_id"], updates[0]["reason"]) == (stale, "timeout")


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
