import pytest

from chess_core import (
    EXHIBITION_INCREMENT_NS,
    EXHIBITION_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_FEN,
    STARTING_RATING,
    ns_to_ms,
)
from chess_server.store.repositories import BotRepo, GameRepo, SeatRepo

NOW = 10_000_000_000_000
WALL = "2026-08-24T00:00:00Z"
BLACK_TO_MOVE_FEN = STARTING_FEN.replace(" w ", " b ")


@pytest.fixture
def repos(store):
    return (
        BotRepo(store.writer, store.executor),
        GameRepo(store.writer, store.executor),
        SeatRepo(store.writer, store.executor),
    )


async def _bot(bots, name, **overrides):
    fields = {
        "owner": overrides.pop("owner", name),
        "token_hash": f"hash-{name}",
        "role": "competitor",
        "rating": STARTING_RATING,
        "is_anchor": 0,
        "created_at": WALL,
    }
    fields.update(overrides)
    bot_id = await bots.insert_bot(name=name, **fields)
    return await bots.get_by_id(bot_id)


async def _game(games, white, black, **overrides):
    fields = {
        "time_control_ns": RATED_TIME_CONTROL_NS,
        "increment_ns": RATED_INCREMENT_NS,
        "source": "matchmaker",
        "now_mono": NOW,
        "created_at": WALL,
    }
    fields.update(overrides)
    return await games.insert_game(white=white, black=black, **fields)


async def test_the_delivery_sweep_skips_terminal_games(repos):
    bots, games, _ = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")
    live = await _game(games, white, black)
    finished = await _game(games, white, black)
    aborted = await _game(games, white, black)
    for game_id, status in ((finished, "finished"), (aborted, "aborted")):
        await games._write("UPDATE games SET status = ? WHERE id = ?", (status, game_id))

    ids = [row.id for row in await games.list_undelivered_non_terminal()]

    assert ids == [live]


async def test_get_for_bot_ignores_a_game_with_no_seat(repos):
    bots, games, _ = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")
    await _game(games, white, black)

    assert await games.get_for_bot(white.id) is None


async def test_get_for_bot_resolves_through_the_seat(repos):
    bots, games, seats = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")
    game_id = await _game(games, white, black)
    await seats.insert_seat(white.id, game_id)

    assert (await games.get_for_bot(white.id)).id == game_id


async def test_insert_game_takes_nanoseconds_and_stores_milliseconds(repos):
    bots, games, _ = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")

    row = await games.get_by_id(await _game(games, white, black))

    assert row.time_control_ms == ns_to_ms(RATED_TIME_CONTROL_NS)
    assert row.time_control_ms == 180_000
    assert row.white_ms == row.black_ms == 180_000
    assert row.increment_ms == ns_to_ms(RATED_INCREMENT_NS)


@pytest.mark.parametrize(
    "white_kwargs,black_kwargs,time_control_ns,expected",
    [
        ({"owner": "ada"}, {"owner": "ada"}, RATED_TIME_CONTROL_NS, 0),
        ({}, {}, EXHIBITION_TIME_CONTROL_NS, 0),
        ({"role": "anchor", "is_anchor": 1}, {}, RATED_TIME_CONTROL_NS, 1),
        ({}, {}, RATED_TIME_CONTROL_NS, 1),
    ],
    ids=["shared_owner", "exhibition", "one_anchor", "two_competitors"],
)
async def test_rated_is_settled_at_creation(
    repos, white_kwargs, black_kwargs, time_control_ns, expected
):
    bots, games, _ = repos
    white = await _bot(bots, "white", **white_kwargs)
    black = await _bot(bots, "black", **black_kwargs)
    increment_ns = (
        EXHIBITION_INCREMENT_NS
        if time_control_ns == EXHIBITION_TIME_CONTROL_NS
        else RATED_INCREMENT_NS
    )

    game_id = await _game(
        games, white, black, time_control_ns=time_control_ns, increment_ns=increment_ns
    )

    assert (await games.get_by_id(game_id)).rated == expected


async def test_to_move_comes_from_the_fen_not_from_ply_parity(repos):
    bots, games, _ = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")

    standard = await games.get_by_id(await _game(games, white, black))
    inverted = await games.get_by_id(await _game(games, white, black, fen=BLACK_TO_MOVE_FEN))

    assert (standard.ply, standard.to_move) == (0, "white")
    assert (inverted.ply, inverted.to_move) == (0, "black")


async def test_list_active_summaries_carries_the_board_the_dashboard_draws(repos):
    bots, games, _ = repos
    white = await _bot(bots, "white")
    black = await _bot(bots, "black")
    game_id = await _game(games, white, black)

    (summary,) = await games.list_active_summaries()

    assert summary["game_id"] == game_id
    assert summary["fen"] == STARTING_FEN
    assert summary["to_move"] == "white"
    assert summary["status"] == "pending"
    assert summary["white_bot_name"] == "white"
    assert summary["black_rating"] == STARTING_RATING


async def test_list_anchor_to_move_finds_only_live_games_awaiting_an_anchor(repos):
    bots, games, _ = repos
    anchor = await _bot(bots, "anchor", role="anchor", is_anchor=1)
    human = await _bot(bots, "human")
    anchor_to_move = await _game(games, anchor, human)
    await _game(games, human, anchor)  # anchor is black, white is to move
    over = await _game(games, anchor, human)
    await games._write("UPDATE games SET status = 'finished' WHERE id = ?", (over,))

    ids = [row.id for row in await games.list_anchor_to_move()]

    assert ids == [anchor_to_move]
