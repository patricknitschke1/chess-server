"""Baseline chess bot — `choose_move` is the only function you need to change."""
import time
import chess
import random
from chess_client import ClockView

def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """The move is there, but you must see it... Savielly Tartakower
    
    Args:
        board: Current chess position
        clock: Time control information
        
    Returns:
        Now? A randomly selected legal move
    """
    return random.choice(list(board.legal_moves))