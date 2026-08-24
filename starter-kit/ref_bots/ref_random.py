"""Random move selector — baseline opponent, rating ~800."""
import random
import chess
from chess_client import ClockView


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose a random legal move.
    
    Calibrated rating: 800 (measured from seeded arena ladder)
    
    Args:
        board: Current chess position
        clock: Time control information
        
    Returns:
        A randomly selected legal move
    """
    return random.choice(list(board.legal_moves))
