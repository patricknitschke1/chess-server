"""Opening book for arena randomization.

Without randomized openings, two deterministic bots replay one identical game,
making "100 games" a statistical illusion. This book provides standard opening
positions, selected via a seeded RNG for reproducibility.

Every position has **White to move**. Colour balance comes from the arena
alternating who plays White, not from starting some games mid-move — so a bot
always begins a game the way it begins a real one.
"""
import random
from typing import Optional


# Standard openings, all after an even number of plies so White is to move.
OPENING_BOOK = [
    # Open games
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",       # e4 e5
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",    # e4 e5 Nf3 Nc6
    # Sicilian
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2",       # e4 c5
    "rnbqkbnr/pp2pppp/3p4/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3",     # e4 c5 Nf3 d6
    # French and Caro-Kann
    "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",        # e4 e6
    "rnbqkbnr/pp1ppppp/2p5/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",        # e4 c6
    # Queen's pawn
    "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq d6 0 2",       # d4 d5
    "rnbqkb1r/ppp1pppp/5n2/3p4/2PP4/8/PP2PPPP/RNBQKBNR w KQkq - 1 3",      # d4 d5 c4 Nf6
    "rnbqkb1r/pppppppp/5n2/8/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 1 2",        # d4 Nf6
    "rnbqkb1r/pppppp1p/5np1/8/2PP4/8/PP2PPPP/RNBQKBNR w KQkq - 0 3",       # d4 Nf6 c4 g6
    # English
    "rnbqkbnr/pppp1ppp/8/4p3/2P5/8/PP1PPPPP/RNBQKBNR w KQkq e6 0 2",       # c4 e5
]


def select_opening(rng: Optional[random.Random] = None) -> str:
    """Select an opening position from the book.

    Args:
        rng: Seeded generator for reproducible arena runs. Falls back to the
             global `random` module when omitted.

    Returns:
        FEN string of the selected opening, always with White to move.
    """
    return (rng or random).choice(OPENING_BOOK)
