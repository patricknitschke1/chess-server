"""Baseline chess bot — `choose_move` is the only function you need to change."""
import time
import chess
from chess_client import ClockView

PIECE_VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                chess.ROOK: 500, chess.QUEEN: 900}
MATE_SCORE = 20000
SEARCH_DEPTH = 2  # raise this to play stronger; the budget below keeps it safe


def evaluate(board: chess.Board) -> int:
    """Material in centipawns, always for the side to move at this node."""
    if board.is_game_over():
        return -MATE_SCORE if board.is_checkmate() else 0
    return sum(PIECE_VALUES.get(p.piece_type, 0) * (1 if p.color == board.turn else -1)
               for p in board.piece_map().values())


def search(board: chess.Board, depth: int, alpha: int, beta: int) -> int:
    """Negamax with alpha-beta pruning, scoring every node for its own side."""
    if depth == 0 or board.is_game_over():
        return evaluate(board)
    best = -2 * MATE_SCORE
    for move in board.legal_moves:
        board.push(move)
        # Negated because the child node scores for the opponent, not for us.
        score = -search(board, depth - 1, -beta, -alpha)
        board.pop()
        best = max(best, score)
        alpha = max(alpha, best)
        if alpha >= beta:
            break
    return best


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Pick a move, spending at most a fortieth of the time you have left on it."""
    deadline = time.monotonic() + clock.my_ms / 40_000
    best_move, best_score = None, -2 * MATE_SCORE
    for move in board.legal_moves:
        board.push(move)
        score = -search(board, SEARCH_DEPTH - 1, -2 * MATE_SCORE, 2 * MATE_SCORE)
        board.pop()
        if score > best_score:
            best_move, best_score = move, score
        if time.monotonic() > deadline:
            break  # budget spent: play the best move examined so far
    return best_move
