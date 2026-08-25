"""GET /state — the one snapshot the dashboard can render from alone (role spec
§8.4; design §14; Part 5 `DashboardStateResponse`).

It is a read: it must never deliver a position, because delivery starts a clock
and a spectator opening the page must not start one.
"""
import pytest
from fastapi import status

from chess_core import RATED_INCREMENT_NS, RATED_TIME_CONTROL_NS, ns_to_ms
from chess_server.engine.games import create_game_locked
from chess_server.engine.mailbox import deliver_for_poll
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.run import current_run_id
from chess_server.store.txn import critical_section, current_seq

SECOND_NS = 1_000_000_000


@pytest.fixture
async def two_bots(seed_bots, poll):
    white, black = await seed_bots("alpha", "beta")
    await poll(white.id, black.id)
    return white, black


def summaries_by_id(body: dict) -> dict:
    return {game["game_id"]: game for game in body["active_games"]}


# --- 1. turn_elapsed_ms ------------------------------------------------------

async def test_turn_elapsed_ms_is_none_while_undelivered(client, make_game, two_bots):
    white, black = two_bots
    game = await make_game(white, black)

    body = (await client.get("/state")).json()

    assert summaries_by_id(body)[game.id]["turn_elapsed_ms"] is None
    assert summaries_by_id(body)[game.id]["status"] == "pending"


async def test_turn_elapsed_ms_after_delivery_is_the_time_since_delivery(
    client, api_state, make_game, two_bots, clock
):
    white, black = two_bots
    game = await make_game(white, black)
    await deliver_for_poll(api_state.deps, white.id)
    clock.advance(3 * SECOND_NS)

    body = (await client.get("/state")).json()

    assert summaries_by_id(body)[game.id]["turn_elapsed_ms"] == ns_to_ms(3 * SECOND_NS)
    assert summaries_by_id(body)[game.id]["status"] == "active"


# --- 2. event_id -------------------------------------------------------------

async def test_event_id_is_the_current_seq_and_grows_with_the_stream(
    client, make_game, two_bots
):
    before = (await client.get("/state")).json()["event_id"]
    assert before == current_seq()

    white, black = two_bots
    await make_game(white, black)

    after = (await client.get("/state")).json()["event_id"]
    assert after == current_seq()
    assert after > before


async def test_no_summary_is_newer_than_the_event_id_it_ships_with(
    client, api_state, monkeypatch, two_bots, sink
):
    """The read order is the contract: a client applying `id > event_id` must not
    skip an event that landed between the two reads."""
    white, black = two_bots
    original = GameRepo.list_active_summaries

    async def create_then_list(self):
        async with critical_section(
            api_state.store.writer, api_state.store.executor, api_state.deps.sink
        ) as txn:
            await create_game_locked(
                api_state.deps, txn, white, black,
                time_control_ns=RATED_TIME_CONTROL_NS,
                increment_ns=RATED_INCREMENT_NS,
                source="matchmaker",
                now_mono=api_state.deps.now_mono(),
            )
        return await original(self)

    monkeypatch.setattr(GameRepo, "list_active_summaries", create_then_list)
    body = (await client.get("/state")).json()

    created_seq = {
        data["game_id"]: seq
        for seq, event_type, data in sink.events
        if event_type == "game_created"
    }
    assert body["active_games"]
    for game in body["active_games"]:
        assert created_seq[game["game_id"]] <= body["event_id"]


# --- 3. shape ----------------------------------------------------------------

async def test_an_empty_server_is_a_renderable_snapshot(client):
    body = (await client.get("/state")).json()

    assert body["active_games"] == []
    assert body["featured_game_id"] is None
    assert body["run_id"] == current_run_id()


async def test_a_summary_carries_every_part5_field(client, make_game, two_bots):
    white, black = two_bots
    game = await make_game(white, black)

    summary = summaries_by_id((await client.get("/state")).json())[game.id]

    assert summary["white_bot_name"] == white.name
    assert summary["black_bot_name"] == black.name
    assert summary["white_rating"] == white.rating
    assert summary["black_rating"] == black.rating
    assert summary["to_move"] == "white"
    assert summary["ply"] == 0
    assert summary["rated"] is True
    assert summary["fen"].startswith("rnbqkbnr")


async def test_the_leaderboard_matches_the_leaderboard_route(client, two_bots):
    state = (await client.get("/state")).json()["leaderboard"]
    board = (await client.get("/leaderboard")).json()["bots"]

    assert state == board


# --- 4. featured -------------------------------------------------------------

async def test_featured_game_id_flags_exactly_one_summary(
    client, make_game, seed_bots, poll
):
    quiet_white, quiet_black = await seed_bots("quiet_w", "quiet_b")
    loud_white, loud_black = await seed_bots("loud_w", "loud_b", rating=1600)
    await poll(quiet_white.id, quiet_black.id, loud_white.id, loud_black.id)
    await make_game(quiet_white, quiet_black)
    loud = await make_game(loud_white, loud_black)

    body = (await client.get("/state")).json()

    assert body["featured_game_id"] == loud.id
    flagged = [g["game_id"] for g in body["active_games"] if g["is_featured"]]
    assert flagged == [loud.id]


async def test_the_featured_stamp_on_the_stream_agrees_with_state(
    client, api_state, make_game, two_bots
):
    game = await make_game(*two_bots)
    body = (await client.get("/state")).json()

    assert api_state.hub.featured_game_id == body["featured_game_id"] == game.id


# --- 5. a read, not a delivery ------------------------------------------------

async def test_state_neither_delivers_nor_records_a_poll(
    client, api_state, make_game, two_bots
):
    white, _ = two_bots
    game = await make_game(*two_bots)
    bots = BotRepo(api_state.store.reader, api_state.store.reader_executor)
    before = (await bots.get_by_id(white.id)).last_poll_mono

    assert (await client.get("/state")).status_code == status.HTTP_200_OK

    after = await GameRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).get_by_id(game.id)
    assert after.delivered_to_mover == 0
    assert after.status == "pending"
    assert (await bots.get_by_id(white.id)).last_poll_mono == before
