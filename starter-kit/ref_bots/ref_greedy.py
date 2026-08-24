"""Greedy material maximizer — intermediate opponent, rating ~1000."""
import chess
from chess_client import ClockView


# Standard piece values in centipawns
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0
}


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose move that maximizes immediate material gain.
    
    Calibrated rating: 1000 (measured from seeded arena ladder)
    
    Prefers captures based on piece value, with a slight bonus for
    center control. Does not look ahead - purely greedy one-ply evaluation.
    
    Args:
        board: Current chess position
        clock: Time control information
        
    Returns:
        Move that maximizes immediate material gain
    """
    best_move = None
    best_score = float('-inf')
    
    for move in board.legal_moves:
        score = 0
        
        # Prefer captures
        if board.is_capture(move):
            captured_piece = board.piece_at(move.to_square)
            if captured_piece:
                score += PIECE_VALUES.get(captured_piece.piece_type, 0)
        
        # Slight bonus for center control
        if move.to_square in [chess.E4, chess.D4, chess.E5, chess.D5]:
            score += 10
        
        if score > best_score:
            best_score = score
            best_move = move
    
    # If no move scored positive, pick first legal move
    return best_move if best_move else list(board.legal_moves)[0]
