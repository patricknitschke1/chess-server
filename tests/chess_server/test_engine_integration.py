"""Task 20: whole games, driven by the ticker.

Stands in for role spec §11.13's harness until 3c gives it real endpoints. The
scripted competitors go through the outer forms — `deliver_position` then
`apply_move` — which is the same path a route will take. Nothing sleeps: the
clock is injected and `_tick_once` is the only thing that advances the world.
"""
import chess
import pytest

from chess_core import (
    DELIVERY_GRACE_NS,
    POLL_RECENCY_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_RATING,
    GameResult,
    TerminationReason,
)
from chess_server.engine import state
from chess_server.engine.reference_bots import bot_for, seed_anchors
from chess_server.engine.runner import _mover_id, apply_move, deliver_position
from chess_server.engine.ticker import TickerMetrics, _tick_once
from chess_server.store.repositories import (
    NON_TERMINAL,
    BotRepo,
    GameRepo,
    RatingHistoryRepo,
    SeatRepo,
)

ONE_MS_NS = 1_000_000
MOVE_LIMIT = 500

# 1. f3 e5 2. g4 Qh4#. Scripted by colour, because the pairing picks the colours.
FOOLS_MATE = {"white": ["f2f3", "g2g4"], "black": ["e7e5", "d8h4"]}


class Scripted:
    """An in-process competitor. Reads its seat, delivers only when it is to move
    — a route would do the same — then submits over the outer form."""

    def __init__(self, deps, store, bot_id, script=None):
        self.deps = deps
        self.games = GameRepo(store.writer, store.executor)
        self.bot_id = bot_id
        self.script = script
        self.played = 0

    async def seat(self):
        return await self.games.get_for_bot(self.bot_id)

    def _uci(self, game):
        if self.script is None:
            colour = "white" if game.white_bot_id == self.bot_id else "black"
            board = chess.Board(game.fen)
            return sorted(move.uci() for move in board.legal_moves)[0]
        colour = "white" if game.white_bot_id == self.bot_id else "black"
        return self.script[colour][self.played]

    async def play(self):
        game = await self.seat()
        if game is None or game.status not in NON_TERMINAL:
            return None
        if _mover_id(game) != self.bot_id:
            return None
        game = await deliver_position(self.deps, self.bot_id)
        uci = self._uci(game)
        self.played += 1
        return await apply_move(self.deps, game.id, game.ply, uci)


async def tick(deps, metrics=None):
    await _tick_once(deps, metrics or TickerMetrics())


async def _status(games, game_id):
    return (await games.get_by_id(game_id)).status


async def _play_out(deps, games, game_id, *clients):
    """Tick, then let whoever is to move play, stopping the instant it is over so
    a later tick cannot pair the freed bots into a second game."""
    for _ in range(MOVE_LIMIT):
        await tick(deps)
        if await _status(games, game_id) not in NON_TERMINAL:
            return
        for client in clients:
            await client.play()
        if await _status(games, game_id) not in NON_TERMINAL:
            return
    raise AssertionError("game did not terminate within the move limit")


async def _pair_two(deps, games, seed_bots, poll, **kw):
    a, b = await seed_bots("bot-a", "bot-b", **kw)
    await poll(a.id, b.id)
    await tick(deps)
    game = await games.get_for_bot(a.id)
    assert game is not None
    return a, b, game


async def test_a_full_game_against_an_anchor_rates_only_the_competitor(
    store, deps, games, bot_repo, seed_bots, poll, sink
):
    bot_for("ref-random").rng.seed(0)  # the anchor is random; the test is not
    await seed_anchors(store.writer, store.executor)
    (competitor,) = await seed_bots("attendee", rating=850)
    await poll(competitor.id)

    await tick(deps)
    game = await games.get_for_bot(competitor.id)
    assert game is not None
    anchor_id = game.black_bot_id if game.white_bot_id == competitor.id else game.white_bot_id
    anchor = await bot_repo.get_by_id(anchor_id)
    assert anchor.is_anchor == 1

    await _play_out(deps, games, game.id, Scripted(deps, store, competitor.id))

    after = await games.get_by_id(game.id)
    assert after.status == "finished"
    assert after.rated == 1

    history = RatingHistoryRepo(store.writer, store.executor)
    assert len(await history.list_points_for_bot(competitor.id)) == 1
    assert await history.list_points_for_bot(anchor_id) == []
    assert (await bot_repo.get_by_id(anchor_id)).rating == anchor.rating
    assert (await bot_repo.get_by_id(competitor.id)).games_played == 1
    assert (await bot_repo.get_by_id(anchor_id)).games_played == 1

    assert await SeatRepo(store.writer, store.executor).list_seated_bot_ids() == []
    assert game.id not in state.history
    assert state.mailbox == {}


async def test_two_competitors_play_a_decisive_game_and_the_ratings_are_zero_sum(
    store, deps, games, bot_repo, seed_bots, poll
):
    a, b, game = await _pair_two(deps, games, seed_bots, poll)
    clients = [Scripted(deps, store, bot.id, FOOLS_MATE) for bot in (a, b)]

    await _play_out(deps, games, game.id, *clients)

    after = await games.get_by_id(game.id)
    assert after.status == "finished"
    assert after.termination == TerminationReason.CHECKMATE.value
    assert after.result == GameResult.BLACK_WIN.value

    history = RatingHistoryRepo(store.writer, store.executor)
    deltas = []
    for bot in (a, b):
        points = await history.list_points_for_bot(bot.id)
        assert len(points) == 1
        deltas.append(points[0].delta)
        assert (await bot_repo.get_by_id(bot.id)).rating == STARTING_RATING + points[0].delta
    assert sum(deltas) == 0


async def test_a_competitor_that_stops_moving_is_flagged_by_the_ticker(
    store, deps, clock, games, seed_bots, poll
):
    a, b, game = await _pair_two(deps, games, seed_bots, poll)
    await Scripted(deps, store, _mover_id(game)).play()
    game = await games.get_by_id(game.id)
    assert game.ply == 1
    # Delivered, then silent. Without the delivery this is task 12's abandonment
    # sweep instead: an undelivered position has started nobody's turn.
    await deliver_position(deps, _mover_id(game))

    clock.advance(RATED_TIME_CONTROL_NS + ONE_MS_NS)
    await tick(deps)

    after = await games.get_by_id(game.id)
    assert (after.status, after.termination) == ("finished", TerminationReason.FLAG.value)
    assert after.result == GameResult.WHITE_WIN.value
    assert after.rated == 1


async def test_a_competitor_that_stops_polling_mid_game_abandons(
    store, deps, clock, games, seed_bots, poll
):
    a, b, game = await _pair_two(deps, games, seed_bots, poll)
    mover = Scripted(deps, store, _mover_id(game))
    await mover.play()
    assert (await games.get_by_id(game.id)).ply == 1

    clock.advance(DELIVERY_GRACE_NS + ONE_MS_NS)
    await tick(deps)

    after = await games.get_by_id(game.id)
    assert (after.status, after.termination) == ("finished", TerminationReason.ABANDONED.value)
    assert after.result == GameResult.WHITE_WIN.value
    assert after.rated == 1


async def test_a_no_show_at_ply_zero_aborts_unrated_and_frees_the_pool(
    store, deps, clock, games, bot_repo, seed_bots, poll
):
    await seed_anchors(store.writer, store.executor)
    a, b, game = await _pair_two(deps, games, seed_bots, poll)

    clock.advance(DELIVERY_GRACE_NS + ONE_MS_NS)
    await tick(deps)

    after = await games.get_by_id(game.id)
    assert (after.status, after.termination) == ("aborted", TerminationReason.NO_SHOW.value)
    assert (after.rated, after.result) == (0, None)
    assert await SeatRepo(store.writer, store.executor).list_seated_bot_ids() == []

    # The grace already outran POLL_RECENCY_NS, so only the bot that polls again
    # is in the pool — and it pairs with an anchor rather than sitting idle.
    assert DELIVERY_GRACE_NS > POLL_RECENCY_NS
    await poll(a.id)
    await tick(deps)
    fresh = await games.get_for_bot(a.id)
    assert fresh is not None and fresh.id != game.id
    other = fresh.black_bot_id if fresh.white_bot_id == a.id else fresh.white_bot_id
    assert (await bot_repo.get_by_id(other)).is_anchor == 1


async def test_three_illegal_moves_forfeit_over_the_full_path(
    store, deps, games, seed_bots, poll
):
    a, b, game = await _pair_two(deps, games, seed_bots, poll)
    mover_id = _mover_id(game)
    await deliver_position(deps, mover_id)

    for expected in (1, 2):
        rejected = await apply_move(deps, game.id, 0, "e2e5")
        assert (rejected.strikes, rejected.forfeited) == (expected, False)
        assert (await games.get_by_id(game.id)).ply == 0

    rejected = await apply_move(deps, game.id, 0, "e2e5")
    assert (rejected.strikes, rejected.forfeited) == (3, True)

    after = await games.get_by_id(game.id)
    assert after.status == "finished"
    assert after.termination == TerminationReason.ILLEGAL_FORFEIT.value
    assert after.result == GameResult.BLACK_WIN.value


async def test_the_event_stream_is_ordered_and_gapless(
    store, deps, games, seed_bots, poll, sink
):
    a, b, game = await _pair_two(deps, games, seed_bots, poll)
    clients = [Scripted(deps, store, bot.id, FOOLS_MATE) for bot in (a, b)]
    await _play_out(deps, games, game.id, *clients)

    seqs = [seq for seq, _, _ in sink.events]
    assert seqs == sorted(seqs)
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))

    for_game = [
        name for _, name, data in sink.events if data.get("game_id") == game.id
    ]
    assert for_game == (
        ["game_created", "game_started"]
        + ["move_played"] * 4
        + ["game_ended", "rating_changed", "rating_changed"]
    )


async def test_ticking_on_after_everything_has_ended_changes_nothing(
    store, deps, clock, games, seed_bots, poll
):
    a, b, game = await _pair_two(deps, games, seed_bots, poll)
    clients = [Scripted(deps, store, bot.id, FOOLS_MATE) for bot in (a, b)]
    await _play_out(deps, games, game.id, *clients)

    clock.advance(POLL_RECENCY_NS + ONE_MS_NS)  # nobody is in the pool any more
    metrics = TickerMetrics()
    before = _snapshot(store)
    for _ in range(20):
        await tick(deps, metrics)

    # A raising tick propagates out of _tick_once and fails this test outright;
    # consecutive_tick_errors is only ever written by run_ticker.
    assert metrics.tick_number == 20
    assert metrics.consecutive_tick_errors == 0
    assert _snapshot(store) == before


def _snapshot(store) -> dict[str, list[tuple]]:
    tables = [
        row["name"]
        for row in store.reader.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    return {
        name: store.reader.execute(f"SELECT * FROM {name}").fetchall()
        for name in tables
    }
