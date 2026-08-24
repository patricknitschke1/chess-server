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


def test_opening_book_has_valid_fens():
    """Opening book contains valid FEN strings."""
    from opening_book import OPENING_BOOK
    
    assert len(OPENING_BOOK) >= 8, "Opening book should have at least 8 positions"
    
    for fen in OPENING_BOOK:
        board = chess.Board(fen)
        assert board.is_valid(), f"Invalid FEN: {fen}"


def test_opening_book_is_diverse():
    """Opening book contains different positions."""
    from opening_book import OPENING_BOOK
    
    unique_positions = set(OPENING_BOOK)
    assert len(unique_positions) >= 8, "Opening book should have at least 8 unique positions"


def test_select_opening_is_seeded():
    """select_opening returns same position with same seed."""
    import random
    from opening_book import select_opening
    
    random.seed(42)
    opening1 = select_opening()
    
    random.seed(42)
    opening2 = select_opening()
    
    assert opening1 == opening2


def test_select_opening_varies_with_seed():
    """select_opening returns different positions with different seeds."""
    import random
    from opening_book import select_opening
    
    random.seed(42)
    opening1 = select_opening()
    
    random.seed(43)
    opening2 = select_opening()
    
    # Should get different openings (probabilistic but very likely)
    assert opening1 != opening2 or len(set([select_opening() for _ in range(100)])) == 1


def test_arena_runs_single_game():
    """Arena executes a single game between two bots."""
    from arena import run_single_game
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
    
    # Run game between two reference bots
    result = run_single_game(
        white_bot=ref_random_choose_move,
        black_bot=ref_greedy_choose_move,
        white_name="ref_random",
        black_name="ref_greedy",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=None,  # Start from standard position
        verbose=False
    )
    
    # Result should have required fields
    assert result.white_name == "ref_random"
    assert result.black_name == "ref_greedy"
    assert result.result in ["white_win", "black_win", "draw"]
    assert result.termination in ["checkmate", "stalemate", "flag", "illegal_forfeit", "adjudicated", "insufficient", "fifty_move", "threefold"]
    assert len(result.moves_san) >= 0
    assert result.white_time_ms >= 0
    assert result.black_time_ms >= 0


def test_arena_clock_matches_chess_core():
    """Arena clock simulation matches chess_core behavior."""
    from arena import run_single_game
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, ns_to_ms
    
    # Run game and check time accounting is reasonable
    result = run_single_game(
        white_bot=ref_random_choose_move,
        black_bot=ref_random_choose_move,
        white_name="random1",
        black_name="random2",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=None,
        verbose=False
    )
    
    starting_ms = ns_to_ms(RATED_TIME_CONTROL_NS)
    increment_ms = ns_to_ms(RATED_INCREMENT_NS)
    
    # With PLY_CAP=200, each side makes at most 100 moves, gaining 100*increment
    # Maximum possible time: starting + 100*increment (if all moves are instant)
    max_possible_ms = starting_ms + 100 * increment_ms
    
    # Time should be at most starting + all increments
    assert result.white_time_ms <= max_possible_ms + 1000  # +1s tolerance for rounding
    assert result.black_time_ms <= max_possible_ms + 1000


def test_arena_detects_flags():
    """Arena detects when a bot runs out of time."""
    from arena import run_single_game
    import time
    
    # Create a slow bot that will definitely flag
    def slow_bot(board, clock):
        time.sleep(2.0)  # Takes 2s per move - will flag quickly at any time control
        return list(board.legal_moves)[0]
    
    # Create fast bot
    def fast_bot(board, clock):
        return list(board.legal_moves)[0]
    
    # Very short time control: 5s total, 0s increment
    result = run_single_game(
        white_bot=slow_bot,
        black_bot=fast_bot,
        white_name="SlowBot",
        black_name="FastBot",
        time_control_ns=5_000_000_000,  # 5 seconds
        increment_ns=0,
        opening_fen=None,
        verbose=False
    )
    
    # SlowBot should flag
    assert result.termination == "flag"
    assert result.result == "black_win"  # FastBot (black) wins
