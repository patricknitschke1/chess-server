import pytest
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
