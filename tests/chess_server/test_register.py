"""POST /bots (role spec §8.2). Failure paths first."""
import asyncio

import pytest

from chess_server.api.auth import hash_token
from chess_server.api.errors import (
    INVALID_JOIN_CODE,
    INVALID_ROLE,
    NAME_TAKEN,
    SECOND_COMPETITOR,
)
from chess_server.store.repositories import BotRepo

from tests.chess_server.conftest import JOIN_CODE


def _payload(**overrides):
    body = {"name": "ada", "owner": "ada", "join_code": JOIN_CODE, "role": "competitor"}
    body.update(overrides)
    return body


async def _names(store):
    return {bot.name for bot in await BotRepo(store.reader, store.executor).list_leaderboard()}


async def test_a_wrong_join_code_registers_nothing(client, store):
    response = await client.post("/bots", json=_payload(join_code="guessed"))

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_JOIN_CODE
    assert await _names(store) == set()


@pytest.mark.parametrize("role", ["anchor", "wizard"])
async def test_only_competitor_and_benchmark_are_registrable(client, store, role):
    response = await client.post("/bots", json=_payload(role=role))

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_ROLE.format(role=role)
    assert await _names(store) == set()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"name": "<img src=x onerror=1>"}, id="markup"),
        pytest.param({"name": "x" * 33}, id="too-long"),
        pytest.param({"owner": ""}, id="empty-owner"),
    ],
)
async def test_names_and_owners_that_reach_a_projector_are_rejected(
    client, store, overrides
):
    response = await client.post("/bots", json=_payload(**overrides))

    assert response.status_code == 422
    assert await _names(store) == set()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"name": "ref-greedy"}, id="anchor-name"),
        pytest.param({"name": "REF-Greedy"}, id="anchor-name-case-folded"),
        pytest.param({"owner": "server"}, id="server-owner"),
        pytest.param({"owner": "SERVER"}, id="server-owner-case-folded"),
    ],
)
async def test_reference_identities_cannot_be_impersonated(client, store, overrides):
    """An attendee registering as an anchor makes the leaderboard, the anchor gate
    and /admin/consistency all read a bot that is not what they think it is."""
    response = await client.post("/bots", json=_payload(**overrides))

    assert response.status_code == 422
    assert await _names(store) == set()


async def test_a_duplicate_name_is_refused(client):
    await client.post("/bots", json=_payload())

    response = await client.post("/bots", json=_payload(owner="grace"))

    assert response.status_code == 400
    assert response.json()["error"] == NAME_TAKEN.format(name="ada")


async def test_a_second_competitor_for_one_owner_is_pointed_at_benchmark(client):
    await client.post("/bots", json=_payload(name="first"))

    response = await client.post("/bots", json=_payload(name="second"))

    assert response.status_code == 409
    assert response.json()["error"] == SECOND_COMPETITOR.format(existing_name="first")
    assert "benchmark" in response.json()["error"]


async def test_a_benchmark_bot_is_allowed_alongside_the_competitor(client):
    await client.post("/bots", json=_payload(name="first"))

    response = await client.post("/bots", json=_payload(name="sparring", role="benchmark"))

    assert response.status_code == 201


async def test_two_simultaneous_registrations_for_one_owner_leave_one_row(client, store):
    """The uniqueness check and the insert are one transaction, so a second
    request cannot pass a check the first has already invalidated."""
    responses = await asyncio.gather(
        client.post("/bots", json=_payload(name="first")),
        client.post("/bots", json=_payload(name="second")),
    )

    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(await _names(store)) == 1


async def test_registration_returns_a_token_that_authenticates(client, store, sink):
    response = await client.post("/bots", json=_payload())
    body = response.json()

    assert response.status_code == 201
    assert body["name"] == "ada"

    me = await client.get("/bots/me", headers={"Authorization": f"Bearer {body['token']}"})

    assert me.status_code == 200
    assert me.json()["bot_id"] == body["bot_id"]

    stored = await BotRepo(store.reader, store.executor).get_by_id(body["bot_id"])
    assert stored.token_hash == hash_token(body["token"])
    assert stored.token_hash != body["token"]


async def test_the_registration_event_carries_no_token(client, sink):
    response = await client.post("/bots", json=_payload())
    token = response.json()["token"]

    assert sink.of("bot_registered") == [
        {"bot_id": response.json()["bot_id"], "bot_name": "ada", "role": "competitor"}
    ]
    assert token not in str(sink.events)
    assert hash_token(token) not in str(sink.events)
