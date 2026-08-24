"""Test task 16: anchors deliver to themselves and move (§7.3, §11.5).

Without this step the three reference bots are dead code, every anchor game
`no_show`s fifteen seconds after creation, and a lone attendee never gets a game.
"""
import random

import chess
import pytest

from chess_core import (
    DELIVERY_GRACE_NS,
    PLY_CAP,
    GameResult,
    TerminationReason,
    ns_to_ms,
)
from chess_server.engine import reference_bots
from chess_server.engine.games import finalise_game_locked
from chess_server.engine.reference_bots import (
    RefGreedyBot,
    RefRandomBot,
    seed_anchors_locked,
)
from chess_server.engine.runner import _mover_id, apply_move, deliver_position
from chess_server.engine.ticker import (
    TickerMetrics,
    _clock_view,
    _tick_once,
    step_anchor_moves,
    step_delivery_grace,
)
from chess_server.store.repositories import BotRepo, MoveRepo
from chess_server.store.txn import critical_section

STEP_NS = 250_000_000  # a quarter second of "thinking" per clock read


class SteppingClock:
    """Advances on every read, so the anchor is charged real time between the
    tick's delivery and its move. A constant clock cannot see that at all."""

    def __init__(self, value: int, step: int = STEP_NS):
        self.value = value
        self.step = step

    def __call__(self) -> int:
        current = self.value
        self.value += self.step
        return current


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


@pytest.fixture
def moves(store):
    return MoveRepo(store.writer, store.executor)


async def tick(deps, metrics=None, steps=(step_anchor_moves,)):
    await _tick_once(deps, metrics or TickerMetrics(), steps=list(steps))


async def test_a_raising_choose_move_rolls_back_the_whole_unit(
    deps, games, moves, sink, seed_bots, make_game, anchors, monkeypatch
):
    """The delivery is inside the unit, so the rollback undoes it too."""
    seeded = await anchors()
    (competitor,) = await seed_bots("bot-a")
    game = await make_game(competitor, seeded["ref-random"])

    def boom(self, board, clock):
        raise RuntimeError("the reference bot is our bug, not an attendee's")

    monkeypatch.setattr(RefRandomBot, "choose_move", boom)
    metrics = TickerMetrics()
    await tick(deps, metrics)   # black to move is the anchor: play White first
    await deliver_position(deps, competitor.id)
    await apply_move(deps, game.id, 0, "e2e4")
    sink.events.clear()

    await tick(deps, metrics)

    after = await games.get_by_id(game.id)
    assert after.status == "active"
    assert after.ply == 1
    assert after.delivered_to_mover == 0
    assert len(await moves.list_moves_for_game(game.id)) == 1
    assert sink.of("move_played") == []
    assert metrics.consecutive_tick_errors == 0


async def test_the_failure_is_self_limiting_and_the_game_is_abandoned(
    deps, games, clock, seed_bots, make_game, anchors, monkeypatch
):
    """Task 12's sweep finishes what the rollback left undelivered."""
    seeded = await anchors()
    (competitor,) = await seed_bots("bot-a")
    game = await make_game(seeded["ref-random"], competitor)

    def boom(self, board, clock):
        raise RuntimeError("boom")

    monkeypatch.setattr(RefRandomBot, "choose_move", boom)
    await tick(deps)
    clock.advance(DELIVERY_GRACE_NS + 1)
    await tick(deps, steps=(step_anchor_moves, step_delivery_grace))

    after = await games.get_by_id(game.id)
    assert after.status == "aborted"
    assert after.termination == TerminationReason.NO_SHOW.value


async def test_an_anchor_plays_a_whole_game_without_ever_polling(
    deps, games, moves, bot_repo, seed_bots, make_game, anchors, monkeypatch
):
    """§11.5. The competitor is scripted through the outer apply_move; the anchor
    is only ever touched by the ticker."""
    monkeypatch.setitem(reference_bots._BY_NAME, "ref-random", RefRandomBot(random.Random(7)))
    seeded = await anchors()
    (competitor,) = await seed_bots("bot-a")
    game = await make_game(competitor, seeded["ref-random"])
    scripted = RefGreedyBot()

    for _ in range(PLY_CAP + 2):
        current = await games.get_by_id(game.id)
        if current.status not in ("pending", "active"):
            break
        if _mover_id(current) == competitor.id:
            await deliver_position(deps, competitor.id)
            current = await games.get_by_id(game.id)
            move = scripted.choose_move(chess.Board(current.fen), _clock_view(current))
            await apply_move(deps, game.id, current.ply, move.uci())
        else:
            await tick(deps)

    finished = await games.get_by_id(game.id)
    assert (finished.status, finished.termination) == ("finished", "checkmate")
    played = await moves.list_moves_for_game(game.id)
    assert len(played) == finished.ply >= 2
    # The anchor never called an endpoint; its last_poll_mono is still untouched.
    assert (await bot_repo.get_by_id(seeded["ref-random"].id)).last_poll_mono is None


async def test_the_anchor_is_charged_real_time_through_the_locked_path(
    deps, games, moves, clock, seed_bots, make_game, anchors
):
    """A second move implementation would leave the clock untouched."""
    seeded = await anchors()
    (competitor,) = await seed_bots("bot-a")
    game = await make_game(seeded["ref-greedy"], competitor)
    deps.now_mono = SteppingClock(clock())

    await tick(deps)

    played = await moves.list_moves_for_game(game.id)
    assert len(played) == 1
    # One read starts the tick, the next is what apply_move_locked is given.
    assert played[0].server_elapsed_ms == ns_to_ms(STEP_NS)
    after = await games.get_by_id(game.id)
    assert after.white_ms == game.white_ms - ns_to_ms(STEP_NS) + after.increment_ms
    assert after.ply == 1
    assert after.to_move == "black"


async def test_two_anchor_games_both_move_and_one_failure_is_isolated(
    deps, games, moves, seed_bots, make_game, anchors, monkeypatch
):
    seeded = await anchors()
    a, b = await seed_bots("bot-a", "bot-b")
    doomed = await make_game(seeded["ref-random"], a)
    survivor = await make_game(seeded["ref-greedy"], b)

    def boom(self, board, clock):
        raise RuntimeError("boom")

    monkeypatch.setattr(RefRandomBot, "choose_move", boom)
    metrics = TickerMetrics()
    await tick(deps, metrics)

    assert await moves.list_moves_for_game(doomed.id) == []
    assert len(await moves.list_moves_for_game(survivor.id)) == 1
    assert (await games.get_by_id(doomed.id)).delivered_to_mover == 0
    assert metrics.consecutive_tick_errors == 0


async def test_an_anchor_to_move_in_a_finished_game_is_never_a_candidate(
    deps, games, moves, seed_bots, make_game, anchors
):
    seeded = await anchors()
    (competitor,) = await seed_bots("bot-a")
    game = await make_game(competitor, seeded["ref-random"])
    await deliver_position(deps, competitor.id)
    await apply_move(deps, game.id, 0, "e2e4")

    async with critical_section(deps.conn, deps.executor) as txn:
        await finalise_game_locked(
            deps, txn, await games.get_by_id(game.id),
            GameResult.BLACK_WIN, TerminationReason.RESIGNATION,
        )

    assert await games.list_anchor_to_move() == []
    await tick(deps)
    assert len(await moves.list_moves_for_game(game.id)) == 1
