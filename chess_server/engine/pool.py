"""The matchmaking pool snapshot and the anchor offer (role spec §9, design §9.1-§9.4)."""
from chess_core import (
    POLL_RECENCY_NS,
    Color,
    PoolEntry,
    should_offer_anchor,
    window_start_mono,
)

from chess_server.engine import state
from chess_server.store.repositories import BotRepo
from chess_server.store.rows import BotRow
from chess_server.store.txn import Txn


def build_entry(bot: BotRow) -> PoolEntry:
    """Every field supplied truthfully. A builder who cannot find one and passes
    0 produces a silent deadlock, not an error (role spec §9.2)."""
    return PoolEntry(
        bot_id=bot.id,
        owner=bot.owner,
        rating=bot.rating,
        games_played=bot.games_played,
        is_anchor=bool(bot.is_anchor),
        last_color=Color(bot.last_color) if bot.last_color is not None else None,
        white_count=bot.white_count,
        last_opponent_id=bot.last_opponent_id,
        unpaired_ticks=state.unpaired_ticks.get(bot.id, 0),
    )


async def snapshot_pool(txn: Txn, now_mono: int) -> list[PoolEntry]:
    """§9.1 lives in `list_pool_candidates`; the recency bound is a window start,
    never a subtraction here."""
    cutoff_mono = window_start_mono(now_mono, POLL_RECENCY_NS)
    rows = await BotRepo(txn.conn, txn.executor).list_pool_candidates(cutoff_mono)
    return [build_entry(row) for row in rows]


def offer_anchors(
    competitors: list[PoolEntry], anchors: list[PoolEntry], paired_ids: set[int]
) -> list[tuple[PoolEntry, PoolEntry]]:
    """Design §9.3. `pair_bots` only refuses anchor-versus-anchor; the idle-only,
    fewest-games and ±ANCHOR_RATING_WINDOW rules all live in `should_offer_anchor`.
    """
    # (games_played, rating, bot_id) is what "fewest-games eligible bot" means.
    idle = sorted(
        (entry for entry in competitors if entry.bot_id not in paired_ids),
        key=lambda entry: (entry.games_played, entry.rating, entry.bot_id),
    )
    available = list(anchors)
    offers: list[tuple[PoolEntry, PoolEntry]] = []
    for competitor in idle:
        nearest = sorted(available, key=lambda a: (abs(competitor.rating - a.rating), a.bot_id))
        for anchor in nearest:
            # False by construction: pair_bots left this bot out, which is exactly
            # "would otherwise sit idle".
            if should_offer_anchor(competitor, anchor, has_other_pairing_option=False):
                offers.append((competitor, anchor))
                available.remove(anchor)
                break
    return offers
