"""Tests for matchmaker pairing policy per §9.2."""
import random

import chess_core.matchmaker as matchmaker
from chess_core.types import PoolEntry, Pairing, Color


# Failure path tests: edge cases

def test_pair_odd_pool_leaves_one_unpaired():
    """Odd pool leaves one bot unpaired."""
    pool = [
        PoolEntry(1, "alice", 1200, 0, False, None, 0, None, 0),
        PoolEntry(2, "bob", 1200, 0, False, None, 0, None, 0),
        PoolEntry(3, "charlie", 1200, 0, False, None, 0, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    assert len(pairings) == 1  # one pairing, one bot unpaired


def test_pair_single_bot_returns_empty():
    """Single bot returns empty pairings."""
    pool = [
        PoolEntry(1, "alice", 1200, 0, False, None, 0, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    assert len(pairings) == 0


def test_pair_empty_pool_returns_empty():
    """Empty pool returns empty pairings."""
    pairings = matchmaker.pair_bots([], seed=42)
    assert len(pairings) == 0


def test_pair_skips_same_owner_until_relaxed():
    """Same owner blocks pairing until unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(2, "alice", 1200, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(3, "bob", 1200, 5, False, Color.WHITE, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 and 2 share owner, so bot 1 pairs with bot 3
    assert len(pairings) == 1
    assert (pairings[0].white_bot_id, pairings[0].black_bot_id) in [(1, 3), (3, 1)]


def test_pair_relaxes_same_owner_after_3_ticks():
    """Same owner allowed when unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 3),  # waited 3 ticks
        PoolEntry(2, "alice", 1200, 5, False, Color.BLACK, 2, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Relaxed, so pairing allowed
    assert len(pairings) == 1


def test_pair_skips_rematch_until_relaxed():
    """Rematch of last_opponent_id blocks pairing until unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, 2, 0),  # last opponent was 2
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, 1, 0),    # last opponent was 1
        PoolEntry(3, "charlie", 1200, 5, False, Color.WHITE, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 and 2 are a rematch, so bot 1 pairs with bot 3
    assert len(pairings) == 1
    assert (pairings[0].white_bot_id, pairings[0].black_bot_id) in [(1, 3), (3, 1)]


def test_pair_relaxes_rematch_after_3_ticks():
    """Rematch allowed when unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, 2, 3),  # waited 3 ticks
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, 1, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Relaxed, so rematch allowed
    assert len(pairings) == 1


def test_pair_never_pairs_two_anchors():
    """Two anchors never pair with each other."""
    pool = [
        PoolEntry(99, "ref-random", 900, 100, True, Color.WHITE, 50, None, 0),
        PoolEntry(98, "ref-greedy", 1000, 100, True, Color.BLACK, 50, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # No pairing should be created
    assert len(pairings) == 0


# Happy path tests

def test_pair_sorts_by_games_played_first():
    """New bots paired first per §9.2."""
    pool = [
        PoolEntry(1, "alice", 1200, 10, False, Color.WHITE, 5, None, 0),
        PoolEntry(2, "bob", 1200, 0, False, None, 0, None, 0),  # new bot
        PoolEntry(3, "charlie", 1200, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(4, "dave", 1200, 0, False, None, 0, None, 0),  # new bot
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 2 and 4 (new bots) should be paired
    bot_ids = {pairings[0].white_bot_id, pairings[0].black_bot_id}
    assert bot_ids == {2, 4}


def test_pair_sorts_by_rating_second():
    """Rating sorts after games_played per §9.2."""
    pool = [
        PoolEntry(1, "alice", 1300, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(2, "bob", 1100, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(3, "charlie", 1200, 5, False, Color.WHITE, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Should pair adjacent: 2 (1100) with 3 (1200)
    bot_ids = {pairings[0].white_bot_id, pairings[0].black_bot_id}
    assert bot_ids == {2, 3}


def test_colour_precedence_alternates_from_last_color():
    """Colour alternates from last_color per §9.2."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),  # was White
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, None, 0),    # was Black
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 was White, should be Black now
    # Bot 2 was Black, should be White now
    assert pairings[0].white_bot_id == 2
    assert pairings[0].black_bot_id == 1


def test_colour_precedence_tie_break_by_white_count():
    """Tie-break by white_count when both have same last_color."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.BLACK, 5, None, 0),  # white_count=5
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, None, 0),    # white_count=2
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 2 has lower white_count, gets White
    assert pairings[0].white_bot_id == 2
    assert pairings[0].black_bot_id == 1


def test_colour_precedence_tie_break_by_bot_id():
    """Tie-break by bot_id when white_count is equal."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.BLACK, 3, None, 0),
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 has lower id, gets White
    assert pairings[0].white_bot_id == 1
    assert pairings[0].black_bot_id == 2


def test_pairing_is_deterministic_and_ignores_seed():
    """§9.2 pairing has no random component: the seed cannot change the result.

    Pinned as an exact expected list rather than a self-comparison, so a
    matchmaker that shuffled its input would fail rather than agree with itself.
    """
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(2, "bob", 1250, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(3, "charlie", 1150, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(4, "dave", 1300, 5, False, Color.BLACK, 2, None, 0),
    ]

    # Sorted by rating asc (games_played ties): charlie(3), alice(1), bob(2), dave(4).
    # Colours: charlie/alice both last played White, so bot_id breaks the tie.
    expected = [
        Pairing(white_bot_id=1, black_bot_id=3),
        Pairing(white_bot_id=2, black_bot_id=4),
    ]

    assert matchmaker.pair_bots(pool) == expected
    assert matchmaker.pair_bots(pool, seed=42) == expected
    assert matchmaker.pair_bots(pool, seed=99) == expected


def test_pair_bots_does_not_mutate_the_global_rng():
    """chess_core stays pure: pairing must not touch process-global state."""
    random.seed(0)
    before = random.random()

    random.seed(0)
    matchmaker.pair_bots(
        [
            PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),
            PoolEntry(2, "bob", 1250, 5, False, Color.BLACK, 2, None, 0),
        ],
        seed=999,
    )
    after = random.random()

    assert before == after


# Anchor gating tests

def test_should_offer_anchor_within_400():
    """Anchor offered within ±400 rating window per §9.3."""
    bot = PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0)
    anchor = PoolEntry(99, "ref-greedy", 1000, 100, True, Color.WHITE, 50, None, 0)
    
    # Within 400
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=False) is True
    
    # Exactly 400
    bot_at_1400 = PoolEntry(1, "alice", 1400, 5, False, Color.WHITE, 3, None, 0)
    assert matchmaker.should_offer_anchor(bot_at_1400, anchor, has_other_pairing_option=False) is True


def test_should_offer_anchor_beyond_400():
    """Anchor not offered beyond ±400 rating window."""
    bot = PoolEntry(1, "alice", 1500, 5, False, Color.WHITE, 3, None, 0)
    anchor = PoolEntry(99, "ref-greedy", 1000, 100, True, Color.WHITE, 50, None, 0)
    
    # Beyond 400 (diff = 500)
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=False) is False


def test_should_offer_anchor_only_when_idle():
    """Anchor only offered when bot would otherwise sit idle."""
    bot = PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0)
    anchor = PoolEntry(99, "ref-greedy", 1000, 100, True, Color.WHITE, 50, None, 0)
    
    # Has other pairing option
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=True) is False
    
    # No other option
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=False) is True


# Constants test

def test_constants_exist():
    assert matchmaker.ANCHOR_RATING_WINDOW == 400
