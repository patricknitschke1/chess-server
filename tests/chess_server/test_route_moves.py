"""`POST /games/{id}/moves` — the outcome mapping (role spec §6.1, §8.1; design §8.3)."""
import asyncio

import pytest

from chess_core import RATED_TIME_CONTROL_NS, GameStatus

from chess_server.engine.mailbox import deliver_for_poll
from chess_server.store.repositories import BotRepo, MoveRepo
from chess_server.store.txn import critical_section

from tests.chess_server.test_turn import pair, register

SCHOLARS = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]
GATHER_TIMEOUT_SECONDS = 5.0


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def submit(client, token, game_id, ply, move, **extra):
    return await client.post(
        f"/games/{game_id}/moves",
        json={"ply": ply, "move": move, **extra},
        headers=auth(token),
    )


@pytest.fixture
async def table(client, api_state):
    """Two registered bots, paired, White's position delivered."""
    white = await register(client, "ada")
    black = await register(client, "bob")
    game_id = await pair(api_state, white["bot_id"], black["bot_id"])
    await deliver_for_poll(api_state.deps, white["bot_id"])
    return white, black, game_id


async def deliver(api_state, bot_id):
    await deliver_for_poll(api_state.deps, bot_id)


async def play(client, api_state, table, moves):
    """Drive the real endpoint, delivering to each side as the turn arrives."""
    white, black, game_id = table
    responses = []
    for index, uci in enumerate(moves):
        mover = white if index % 2 == 0 else black
        await deliver(api_state, mover["bot_id"])
        responses.append(await submit(client, mover["token"], game_id, index, uci))
    return responses


async def test_an_unauthenticated_move_is_rejected(client, table):
    _, _, game_id = table
    response = await client.post(
        f"/games/{game_id}/moves", json={"ply": 0, "move": "e2e4"}
    )
    assert response.status_code == 401


async def test_an_illegal_move_is_400_with_the_legal_moves_and_the_position(
    client, api_state, table, games
):
    white, _, game_id = table

    response = await submit(client, white["token"], game_id, 0, "e2e5")

    assert response.status_code == 400
    details = response.json()["details"]
    assert details["legal_moves"] == sorted(details["legal_moves"])
    assert "e2e4" in details["legal_moves"]
    game = await games.get_by_id(game_id)
    assert details["fen"] == game.fen
    assert game.status == GameStatus.ACTIVE.value
    # §11.6: the strike is committed, not rolled back with the rejection.
    assert game.white_strikes == 1


async def test_three_illegal_moves_forfeit_the_game(client, api_state, table, games):
    white, _, game_id = table

    for _ in range(3):
        await deliver(api_state, white["bot_id"])
        response = await submit(client, white["token"], game_id, 0, "e2e5")
        assert response.status_code == 400

    game = await games.get_by_id(game_id)
    assert (game.status, game.result, game.termination) == (
        "finished", "black_win", "illegal_forfeit",
    )


async def test_a_bot_that_is_not_a_player_cannot_move(client, api_state, table):
    _, _, game_id = table
    stranger = await register(client, "eve")

    response = await submit(client, stranger["token"], game_id, 0, "e2e4")

    assert response.status_code == 403
    assert "not a player" in response.json()["error"]


async def test_the_side_not_to_move_cannot_move(client, api_state, table):
    _, black, game_id = table

    response = await submit(client, black["token"], game_id, 0, "e7e5")

    assert response.status_code == 403
    assert "not your turn" in response.json()["error"].lower()


async def test_a_stale_ply_is_409_carrying_ply_fen_and_status(
    client, api_state, table, games
):
    """Still the mover, but a ply behind — design §8.3's discard-and-re-poll case,
    which must not be dressed up as an authorisation failure."""
    white, black, game_id = table
    await play(client, api_state, table, ["e2e4", "e7e5"])

    await deliver(api_state, white["bot_id"])
    response = await submit(client, white["token"], game_id, 0, "d2d4")

    assert response.status_code == 409
    game = await games.get_by_id(game_id)
    assert response.json()["details"] == {
        "ply": game.ply, "fen": game.fen, "status": game.status,
    }


async def test_an_undelivered_position_is_409_pointing_at_the_poll(
    client, api_state, games
):
    white = await register(client, "ada")
    black = await register(client, "bob")
    game_id = await pair(api_state, white["bot_id"], black["bot_id"])

    response = await submit(client, white["token"], game_id, 0, "e2e4")

    assert response.status_code == 409
    assert "GET /bots/me/turn" in response.json()["error"]
    assert (await games.get_by_id(game_id)).status == "pending"


async def test_a_flagged_clock_beats_an_illegal_move(
    client, api_state, table, games, clock
):
    """§11.7: step 3 of design §6.4 precedes validation. A bot whose flag has fallen
    has flagged, whatever it submitted."""
    white, _, game_id = table
    clock.advance(RATED_TIME_CONTROL_NS * 2)

    response = await submit(client, white["token"], game_id, 0, "e2e5")

    assert response.status_code == 409
    game = await games.get_by_id(game_id)
    assert (game.result, game.termination) == ("black_win", "flag")
    assert game.white_strikes == 0
    assert response.json()["details"]["termination"] == "flag"


async def test_a_rejected_move_does_not_restart_the_clock(
    client, api_state, table, games, clock
):
    white, _, game_id = table
    before = await games.get_by_id(game_id)
    clock.advance(1_000_000)

    assert (await submit(client, white["token"], game_id, 0, "e2e5")).status_code == 400

    after = await games.get_by_id(game_id)
    assert after.turn_started_mono == before.turn_started_mono


async def test_an_accepted_move_returns_the_part_5_fields(client, api_state, table, games):
    white, _, game_id = table

    response = await submit(client, white["token"], game_id, 0, "e2e4", client_reported_ms=12)

    assert response.status_code == 200
    game = await games.get_by_id(game_id)
    assert response.json() == {
        "game_id": game_id,
        "ply": 1,
        "fen": game.fen,
        "status": "active",
        "result": None,
        "termination": None,
    }


async def test_a_mating_move_reports_result_and_termination(client, api_state, table):
    responses = await play(client, api_state, table, SCHOLARS)

    assert [r.status_code for r in responses] == [200] * len(SCHOLARS)
    assert responses[-1].json() == {
        **responses[-1].json(),
        "status": "finished",
        "result": "white_win",
        "termination": "checkmate",
    }


async def test_client_reported_ms_is_recorded_and_never_charged(
    client, api_state, table, store
):
    """Diagnostics only: it passes through to `moves` and touches no clock."""
    white, _, game_id = table

    await submit(client, white["token"], game_id, 0, "e2e4", client_reported_ms=999_999)

    moves = await MoveRepo(store.reader, store.reader_executor).list_moves_for_game(game_id)
    assert moves[0].client_reported_ms == 999_999


@pytest.mark.parametrize("scenario", ["illegal", "stale", "ok"])
async def test_no_response_body_carries_a_token(client, api_state, table, scenario):
    white, _, game_id = table
    token = white["token"]
    if scenario == "stale":
        await play(client, api_state, table, ["e2e4", "e7e5"])
        await deliver(api_state, white["bot_id"])
    move = "e2e5" if scenario == "illegal" else "d2d4"

    body = (await submit(client, token, game_id, 0, move)).text

    assert token not in body
