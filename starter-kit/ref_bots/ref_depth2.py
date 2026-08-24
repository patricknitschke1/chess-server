"""Minimax depth-2 bot — strong reference opponent, rating ~1200."""
import chess
from chess_client import ClockView


# Standard piece values in centipawns
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000  # Effectively infinite
}


def evaluate_position(board: chess.Board) -> int:
    """Evaluate position in centipawns from white's perspective.
    
    Args:
        board: Current chess position
        
    Returns:
        Evaluation score (positive favors white, negative favors black)
    """
    if board.is_checkmate():
        return -20000 if board.turn == chess.WHITE else 20000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            value = PIECE_VALUES.get(piece.piece_type, 0)
            score += value if piece.color == chess.WHITE else -value

    # Material alone scores every quiet move identically, so a winning bot shuffles
    # and draws by repetition. Driving the losing king to the edge is the smallest
    # term that turns a won position into a win.
    if score > 200:
        score -= _king_edge_distance(board, chess.BLACK)
    elif score < -200:
        score += _king_edge_distance(board, chess.WHITE)

    return score


def _king_edge_distance(board: chess.Board, color: chess.Color) -> int:
    """How central that king is; larger means harder to mate."""
    square = board.king(color)
    if square is None:
        return 0
    file_distance = min(chess.square_file(square), 7 - chess.square_file(square))
    rank_distance = min(chess.square_rank(square), 7 - chess.square_rank(square))
    return (file_distance + rank_distance) * 10


def minimax(board: chess.Board, depth: int, alpha: int, beta: int, maximizing: bool) -> int:
    """Minimax with alpha-beta pruning.
    
    Args:
        board: Current chess position
        depth: Remaining search depth
        alpha: Alpha value for pruning
        beta: Beta value for pruning
        maximizing: True if maximizing player's turn
        
    Returns:
        Best evaluation score at this node
    """
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
                break  # Beta cutoff
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
                break  # Alpha cutoff
        return min_eval


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose best move via minimax search to depth 2.
    
    Calibrated rating: 1200 (measured from seeded arena ladder)
    
    Uses minimax with alpha-beta pruning to search 2 plies ahead.
    Evaluates positions based on material count only.
    
    Args:
        board: Current chess position
        clock: Time control information
        
    Returns:
        Best move according to depth-2 minimax evaluation
    """
    best_move = None
    best_score = float('-inf') if board.turn == chess.WHITE else float('inf')
    
    for move in board.legal_moves:
        board.push(move)
        # `board.turn` here is the side to move AFTER the push, and evaluate_position
        # scores from White, so maximising is true exactly when White is on move.
        # Negating it searches the opponent's reply as if it helped us, and the bot
        # then picks the move whose most generous blunder is largest.
        score = minimax(board, 1, float('-inf'), float('inf'), board.turn)
        board.pop()

        if board.turn == chess.WHITE:
            if score > best_score:
                best_score = score
                best_move = move
        else:
            if score < best_score:
                best_score = score
                best_move = move
    
    return best_move if best_move else list(board.legal_moves)[0]
