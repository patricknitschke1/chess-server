import pathlib
import sqlite3

import pytest

from chess_core import POLL_RECENCY_NS, STARTING_RATING, window_start_mono
from chess_server.store.repositories import BotRepo

NOW = 10_000_000_000_000
CUTOFF = window_start_mono(NOW, POLL_RECENCY_NS)
WALL = "2026-08-24T00:00:00Z"


@pytest.fixture
def bots(store):
    return BotRepo(store.writer, store.executor)


async def _bot(bots, name, **overrides):
    fields = {
        "owner": name,
        "token_hash": f"hash-{name}",
        "role": "competitor",
        "rating": STARTING_RATING,
        "is_anchor": 0,
        "created_at": WALL,
    }
    fields.update(overrides)
    return await bots.insert_bot(name=name, **fields)


async def test_duplicate_name_raises(bots):
    await _bot(bots, "ada")
    with pytest.raises(sqlite3.IntegrityError):
        await _bot(bots, "ada", owner="grace", token_hash="other")


async def test_get_by_token_hash_returns_none_for_an_unknown_hash(bots):
    assert await bots.get_by_token_hash("nobody") is None


async def test_presence_candidates_are_the_bots_that_poll(bots):
    """Anchors are excluded because they never hold an HTTP connection; benchmark
    bots are included because they do."""
    competitor = await _bot(bots, "competitor")
    benchmark = await _bot(bots, "benchmark", role="benchmark")
    await _bot(bots, "ref-random", role="anchor", is_anchor=1)

    found = await bots.list_presence_candidates()

    assert [bot.id for bot in found] == [competitor, benchmark]


async def test_list_pool_candidates_excludes_the_ineligible(store, bots):
    eligible = await _bot(bots, "eligible")
    seated = await _bot(bots, "seated")
    benchmark = await _bot(bots, "benchmark", role="benchmark")
    stale = await _bot(bots, "stale")
    for bot_id in (eligible, seated, benchmark):
        await bots.update_last_poll(bot_id, WALL, NOW)
    await bots.update_last_poll(stale, WALL, CUTOFF - 1)
    game_id = store.writer.execute(
        "INSERT INTO games (white_bot_id, black_bot_id, status, fen, ply, to_move,"
        " white_ms, black_ms, time_control_ms, increment_ms, to_move_since_mono,"
        " rated, source, created_at)"
        " VALUES (?, ?, 'pending', 'f', 0, 'white', 1, 1, 1, 1, 0, 1, 'matchmaker', ?)",
        (eligible, seated, WALL),
    ).lastrowid
    store.writer.execute("INSERT INTO seats (bot_id, game_id) VALUES (?, ?)", (seated, game_id))

    ids = [row.id for row in await bots.list_pool_candidates(CUTOFF)]

    assert ids == [eligible]


async def test_list_pool_candidates_includes_an_anchor_that_never_polled(bots):
    anchor = await _bot(bots, "anchor", role="anchor", is_anchor=1)

    ids = [row.id for row in await bots.list_pool_candidates(CUTOFF)]

    assert ids == [anchor]


async def test_update_pool_history_increments_white_count_only_when_told(bots):
    bot_id = await _bot(bots, "ada")
    opponent = await _bot(bots, "grace")

    await bots.update_pool_history(bot_id, "black", opponent, increment_white=False)
    after_black = await bots.get_by_id(bot_id)
    assert (after_black.last_color, after_black.last_opponent_id) == ("black", opponent)
    assert after_black.white_count == 0

    await bots.update_pool_history(bot_id, "white", opponent, increment_white=True)
    after_white = await bots.get_by_id(bot_id)
    assert (after_white.last_color, after_white.last_opponent_id) == ("white", opponent)
    assert after_white.white_count == 1


@pytest.mark.parametrize(
    "outcome,expected",
    [("win", (1, 0, 0)), ("loss", (0, 1, 0)), ("draw", (0, 0, 1))],
)
async def test_update_rating_and_counters_moves_one_counter_and_games_played(
    bots, outcome, expected
):
    bot_id = await _bot(bots, "ada")

    await bots.update_rating_and_counters(bot_id, STARTING_RATING + 12, outcome)

    row = await bots.get_by_id(bot_id)
    assert (row.wins, row.losses, row.draws) == expected
    assert row.games_played == 1
    assert row.rating == STARTING_RATING + 12


async def test_update_last_poll_writes_the_wall_string_and_the_monotonic_count(bots):
    bot_id = await _bot(bots, "ada")

    await bots.update_last_poll(bot_id, WALL, NOW)

    row = await bots.get_by_id(bot_id)
    assert row.last_poll_at == WALL
    assert row.last_poll_mono == NOW


def test_no_repository_method_issues_a_transaction_statement():
    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "chess_server"
        / "store"
        / "repositories.py"
    ).read_text().upper()
    for keyword in ("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT"):
        assert keyword not in source, f"repositories.py issues {keyword}"
