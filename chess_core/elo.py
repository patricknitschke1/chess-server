"""Elo rating calculations per §10.

K=24 flat for all bots. Exchange is zero-sum for competitor-vs-competitor games.
It is not swap-symmetric: the underdog gains more than the favourite.
"""
from chess_core.types import RatingUpdate


STARTING_RATING = 1200
K_FACTOR = 24


def compute_rating_exchange(
    winner_rating: int,
    loser_rating: int
) -> tuple[RatingUpdate, RatingUpdate]:
    """Compute two-sided Elo exchange for a decisive game, K=24 flat per §10.1.
    
    Exchange is zero-sum. It is not swap-symmetric: 1000 beating 1400 gains 22,
    while 1400 beating 1000 gains 2.
    
    Args:
        winner_rating: Winner's current rating
        loser_rating: Loser's current rating
    
    Returns:
        (winner_update, loser_update) where winner gains and loser loses
    """
    # Expected scores
    winner_expected = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
    loser_expected = 1 - winner_expected
    
    # Actual scores (1 for winner, 0 for loser)
    winner_actual = 1.0
    loser_actual = 0.0
    
    # Deltas - force exact zero-sum by negating
    winner_delta = round(K_FACTOR * (winner_actual - winner_expected))
    loser_delta = -winner_delta
    
    winner_update = RatingUpdate(
        rating_before=winner_rating,
        rating_after=winner_rating + winner_delta,
        delta=winner_delta
    )
    
    loser_update = RatingUpdate(
        rating_before=loser_rating,
        rating_after=loser_rating + loser_delta,
        delta=loser_delta
    )
    
    return winner_update, loser_update


def compute_draw_exchange(
    white_rating: int,
    black_rating: int
) -> tuple[RatingUpdate, RatingUpdate]:
    """Compute two-sided Elo exchange for a draw, K=24 flat per §10.1.
    
    Exchange is zero-sum. A draw moves points from the favourite to the underdog.
    
    Args:
        white_rating: White's current rating
        black_rating: Black's current rating
    
    Returns:
        (white_update, black_update) summing to zero delta
    """
    # Expected scores
    white_expected = 1 / (1 + 10 ** ((black_rating - white_rating) / 400))
    black_expected = 1 - white_expected
    
    # Actual scores (0.5 for each in a draw)
    white_actual = 0.5
    black_actual = 0.5
    
    # Deltas - force exact zero-sum by negating
    white_delta = round(K_FACTOR * (white_actual - white_expected))
    black_delta = -white_delta
    
    white_update = RatingUpdate(
        rating_before=white_rating,
        rating_after=white_rating + white_delta,
        delta=white_delta
    )
    
    black_update = RatingUpdate(
        rating_before=black_rating,
        rating_after=black_rating + black_delta,
        delta=black_delta
    )
    
    return white_update, black_update


def compute_one_sided_exchange(
    competitor_rating: int,
    anchor_rating: int,
    competitor_score: float
) -> RatingUpdate:
    """Compute one-sided Elo update against a fixed anchor per §10.3.
    
    Anchor rating never changes. Net injection into pool per game, but
    shrinks toward zero as competitor approaches anchor rating.
    
    Args:
        competitor_rating: Competitor's current rating
        anchor_rating: Fixed anchor rating
        competitor_score: 1.0 win, 0.5 draw, 0.0 loss — the S term of
            R' = R + K(S - E). Draws are rated: they are 8-12 of every 24
            anchor games, and making them free would make shuffling free.
    
    Returns:
        RatingUpdate for competitor only
    
    Raises:
        ValueError: if competitor_score is not 1.0, 0.5 or 0.0
    """
    if competitor_score not in (1.0, 0.5, 0.0):
        raise ValueError(
            f"competitor_score must be 1.0 (win), 0.5 (draw) or 0.0 (loss), "
            f"got {competitor_score!r}."
        )
    
    expected = 1 / (1 + 10 ** ((anchor_rating - competitor_rating) / 400))
    delta = round(K_FACTOR * (competitor_score - expected))
    
    return RatingUpdate(
        rating_before=competitor_rating,
        rating_after=competitor_rating + delta,
        delta=delta
    )
