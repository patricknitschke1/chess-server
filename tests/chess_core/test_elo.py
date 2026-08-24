"""Tests for Elo rating calculations.

The zero-sum property test is the critical one. Swap symmetry is NOT a property
of Elo and is not tested: an upset (1000 beats 1400) moves 22 points, the
expected result (1400 beats 1000) moves 2.
"""
import chess_core.elo as elo


# Property tests: zero-sum, and the direction of the exchange

def test_elo_zero_sum():
    """Property test: exchange is zero-sum across the rating space per §10.1.

    This sweeps the whole plausible rating range rather than sampling a handful of
    pairs. Rounding is where zero-sum breaks, and rounding boundaries are exactly
    what a hand-picked case list misses. Verified: 3,249 pairs, 0 violations, runs
    in well under a second.
    """
    grid = range(600, 2001, 25)

    for winner_rating in grid:
        for loser_rating in grid:
            winner_update, loser_update = elo.compute_rating_exchange(winner_rating, loser_rating)

            # Zero-sum: the points the winner gains equal the points the loser loses.
            assert winner_update.delta + loser_update.delta == 0, \
                f"Not zero-sum for {winner_rating} vs {loser_rating}: " \
                f"{winner_update.delta} + {loser_update.delta}"


def test_underdog_gains_more_than_the_favourite():
    """The property that actually holds where symmetry does not.

    Swapping the two ratings does not negate the deltas; it produces a different
    magnitude. That asymmetry is the whole point of Elo, so pin its direction.
    """
    upset, _ = elo.compute_rating_exchange(1000, 1400)
    expected_result, _ = elo.compute_rating_exchange(1400, 1000)

    assert upset.delta == 22
    assert expected_result.delta == 2
    assert upset.delta > expected_result.delta


def test_draw_exchange_zero_sum():
    """Draw exchange is zero-sum across the rating space.

    Verified: 3,249 pairs, 0 violations.
    """
    grid = range(600, 2001, 25)

    for white_rating in grid:
        for black_rating in grid:
            white_update, black_update = elo.compute_draw_exchange(white_rating, black_rating)
            assert white_update.delta + black_update.delta == 0, \
                f"Draw not zero-sum for {white_rating} vs {black_rating}: " \
                f"{white_update.delta} + {black_update.delta}"


def test_draw_between_equal_ratings_moves_nothing():
    """An equal-rated draw is the expected result, so neither rating moves.

    Swept rather than checked at 1200 alone, so a bug that only bites away from
    the starting rating cannot hide.
    """
    for rating in range(600, 2001, 25):
        white_update, black_update = elo.compute_draw_exchange(rating, rating)

        assert white_update.delta == 0, rating
        assert black_update.delta == 0, rating
        assert white_update.rating_after == rating
        assert black_update.rating_after == rating


def test_draw_moves_points_from_the_favourite_to_the_underdog():
    """A draw against a much weaker bot is a bad result and must cost rating."""
    white_update, black_update = elo.compute_draw_exchange(1400, 1000)

    assert white_update.delta < 0
    assert black_update.delta > 0
    assert white_update.delta + black_update.delta == 0


# Extreme rating gap tests

def test_extreme_rating_gap_1000_vs_2000():
    """Verify exchange is sane at 1000-point gap."""
    winner_update, loser_update = elo.compute_rating_exchange(1000, 2000)
    
    # Underdog wins: big gain
    assert winner_update.delta > 20
    # Favorite loses: big loss
    assert loser_update.delta < -20
    # Zero-sum
    assert winner_update.delta + loser_update.delta == 0


def test_extreme_rating_gap_800_vs_1600():
    """Verify exchange is sane at 800-point gap."""
    winner_update, loser_update = elo.compute_rating_exchange(800, 1600)
    
    assert winner_update.delta > 20
    assert loser_update.delta < -20
    assert winner_update.delta + loser_update.delta == 0


# One-sided anchor tests

def test_one_sided_exchange_competitor_only():
    """Anchor rating never changes per §10.3."""
    competitor_update = elo.compute_one_sided_exchange(
        competitor_rating=1200,
        anchor_rating=1000,
        competitor_won=True
    )
    
    # Competitor gains points
    assert competitor_update.delta > 0
    # Anchor rating not returned (one-sided)


def test_one_sided_exchange_competitor_loses():
    """Competitor loses points when losing to anchor."""
    competitor_update = elo.compute_one_sided_exchange(
        competitor_rating=1200,
        anchor_rating=1000,
        competitor_won=False
    )
    
    # Competitor loses points
    assert competitor_update.delta < 0


def test_one_sided_exchange_shrinks_near_anchor():
    """Injection shrinks as competitor approaches anchor per §10.3."""
    # Competitor far from anchor
    far_update = elo.compute_one_sided_exchange(
        competitor_rating=600,
        anchor_rating=1000,
        competitor_won=True
    )
    
    # Competitor near anchor
    near_update = elo.compute_one_sided_exchange(
        competitor_rating=980,
        anchor_rating=1000,
        competitor_won=True
    )
    
    assert far_update.delta > near_update.delta


# Happy path tests

def test_compute_rating_exchange_equal_ratings():
    """Equal ratings, winner gains ~12, loser loses ~12."""
    winner_update, loser_update = elo.compute_rating_exchange(1200, 1200)
    
    assert winner_update.rating_before == 1200
    assert winner_update.delta == 12  # K/2 for equal ratings
    assert winner_update.rating_after == 1212
    
    assert loser_update.rating_before == 1200
    assert loser_update.delta == -12
    assert loser_update.rating_after == 1188


def test_compute_rating_exchange_underdog_wins():
    """Lower-rated player winning gains more points."""
    winner_update, loser_update = elo.compute_rating_exchange(1000, 1200)
    
    assert winner_update.delta > 12  # more than equal-rating case
    assert loser_update.delta < -12


def test_compute_rating_exchange_favorite_wins():
    """Higher-rated player winning gains fewer points."""
    winner_update, loser_update = elo.compute_rating_exchange(1200, 1000)
    
    assert winner_update.delta < 12  # less than equal-rating case
    assert loser_update.delta > -12


def test_compute_draw_exchange_equal_ratings():
    """Equal ratings draw, no change."""
    white_update, black_update = elo.compute_draw_exchange(1200, 1200)
    
    assert white_update.delta == 0
    assert black_update.delta == 0


def test_compute_draw_exchange_unequal_ratings():
    """Unequal ratings draw, lower-rated gains, higher-rated loses."""
    white_update, black_update = elo.compute_draw_exchange(1000, 1200)
    
    assert white_update.delta > 0  # underdog gains
    assert black_update.delta < 0  # favorite loses
    assert white_update.delta + black_update.delta == 0


# Constants test

def test_constants_exist():
    assert elo.STARTING_RATING == 1200
    assert elo.K_FACTOR == 24
