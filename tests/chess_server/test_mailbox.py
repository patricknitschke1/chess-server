"""The mailbox: the transport between a delivery and the poll that collects it.

Role spec §5.1-§5.3. Nothing wrote `state.mailbox` before this module existed.
"""
import dataclasses

import pytest

from chess_core import STARTING_FEN, get_legal_moves

from chess_server.engine import reference_bots, state
from chess_server.engine.mailbox import TurnPayload, deliver_for_poll, fill_mailbox_locked
from chess_server.engine.reference_bots import seed_anchors_locked
from chess_server.engine.runner import apply_move
from chess_server.engine.ticker import TickerMetrics, _tick_once, step_anchor_moves
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.txn import critical_section

SECOND_NS = 1_000_000_000


@pytest.fixture
def anchors(store):
    async def _seed():
        async with critical_section(store.writer, store.executor) as txn:
            await seed_anchors_locked(txn)
            bots = BotRepo(txn.conn, txn.executor)
            return {
                name: await bots.get_by_name(name)
                for name, _bot, _rating in reference_bots.ANCHORS
            }

    return _seed


async def test_no_seat_delivers_nothing_and_leaves_no_entry(deps, seed_bots):
    (lonely,) = await seed_bots("bot-a")

    assert await deliver_for_poll(deps, lonely.id) is None
    assert state.mailbox == {}


async def test_re_delivery_is_free_and_never_restarts_the_clock(
    deps, seed_bots, make_game, games, clock
):
    """A bot that re-polls while thinking must not reset its own clock — the same
    exploit design §8.3 closes for rejected moves."""
    white, black = await seed_bots("bot-a", "bot-b")
    game = await make_game(white, black)

    await deliver_for_poll(deps, white.id)
    first = state.mailbox[white.id]
    started = (await games.get_by_id(game.id)).turn_started_mono

    clock.advance(30 * SECOND_NS)
    await deliver_for_poll(deps, white.id)

    assert (await games.get_by_id(game.id)).turn_started_mono == started
    assert state.mailbox[white.id] == first


async def test_the_payload_carries_the_san_history_and_this_position_s_moves(
    deps, seed_bots, make_game, games
):
    white, black = await seed_bots("bot-a", "bot-b")
    game = await make_game(white, black)
    played = []
    for uci in ("e2e4", "e7e5", "g1f3"):
        current = await games.get_by_id(game.id)
        await deliver_for_poll(deps, current.white_bot_id if current.to_move == "white"
                               else current.black_bot_id)
        outcome = await apply_move(deps, game.id, current.ply, uci)
        played.append(outcome.san)

    await deliver_for_poll(deps, black.id)
    payload = state.mailbox[black.id]

    assert payload.history_san == played
    assert payload.ply == 3
    assert payload.color == "black"
    assert payload.legal_moves == sorted(get_legal_moves(payload.fen))
    assert payload.fen == (await games.get_by_id(game.id)).fen


async def test_the_payload_echoes_the_game_s_own_time_control(
    deps, seed_bots, make_game
):
    white, black = await seed_bots("bot-a", "bot-b")
    game = await make_game(
        white, black, time_control_ns=60 * SECOND_NS, increment_ns=SECOND_NS
    )

    await deliver_for_poll(deps, white.id)

    payload = state.mailbox[white.id]
    assert (payload.time_control_ms, payload.increment_ms) == (60_000, 1_000)
    assert (payload.white_ms, payload.black_ms) == (game.white_ms, game.black_ms)


async def test_a_rolled_back_delivery_leaves_no_mailbox_entry(
    store, deps, seed_bots, make_game, games
):
    """The write is deferred, so a transaction that never commits hands a bot a
    position the database does not believe was delivered."""
    white, black = await seed_bots("bot-a", "bot-b")
    game = await make_game(white, black)

    with pytest.raises(RuntimeError):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            bots = BotRepo(txn.conn, txn.executor)
            fill_mailbox_locked(txn, await bots.get_by_id(white.id), game)
            raise RuntimeError("the transaction fails after the fill")

    assert state.mailbox == {}


async def test_a_rolled_back_ply_leaves_neither_cache_ahead_of_the_database(
    store, deps, seed_bots, make_game, games
):
    white, black = await seed_bots("bot-a", "bot-b")
    game = await make_game(white, black)
    await deliver_for_poll(deps, white.id)

    with pytest.raises(RuntimeError):
        async with critical_section(store.writer, store.executor, deps.sink) as txn:
            from chess_server.engine.runner import apply_move_locked

            await apply_move_locked(
                deps, txn, game.id, game.ply, "e2e4",
                client_reported_ms=None, now_mono=deps.now_mono(),
            )
            raise RuntimeError("the transaction fails after the move")

    assert state.history[game.id] == [STARTING_FEN]
    assert state.history_san[game.id] == []
    assert state.mailbox[white.id].ply == 0


async def test_both_caches_are_dropped_when_the_game_ends(
    deps, seed_bots, make_game, games
):
    white, black = await seed_bots("bot-a", "bot-b")
    game = await make_game(white, black)
    fools = ("f2f3", "e7e5", "g2g4", "d8h4")
    for uci in fools:
        current = await games.get_by_id(game.id)
        mover = (current.white_bot_id if current.to_move == "white"
                 else current.black_bot_id)
        await deliver_for_poll(deps, mover)
        await apply_move(deps, game.id, current.ply, uci)

    assert (await games.get_by_id(game.id)).status == "finished"
    assert game.id not in state.history_san
    assert game.id not in state.history
    assert state.mailbox == {}


async def test_an_anchor_never_gets_a_mailbox_entry(
    deps, seed_bots, make_game, anchors
):
    """The mailbox is the transport for HTTP clients. An anchor has no client."""
    seeded = await anchors()
    (competitor,) = await seed_bots("bot-a")
    await make_game(seeded["ref-random"], competitor)

    await _tick_once(deps, TickerMetrics(), steps=[step_anchor_moves])

    assert state.mailbox == {}


def test_the_payload_is_frozen_and_carries_exactly_the_wire_fields():
    from chess_server.api.models import TurnResponse

    fields = {f.name for f in dataclasses.fields(TurnPayload)}
    assert fields == set(TurnResponse.model_fields) | {"bot_id"}
    payload = TurnPayload(
        bot_id=1, game_id=1, ply=0, color="white", fen=STARTING_FEN,
        legal_moves=[], history_san=[], white_ms=1, black_ms=1,
        time_control_ms=1, increment_ms=0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.ply = 1
