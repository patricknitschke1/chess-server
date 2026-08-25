"""Challenge routes (role spec §7.2, §8.1; design §12; interfaces Part 5).

Challenges do not create games. Accepting queues one; the **ticker** consumes it,
before pairing, so an accepted challenge always beats matchmaking to the seat.
No transition here is silent: every one buffers `challenge_updated`.
"""
import pytest

from chess_core import (
    CHALLENGE_TTL_NS,
    EXHIBITION_INCREMENT_NS,
    EXHIBITION_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    ns_to_ms,
)

from chess_server.engine.ticker import step_challenge_ttl, step_challenges
from chess_server.store.repositories import ChallengeRepo, GameRepo
from chess_server.store.txn import critical_section

from tests.chess_server.test_control import set_controller
from tests.chess_server.test_route_moves import auth
from tests.chess_server.test_turn import pair, register


async def create(client, token, opponent, time_control="rated"):
    return await client.post(
        "/challenges",
        json={"opponent": opponent, "time_control": time_control},
        headers=auth(token),
    )


async def act(client, token, challenge_id, action):
    return await client.post(
        f"/challenges/{challenge_id}/{action}", headers=auth(token)
    )


async def inbox(client, token):
    return await client.get("/challenges", headers=auth(token))


async def tick(api_state, steps):
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        for step in steps:
            await step(api_state.deps, txn, api_state.deps.now_mono())


@pytest.fixture
async def pair_of_bots(client):
    return await register(client, "ada"), await register(client, "bob")


async def test_an_unauthenticated_challenge_is_rejected(client):
    assert (await client.post("/challenges", json={"opponent": "bob"})).status_code == 401


async def test_an_unknown_opponent_is_400(client, pair_of_bots):
    ada, _ = pair_of_bots

    response = await create(client, ada["token"], "nobody")

    assert response.status_code == 400
    assert "nobody" in response.json()["error"]


async def test_a_self_challenge_is_400(client, pair_of_bots):
    """Otherwise the `seats` primary key kills it at consumption as
    `seat_unavailable`, which reads as a server fault rather than an input error."""
    ada, _ = pair_of_bots

    response = await create(client, ada["token"], "ada")

    assert response.status_code == 400
    assert "yourself" in response.json()["error"]


async def test_an_unknown_time_control_is_400_naming_both(client, pair_of_bots):
    ada, _ = pair_of_bots

    response = await create(client, ada["token"], "bob", time_control="bullet")

    assert response.status_code == 400
    error = response.json()["error"]
    assert "rated" in error and "exhibition" in error


async def test_a_second_open_outgoing_challenge_is_409(client, pair_of_bots):
    ada, _ = pair_of_bots
    await register(client, "cy")
    assert (await create(client, ada["token"], "bob")).status_code == 201

    response = await create(client, ada["token"], "cy")

    assert response.status_code == 409
    assert "outgoing" in response.json()["error"]


@pytest.mark.parametrize("seated", ["challenger", "opponent"])
async def test_a_seated_participant_is_409(client, api_state, pair_of_bots, seated):
    ada, bob = pair_of_bots
    cy = await register(client, "cy")
    busy = ada if seated == "challenger" else bob
    await pair(api_state, busy["bot_id"], cy["bot_id"])

    response = await create(client, ada["token"], "bob")

    assert response.status_code == 409
    assert "already in a game" in response.json()["error"]


async def test_an_agent_controlled_participant_cannot_be_challenged_at_rated(
    client, api_state, pair_of_bots
):
    """Design §13.3: an agent may only be handed the controls in an exhibition."""
    ada, bob = pair_of_bots
    await set_controller(api_state, bob["bot_id"], "agent")

    refused = await create(client, ada["token"], "bob")
    allowed = await create(client, ada["token"], "bob", time_control="exhibition")

    assert refused.status_code == 409
    assert allowed.status_code == 201


async def test_creating_a_challenge_returns_the_part_5_fields_and_emits(
    client, pair_of_bots, sink
):
    ada, bob = pair_of_bots

    response = await create(client, ada["token"], "bob")

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "challenge_id": body["challenge_id"],
        "challenger_bot_id": ada["bot_id"],
        "opponent_bot_id": bob["bot_id"],
        "status": "open",
        "time_control_ms": ns_to_ms(RATED_TIME_CONTROL_NS),
        "increment_ms": ns_to_ms(RATED_INCREMENT_NS),
    }
    assert sink.of("challenge_updated")[-1]["status"] == "open"


async def test_the_inbox_separates_incoming_from_outgoing(client, pair_of_bots):
    ada, bob = pair_of_bots
    created = (await create(client, ada["token"], "bob")).json()

    outgoing = (await inbox(client, ada["token"])).json()
    incoming = (await inbox(client, bob["token"])).json()

    assert outgoing["incoming"] == [] and incoming["outgoing"] == []
    entry = incoming["incoming"][0]
    assert entry["challenge_id"] == created["challenge_id"]
    assert (entry["challenger_bot_name"], entry["opponent_bot_name"]) == ("ada", "bob")
    assert entry["status"] == "open"
    assert outgoing["outgoing"][0]["challenge_id"] == created["challenge_id"]


async def test_only_the_opponent_may_accept(client, pair_of_bots):
    ada, _ = pair_of_bots
    cy = await register(client, "cy")
    created = (await create(client, ada["token"], "bob")).json()

    for token in (ada["token"], cy["token"]):
        response = await act(client, token, created["challenge_id"], "accept")
        assert response.status_code == 403


async def test_an_unknown_challenge_is_404(client, pair_of_bots):
    _, bob = pair_of_bots
    assert (await act(client, bob["token"], 9999, "accept")).status_code == 404


async def test_accepting_twice_is_409(client, pair_of_bots):
    ada, bob = pair_of_bots
    created = (await create(client, ada["token"], "bob")).json()

    first = await act(client, bob["token"], created["challenge_id"], "accept")
    second = await act(client, bob["token"], created["challenge_id"], "accept")

    assert (first.status_code, first.json()["status"]) == (200, "queued")
    assert second.status_code == 409
    assert "queued" in second.json()["error"]


async def test_declining_marks_it_declined(client, pair_of_bots, sink):
    ada, bob = pair_of_bots
    created = (await create(client, ada["token"], "bob")).json()

    response = await act(client, bob["token"], created["challenge_id"], "decline")

    assert response.json() == {
        "challenge_id": created["challenge_id"], "status": "declined"
    }
    assert sink.of("challenge_updated")[-1]["status"] == "declined"


async def test_an_expired_challenge_cannot_be_accepted(
    client, api_state, pair_of_bots, clock
):
    ada, bob = pair_of_bots
    created = (await create(client, ada["token"], "bob")).json()
    clock.advance(CHALLENGE_TTL_NS + 1)
    await tick(api_state, [step_challenge_ttl])

    response = await act(client, bob["token"], created["challenge_id"], "accept")

    assert response.status_code == 409
    assert "expired" in response.json()["error"]


async def test_the_round_trip_emits_created_queued_consumed(
    client, api_state, pair_of_bots, sink, store
):
    ada, bob = pair_of_bots
    created = (await create(client, ada["token"], "bob")).json()
    assert (await act(client, bob["token"], created["challenge_id"], "accept")).status_code == 200

    await tick(api_state, [step_challenges])

    assert [event["status"] for event in sink.of("challenge_updated")] == [
        "open", "queued", "consumed",
    ]
    challenge = await ChallengeRepo(store.reader, store.reader_executor).get_by_id(
        created["challenge_id"]
    )
    assert challenge.status == "consumed" and challenge.game_id is not None
    assert sink.of("challenge_updated")[-1]["game_id"] == challenge.game_id


async def test_an_exhibition_challenge_creates_an_unrated_game_at_its_own_control(
    client, api_state, pair_of_bots, store
):
    ada, bob = pair_of_bots
    created = (await create(client, ada["token"], "bob", time_control="exhibition")).json()
    await act(client, bob["token"], created["challenge_id"], "accept")

    await tick(api_state, [step_challenges])

    games = GameRepo(store.reader, store.reader_executor)
    game = await games.get_for_bot(ada["bot_id"])
    assert game.rated == 0
    assert game.time_control_ms == ns_to_ms(EXHIBITION_TIME_CONTROL_NS)
    assert game.increment_ms == ns_to_ms(EXHIBITION_INCREMENT_NS)
    assert created["increment_ms"] == ns_to_ms(EXHIBITION_INCREMENT_NS)


async def test_a_challenge_that_loses_the_seat_expires_with_a_reason(
    client, api_state, pair_of_bots, sink, store
):
    """§11.12: two accepted challenges sharing a bot yield exactly one game, and
    the loser says why rather than vanishing."""
    ada, bob = pair_of_bots
    cy = await register(client, "cy")
    first = (await create(client, ada["token"], "bob")).json()
    second = (await create(client, cy["token"], "bob")).json()
    await act(client, bob["token"], first["challenge_id"], "accept")
    await act(client, bob["token"], second["challenge_id"], "accept")

    await tick(api_state, [step_challenges])

    challenges = ChallengeRepo(store.reader, store.reader_executor)
    winner = await challenges.get_by_id(first["challenge_id"])
    loser = await challenges.get_by_id(second["challenge_id"])
    assert (winner.status, loser.status) == ("consumed", "expired")
    assert loser.reason == "seat_unavailable"
    assert [
        event["reason"] for event in sink.of("challenge_updated")
        if event["challenge_id"] == loser.id
    ][-1] == "seat_unavailable"


async def test_no_challenge_body_carries_a_token_or_an_owner(client, pair_of_bots, sink):
    ada, bob = pair_of_bots

    body = (await create(client, ada["token"], "bob")).text
    listing = (await inbox(client, bob["token"])).text

    assert ada["token"] not in body and bob["token"] not in listing
    assert all("owner" not in event for event in sink.of("challenge_updated"))
