"""Who gets a rating_history row, and for how much (role spec §6.6, design §10.2-10.3)."""
from chess_core import (
    Color,
    GameResult,
    RatingUpdate,
    compute_draw_exchange,
    compute_one_sided_exchange,
    compute_rating_exchange,
)

from chess_server.store.cas import InvariantViolation
from chess_server.store.rows import BotRow


def _competitor_score(result: GameResult, competitor_color: Color) -> float:
    if result == GameResult.DRAW:
        return 0.5
    winner = Color.WHITE if result == GameResult.WHITE_WIN else Color.BLACK
    return 1.0 if winner == competitor_color else 0.0


def derive_rating_updates(
    white: BotRow, black: BotRow, result: GameResult
) -> list[tuple[BotRow, RatingUpdate]]:
    """Zero, one or two rows. An anchor's rating never moves, so it never gets one."""
    if white.is_anchor and black.is_anchor:
        raise InvariantViolation(
            f"game between two anchors: {white.name} vs {black.name}"
        )

    if not white.is_anchor and not black.is_anchor:
        if result == GameResult.DRAW:
            white_update, black_update = compute_draw_exchange(white.rating, black.rating)
            return [(white, white_update), (black, black_update)]
        winner, loser = (
            (white, black) if result == GameResult.WHITE_WIN else (black, white)
        )
        winner_update, loser_update = compute_rating_exchange(winner.rating, loser.rating)
        return [(winner, winner_update), (loser, loser_update)]

    competitor, anchor = (black, white) if white.is_anchor else (white, black)
    color = Color.BLACK if white.is_anchor else Color.WHITE
    # Deliberately not branching around draws: compute_one_sided_exchange raising
    # on any score but 1.0/0.5/0.0 is the guard that keeps this honest.
    return [(
        competitor,
        compute_one_sided_exchange(
            competitor.rating, anchor.rating, _competitor_score(result, color)
        ),
    )]
