"""`GET /bots/me/turn` — the long-poll (role spec §5.2, §5.4-§5.6; design §8.2, §8.4).

Handler order is the specification: record the poll, **register the waiter**, then
read. No test here sleeps to observe behaviour; a hold that must expire uses
`hold_seconds = 0`, and a hold that must be woken awaits the `on_register` hook.
"""
import asyncio
from dataclasses import replace

import pytest

from chess_core import POLL_HOLD_NS, RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS

from chess_server.api.models import TurnResponse
from chess_server.api.settings import POLL_HOLD_SECONDS
from chess_server.engine import state
from chess_server.engine.games import create_game_locked
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.txn import critical_section

from tests.chess_server.conftest import JOIN_CODE

SECOND_NS = 1_000_000_000
HOLD_SECONDS = 5.0  # long enough that a woken poll can only have been woken


@pytest.fixture
def no_hold(api_state):
    """The hold expires on the next loop iteration, deterministically."""
    api_state.settings = replace(api_state.settings, poll_hold_seconds=0)
    return api_state


async def register(client, name, role="competitor"):
    response = await client.post("/bots", json={
        "name": name, "owner": name, "join_code": JOIN_CODE, "role": role,
    })
    assert response.status_code == 201
    return response.json()


async def poll(client, token):
    return await client.get(
        "/bots/me/turn", headers={"Authorization": f"Bearer {token}"}
    )


async def pair(api_state, white_id, black_id, *, time_control_ns=RATED_TIME_CONTROL_NS,
               increment_ns=RATED_INCREMENT_NS):
    """Through the real creation path, on the app's own deps, so the deferred
    `wake` reaches the app's waiter registry."""
    bots = BotRepo(api_state.store.writer, api_state.store.executor)
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        return await create_game_locked(
            api_state.deps, txn,
            await bots.get_by_id(white_id), await bots.get_by_id(black_id),
            time_control_ns=time_control_ns, increment_ns=increment_ns,
            source="matchmaker", now_mono=api_state.deps.now_mono(),
        )


class Registrations:
    """Observes waiter registration without a sleep. Each registration sets the
    next event, so a test can await 'the poll is now holding'."""

    def __init__(self, expected: int = 4):
        self.events = [asyncio.Event() for _ in range(expected)]
        self.count = 0

    def __call__(self, bot_id: int) -> None:
        self.events[self.count].set()
        self.count += 1

    async def nth(self, index: int) -> None:
        await self.events[index].wait()


async def test_an_unauthenticated_poll_is_rejected(client):
    assert (await client.get("/bots/me/turn")).status_code == 401
    assert (await poll(client, "not-a-token")).status_code == 401


async def test_a_flooding_bot_is_rate_limited(client, no_hold):
    registered = await register(client, "ada")
    no_hold.limiter.capacity = 1

    assert (await poll(client, registered["token"])).status_code == 200
    limited = await poll(client, registered["token"])

    assert limited.status_code == 429
    assert limited.headers["retry-after"]


async def test_an_unpaired_bot_is_told_it_is_waiting(client, no_hold):
    registered = await register(client, "ada")

    body = (await poll(client, registered["token"])).json()

    assert body == {"game_id": None, "reason": "waiting_for_pairing"}


async def test_a_paused_arena_says_so_rather_than_waiting(client, no_hold):
    registered = await register(client, "ada")
    no_hold.matchmaking_paused = True

    assert (await poll(client, registered["token"])).json()["reason"] == "paused"


async def test_the_side_not_to_move_is_told_so_and_nothing_is_delivered(
    client, no_hold, store
):
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(no_hold, white["bot_id"], black["bot_id"])

    body = (await poll(client, black["token"])).json()

    assert body == {"game_id": None, "reason": "not_your_turn"}
    game = await GameRepo(store.reader, store.reader_executor).get_by_id(game_id)
    assert game.turn_started_mono is None
    assert game.status == "pending"
    assert state.mailbox == {}


async def test_the_payload_carries_every_wire_field_and_the_game_s_own_clock(
    client, no_hold, store
):
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(
        no_hold, white["bot_id"], black["bot_id"],
        time_control_ns=60 * SECOND_NS, increment_ns=SECOND_NS,
    )

    body = (await poll(client, white["token"])).json()

    assert set(body) == set(TurnResponse.model_fields)
    assert body["game_id"] == game_id
    assert body["ply"] == 0
    assert body["color"] == "white"
    assert body["history_san"] == []
    assert "e2e4" in body["legal_moves"]
    assert (body["time_control_ms"], body["increment_ms"]) == (60_000, 1_000)
    assert (body["white_ms"], body["black_ms"]) == (60_000, 60_000)
    # Delivery is what moves a game pending -> active.
    game = await GameRepo(store.reader, store.reader_executor).get_by_id(game_id)
    assert game.status == "active"
    assert game.turn_started_mono is not None


async def test_the_waiter_is_registered_before_the_mailbox_is_read(
    client, no_hold, monkeypatch
):
    """Handler order is the specification. The `on_register` hook cannot catch
    this on its own — it moves with the registration — so the order is asserted."""
    from chess_server.api import routes_bots

    order = []
    no_hold.waiters.on_register = lambda bot_id: order.append("register")
    real = routes_bots._resolve_turn

    async def spy(app_state, bot_id):
        order.append("read")
        return await real(app_state, bot_id)

    monkeypatch.setattr(routes_bots, "_resolve_turn", spy)
    registered = await register(client, "ada")

    await poll(client, registered["token"])

    assert order[:2] == ["register", "read"]


async def test_a_wake_during_the_hold_delivers_the_new_game(client, api_state):
    """Registering before reading is what makes this pass: a poll that reads
    first loses the wake that fires in the gap and hangs for the whole hold."""
    api_state.settings = replace(api_state.settings, poll_hold_seconds=HOLD_SECONDS)
    registered = Registrations()
    api_state.waiters.on_register = registered
    white = await register(client, "ada")
    black = await register(client, "grace")

    held = asyncio.create_task(poll(client, white["token"]))
    await registered.nth(0)
    game_id = await pair(api_state, white["bot_id"], black["bot_id"])

    body = (await held).json()
    assert body["game_id"] == game_id
    assert body["ply"] == 0


async def test_a_second_poll_supersedes_the_first_and_keeps_the_delivery(
    client, api_state
):
    """Supersede cancels a waiter, never a delivery."""
    api_state.settings = replace(api_state.settings, poll_hold_seconds=HOLD_SECONDS)
    registered = Registrations()
    api_state.waiters.on_register = registered
    white = await register(client, "ada")
    black = await register(client, "grace")

    first = asyncio.create_task(poll(client, white["token"]))
    await registered.nth(0)
    second = asyncio.create_task(poll(client, white["token"]))
    await registered.nth(1)
    game_id = await pair(api_state, white["bot_id"], black["bot_id"])

    assert (await first).json() == {"game_id": None, "reason": "superseded"}
    assert (await second).json()["game_id"] == game_id
    assert api_state.waiters.held_count() == 0


async def test_only_the_turn_endpoint_refreshes_the_poll_stamps(
    client, no_hold, store, clock
):
    """Pool eligibility means 'the bot is actually running'. A dashboard refresh
    that touched these would put a dead bot back in the pool."""
    registered = await register(client, "ada")
    bots = BotRepo(store.reader, store.reader_executor)
    assert (await bots.get_by_id(registered["bot_id"])).last_poll_mono is None

    headers = {"Authorization": f"Bearer {registered['token']}"}
    await client.get("/bots/me", headers=headers)
    assert (await bots.get_by_id(registered["bot_id"])).last_poll_mono is None

    clock.advance(SECOND_NS)
    await poll(client, registered["token"])

    bot = await bots.get_by_id(registered["bot_id"])
    assert bot.last_poll_mono == clock()
    assert bot.last_poll_at is not None


async def test_the_opponent_s_move_does_not_wake_the_waiting_side(
    client, api_state
):
    """Why `not_your_turn` answers immediately rather than holding: nothing on the
    move path wakes the other seat, so a hold there would expire on the timeout and
    answer `not_your_turn` anyway — one poll per 20 s instead of one per move."""
    api_state.settings = replace(api_state.settings, poll_hold_seconds=HOLD_SECONDS)
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(api_state, white["bot_id"], black["bot_id"])
    assert (await poll(client, white["token"])).json()["ply"] == 0
    waiter = api_state.waiters.register(black["bot_id"])

    moved = await client.post(
        f"/games/{game_id}/moves",
        json={"ply": 0, "move": "e2e4"},
        headers={"Authorization": f"Bearer {white['token']}"},
    )
    assert moved.status_code == 200

    assert not waiter.event.is_set()
    api_state.waiters.discard(black["bot_id"], waiter)


async def test_the_side_not_to_move_answers_without_holding(client, api_state):
    """The hold is 5 s and the answer must arrive inside a fraction of that."""
    api_state.settings = replace(api_state.settings, poll_hold_seconds=HOLD_SECONDS)
    white = await register(client, "ada")
    black = await register(client, "grace")
    await pair(api_state, white["bot_id"], black["bot_id"])

    answered = await asyncio.wait_for(poll(client, black["token"]), timeout=0.5)

    assert answered.json() == {"game_id": None, "reason": "not_your_turn"}


def test_the_default_hold_is_the_shared_constant(api_state):
    """20 s here against the SDK's 30 s timeout: the skew is deliberate, so a
    healthy hold never times out on the client."""
    assert api_state.settings.poll_hold_seconds == POLL_HOLD_NS / 1e9
    assert POLL_HOLD_SECONDS == POLL_HOLD_NS / 1e9
