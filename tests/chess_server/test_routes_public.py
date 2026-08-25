"""The unauthenticated read routes (role spec §8.1; design §10.4, §14; Part 5).

Every one of these reads on the reader connection, outside write_lock. They are
what a dashboard refresh and `analyze_game` are built from, so they must answer
for finished games as readily as for live ones.
"""
import pytest
from fastapi import status

from chess_core import GameResult, TerminationReason, ms_to_ns
from chess_server.engine import state as engine_state
from chess_server.engine.games import finalise_game_locked
from chess_server.engine.mailbox import deliver_for_poll
from chess_server.engine.runner import apply_move
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.txn import critical_section


@pytest.fixture
async def two_bots(seed_bots, poll):
    white, black = await seed_bots("alpha", "beta")
    await poll(white.id, black.id)
    return white, black


@pytest.fixture
async def live_game(api_state, make_game, two_bots):
    white, black = two_bots
    game = await make_game(white, black)
    await deliver_for_poll(api_state.deps, white.id)
    return game


async def _play(api_state, game_id, *ucis):
    games = GameRepo(api_state.store.writer, api_state.store.executor)
    for uci in ucis:
        game = await games.get_by_id(game_id)
        mover = game.white_bot_id if game.to_move == "white" else game.black_bot_id
        await deliver_for_poll(api_state.deps, mover)
        await apply_move(api_state.deps, game_id, game.ply, uci)


# --- 1. not found, and a non-numeric id ---------------------------------------

async def test_an_unknown_game_is_404_with_actionable_prose(client):
    response = await client.get("/games/4242")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "4242" in response.json()["error"]


async def test_an_unknown_games_moves_is_404(client):
    assert (await client.get("/games/4242/moves")).status_code == status.HTTP_404_NOT_FOUND


async def test_an_unknown_bots_rating_history_is_404(client):
    response = await client.get("/bots/4242/rating_history")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "4242" in response.json()["error"]


@pytest.mark.parametrize(
    "path", ["/games/not-a-number", "/games/not-a-number/moves",
             "/bots/not-a-number/rating_history"]
)
async def test_a_non_numeric_id_is_422(client, path):
    assert (await client.get(path)).status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- 2. the leaderboard --------------------------------------------------------

async def test_the_leaderboard_hides_benchmarks_and_marks_anchors(
    client, seed_bots
):
    await seed_bots("comp-a", rating=1300)
    await seed_bots("spar", role="benchmark", rating=1900)
    await seed_bots("ref-random", role="anchor", rating=800, is_anchor=1)

    body = (await client.get("/leaderboard")).json()

    assert [entry["bot_name"] for entry in body["bots"]] == ["comp-a", "ref-random"]
    assert [entry["is_anchor"] for entry in body["bots"]] == [False, True]
    assert body["total_bots"] == 2


async def test_the_leaderboard_orders_by_rating_then_name(client, seed_bots):
    await seed_bots("zulu", rating=1400)
    await seed_bots("alpha", rating=1400)
    await seed_bots("mike", rating=1500)

    names = [entry["bot_name"] for entry in (await client.get("/leaderboard")).json()["bots"]]

    assert names == ["mike", "alpha", "zulu"]


async def test_provisional_is_computed_from_games_played(client, seed_bots, store):
    bot, = await seed_bots("rookie")
    body = (await client.get("/leaderboard")).json()
    assert body["bots"][0]["is_provisional"] is True

    async with critical_section(store.writer, store.executor):
        store.writer.execute("UPDATE bots SET games_played = 10 WHERE id = ?", (bot.id,))

    body = (await client.get("/leaderboard")).json()
    assert body["bots"][0]["is_provisional"] is False


async def test_the_leaderboard_carries_no_token(client, seed_bots):
    await seed_bots("alpha")

    assert "token" not in (await client.get("/leaderboard")).text


# --- 3. GET /games/{id} never delivers ------------------------------------------

async def test_game_detail_does_not_deliver(client, api_state, two_bots, make_game):
    white, black = two_bots
    game = await make_game(white, black)
    before = await GameRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).get_by_id(game.id)

    assert (await client.get(f"/games/{game.id}")).status_code == status.HTTP_200_OK

    after = await GameRepo(
        api_state.store.reader, api_state.store.reader_executor
    ).get_by_id(game.id)
    assert (after.status, after.turn_started_mono, after.delivered_to_mover) == (
        before.status, before.turn_started_mono, before.delivered_to_mover
    )
    assert after.status == "pending"


async def test_game_detail_carries_every_part5_field(client, api_state, live_game):
    await _play(api_state, live_game.id, "e2e4")

    body = (await client.get(f"/games/{live_game.id}")).json()

    assert set(body) == {
        "game_id", "white_bot_id", "white_bot_name", "black_bot_id", "black_bot_name",
        "status", "result", "termination", "fen", "ply", "history_san",
        "white_ms", "black_ms", "time_control_ms", "increment_ms", "rated",
        "source", "created_at", "started_at", "ended_at",
    }
    assert body["history_san"] == ["e4"]
    assert body["status"] == "active"


# --- 4. GET /games/{id}/moves ---------------------------------------------------

async def test_three_plies_return_three_entries_in_order(client, api_state, live_game):
    await _play(api_state, live_game.id, "e2e4", "e7e5", "g1f3")

    body = (await client.get(f"/games/{live_game.id}/moves")).json()

    assert [entry["ply"] for entry in body["moves"]] == [1, 2, 3]
    assert [entry["san"] for entry in body["moves"]] == ["e4", "e5", "Nf3"]
    assert body["final_ply"] == 3
    assert body["starting_fen"].startswith("rnbqkbnr/pppppppp")
    assert (body["white_strikes"], body["black_strikes"]) == (0, 0)


async def test_each_colours_clock_moves_only_on_its_own_plies(
    client, api_state, live_game, clock
):
    """Not "non-increasing": with a 2 s increment a mover who thinks for 500 ms
    ends the ply richer. The invariant is that the recorded clock agrees with the
    recorded elapsed, and that the idle side is untouched."""
    for uci in ("e2e4", "e7e5", "g1f3"):
        clock.advance(ms_to_ns(500))
        await _play(api_state, live_game.id, uci)

    body = (await client.get(f"/games/{live_game.id}/moves")).json()
    increment = (await client.get(f"/games/{live_game.id}")).json()["increment_ms"]
    entries = body["moves"]

    assert entries[1]["white_ms_after"] == entries[0]["white_ms_after"]
    assert entries[2]["black_ms_after"] == entries[1]["black_ms_after"]
    assert entries[2]["white_ms_after"] == (
        entries[0]["white_ms_after"] + increment - entries[2]["server_elapsed_ms"]
    )


async def test_client_reported_ms_is_null_where_none_was_sent(
    client, api_state, live_game
):
    await _play(api_state, live_game.id, "e2e4")

    entry = (await client.get(f"/games/{live_game.id}/moves")).json()["moves"][0]

    assert entry["client_reported_ms"] is None
    assert isinstance(entry["server_elapsed_ms"], int)


async def test_moves_carries_every_part5_field(client, api_state, live_game):
    await _play(api_state, live_game.id, "e2e4")

    body = (await client.get(f"/games/{live_game.id}/moves")).json()

    assert set(body) == {
        "game_id", "white_bot_name", "black_bot_name", "white_rating", "black_rating",
        "status", "result", "termination", "starting_fen", "final_ply", "moves",
        "white_strikes", "black_strikes",
    }
    assert set(body["moves"][0]) == {
        "ply", "uci", "san", "fen_after", "server_elapsed_ms", "client_reported_ms",
        "white_ms_after", "black_ms_after",
    }


# --- 5. a finished game still answers -------------------------------------------

async def test_history_san_of_a_finished_game_matches_what_the_cache_held(
    client, api_state, live_game
):
    await _play(api_state, live_game.id, "e2e4", "e7e5")
    live_history = list(engine_state.history_san[live_game.id])

    games = GameRepo(api_state.store.writer, api_state.store.executor)
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        await finalise_game_locked(
            api_state.deps, txn, await games.get_by_id(live_game.id),
            GameResult.WHITE_WIN, TerminationReason.RESIGNATION,
        )

    assert live_game.id not in engine_state.history_san  # the cache is gone
    body = (await client.get(f"/games/{live_game.id}")).json()
    assert body["history_san"] == live_history == ["e4", "e5"]
    assert body["status"] == "finished"


async def test_a_finished_games_moves_still_answer(client, api_state, live_game):
    await _play(api_state, live_game.id, "e2e4")
    games = GameRepo(api_state.store.writer, api_state.store.executor)
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        await finalise_game_locked(
            api_state.deps, txn, await games.get_by_id(live_game.id),
            GameResult.BLACK_WIN, TerminationReason.RESIGNATION,
        )

    body = (await client.get(f"/games/{live_game.id}/moves")).json()

    assert len(body["moves"]) == 1
    assert (body["result"], body["termination"]) == ("black_win", "resignation")


# --- 6. attendee strings round-trip unescaped -----------------------------------

async def test_a_name_with_spaces_hyphens_and_underscores_round_trips(
    client, seed_bots
):
    await seed_bots("Ada_Lovelace-1 bot")

    body = (await client.get("/leaderboard")).json()

    assert body["bots"][0]["bot_name"] == "Ada_Lovelace-1 bot"


# --- rating history --------------------------------------------------------------

async def test_rating_history_returns_a_point_per_rated_game(
    client, api_state, live_game, two_bots
):
    white, _ = two_bots
    games = GameRepo(api_state.store.writer, api_state.store.executor)
    async with critical_section(
        api_state.store.writer, api_state.store.executor, api_state.deps.sink
    ) as txn:
        await finalise_game_locked(
            api_state.deps, txn, await games.get_by_id(live_game.id),
            GameResult.WHITE_WIN, TerminationReason.CHECKMATE,
        )

    body = (await client.get(f"/bots/{white.id}/rating_history")).json()

    assert body["bot_id"] == white.id
    assert body["bot_name"] == "alpha"
    assert len(body["points"]) == 1
    assert set(body["points"][0]) == {"game_id", "rating_after", "delta", "ts"}
    assert body["points"][0]["delta"] > 0


async def test_rating_history_is_empty_for_a_bot_that_has_not_played(
    client, seed_bots
):
    bot, = await seed_bots("alpha")

    body = (await client.get(f"/bots/{bot.id}/rating_history")).json()

    assert body["points"] == []


async def test_rating_history_never_exposes_a_token_hash(client, seed_bots, store):
    bot, = await seed_bots("alpha")

    text = (await client.get(f"/bots/{bot.id}/rating_history")).text

    assert "hash-alpha" not in text
    assert "token" not in text


# --- the reader connection --------------------------------------------------------

async def test_the_read_routes_answer_while_the_writer_holds_a_transaction(
    client, api_state, live_game
):
    """They read on the reader connection, outside write_lock. A read that queued
    behind the game loop would freeze the projector every tick."""
    async with critical_section(api_state.store.writer, api_state.store.executor):
        assert (await client.get("/leaderboard")).status_code == status.HTTP_200_OK
        assert (await client.get(f"/games/{live_game.id}")).status_code == status.HTTP_200_OK
        assert (
            await client.get(f"/games/{live_game.id}/moves")
        ).status_code == status.HTTP_200_OK


async def test_bot_repo_leaderboard_includes_anchors_but_not_benchmarks(
    store, seed_bots
):
    await seed_bots("comp")
    await seed_bots("spar", role="benchmark")
    await seed_bots("ref-random", role="anchor", is_anchor=1)

    rows = await BotRepo(store.reader, store.executor).list_leaderboard()

    assert {row.name for row in rows} == {"comp", "ref-random"}
