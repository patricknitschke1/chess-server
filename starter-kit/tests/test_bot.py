import pytest
import chess
import random
import time
from pathlib import Path
from chess_client import ClockView

STARTER_KIT = Path(__file__).resolve().parent.parent


def load_bot(relative_path: str):
    """Load a bot shipped in the starter kit, regardless of the working directory."""
    from arena import load_bot_module

    return load_bot_module(str(STARTER_KIT / relative_path))


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


def test_baseline_bot_completes_full_games_without_flagging():
    """The shipped baseline plays complete games at 3+2 without running out of time.

    This is the acceptance gate: if the baseline flags, it is not safe to hand to
    attendees, because flagging is how most first bots lose.
    """
    from arena import run_single_game
    from opening_book import select_opening
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    baseline = load_bot("bot.py")
    ref_random = load_bot("ref_bots/ref_random.py")

    rng = random.Random(20260824)
    random.seed(20260824)

    for game_num in range(4):
        baseline_is_white = game_num % 2 == 0
        result = run_single_game(
            white_bot=baseline.choose_move if baseline_is_white else ref_random.choose_move,
            black_bot=ref_random.choose_move if baseline_is_white else baseline.choose_move,
            white_name="baseline" if baseline_is_white else "ref_random",
            black_name="ref_random" if baseline_is_white else "baseline",
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            opening_fen=select_opening(rng),
            verbose=False,
        )

        baseline_flags = result.white_flags if baseline_is_white else result.black_flags
        seat = "White" if baseline_is_white else "Black"
        assert baseline_flags == 0, f"Game {game_num + 1}: baseline flagged as {seat}"


def test_baseline_bot_makes_only_legal_moves():
    """The baseline never has a move rejected, in either seat."""
    from arena import run_single_game
    from opening_book import select_opening
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    baseline = load_bot("bot.py")
    ref_random = load_bot("ref_bots/ref_random.py")

    rng = random.Random(99)
    random.seed(99)

    for baseline_is_white in (True, False):
        result = run_single_game(
            white_bot=baseline.choose_move if baseline_is_white else ref_random.choose_move,
            black_bot=ref_random.choose_move if baseline_is_white else baseline.choose_move,
            white_name="baseline" if baseline_is_white else "ref_random",
            black_name="ref_random" if baseline_is_white else "baseline",
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            opening_fen=select_opening(rng),
            verbose=False,
        )

        illegal = (
            result.white_illegal_attempts if baseline_is_white
            else result.black_illegal_attempts
        )
        assert illegal == 0, "baseline attempted an illegal move"
        assert result.termination != "crash", "baseline crashed"


def test_baseline_bot_stops_searching_once_its_budget_is_spent():
    """A deeper search must cost move quality, not the game.

    The budget used to be computed and then ignored — depth was hard-coded, so
    an attendee who raised it found the "time management" did nothing and
    flagged, which is the most common way a first bot loses. At depth 5 from the
    opening the unbounded search takes about 1.5s; with 2s on the clock the
    budget is 50ms and the bot must come back near that, not near 1.5s.
    """
    baseline = load_bot("bot.py")
    baseline.SEARCH_DEPTH = 5

    board = chess.Board()
    clock = ClockView(my_ms=2000, opponent_ms=180000, increment_ms=0, ply=20)

    start = time.monotonic()
    move = baseline.choose_move(board, clock)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert move in board.legal_moves
    assert elapsed_ms < 400, (
        f"depth-5 search took {elapsed_ms:.0f}ms against a 50ms budget; "
        "the budget is not bounding the search"
    )


def test_baseline_bot_plays_mate_in_one():
    """The baseline must take checkmate when it is one move away.

    Scoring terminal nodes from `board.turn` instead of a fixed perspective makes
    delivering mate come back as -20000, the worst score available, so the bot
    avoids winning. That reads as "it just shuffles" and is very hard to spot.
    """
    baseline = load_bot("bot.py")

    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 1")
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=10)

    move = baseline.choose_move(board, clock)

    board.push(move)
    assert board.is_checkmate(), f"baseline played {move} instead of the mate on f7"
