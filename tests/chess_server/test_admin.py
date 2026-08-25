"""The admin router (role spec §10, §8.5; design §15; Part 5).

Never exposed to attendees. Every route here is behind `ADMIN_TOKEN` and refuses
before it reads anything, which is why the auth cases come first.
"""
import logging

import pytest
from fastapi import status

from chess_core import STARTING_RATING, GameResult, TerminationReason
from chess_server.api.admin import check_consistency
from chess_server.api.auth import hash_token
from chess_server.engine import state as engine_state
from chess_server.engine.games import finalise_game_locked
from chess_server.engine.mailbox import deliver_for_poll
from chess_server.engine.ticker import _tick_once
from chess_server.store.repositories import (
    BotRepo,
    GameRepo,
    RatingHistoryRepo,
    SeatRepo,
)
from chess_server.store.txn import critical_section

from tests.chess_server.conftest import ADMIN_TOKEN, WALL

ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

ROUTES = (
    ("post", "/admin/games/1/abort"),
    ("post", "/admin/matchmaking/pause"),
    ("post", "/admin/matchmaking/resume"),
)


@pytest.fixture
async def two_bots(seed_bots, poll):
    white, black = await seed_bots("alpha", "beta")
    await poll(white.id, black.id)
    return white, black


@pytest.fixture
async def live_game(api_state, make_game, two_bots):
    game = await make_game(*two_bots)
    await deliver_for_poll(api_state.deps, two_bots[0].id)
    # Re-read: delivery moved the row pending -> active, and a CAS is on the
    # state you are transitioning *from*.
    return await GameRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).get_by_id(game.id)


# --- 1. authentication --------------------------------------------------------

@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer token-alpha"}]
)
async def test_no_admin_token_no_wrong_token_and_no_bot_token(
    client, method, path, headers
):
    response = await getattr(client, method)(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_token_that_authenticates_a_bot_is_still_refused_here(
    client, api_state, seed_bots
):
    """A bot token is not an admin token, however real it is elsewhere."""
    (bot,) = await seed_bots("gamma")
    token = "a-real-bot-token"
    async with critical_section(api_state.store.writer, api_state.store.executor):
        await BotRepo(api_state.store.writer, api_state.store.executor).update_token_hash(
            bot.id, hash_token(token)
        )
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/bots/me", headers=headers)).status_code == status.HTTP_200_OK

    response = await client.post("/admin/matchmaking/pause", headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# --- 2. abort -----------------------------------------------------------------

async def test_aborting_an_unknown_game_is_404(client):
    response = await client.post("/admin/games/4242/abort", headers=ADMIN)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "4242" in response.json()["error"]


async def test_aborting_a_finished_game_is_409(client, api_state, live_game):
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        await finalise_game_locked(
            api_state.deps, txn, live_game, GameResult.WHITE_WIN,
            TerminationReason.RESIGNATION,
        )

    response = await client.post(f"/admin/games/{live_game.id}/abort", headers=ADMIN)

    assert response.status_code == status.HTTP_409_CONFLICT


async def test_aborting_a_live_game_is_unrated_and_frees_everything(
    client, api_state, live_game, sink, two_bots
):
    white, black = two_bots
    waiters = [api_state.waiters.register(bot.id) for bot in two_bots]
    sink.events.clear()

    body = (await client.post(f"/admin/games/{live_game.id}/abort", headers=ADMIN)).json()

    assert body == {
        "game_id": live_game.id, "status": "aborted", "termination": "admin_abort",
    }
    games = GameRepo(api_state.store.reader, api_state.store.reader_executor)
    ended = await games.get_by_id(live_game.id)
    assert (ended.status, ended.termination, ended.rated) == ("aborted", "admin_abort", 0)

    history = RatingHistoryRepo(api_state.store.reader, api_state.store.reader_executor)
    assert await history.sum_deltas_by_bot(white.id) == 0
    assert await SeatRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).list_seated_bot_ids() == []
    assert engine_state.mailbox == {}
    assert sink.types().count("game_ended") == 1
    assert all(waiter.event.is_set() for waiter in waiters)


# --- 3. pause and resume ------------------------------------------------------

async def test_pause_stops_the_ticker_creating_games_and_resume_restores_it(
    client, api_state, two_bots, poll
):
    assert (await client.post("/admin/matchmaking/pause", headers=ADMIN)).json() == {
        "paused": True
    }

    await _tick_once(api_state.deps, api_state.metrics)
    games = GameRepo(api_state.store.reader, api_state.store.reader_executor)
    assert await games.list_active_summaries() == []

    assert (await client.post("/admin/matchmaking/resume", headers=ADMIN)).json() == {
        "paused": False
    }
    await poll(*[bot.id for bot in two_bots])
    await _tick_once(api_state.deps, api_state.metrics)

    assert len(await games.list_active_summaries()) == 1


# --- 4. token re-issue — CUT with §15. A lost token means registering a new bot.


# --- 5. the consistency alarm (startup only; §15's route was cut) -------------

async def test_a_healthy_server_with_anchors_that_have_played_is_consistent(
    client, api_state, make_game, seed_bots, poll
):
    (competitor,) = await seed_bots("player")
    anchors = await api_state_anchors(api_state)
    await poll(competitor.id, anchors[0].id)
    game = await make_game(competitor, anchors[0])
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        await finalise_game_locked(
            api_state.deps, txn, game, GameResult.WHITE_WIN, TerminationReason.CHECKMATE
        )

    report = await check_consistency(api_state)

    assert report.consistent is True
    assert report.violations == []


async def test_a_corrupted_competitor_rating_is_reported_with_both_numbers(
    client, api_state, seed_bots
):
    (bot,) = await seed_bots("drifted")
    async with critical_section(api_state.store.writer, api_state.store.executor):
        await BotRepo(
            api_state.store.writer, api_state.store.executor
        ).update_rating_and_counters(bot.id, STARTING_RATING + 150, "win")

    report = await check_consistency(api_state)

    assert report.consistent is False
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.bot_name == "drifted"
    assert violation.expected_rating == STARTING_RATING
    assert violation.actual_rating == STARTING_RATING + 150
    assert violation.delta_sum == 0


async def api_state_anchors(api_state):
    from chess_server.engine.reference_bots import seed_anchors

    await seed_anchors(api_state.store.writer, api_state.store.executor)
    return await BotRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).list_anchors()
