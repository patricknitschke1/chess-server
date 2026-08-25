"""The three reference opponents, and their seeding (role spec §7.3, design §9.3).

The single exception to "no untrusted code runs on the server", because we wrote
it. Ported from `chess-bot-starter-kit/ref_bots/`, never imported from it: the starter kit
is not an installed package and the server must not depend on attendee-facing code.

**Every rating below is a provisional placeholder, not a measurement.**
Calibration is deferred (design §21) and nothing here depends on them being right.
"""
import hashlib
import random
import secrets
from typing import Optional, Protocol

import chess

from chess_core import ClockView

from chess_server.store.repositories import BotRepo
from chess_server.store.txn import Txn, critical_section
from chess_server.engine.wall import utc_now_iso

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
}

MATE_SCORE = 20_000
CENTRAL_SQUARES = (chess.E4, chess.D4, chess.E5, chess.D5)


class ReferenceBot(Protocol):
    def choose_move(self, board: chess.Board, clock: ClockView) -> chess.Move: ...


class RefRandomBot:
    """Uniform over legal moves. Provisional rating 800 — a placeholder, not a
    measurement (design §21)."""

    def __init__(self, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()

    def choose_move(self, board: chess.Board, clock: ClockView) -> chess.Move:
        return self.rng.choice(list(board.legal_moves))


class RefGreedyBot:
    """Immediate material, plus two tie-breaks. Provisional rating 1000 — a
    placeholder, not a measurement (design §21)."""

    def choose_move(self, board: chess.Board, clock: ClockView) -> chess.Move:
        best_move = None
        best_score = float("-inf")
        for move in board.legal_moves:
            board.push(move)
            delivers_mate = board.is_checkmate()
            gives_check = board.is_check()
            board.pop()
            if delivers_mate:
                return move

            # Material alone scores every quiet move identically, so a winning bot
            # shuffles and draws. These two terms exist only to make progress.
            score = 30 if gives_check else 0
            score -= chess.square_distance(move.to_square, board.king(not board.turn))
            if board.is_capture(move):
                captured = board.piece_at(move.to_square)
                if captured:
                    score += PIECE_VALUES.get(captured.piece_type, 0)
            if move.to_square in CENTRAL_SQUARES:
                score += 10

            if score > best_score:
                best_score = score
                best_move = move
        return best_move if best_move else list(board.legal_moves)[0]


def evaluate_position(board: chess.Board) -> int:
    """Centipawns, always from White's perspective — the fixed sign convention.

    Keying the perspective on `board.turn` is wrong at terminal nodes, which
    return before the side flips.
    """
    if board.is_checkmate():
        return -MATE_SCORE if board.turn == chess.WHITE else MATE_SCORE
    if board.is_stalemate() or board.is_insufficient_material():
        return 0

    score = 0
    for square, piece in board.piece_map().items():
        value = PIECE_VALUES.get(piece.piece_type, 0)
        score += value if piece.color == chess.WHITE else -value

    # Smallest term that turns a won position into a win rather than a shuffle.
    if score > 200:
        score -= _king_edge_distance(board, chess.BLACK)
    elif score < -200:
        score += _king_edge_distance(board, chess.WHITE)
    return score


def _king_edge_distance(board: chess.Board, color: chess.Color) -> int:
    square = board.king(color)
    if square is None:
        return 0
    file_distance = min(chess.square_file(square), 7 - chess.square_file(square))
    rank_distance = min(chess.square_rank(square), 7 - chess.square_rank(square))
    return (file_distance + rank_distance) * 10


def minimax(board: chess.Board, depth: int, alpha: float, beta: float, maximizing: bool) -> float:
    if depth == 0 or board.is_game_over():
        return evaluate_position(board)
    if maximizing:
        best = float("-inf")
        for move in board.legal_moves:
            board.push(move)
            best = max(best, minimax(board, depth - 1, alpha, beta, False))
            board.pop()
            alpha = max(alpha, best)
            if beta <= alpha:
                break
        return best
    best = float("inf")
    for move in board.legal_moves:
        board.push(move)
        best = min(best, minimax(board, depth - 1, alpha, beta, True))
        board.pop()
        beta = min(beta, best)
        if beta <= alpha:
            break
    return best


class Refdepth3Bot:
    """Depth-3 alpha-beta over a fixed White-perspective evaluation. Provisional
    rating 1200 — a placeholder, not a measurement (design §21)."""

    def choose_move(self, board: chess.Board, clock: ClockView) -> chess.Move:
        maximiser = board.turn == chess.WHITE
        best_move = None
        best_score = float("-inf") if maximiser else float("inf")
        for move in board.legal_moves:
            board.push(move)
            # board.turn is now the *opponent*; maximising is true exactly when
            # White is on move, because the evaluation never flips sign.
            score = minimax(board, 2, float("-inf"), float("inf"), board.turn == chess.WHITE)
            board.pop()
            if (score > best_score) if maximiser else (score < best_score):
                best_score = score
                best_move = move
        return best_move if best_move else list(board.legal_moves)[0]


ANCHORS: tuple[tuple[str, ReferenceBot, int], ...] = (
    ("ref-random", RefRandomBot(), 800),
    ("ref-greedy", RefGreedyBot(), 1000),
    ("ref-depth3", Refdepth3Bot(), 1200),
)

_BY_NAME = {name: bot for name, bot, _ in ANCHORS}

# Cosmetic only — shown on the dashboard in place of the internal identifier,
# which stays "ref-random" etc. everywhere else (matchmaking, tests, docs).
ANCHOR_DISPLAY_NAMES = {
    "ref-random": "Fool's Gambit",
    "ref-greedy": "King's Gambit",
    "ref-depth3": "Queen's Gambit",
}


def bot_for(name: str) -> Optional[ReferenceBot]:
    """The ticker's only route from a `bots` row to the code that plays it."""
    return _BY_NAME.get(name)


def display_name(name: str) -> str:
    """The name shown on the dashboard: an anchor's alias, or a bot's own name."""
    return ANCHOR_DISPLAY_NAMES.get(name, name)


def _discarded_token_hash() -> str:
    """token_hash is NOT NULL, so an anchor needs one — and no token may ever
    authenticate as an anchor, so the plaintext dies inside this function."""
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


async def seed_anchors_locked(txn: Txn) -> None:
    bots = BotRepo(txn.conn, txn.executor)
    for name, _bot, rating in ANCHORS:
        if await bots.get_by_name(name) is not None:
            continue
        await bots.insert_bot(
            name=name,
            owner="server",
            token_hash=_discarded_token_hash(),
            role="anchor",
            rating=rating,
            is_anchor=1,
            created_at=utc_now_iso(),
        )


async def seed_anchors(conn, executor) -> None:
    """Called from the lifespan before recovery, so recovery's clear_monotonic_state
    covers the anchors too and recovery stays the last write before the socket opens."""
    async with critical_section(conn, executor) as txn:
        await seed_anchors_locked(txn)
