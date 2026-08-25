"""C1: the mailbox is cleared on the side switch, and the poll guards on ply.

Role spec §5.3 — *"the highest-cost rule in the document"*. Without it a bot
re-polls after its own move lands, drains the payload for the ply it just played,
submits for that ply, takes a 409, discards and re-polls: a loop with no error,
no log, and a request rate below the limiter's threshold. It burns its clock and
flags, and the attendee sees "my bot never moves".

Two independent layers, proved by two independent mutations. The clearing site is
one line inside a long function, and the ply guard is one comparison.
"""
from dataclasses import replace

import pytest

from chess_server.engine import state
from chess_server.engine.mailbox import TurnPayload
from chess_server.engine.runner import apply_move
from chess_server.store.repositories import GameRepo

from tests.chess_server.test_turn import pair, poll, register


@pytest.fixture
def no_hold(api_state):
    api_state.settings = replace(api_state.settings, poll_hold_seconds=0)
    return api_state


async def _reader_game(store, game_id):
    return await GameRepo(store.reader, store.reader_executor).get_by_id(game_id)


async def test_a_bot_that_polls_twice_is_not_handed_its_own_move_back(
    client, no_hold, store
):
    """Layer one: `apply_move_locked` clears the mover's mailbox in the same
    critical section as the side-switch CAS."""
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(no_hold, white["bot_id"], black["bot_id"])

    first = (await poll(client, white["token"])).json()
    assert first["ply"] == 0
    await apply_move(no_hold.deps, game_id, 0, first["legal_moves"][0])

    # The side has switched, so nothing may be left addressed to White.
    assert white["bot_id"] not in state.mailbox

    second = (await poll(client, white["token"])).json()
    assert second == {"game_id": None, "reason": "not_your_turn"}

    black_turn = (await poll(client, black["token"])).json()
    assert black_turn["ply"] == 1
    await apply_move(no_hold.deps, game_id, 1, black_turn["legal_moves"][0])

    third = (await poll(client, white["token"])).json()
    assert third["ply"] == 2
    assert third["fen"] == (await _reader_game(store, game_id)).fen


async def test_a_payload_for_a_ply_that_has_passed_is_discarded_not_served(
    client, no_hold, store
):
    """Layer two, alone: the entry is written by hand, so the clearing site never
    ran. The poll must still refuse to serve it, and must not leave it there."""
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(no_hold, white["bot_id"], black["bot_id"])
    served = (await poll(client, white["token"])).json()
    await apply_move(no_hold.deps, game_id, 0, served["legal_moves"][0])

    state.mailbox[white["bot_id"]] = TurnPayload(
        bot_id=white["bot_id"], game_id=game_id, ply=served["ply"],
        color="white", fen=served["fen"], legal_moves=served["legal_moves"],
        history_san=[], white_ms=served["white_ms"], black_ms=served["black_ms"],
        time_control_ms=served["time_control_ms"],
        increment_ms=served["increment_ms"],
    )

    answer = (await poll(client, white["token"])).json()

    assert answer == {"game_id": None, "reason": "not_your_turn"}
    assert white["bot_id"] not in state.mailbox


async def test_a_payload_for_a_game_that_has_ended_is_discarded(
    client, no_hold, store
):
    """`game_id` is compared as well as `ply`: a bot paired again lands on ply 0
    of a new game, where a stale ply-0 payload from the last one would match."""
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(no_hold, white["bot_id"], black["bot_id"])
    served = (await poll(client, white["token"])).json()

    state.mailbox.clear()
    next_game_id = game_id + 1
    state.mailbox[white["bot_id"]] = TurnPayload(
        bot_id=white["bot_id"], game_id=next_game_id, ply=0, color="white",
        fen=served["fen"], legal_moves=served["legal_moves"], history_san=[],
        white_ms=1, black_ms=1, time_control_ms=1, increment_ms=0,
    )

    answer = (await poll(client, white["token"])).json()

    assert answer["game_id"] == game_id
    assert answer["white_ms"] != 1


async def test_re_polling_the_same_position_returns_it_unchanged(
    client, no_hold, store, clock
):
    """The served payload stays in the mailbox: it is the record of the position
    currently delivered. Re-reading it is free and must not touch the clock."""
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(no_hold, white["bot_id"], black["bot_id"])

    first = (await poll(client, white["token"])).json()
    started = (await _reader_game(store, game_id)).turn_started_mono
    clock.advance(30_000_000_000)
    second = (await poll(client, white["token"])).json()

    assert second == first
    assert (await _reader_game(store, game_id)).turn_started_mono == started


async def test_both_mailboxes_are_empty_after_a_terminal_transition(
    client, no_hold, store
):
    white = await register(client, "ada")
    black = await register(client, "grace")
    game_id = await pair(no_hold, white["bot_id"], black["bot_id"])
    fools = ("f2f3", "e7e5", "g2g4", "d8h4")
    for ply, uci in enumerate(fools):
        token = white["token"] if ply % 2 == 0 else black["token"]
        assert (await poll(client, token)).json()["ply"] == ply
        await apply_move(no_hold.deps, game_id, ply, uci)

    assert (await _reader_game(store, game_id)).status == "finished"
    assert state.mailbox == {}
