"""Baseline chess bot - starting point for workshop attendees.

This bot uses material-counting minimax to depth 2 with time management.
It beats ref_random reliably and loses to ref_greedy reliably.
Most importantly: it does NOT flag at 3+2 time control.
"""
import chess
from chess_client import ClockView


# Standard piece values in centipawns
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000
}


def evaluate_position(board: chess.Board) -> int:
    """Simple material count from current player's perspective."""
    if board.is_checkmate():
        return -20000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            value = PIECE_VALUES.get(piece.piece_type, 0)
            if piece.color == board.turn:
                score += value
            else:
                score -= value
    
    return score


def minimax(board: chess.Board, depth: int, alpha: int, beta: int, maximizing: bool) -> int:
    """Minimax with alpha-beta pruning."""
    if depth == 0 or board.is_game_over():
        return evaluate_position(board)
    
    if maximizing:
        max_eval = float('-inf')
        for move in board.legal_moves:
            board.push(move)
            eval_score = minimax(board, depth - 1, alpha, beta, False)
            board.pop()
            max_eval = max(max_eval, eval_score)
            alpha = max(alpha, eval_score)
            if beta <= alpha:
                break
        return max_eval
    else:
        min_eval = float('inf')
        for move in board.legal_moves:
            board.push(move)
            eval_score = minimax(board, depth - 1, alpha, beta, True)
            board.pop()
            min_eval = min(min_eval, eval_score)
            beta = min(beta, eval_score)
            if beta <= alpha:
                break
        return min_eval


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose a move for your bot.
    
    This is the only function you need to implement. It is called whenever
    it's your turn to move.
    
    The chess.Board object gives you the current position and all legal moves.
    The ClockView gives you time information without needing to know which
    color you are.
    
    Args:
        board: chess.Board with the current position (use board.turn for your
               color, board.legal_moves for available moves)
        clock: ClockView with my_ms (your remaining time), opponent_ms,
               increment_ms, and ply
    
    Returns:
        Your chosen move as a chess.Move object (must be in board.legal_moves)
    
    Time management strategy:
        Budget ~1/40th of remaining time per move (assumes ~40 moves left).
        This is safe at 3+2: 180s + 40*2s = 260s total budget / 40 = 6.5s/move.
        Even with no increment, 180s / 40 = 4.5s/move leaves margin.
    """
    # Time budget: assume 40 moves remaining
    time_budget_ms = clock.my_ms / 40
    
    # For very low time, reduce depth or pick quickly
    if time_budget_ms < 100:
        # Just pick first legal move when < 100ms budget
        return list(board.legal_moves)[0]
    
    # Search depth 2 (our move + opponent's response)
    best_move = None
    best_score = float('-inf')
    
    for move in board.legal_moves:
        board.push(move)
        # Maximize our position after opponent's best response
        score = minimax(board, 1, float('-inf'), float('inf'), False)
        board.pop()
        
        if score > best_score:
            best_score = score
            best_move = move
    
    return best_move if best_move else list(board.legal_moves)[0]
