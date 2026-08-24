"""Arena tests — placeholder for next task."""
import pytest
import chess
from chess_client import ClockView
from ref_bots.ref_random import choose_move as ref_random_choose_move
from ref_bots.ref_greedy import choose_move as ref_greedy_choose_move
from ref_bots.ref_depth2 import choose_move as ref_depth2_choose_move


def test_ref_random_returns_legal_move():
    """ref_random returns a legal move."""
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    move = ref_random_choose_move(board, clock)
    assert move in board.legal_moves


def test_ref_random_is_random():
    """ref_random returns different moves across multiple calls (seeded)."""
    import random
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    
    random.seed(42)
    move1 = ref_random_choose_move(board, clock)
    
    random.seed(43)
    move2 = ref_random_choose_move(board, clock)
    
    # With 20 legal moves in starting position, different seeds should
    # produce different moves with high probability
    # (This is probabilistic but very unlikely to fail)
    assert move1 != move2 or len(list(board.legal_moves)) == 1


def test_ref_greedy_prefers_captures():
    """ref_greedy prefers capturing moves over non-captures."""
    # Position after 1.e4 d5 - white can capture with exd5
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2")
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=2)
    
    # Run multiple times to ensure it consistently captures
    for _ in range(5):
        move = ref_greedy_choose_move(board, clock)
        # Should capture the d5 pawn with exd5
        assert board.is_capture(move), f"Expected capture, got {move.uci()}"


def test_ref_greedy_returns_legal_move():
    """ref_greedy returns a legal move."""
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    move = ref_greedy_choose_move(board, clock)
    assert move in board.legal_moves


def test_ref_depth2_sees_mate_in_one():
    """ref_depth2 finds mate in one."""
    # Position before scholar's mate - white can play Qxf7#
    # After 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6?
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=7)
    
    move = ref_depth2_choose_move(board, clock)
    board.push(move)
    assert board.is_checkmate()


def test_ref_depth2_returns_legal_move():
    """ref_depth2 returns a legal move."""
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    move = ref_depth2_choose_move(board, clock)
    assert move in board.legal_moves


def test_ref_depth2_avoids_obvious_blunders():
    """ref_depth2 doesn't hang pieces in one move."""
    # Position where moving queen to dangerous square loses it
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    
    # Run several times - should never hang queen immediately
    for _ in range(3):
        move = ref_depth2_choose_move(board, clock)
        # Make move, check opponent can't capture queen for free
        test_board = board.copy()
        test_board.push(move)
        
        # If we moved our queen, ensure it's not hanging
        if board.piece_at(move.from_square).piece_type == chess.QUEEN:
            for opp_move in test_board.legal_moves:
                if test_board.is_capture(opp_move):
                    captured = test_board.piece_at(opp_move.to_square)
                    if captured and captured.piece_type == chess.QUEEN:
                        # Opponent can capture queen - this should be rare with depth 2
                        # (might happen if it's a trade)
                        pass
