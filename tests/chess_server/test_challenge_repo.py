import pytest

from chess_core import (
    CHALLENGE_TTL_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_RATING,
    window_start_mono,
)
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import BotRepo, ChallengeRepo

NOW = 10_000_000_000_000
CUTOFF = window_start_mono(NOW, CHALLENGE_TTL_NS)
EARLY_WALL = "2026-08-24T08:00:00Z"
LATE_WALL = "2026-08-24T09:00:00Z"
RESOLVED_WALL = "2026-08-24T09:30:00Z"


@pytest.fixture
def repos(store):
    return BotRepo(store.writer, store.executor), ChallengeRepo(store.writer, store.executor)


async def _two_bots(bots):
    made = []
    for name in ("challenger", "opponent"):
        made.append(
            await bots.insert_bot(
                name=name,
                owner=name,
                token_hash=f"hash-{name}",
                role="competitor",
                rating=STARTING_RATING,
                is_anchor=0,
                created_at=EARLY_WALL,
            )
        )
    return made


async def _challenge(repos, status="open", created_mono=NOW, created_at=EARLY_WALL):
    bots, challenges = repos
    existing = await bots.get_by_name("challenger")
    challenger, opponent = (
        (existing.id, (await bots.get_by_name("opponent")).id)
        if existing
        else await _two_bots(bots)
    )
    return await challenges.insert_challenge(
        challenger_bot_id=challenger,
        opponent_bot_id=opponent,
        status=status,
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        created_at=created_at,
        created_mono=created_mono,
    )


async def _raw(challenges, challenge_id):
    return dict(await challenges._one("SELECT * FROM challenges WHERE id = ?", (challenge_id,)))


async def test_a_stale_status_conflicts_and_moves_nothing(repos):
    _, challenges = repos
    challenge_id = await _challenge(repos)
    before = await _raw(challenges, challenge_id)

    with pytest.raises(CASConflict):
        await challenges.cas_set_status(challenge_id, "queued", "consumed")

    assert await _raw(challenges, challenge_id) == before


async def test_exactly_one_of_two_transitions_wins(repos):
    _, challenges = repos
    challenge_id = await _challenge(repos)

    await challenges.cas_set_status(challenge_id, "open", "declined", resolved_at=RESOLVED_WALL)
    with pytest.raises(CASConflict):
        await challenges.cas_set_status(challenge_id, "open", "queued")

    assert (await _raw(challenges, challenge_id))["status"] == "declined"


async def test_the_reason_reaches_the_column_so_it_can_reach_the_wire(repos):
    _, challenges = repos
    challenge_id = await _challenge(repos, status="queued")

    await challenges.cas_set_status(
        challenge_id, "queued", "expired", reason="seat_unavailable", resolved_at=RESOLVED_WALL
    )

    assert (await challenges.get_by_id(challenge_id)).reason == "seat_unavailable"


async def test_list_expired_open_reads_the_monotonic_column_not_the_wall_string(repos):
    _, challenges = repos
    # The wall order is the reverse of the monotonic order: a created_at comparison
    # picks the wrong row rather than none.
    stale = await _challenge(repos, created_mono=CUTOFF - 1, created_at=LATE_WALL)
    await _challenge(repos, created_mono=NOW, created_at=EARLY_WALL)
    for status in ("queued", "consumed", "declined", "expired"):
        await _challenge(repos, status=status, created_mono=CUTOFF - 1, created_at=LATE_WALL)

    assert [row.id for row in await challenges.list_expired_open(CUTOFF)] == [stale]


async def test_created_mono_is_an_integer_and_created_at_is_text(repos):
    _, challenges = repos
    challenge_id = await _challenge(repos)

    row = await _raw(challenges, challenge_id)

    assert isinstance(row["created_mono"], int)
    assert isinstance(row["created_at"], str)


async def test_expire_all_non_terminal_leaves_settled_challenges_alone(repos):
    _, challenges = repos
    moved = {status: await _challenge(repos, status=status) for status in ("open", "queued")}
    settled = {
        status: await _challenge(repos, status=status)
        for status in ("consumed", "declined", "expired")
    }
    before = {status: await _raw(challenges, cid) for status, cid in settled.items()}

    await challenges.expire_all_non_terminal("server_restart")

    for challenge_id in moved.values():
        row = await _raw(challenges, challenge_id)
        assert (row["status"], row["reason"]) == ("expired", "server_restart")
    for status, challenge_id in settled.items():
        assert await _raw(challenges, challenge_id) == before[status]
