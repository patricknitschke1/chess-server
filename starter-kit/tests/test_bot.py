import pytest
import chess
import time
from chess_client import ClockView


def test_clock_view_construction():
    """ClockView holds time info without color indexing."""
    clock = ClockView(my_ms=120000, opponent_ms=150000, increment_ms=2000, ply=5)
    assert clock.my_ms == 120000
    assert clock.opponent_ms == 150000
    assert clock.increment_ms == 2000
    assert clock.ply == 5


def test_clock_view_immutable():
    """ClockView is frozen (immutable)."""
    clock = ClockView(my_ms=120000, opponent_ms=150000, increment_ms=2000, ply=5)
    with pytest.raises(AttributeError):
        clock.my_ms = 100000


def test_baseline_bot_returns_legal_move():
    """Baseline bot returns a legal move."""
    from bot import choose_move
    
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    move = choose_move(board, clock)
    assert move in board.legal_moves


def test_baseline_bot_respects_time_budget():
    """Baseline bot should complete move within reasonable time."""
    from bot import choose_move
    
    board = chess.Board()
    clock = ClockView(my_ms=5000, opponent_ms=180000, increment_ms=2000, ply=10)
    
    start = time.monotonic()
    move = choose_move(board, clock)
    elapsed_ms = (time.monotonic() - start) * 1000
    
    # With 5s remaining, budget is ~125ms (5000/40)
    # Should complete well within that
    assert elapsed_ms < 500, f"Move took {elapsed_ms}ms with 5s remaining"
    assert move in board.legal_moves


def test_baseline_bot_handles_low_time():
    """Baseline bot handles very low time gracefully."""
    from bot import choose_move
    
    board = chess.Board()
    clock = ClockView(my_ms=500, opponent_ms=180000, increment_ms=2000, ply=30)
    
    start = time.monotonic()
    move = choose_move(board, clock)
    elapsed_ms = (time.monotonic() - start) * 1000
    
    # With 500ms remaining, should complete very quickly
    assert elapsed_ms < 100, f"Move took {elapsed_ms}ms with 0.5s remaining"
    assert move in board.legal_moves
