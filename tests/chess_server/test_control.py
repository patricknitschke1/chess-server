"""Control handoff and the agent's delivery site (role spec §5.2, §8.3; design §13.3).

`GET /games/{id}/legal_moves` is a mutating route wearing a read-only name: it is
where delivery happens while `controller='agent'`, and delivery starts a clock.
"""
import asyncio
from dataclasses import replace

import pytest

from chess_core import AGENT_AUTO_RELEASE_NS, RATED_TIME_CONTROL_NS

from chess_server.engine.mailbox import deliver_for_poll
from chess_server.engine.ticker import step_agent_release
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.txn import critical_section

from tests.chess_server.test_route_moves import auth
from tests.chess_server.test_turn import Registrations, pair, register

HOLD_SECONDS = 5.0
GATHER_TIMEOUT_SECONDS = 5.0


async def control(client, token, action):
    return await client.post(
        "/bots/me/control", json={"action": action}, headers=auth(token)
    )


async def legal_moves(client, token, game_id):
    return await client.get(f"/games/{game_id}/legal_moves", headers=auth(token))


async def set_controller(api_state, bot_id, controller):
    async with critical_section(api_state.store.writer, api_state.store.executor) as txn:
        await BotRepo(txn.conn, txn.executor).update_controller(bot_id, controller)


async def test_an_unauthenticated_control_call_is_rejected(client):
    assert (await client.post("/bots/me/control", json={"action": "take"})).status_code == 401


async def test_an_unknown_action_names_both_valid_ones(client):
    registered = await register(client, "ada")

    response = await control(client, registered["token"], "pause")

    assert response.status_code == 400
    assert "take" in response.json()["error"] and "release" in response.json()["error"]


@pytest.mark.parametrize("deliver_first", [False, True], ids=["pending", "active"])
async def test_take_is_refused_whenever_the_bot_holds_a_seat(
    client, api_state, games, deliver_first
):
    """Seat-held, not 'a rated game in progress' — the latter is not evaluable at
    call time, and `pending` is exactly where it goes wrong (design §13.3)."""
    white = await register(client, "ada")
    black = await register(client, "bob")
    game_id = await pair(api_state, white["bot_id"], black["bot_id"])
    if deliver_first:
        await deliver_for_poll(api_state.deps, white["bot_id"])
    assert (await games.get_by_id(game_id)).status == (
        "active" if deliver_first else "pending"
    )

    response = await control(client, white["token"], "take")

    assert response.status_code == 409
    assert "in a game" in response.json()["error"]
    assert (await BotRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).get_by_id(white["bot_id"])).controller == "client"


async def test_take_and_release_round_trip_for_a_free_bot(client, api_state):
    registered = await register(client, "ada")

    taken = await control(client, registered["token"], "take")
    released = await control(client, registered["token"], "release")

    assert (taken.status_code, taken.json()["controller"]) == (200, "agent")
    assert (released.status_code, released.json()["controller"]) == (200, "client")


async def test_take_records_the_agent_action_for_auto_release(client, api_state, clock):
    registered = await register(client, "ada")

    await control(client, registered["token"], "take")

    bot = await BotRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).get_by_id(registered["bot_id"])
    assert bot.last_agent_action_mono == clock()


async def test_take_does_not_pause_the_clock(client, api_state, games):
    """A bot that could stop its own clock by switching controller would never
    flag. The seat rule refuses the call outright; the clock assertion is what
    keeps that true if the seat rule is ever relaxed."""
    white = await register(client, "ada")
    black = await register(client, "bob")
    game_id = await pair(api_state, white["bot_id"], black["bot_id"])
    await deliver_for_poll(api_state.deps, white["bot_id"])
    before = await games.get_by_id(game_id)

    assert (await control(client, white["token"], "take")).status_code == 409
    await set_controller(api_state, white["bot_id"], "agent")
    assert (await control(client, white["token"], "release")).status_code == 200

    after = await games.get_by_id(game_id)
    assert after.turn_started_mono == before.turn_started_mono
    assert (after.white_ms, after.black_ms) == (before.white_ms, before.black_ms)


async def test_take_wakes_a_held_poll_with_agent_has_control(client, api_state):
    """There must be no window in which the SDK still believes it may move. The
    bot is unseated — `take` is refused while it holds a seat — so the woken poll
    must answer on `controller`, not on the absence of a game."""
    registered = await register(client, "ada")
    registrations = Registrations()
    api_state.waiters.on_register = registrations
    api_state.settings = replace(api_state.settings, poll_hold_seconds=HOLD_SECONDS)

    async def hold():
        return await client.get("/bots/me/turn", headers=auth(registered["token"]))

    async def take():
        await registrations.nth(0)
        return await control(client, registered["token"], "take")

    async with asyncio.timeout(GATHER_TIMEOUT_SECONDS):
        held, taken = await asyncio.gather(hold(), take())

    assert taken.status_code == 200
    assert held.json() == {"game_id": None, "reason": "agent_has_control"}


async def test_an_agent_controlled_bot_is_not_a_pairing_candidate(
    client, api_state, store, clock
):
    """Otherwise a rated game is created *for* the agent immediately after the
    409 above has done its job (design §13.3)."""
    registered = await register(client, "ada")
    await control(client, registered["token"], "take")

    candidates = await BotRepo(store.reader, store.reader_executor).list_pool_candidates(0)

    assert registered["bot_id"] not in [bot.id for bot in candidates]


async def test_legal_moves_refuses_a_client_controlled_bot(client, api_state):
    white = await register(client, "ada")
    game_id = await pair(
        api_state, white["bot_id"], (await register(client, "bob"))["bot_id"]
    )

    response = await legal_moves(client, white["token"], game_id)

    assert response.status_code == 403
    assert "take_control()" in response.json()["error"]


async def test_legal_moves_delivers_and_starts_the_game(client, api_state, games, clock):
    white = await register(client, "ada")
    game_id = await pair(
        api_state, white["bot_id"], (await register(client, "bob"))["bot_id"]
    )
    await set_controller(api_state, white["bot_id"], "agent")

    response = await legal_moves(client, white["token"], game_id)

    assert response.status_code == 200
    body = response.json()
    game = await games.get_by_id(game_id)
    assert body == {
        "game_id": game_id, "ply": 0,
        "legal_moves": sorted(body["legal_moves"]), "fen": game.fen,
    }
    assert "e2e4" in body["legal_moves"]
    assert (game.status, game.delivered_to_mover) == ("active", 1)
    assert game.turn_started_mono == clock()


async def test_a_second_legal_moves_call_does_not_restart_the_clock(
    client, api_state, games, clock
):
    """§6.2: re-reading a position is free. Restarting here would let an agent
    refresh its own clock every time it thought again."""
    white = await register(client, "ada")
    game_id = await pair(
        api_state, white["bot_id"], (await register(client, "bob"))["bot_id"]
    )
    await set_controller(api_state, white["bot_id"], "agent")
    await legal_moves(client, white["token"], game_id)
    started = (await games.get_by_id(game_id)).turn_started_mono

    clock.advance(RATED_TIME_CONTROL_NS // 2)
    assert (await legal_moves(client, white["token"], game_id)).status_code == 200

    assert (await games.get_by_id(game_id)).turn_started_mono == started


async def test_legal_moves_keeps_the_agent_from_being_auto_released(
    client, api_state, store, clock
):
    """Step 6 is keyed on `last_agent_action_mono`. An agent that is working must
    not be released mid-thought, so every agent-facing call refreshes it."""
    white = await register(client, "ada")
    game_id = await pair(
        api_state, white["bot_id"], (await register(client, "bob"))["bot_id"]
    )
    await set_controller(api_state, white["bot_id"], "agent")
    async with critical_section(api_state.store.writer, api_state.store.executor) as txn:
        await BotRepo(txn.conn, txn.executor).update_last_agent_action(
            white["bot_id"], clock()
        )

    clock.advance(AGENT_AUTO_RELEASE_NS - 1)
    await legal_moves(client, white["token"], game_id)
    clock.advance(AGENT_AUTO_RELEASE_NS - 1)
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        await step_agent_release(api_state.deps, txn, clock())

    bots = BotRepo(store.reader, store.reader_executor)
    assert (await bots.get_by_id(white["bot_id"])).controller == "agent"


async def test_an_idle_agent_is_released_and_its_waiter_woken(
    client, api_state, store, clock
):
    registered = await register(client, "ada")
    await control(client, registered["token"], "take")
    waiter = api_state.waiters.register(registered["bot_id"])

    clock.advance(AGENT_AUTO_RELEASE_NS + 1)
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        await step_agent_release(api_state.deps, txn, clock())

    bots = BotRepo(store.reader, store.reader_executor)
    assert (await bots.get_by_id(registered["bot_id"])).controller == "client"
    assert waiter.event.is_set()


async def test_no_control_or_legal_moves_body_carries_a_token(client, api_state):
    registered = await register(client, "ada")
    game_id = await pair(
        api_state, registered["bot_id"], (await register(client, "bob"))["bot_id"]
    )

    bodies = [
        (await control(client, registered["token"], "take")).text,
        (await legal_moves(client, registered["token"], game_id)).text,
        (await control(client, registered["token"], "release")).text,
    ]

    assert all(registered["token"] not in body for body in bodies)
