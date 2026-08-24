"""Test task 15: queued-challenge consumption and the seat race (§7.2, §12, §11.12)."""
import pytest

from chess_core import (
    EXHIBITION_INCREMENT_NS,
    EXHIBITION_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    ns_to_ms,
)
from chess_server.engine.ticker import (
    STEPS,
    TickerMetrics,
    _tick_once,
    step_challenges,
    step_matchmaking,
)
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import BotRepo, ChallengeRepo, SeatRepo
from chess_server.store.txn import critical_section

WALL = "2026-08-24T00:00:00Z"


@pytest.fixture
def challenges(store):
    return ChallengeRepo(store.writer, store.executor)


@pytest.fixture
def queue_challenge(store, deps, challenges):
    async def _queue(challenger, opponent, *, exhibition=False, status="queued"):
        async with critical_section(store.writer, store.executor):
            return await challenges.insert_challenge(
                challenger_bot_id=challenger.id,
                opponent_bot_id=opponent.id,
                status=status,
                time_control_ns=(
                    EXHIBITION_TIME_CONTROL_NS if exhibition else RATED_TIME_CONTROL_NS
                ),
                increment_ns=(
                    EXHIBITION_INCREMENT_NS if exhibition else RATED_INCREMENT_NS
                ),
                created_at=WALL,
                created_mono=deps.now_mono(),
            )

    return _queue


async def tick(deps, metrics=None, steps=(step_challenges,)):
    await _tick_once(deps, metrics or TickerMetrics(), steps=list(steps))


def in_production_order(*wanted):
    """Relative order is read out of STEPS, so reordering the tick reorders this
    test. Naming the two steps here instead would assert the plan, not the build."""
    return [step for step in STEPS if step in wanted]


async def test_a_seated_opponent_expires_the_challenge_out_loud(
    deps, games, sink, seed_bots, make_game, queue_challenge, challenges
):
    """Never a silent drop: the reason reaches the wire."""
    a, b, c = await seed_bots("bot-a", "bot-b", "bot-c")
    await make_game(b, c)
    challenge_id = await queue_challenge(a, b)
    before = len(await games.list_active_summaries())

    await tick(deps)

    challenge = await challenges.get_by_id(challenge_id)
    assert (challenge.status, challenge.reason) == ("expired", "seat_unavailable")
    assert len(await games.list_active_summaries()) == before
    updates = sink.of("challenge_updated")
    assert len(updates) == 1
    assert updates[0]["reason"] == "seat_unavailable"
    assert updates[0]["challenge_id"] == challenge_id


async def test_an_agent_controlled_challenger_is_refused_on_a_rated_challenge(
    store, deps, bot_repo, challenges, seed_bots, queue_challenge
):
    a, b = await seed_bots("bot-a", "bot-b")
    async with critical_section(store.writer, store.executor):
        await bot_repo.update_controller(a.id, "agent")
    challenge_id = await queue_challenge(a, b)

    await tick(deps)
    challenge = await challenges.get_by_id(challenge_id)
    assert (challenge.status, challenge.reason) == ("expired", "seat_unavailable")


async def test_an_agent_controlled_challenger_is_allowed_at_exhibition(
    store, deps, bot_repo, challenges, seed_bots, queue_challenge
):
    """Design §13.3: control handoff is exactly what exhibition games are for."""
    a, b = await seed_bots("bot-a", "bot-b")
    async with critical_section(store.writer, store.executor):
        await bot_repo.update_controller(a.id, "agent")
    challenge_id = await queue_challenge(a, b, exhibition=True)

    await tick(deps)
    assert (await challenges.get_by_id(challenge_id)).status == "consumed"


async def test_two_challenges_sharing_a_bot_yield_one_game_and_one_expiry(
    store, deps, games, challenges, seed_bots, queue_challenge
):
    """§11.12. The loser says why, on the wire, and leaves nothing behind."""
    a, b, c = await seed_bots("bot-a", "bot-b", "bot-c")
    first = await queue_challenge(a, b)
    second = await queue_challenge(b, c)

    await tick(deps)

    assert (await challenges.get_by_id(first)).status == "consumed"
    loser = await challenges.get_by_id(second)
    assert (loser.status, loser.reason) == ("expired", "seat_unavailable")
    assert len(await games.list_active_summaries()) == 1
    assert sorted(await SeatRepo(store.writer, store.executor).list_seated_bot_ids()) == sorted(
        [a.id, b.id]
    )


async def test_a_seat_collision_at_the_database_leaves_no_orphan_game(
    store, deps, games, challenges, seed_bots, queue_challenge, monkeypatch
):
    """PRAGMA foreign_keys=ON forces the game insert before its seats, so an
    unhandled collision commits an orphan game plus one stray seat."""
    a, b, c = await seed_bots("bot-a", "bot-b", "bot-c")
    first = await queue_challenge(a, b)
    second = await queue_challenge(b, c)

    async def blind(self, bot_id):
        return None

    monkeypatch.setattr(SeatRepo, "get_seat", blind)   # force the raw IntegrityError
    metrics = TickerMetrics()
    await tick(deps, metrics)

    assert len(await games.list_active_summaries()) == 1
    assert sorted(await SeatRepo(store.writer, store.executor).list_seated_bot_ids()) == sorted(
        [a.id, b.id]
    )
    assert metrics.consecutive_tick_errors == 0
    assert (await challenges.get_by_id(second)).status == "queued"   # retried next tick


async def test_consumption_precedes_pairing(
    deps, games, challenges, seed_bots, poll, queue_challenge
):
    """Matchmaking on this pool would pair a-b and c-d; the challenge wants a-c."""
    a, b, c, d = await seed_bots("bot-a", "bot-b", "bot-c", "bot-d")
    await poll(a.id, b.id, c.id, d.id)
    challenge_id = await queue_challenge(a, c)

    await tick(deps, steps=in_production_order(step_challenges, step_matchmaking))

    assert (await challenges.get_by_id(challenge_id)).status == "consumed"
    by_source = {
        s["game_id"]: {s["white_bot_id"], s["black_bot_id"]}
        for s in await games.list_active_summaries()
    }
    assert {a.id, c.id} in by_source.values()
    assert {b.id, d.id} in by_source.values()


async def test_the_game_carries_the_challenge_s_own_time_control(
    deps, games, challenges, seed_bots, queue_challenge
):
    a, b = await seed_bots("bot-a", "bot-b")
    challenge_id = await queue_challenge(a, b, exhibition=True)
    await tick(deps)

    challenge = await challenges.get_by_id(challenge_id)
    game = await games.get_by_id(challenge.game_id)
    assert game.time_control_ms == ns_to_ms(EXHIBITION_TIME_CONTROL_NS)
    assert game.increment_ms == ns_to_ms(EXHIBITION_INCREMENT_NS)
    assert game.white_ms == game.black_ms == ns_to_ms(EXHIBITION_TIME_CONTROL_NS)
    assert game.rated == 0
    assert game.source == "challenge"


async def test_a_cas_conflict_rolls_back_only_that_challenge(
    deps, games, challenges, seed_bots, queue_challenge, monkeypatch
):
    a, b, c, d = await seed_bots("bot-a", "bot-b", "bot-c", "bot-d")
    doomed = await queue_challenge(a, b)
    survivor = await queue_challenge(c, d)
    real = ChallengeRepo.cas_set_status

    async def flaky(self, challenge_id, from_status, status, **kwargs):
        if challenge_id == doomed:
            raise CASConflict("cancelled underneath us")
        return await real(self, challenge_id, from_status, status, **kwargs)

    monkeypatch.setattr(ChallengeRepo, "cas_set_status", flaky)
    metrics = TickerMetrics()
    await tick(deps, metrics)

    assert (await challenges.get_by_id(doomed)).status == "queued"
    assert (await challenges.get_by_id(survivor)).status == "consumed"
    assert len(await games.list_active_summaries()) == 1
    assert metrics.consecutive_tick_errors == 0
