import chess
import pytest

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_FEN,
    STARTING_RATING,
    account_move_and_switch,
)
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import BotRepo, GameRepo, _clock_from_game

NOW = 10_000_000_000_000
WALL = "2026-08-24T00:00:00Z"
LATER = "2026-08-24T00:05:00Z"


@pytest.fixture
def repos(store):
    return BotRepo(store.writer, store.executor), GameRepo(store.writer, store.executor)


async def _two_bots(bots):
    made = []
    for name in ("white", "black"):
        bot_id = await bots.insert_bot(
            name=name,
            owner=name,
            token_hash=f"hash-{name}",
            role="competitor",
            rating=STARTING_RATING,
            is_anchor=0,
            created_at=WALL,
        )
        made.append(await bots.get_by_id(bot_id))
    return made


async def _fresh_game(repos):
    bots, games = repos
    white, black = await _two_bots(bots)
    return await games.insert_game(
        white=white,
        black=black,
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        source="matchmaker",
        now_mono=NOW,
        created_at=WALL,
    )


async def _raw(games, game_id):
    return dict(await games._one("SELECT * FROM games WHERE id = ?", (game_id,)))


async def _terminate(games, game_id, from_status, from_ply, **overrides):
    fields = {
        "status": "finished",
        "result": "white_win",
        "termination": "checkmate",
        "ended_at": LATER,
    }
    fields.update(overrides)
    return await games.cas_terminate(game_id, from_status, from_ply, **fields)


async def test_cas_terminate_from_a_stale_status_conflicts_and_moves_nothing(repos):
    _, games = repos
    game_id = await _fresh_game(repos)
    before = await _raw(games, game_id)

    with pytest.raises(CASConflict):
        await _terminate(games, game_id, "active", 0)

    assert await _raw(games, game_id) == before


async def test_cas_apply_move_from_a_stale_ply_conflicts_and_moves_nothing(repos):
    _, games = repos
    game_id = await _fresh_game(repos)
    game = await games.get_by_id(game_id)
    before = await _raw(games, game_id)
    board = chess.Board(STARTING_FEN)
    board.push_uci("e2e4")

    with pytest.raises(CASConflict):
        await games.cas_apply_move(
            game_id, from_ply=7, from_status="pending", fen_after=board.fen(),
            clock=_clock_from_game(game),
        )

    assert await _raw(games, game_id) == before


async def test_exactly_one_terminal_transition_survives_two_attempts(repos):
    _, games = repos
    game_id = await _fresh_game(repos)

    await _terminate(games, game_id, "pending", 0)
    with pytest.raises(CASConflict):
        await _terminate(games, game_id, "pending", 0, result="black_win")

    row = await _raw(games, game_id)
    assert (row["status"], row["result"]) == ("finished", "white_win")


async def test_redelivery_reports_no_delivery_and_never_restarts_the_clock(repos):
    _, games = repos
    game_id = await _fresh_game(repos)
    await games.cas_deliver(game_id, ply=0, now_mono=NOW, now_wall=WALL)
    first = await _raw(games, game_id)

    result = await games.cas_deliver(game_id, ply=0, now_mono=NOW + 60_000_000_000, now_wall=LATER)

    assert result == (False, False)
    assert await _raw(games, game_id) == first


async def test_delivery_activates_and_marks_delivered_in_one_statement(repos):
    _, games = repos
    game_id = await _fresh_game(repos)

    delivered, started = await games.cas_deliver(game_id, ply=0, now_mono=NOW, now_wall=WALL)

    row = await _raw(games, game_id)
    assert (delivered, started) == (True, True)
    assert row["status"] == "active"
    assert row["delivered_to_mover"] == 1
    assert row["turn_started_mono"] == NOW
    assert row["started_at"] == WALL


async def test_a_finished_game_cannot_re_enter_the_delivery_sweep(repos):
    _, games = repos
    game_id = await _fresh_game(repos)
    await games.cas_deliver(game_id, ply=0, now_mono=NOW, now_wall=WALL)

    await _terminate(games, game_id, "active", 0)

    row = await _raw(games, game_id)
    assert row["delivered_to_mover"] == 0
    assert row["turn_started_mono"] is None
    assert [g.id for g in await games.list_undelivered_non_terminal()] == []


async def test_a_delivered_move_advances_ply_fen_and_side(repos):
    _, games = repos
    game_id = await _fresh_game(repos)
    await games.cas_deliver(game_id, ply=0, now_mono=NOW, now_wall=WALL)
    game = await games.get_by_id(game_id)
    board = chess.Board(game.fen)
    board.push_uci("e2e4")
    update = account_move_and_switch(
        _clock_from_game(game), receive_mono=NOW + 1_000_000, now_mono=NOW + 1_000_000
    )

    await games.cas_apply_move(
        game_id, from_ply=0, from_status="active", fen_after=board.fen(), clock=update.new_clock
    )

    row = await _raw(games, game_id)
    assert row["ply"] == 1
    assert row["fen"] == board.fen()
    assert row["to_move"] == "black"
    assert row["delivered_to_mover"] == 0
    assert row["turn_started_mono"] is None
