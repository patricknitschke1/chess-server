"""Arena tests — placeholder for next task."""
import pytest
import chess
from chess_client import ClockView
from ref_bots.ref_random import choose_move as ref_random_choose_move
from ref_bots.ref_greedy import choose_move as ref_greedy_choose_move


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
