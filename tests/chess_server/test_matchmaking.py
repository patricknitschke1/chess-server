"""Test task 14: the pool snapshot, pairing and the gated anchor offer (§7.2, §9)."""
import pytest

from chess_core import (
    ANCHOR_RATING_WINDOW,
    POLL_RECENCY_NS,
    Color,
    PoolEntry,
    STARTING_RATING,
)
from chess_server.engine import state
from chess_server.engine.pool import build_entry, snapshot_pool
from chess_server.engine.ticker import TickerMetrics, _tick_once, step_matchmaking
from chess_server.store.repositories import BotRepo, SeatRepo
from chess_server.store.txn import critical_section

RELAX_AFTER_TICKS = 3   # chess_core._allowed's threshold; not exported


async def tick(deps, metrics=None):
    await _tick_once(deps, metrics or TickerMetrics(), steps=[step_matchmaking])


async def anchor(seed_bots, name, rating):
    (bot,) = await seed_bots(name, role="anchor", rating=rating, is_anchor=1, owner="server")
    return bot


async def test_the_anchor_offer_is_gated_on_the_rating_window(
    deps, clock, games, seed_bots, poll
):
    """gap 200 pairs; gap 500 does not. pair_bots on a two-element pool would
    happily pair both — only should_offer_anchor refuses."""
    (near,) = await seed_bots("near-bot", rating=1200)
    await anchor(seed_bots, "ref-greedy", 1000)
    await poll(near.id)
    await tick(deps)
    assert len(await games.list_active_summaries()) == 1


async def test_an_anchor_outside_the_window_is_refused(deps, games, seed_bots, poll):
    (far,) = await seed_bots("far-bot", rating=1300)
    await anchor(seed_bots, "ref-random", 800)
    assert abs(1300 - 800) > ANCHOR_RATING_WINDOW
    await poll(far.id)
    await tick(deps)
    assert await games.list_active_summaries() == []


async def test_two_pairable_competitors_pair_with_each_other_not_the_anchor(
    deps, games, seed_bots, poll
):
    """The offer is idle-only. The anchor is rated *below* both deliberately: it
    then sorts first in pair_bots' (games_played, rating, bot_id) order, so passing
    the whole pool in would pair it with one of them and strand the other."""
    a, b = await seed_bots("bot-a", "bot-b", rating=STARTING_RATING)
    ref = await anchor(seed_bots, "ref-greedy", STARTING_RATING - ANCHOR_RATING_WINDOW // 2)
    await poll(a.id, b.id)
    await tick(deps)

    summaries = await games.list_active_summaries()
    assert len(summaries) == 1
    assert {summaries[0]["white_bot_id"], summaries[0]["black_bot_id"]} == {a.id, b.id}
    assert ref.id not in await SeatRepo(deps.conn, deps.executor).list_seated_bot_ids()


async def test_a_lone_owner_with_two_bots_pairs_once_the_relaxation_fires(
    deps, games, seed_bots, poll
):
    """Design §9.2's own motivating case. A hard-coded unpaired_ticks=0 never pairs
    these two, and nothing errors: pooled_bots 2, active_games 0, silence."""
    a, b = await seed_bots("bot-a", "bot-b", owner="one-attendee")
    await poll(a.id, b.id)

    for expected in range(1, RELAX_AFTER_TICKS + 1):
        await tick(deps)
        assert await games.list_active_summaries() == []
        assert state.unpaired_ticks[a.id] == expected
        assert state.unpaired_ticks[b.id] == expected

    await tick(deps)
    assert len(await games.list_active_summaries()) == 1


async def test_unpaired_ticks_is_cleared_when_a_bot_takes_a_seat(
    deps, games, seed_bots, poll
):
    a, b = await seed_bots("bot-a", "bot-b")
    await poll(a.id, b.id)
    state.unpaired_ticks[a.id] = 2
    state.unpaired_ticks[b.id] = 2

    await tick(deps)
    assert len(await games.list_active_summaries()) == 1
    assert state.unpaired_ticks.get(a.id, 0) == 0
    assert a.id not in state.unpaired_ticks
    assert b.id not in state.unpaired_ticks


async def test_two_anchors_alone_never_pair(deps, games, seed_bots):
    await anchor(seed_bots, "ref-greedy", 1000)
    await anchor(seed_bots, "ref-depth3", 1200)
    await tick(deps)
    assert await games.list_active_summaries() == []


async def test_a_stale_competitor_is_out_of_the_pool_but_a_never_polling_anchor_is_in(
    store, deps, clock, seed_bots, poll
):
    """The second half is what makes the anchor path reachable at all."""
    (stale,) = await seed_bots("stale-bot")
    ref = await anchor(seed_bots, "ref-random", 800)
    await poll(stale.id)
    clock.advance(POLL_RECENCY_NS + 1)

    async with critical_section(store.writer, store.executor) as txn:
        pool = await snapshot_pool(txn, clock())

    assert [e.bot_id for e in pool] == [ref.id]
    assert (await BotRepo(store.writer, store.executor).get_by_id(ref.id)).last_poll_mono is None


async def test_a_paused_matchmaker_creates_nothing_and_touches_no_counters(
    deps, games, seed_bots, poll
):
    a, b = await seed_bots("bot-a", "bot-b")
    await poll(a.id, b.id)
    deps.is_paused = lambda: True

    await tick(deps)
    assert await games.list_active_summaries() == []
    assert state.unpaired_ticks == {}


async def test_colours_alternate_from_last_color(store, deps, games, seed_bots, poll):
    a, b = await seed_bots("bot-a", "bot-b")
    async with critical_section(store.writer, store.executor):
        await BotRepo(store.writer, store.executor).update_pool_history(
            a.id, Color.WHITE.value, None, True
        )
    await poll(a.id, b.id)
    await tick(deps)

    summary = (await games.list_active_summaries())[0]
    assert summary["black_bot_id"] == a.id
    assert summary["white_bot_id"] == b.id


async def test_an_anchor_pairing_takes_its_colours_from_pair_bots(
    store, deps, games, seed_bots, poll
):
    (solo,) = await seed_bots("solo-bot", rating=1000)
    ref = await anchor(seed_bots, "ref-greedy", 1000)
    async with critical_section(store.writer, store.executor):
        await BotRepo(store.writer, store.executor).update_pool_history(
            solo.id, Color.WHITE.value, None, True
        )
    await poll(solo.id)
    await tick(deps)

    summary = (await games.list_active_summaries())[0]
    assert summary["black_bot_id"] == solo.id
    assert summary["white_bot_id"] == ref.id


async def test_every_pool_entry_field_matches_its_bots_row(store, deps, seed_bots, poll):
    """This is where zeros get passed instead of the truth."""
    (bot,) = await seed_bots("bot-a", rating=1111)
    other, = await seed_bots("bot-b")
    async with critical_section(store.writer, store.executor):
        repo = BotRepo(store.writer, store.executor)
        await repo.update_rating_and_counters(bot.id, 1111, "win")
        await repo.update_pool_history(bot.id, Color.WHITE.value, other.id, True)
    await poll(bot.id)
    state.unpaired_ticks[bot.id] = 4

    row = await BotRepo(store.writer, store.executor).get_by_id(bot.id)
    entry = build_entry(row)

    assert entry == PoolEntry(
        bot_id=row.id,
        owner=row.owner,
        rating=row.rating,
        games_played=row.games_played,
        is_anchor=bool(row.is_anchor),
        last_color=Color(row.last_color),
        white_count=row.white_count,
        last_opponent_id=row.last_opponent_id,
        unpaired_ticks=4,
    )
    assert (entry.games_played, entry.white_count, entry.last_opponent_id) == (1, 1, other.id)


async def test_a_bot_with_no_history_gets_none_not_a_zero(store, seed_bots):
    (bot,) = await seed_bots("fresh-bot")
    entry = build_entry(await BotRepo(store.writer, store.executor).get_by_id(bot.id))
    assert entry.last_color is None
    assert entry.last_opponent_id is None
    assert entry.unpaired_ticks == 0
