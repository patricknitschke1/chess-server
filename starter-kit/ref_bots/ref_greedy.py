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

    Prefers checkmate, then material, then makes progress. Does not look ahead
    beyond the move itself apart from spotting mate.

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

        board.push(move)
        delivers_mate = board.is_checkmate()
        repeats = board.is_repetition(2)
        gives_check = board.is_check()
        board.pop()

        if delivers_mate:
            return move

        # Material-only evaluation scores every quiet move identically, so the bot
        # shuffles one piece and draws by repetition from a winning position. These
        # three terms exist only to break that tie and make progress.
        if repeats:
            score -= 10_000
        if gives_check:
            score += 30
        score += chess.square_distance(move.to_square, board.king(not board.turn)) * -1

        if board.is_capture(move):
            captured_piece = board.piece_at(move.to_square)
            if captured_piece:
                score += PIECE_VALUES.get(captured_piece.piece_type, 0)

        if move.to_square in [chess.E4, chess.D4, chess.E5, chess.D5]:
            score += 10

        if score > best_score:
            best_score = score
            best_move = move

    return best_move if best_move else list(board.legal_moves)[0]
