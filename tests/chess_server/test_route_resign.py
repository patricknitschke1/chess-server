"""`POST /games/{id}/resign` (role spec §6.5, §8.1; interfaces Part 5; design §22).

The one thing here that is easy to get wrong and impossible to see afterwards:
the **resigner** loses, not the side to move. A bot that resigns on its
opponent's clock must still lose.
"""
import pytest

from chess_server.store.repositories import RatingHistoryRepo, SeatRepo

from tests.chess_server.test_route_moves import auth, deliver, play, submit, table  # noqa: F401
from tests.chess_server.test_turn import pair, register


async def resign(client, token, game_id, ply):
    return await client.post(
        f"/games/{game_id}/resign", json={"ply": ply}, headers=auth(token)
    )


async def test_an_unauthenticated_resignation_is_rejected(client, table):
    assert (await client.post(f"/games/{table[2]}/resign", json={"ply": 0})).status_code == 401


async def test_a_bot_that_is_not_a_player_cannot_resign(client, table):
    stranger = await register(client, "eve")

    response = await resign(client, stranger["token"], table[2], 0)

    assert response.status_code == 403
    assert "not a player" in response.json()["error"]


async def test_a_wrong_ply_is_409(client, api_state, table, games):
    white, black, game_id = table
    await play(client, api_state, table, ["e2e4"])

    response = await resign(client, black["token"], game_id, 0)

    assert response.status_code == 409
    assert (await games.get_by_id(game_id)).status == "active"


async def test_resigning_a_finished_game_is_409(client, api_state, table, games):
    white, black, game_id = table
    assert (await resign(client, black["token"], game_id, 0)).status_code == 200

    response = await resign(client, white["token"], game_id, 0)

    assert response.status_code == 409


async def test_black_resigning_while_white_is_to_move_gives_white_the_win(
    client, api_state, table, games
):
    """`opposite_win(game.to_move)` would hand Black the win here — the resigner."""
    _, black, game_id = table

    response = await resign(client, black["token"], game_id, 0)

    assert response.status_code == 200
    assert response.json() == {
        "game_id": game_id,
        "status": "finished",
        "result": "white_win",
        "termination": "resignation",
    }
    assert (await games.get_by_id(game_id)).result == "white_win"


async def test_white_resigning_on_its_own_move_gives_black_the_win(
    client, api_state, table, games
):
    white, _, game_id = table

    response = await resign(client, white["token"], game_id, 0)

    assert response.json()["result"] == "black_win"


async def test_a_resignation_is_rated_and_frees_both_seats(
    client, api_state, table, store, games
):
    """§6.5 rule 1 unrates only no_show, server_restart and admin_abort."""
    white, black, game_id = table

    assert (await resign(client, black["token"], game_id, 0)).status_code == 200

    history = RatingHistoryRepo(store.reader, store.reader_executor)
    assert len(await history.list_points_for_bot(white["bot_id"])) == 1
    assert len(await history.list_points_for_bot(black["bot_id"])) == 1
    seats = SeatRepo(store.reader, store.reader_executor)
    assert await seats.list_seated_bot_ids() == []
    assert (await games.get_by_id(game_id)).rated == 1


async def test_no_response_body_carries_a_token(client, api_state, table):
    _, black, game_id = table
    assert black["token"] not in (await resign(client, black["token"], game_id, 0)).text
