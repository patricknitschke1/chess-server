"""Tests for chess rules, move validation, and termination detection."""
import chess_core.rules as rules
from chess_core.types import MoveOutcome, TerminationReason, GameResult


# Failure path tests first per AGENTS.md

def test_validate_illegal_move_rejected():
    """Illegal moves are rejected with actionable reason, not exception."""
    fen = rules.STARTING_FEN
    outcome = rules.validate_and_apply_move(fen, "e2e5")  # illegal for White
    assert outcome.accepted is False
    assert outcome.move_result is None
    assert "illegal" in outcome.rejection_reason.lower()


def test_validate_syntactically_invalid_uci_raises():
    """Malformed UCI raises ValueError, not MoveOutcome."""
    fen = rules.STARTING_FEN
    try:
        rules.validate_and_apply_move(fen, "notUCI")
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "invalid" in str(e).lower() or "uci" in str(e).lower()


def test_validate_malformed_fen_raises():
    """Malformed FEN raises ValueError."""
    try:
        rules.validate_and_apply_move("not a fen", "e2e4")
        assert False, "Should raise ValueError"
    except ValueError:
        pass


# Threefold via position key (failure case: full FEN comparison would miss this)

def test_position_key_omits_halfmove_clock():
    """Position key omits halfmove and fullmove, critical for threefold."""
    fen1 = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    fen2 = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 5 3"
    key1 = rules.position_key(fen1)
    key2 = rules.position_key(fen2)
    assert key1 == key2, "Position keys must be identical despite different halfmove clocks"


def test_threefold_detection_uses_position_key():
    """Threefold detected via position_key, not full FEN."""
    # Setup: replay same position 3 times with different halfmove clocks
    starting_fen = rules.STARTING_FEN
    
    # Move sequence that creates threefold: Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8 Nf3
    history_fens = [starting_fen]
    
    # First repetition cycle
    outcome = rules.validate_and_apply_move(history_fens[-1], "g1f3")
    history_fens.append(outcome.move_result.fen_after)
    outcome = rules.validate_and_apply_move(history_fens[-1], "g8f6")
    history_fens.append(outcome.move_result.fen_after)
    outcome = rules.validate_and_apply_move(history_fens[-1], "f3g1")
    history_fens.append(outcome.move_result.fen_after)
    outcome = rules.validate_and_apply_move(history_fens[-1], "f6g8")
    history_fens.append(outcome.move_result.fen_after)
    
    # Second repetition cycle
    outcome = rules.validate_and_apply_move(history_fens[-1], "g1f3")
    history_fens.append(outcome.move_result.fen_after)
    outcome = rules.validate_and_apply_move(history_fens[-1], "g8f6")
    history_fens.append(outcome.move_result.fen_after)
    outcome = rules.validate_and_apply_move(history_fens[-1], "f3g1")
    history_fens.append(outcome.move_result.fen_after)
    outcome = rules.validate_and_apply_move(history_fens[-1], "f6g8")
    history_fens.append(outcome.move_result.fen_after)
    
    # Third repetition should trigger threefold
    outcome = rules.validate_and_apply_move(history_fens[-1], "g1f3")
    current_fen = outcome.move_result.fen_after
    
    is_terminal, reason, result = rules.detect_termination(current_fen, history_fens + [current_fen])
    assert is_terminal is True
    assert reason == TerminationReason.THREEFOLD
    assert result == GameResult.DRAW


def test_insufficient_material_king_vs_king():
    """K vs K is insufficient material."""
    fen = "8/8/8/4k3/8/8/4K3/8 w - - 0 1"
    is_terminal, reason, result = rules.detect_termination(fen, [fen])
    assert is_terminal is True
    assert reason == TerminationReason.INSUFFICIENT
    assert result == GameResult.DRAW


def test_insufficient_material_king_bishop_vs_king():
    """K+B vs K is insufficient material."""
    fen = "8/8/8/4k3/8/8/4KB2/8 w - - 0 1"
    is_terminal, reason, result = rules.detect_termination(fen, [fen])
    assert is_terminal is True
    assert reason == TerminationReason.INSUFFICIENT


def test_insufficient_material_king_knight_vs_king():
    """K+N vs K is insufficient material."""
    fen = "8/8/8/4k3/8/8/4KN2/8 w - - 0 1"
    is_terminal, reason, result = rules.detect_termination(fen, [fen])
    assert is_terminal is True
    assert reason == TerminationReason.INSUFFICIENT


def test_fifty_move_claim_at_exactly_50():
    """Server claims fifty-move draw at exactly 50 full moves (100 halfmoves)."""
    # FEN with halfmove clock at 100 (50 full moves)
    fen = "8/8/8/4k3/8/8/4K3/8 w - - 100 100"
    is_terminal, reason, result = rules.detect_termination(fen, [fen])
    assert is_terminal is True
    assert reason == TerminationReason.FIFTY_MOVE
    assert result == GameResult.DRAW


# Happy path tests

def test_validate_legal_move_e2e4():
    """Legal move e2e4 from starting position."""
    fen = rules.STARTING_FEN
    outcome = rules.validate_and_apply_move(fen, "e2e4")
    assert outcome.accepted is True
    assert outcome.move_result.san == "e4"
    assert outcome.move_result.is_terminal is False
    assert "4P3" in outcome.move_result.fen_after  # pawn on e4


def test_validate_legal_move_castle():
    """Castling is legal when rights exist."""
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    outcome = rules.validate_and_apply_move(fen, "e1g1")
    assert outcome.accepted is True
    assert outcome.move_result.san == "O-O"


def test_detect_checkmate():
    """Fool's mate is detected as checkmate."""
    fen = rules.STARTING_FEN
    outcome = rules.validate_and_apply_move(fen, "f2f3")
    fen = outcome.move_result.fen_after
    outcome = rules.validate_and_apply_move(fen, "e7e5")
    fen = outcome.move_result.fen_after
    outcome = rules.validate_and_apply_move(fen, "g2g4")
    fen = outcome.move_result.fen_after
    outcome = rules.validate_and_apply_move(fen, "d8h4")  # checkmate
    
    assert outcome.move_result.is_terminal is True
    assert outcome.move_result.termination == TerminationReason.CHECKMATE
    assert outcome.move_result.result == GameResult.BLACK_WIN


def test_detect_stalemate():
    """Stalemate is detected correctly."""
    fen = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
    is_terminal, reason, result = rules.detect_termination(fen, [fen])
    assert is_terminal is True
    assert reason == TerminationReason.STALEMATE
    assert result == GameResult.DRAW


def test_get_legal_moves_sorted():
    """Legal moves returned in sorted UCI order."""
    fen = rules.STARTING_FEN
    moves = rules.get_legal_moves(fen)
    assert len(moves) == 20  # 16 pawn moves + 4 knight moves
    assert moves == sorted(moves)  # lexicographically sorted
    assert "e2e4" in moves


def test_uci_to_san_e2e4():
    """e2e4 converts to 'e4' in SAN."""
    fen = rules.STARTING_FEN
    san = rules.uci_to_san(fen, "e2e4")
    assert san == "e4"


def test_uci_to_san_knight_move():
    """g1f3 converts to 'Nf3' in SAN."""
    fen = rules.STARTING_FEN
    san = rules.uci_to_san(fen, "g1f3")
    assert san == "Nf3"


def test_uci_to_san_illegal_raises():
    """Illegal move raises ValueError in SAN conversion."""
    fen = rules.STARTING_FEN
    try:
        rules.uci_to_san(fen, "e2e5")
        assert False, "Should raise ValueError"
    except ValueError:
        pass


def test_fen_to_ascii_starting_position():
    """Starting position renders recognizable board."""
    fen = rules.STARTING_FEN
    ascii_board = rules.fen_to_ascii(fen)
    assert "♜" in ascii_board or "r" in ascii_board  # rook present
    assert "♔" in ascii_board or "K" in ascii_board  # white king
    assert "8" in ascii_board  # rank labels


def test_san_list_to_pgn():
    """PGN export includes headers and movetext."""
    san_moves = ["e4", "e5", "Nf3", "Nc6"]
    pgn = rules.san_list_to_pgn(
        san_moves=san_moves,
        white_name="AlphaBot",
        black_name="BetaBot",
        result=GameResult.WHITE_WIN,
        white_rating=1250,
        black_rating=1200
    )
    assert "[White \"AlphaBot\"]" in pgn
    assert "[Black \"BetaBot\"]" in pgn
    assert "[WhiteElo \"1250\"]" in pgn
    assert "1. e4 e5" in pgn
    assert "1-0" in pgn or "1‑0" in pgn  # result marker


def test_constants_exist():
    """Constants are defined."""
    assert rules.STARTING_FEN == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    assert rules.PLY_CAP == 200
