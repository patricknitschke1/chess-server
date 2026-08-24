"""Random move selector — baseline opponent, rating ~800."""
import random
import chess
from chess_client import ClockView


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose a random legal move.
    
    Provisional rating: 800 — a placeholder, NOT a measurement. Calibration is deferred (design §21).
    
    Args:
        board: Current chess position
        clock: Time control information
        
    Returns:
        A randomly selected legal move
    """
    return random.choice(list(board.legal_moves))
