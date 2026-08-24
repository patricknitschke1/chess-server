"""Matchmaker pairing policy per §9.2.

Pure function over explicit pool snapshots.
"""
from typing import List, Optional
from chess_core.types import PoolEntry, Pairing, Color


ANCHOR_RATING_WINDOW = 400


def pair_bots(
    pool: List[PoolEntry],
    seed: Optional[int] = None
) -> List[Pairing]:
    """Pure pairing function implementing §9.2 policy.
    
    Algorithm per §9.2:
    1. Sort by games_played asc, then rating asc, then bot_id asc
    2. Walk sorted list pairing adjacent entries
    3. Skip if same owner or rematch of last_opponent_id
    4. Bot with unpaired_ticks >= 3 has constraints relaxed
    5. Color precedence: alternate from last_color; ties broken by white_count,
       then bot_id
    
    Deterministic: the same pool always produces the same pairings.
    
    Args:
        pool: Snapshot of eligible bots
        seed: Currently unused. §9.2 pairing has no random component, so the
            result does not depend on it. Retained because the signature is
            pinned in the interfaces document.
    
    Returns:
        List of Pairing objects (white_bot_id, black_bot_id)
    """
    if len(pool) < 2:
        return []
    
    # Sort by games_played asc, rating asc, bot_id asc
    eligible = sorted(pool, key=lambda e: (e.games_played, e.rating, e.bot_id))
    
    pairings = []
    i = 0
    
    while i < len(eligible) - 1:
        a = eligible[i]
        j = i + 1
        matched = None
        
        while j < len(eligible):
            b = eligible[j]
            if _allowed(a, b):
                matched = j
                break
            j += 1  # b advances; a holds its place
        
        if matched is None:
            i += 1  # a is unpairable this tick
            continue
        
        # Make pairing with color precedence
        pairing = _make_pairing(a, eligible[matched])
        pairings.append(pairing)
        
        # matched > i always (j starts at i+1); pop the higher index first
        eligible.pop(matched)
        eligible.pop(i)
        # i is not incremented: the list shifted, so eligible[i] is a new bot
    
    return pairings


def _allowed(a: PoolEntry, b: PoolEntry) -> bool:
    """Check if pairing is allowed per §9.2 constraints."""
    relaxed = (a.unpaired_ticks >= 3) or (b.unpaired_ticks >= 3)
    
    # Same owner blocks unless relaxed
    if a.owner == b.owner and not relaxed:
        return False
    
    # Rematch blocks unless relaxed
    if a.last_opponent_id == b.bot_id and not relaxed:
        return False
    if b.last_opponent_id == a.bot_id and not relaxed:
        return False
    
    # Both anchors never pair
    if a.is_anchor and b.is_anchor:
        return False
    
    return True


def _make_pairing(a: PoolEntry, b: PoolEntry) -> Pairing:
    """Determine colors and create pairing per §9.2 color precedence."""
    # Determine who gets White
    # 1. Alternate from last_color
    # 2. Tie-break by white_count (lower gets White)
    # 3. Tie-break by bot_id (lower gets White)
    
    a_wants_white = a.last_color == Color.BLACK or a.last_color is None
    b_wants_white = b.last_color == Color.BLACK or b.last_color is None
    
    if a_wants_white and not b_wants_white:
        white_bot = a
        black_bot = b
    elif b_wants_white and not a_wants_white:
        white_bot = b
        black_bot = a
    else:
        # Both want same color or both are new (None): tie-break
        if a.white_count < b.white_count:
            white_bot = a
            black_bot = b
        elif b.white_count < a.white_count:
            white_bot = b
            black_bot = a
        else:
            # Equal white_count: tie-break by bot_id
            if a.bot_id < b.bot_id:
                white_bot = a
                black_bot = b
            else:
                white_bot = b
                black_bot = a
    
    return Pairing(white_bot_id=white_bot.bot_id, black_bot_id=black_bot.bot_id)


def should_offer_anchor(
    bot: PoolEntry,
    anchor: PoolEntry,
    has_other_pairing_option: bool
) -> bool:
    """Gate anchor pairing per §9.3.
    
    Anchor offered only when competitor would otherwise sit idle,
    and |rating - anchor_rating| <= 400.
    
    Args:
        bot: Competitor bot
        anchor: Anchor bot
        has_other_pairing_option: Whether bot has a non-anchor pairing available
    
    Returns:
        True if anchor should be offered to this bot
    """
    # Only offer when bot would otherwise be idle
    if has_other_pairing_option:
        return False
    
    # Rating must be within ±400
    rating_diff = abs(bot.rating - anchor.rating)
    return rating_diff <= ANCHOR_RATING_WINDOW
