"""GET /bots/me (role spec §8.2; interfaces Part 5 `MyBotResponse`)."""
import pytest

from chess_server.store.repositories import BotRepo
from chess_server.store.txn import critical_section

from tests.chess_server.conftest import JOIN_CODE
from chess_server.api.routes_bots import PROVISIONAL_GAMES


async def _register(client, name, owner=None, role="competitor"):
    response = await client.post("/bots", json={
        "name": name, "owner": owner or name, "join_code": JOIN_CODE, "role": role,
    })
    assert response.status_code == 201
    return response.json()


async def _me(client, token):
    return await client.get("/bots/me", headers={"Authorization": f"Bearer {token}"})


async def _set_games_played(store, bot_id, count):
    async with critical_section(store.writer, store.executor):
        await BotRepo(store.writer, store.executor)._write(
            "UPDATE bots SET games_played = ? WHERE id = ?", (count, bot_id)
        )


async def test_an_unseated_bot_reports_no_current_game(client):
    registered = await _register(client, "ada")

    body = (await _me(client, registered["token"])).json()

    assert body["current_game_id"] is None
    assert body["role"] == "competitor"
    assert body["controller"] == "client"


async def test_a_seated_bot_reports_the_game_it_is_seated_in(
    client, store, make_game
):
    white = await _register(client, "ada")
    black = await _register(client, "grace")
    bots = BotRepo(store.writer, store.executor)
    game = await make_game(
        await bots.get_by_id(white["bot_id"]), await bots.get_by_id(black["bot_id"])
    )

    for registered in (white, black):
        assert (await _me(client, registered["token"])).json()["current_game_id"] == game.id


@pytest.mark.parametrize(
    "played, provisional",
    [(0, True), (PROVISIONAL_GAMES - 1, True), (PROVISIONAL_GAMES, False)],
)
async def test_provisional_flips_at_the_tenth_game(client, store, played, provisional):
    registered = await _register(client, "ada")
    await _set_games_played(store, registered["bot_id"], played)

    body = (await _me(client, registered["token"])).json()

    assert body["games_played"] == played
    assert body["is_provisional"] is provisional


async def test_the_response_carries_no_token(client):
    registered = await _register(client, "ada")

    response = await _me(client, registered["token"])

    assert registered["token"] not in response.text
    assert "token" not in response.json()
