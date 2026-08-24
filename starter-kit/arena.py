"""Local chess arena for offline bot testing.

Runs round-robin tournaments between bots using chess_core for all game logic.
Provides diagnostics (time per move, flags, illegal moves) critical for debugging.
"""
import time
import chess
from typing import Callable, Optional, List
from dataclasses import dataclass
from chess_client import ClockView

# Import all chess_core functions we need
from chess_core import (
    validate_and_apply_move,
    detect_termination,
    STARTING_FEN,
    PLY_CAP,
    create_clock,
    deliver_position,
    account_move_and_switch,
    ms_to_ns,
    ns_to_ms,
    Color,
)


@dataclass
class GameResult:
    """Result of a single game."""
    white_name: str
    black_name: str
    result: str  # "white_win", "black_win", "draw"
    termination: str  # "checkmate", "stalemate", "flag", etc.
    moves_san: List[str]
    moves_uci: List[str]
    white_time_ms: int
    black_time_ms: int
    white_move_times: List[int]  # milliseconds per move
    black_move_times: List[int]
    white_flags: int  # 1 if white flagged, else 0
    black_flags: int
    white_illegal_attempts: int
    black_illegal_attempts: int
    ply_count: int


def run_single_game(
    white_bot: Callable,
    black_bot: Callable,
    white_name: str,
    black_name: str,
    time_control_ns: int,
    increment_ns: int,
    opening_fen: Optional[str] = None,
    verbose: bool = False
) -> GameResult:
    """Run a single game between two bots.
    
    Args:
        white_bot: Bot's choose_move function for white
        black_bot: Bot's choose_move function for black
        white_name: Name for white bot
        black_name: Name for black bot
        time_control_ns: Starting time in nanoseconds
        increment_ns: Increment per move in nanoseconds
        opening_fen: Starting position (None = standard starting position)
        verbose: Print move-by-move commentary
    
    Returns:
        GameResult with full game record and statistics
    """
    # Initialize position
    fen = opening_fen or STARTING_FEN
    board = chess.Board(fen)
    history_fens = [fen]
    moves_san = []
    moves_uci = []
    
    # Initialize clock
    now_ns = time.monotonic_ns()
    clock = create_clock(time_control_ns, increment_ns, Color.WHITE, now_ns)
    
    # Statistics
    white_move_times = []
    black_move_times = []
    white_illegal_attempts = 0
    black_illegal_attempts = 0
    white_flagged = False
    black_flagged = False
    
    ply = 0
    
    while ply < PLY_CAP:
        # Check for natural termination
        is_terminal, termination_reason, game_result = detect_termination(fen, history_fens)
        if is_terminal:
            # Natural end (checkmate, stalemate, etc.)
            result_str = {
                "white_win": "white_win",
                "black_win": "black_win",
                "draw": "draw"
            }.get(game_result.value if game_result else "draw", "draw")
            
            return GameResult(
                white_name=white_name,
                black_name=black_name,
                result=result_str,
                termination=termination_reason.value,
                moves_san=moves_san,
                moves_uci=moves_uci,
                white_time_ms=ns_to_ms(clock.white_ns),
                black_time_ms=ns_to_ms(clock.black_ns),
                white_move_times=white_move_times,
                black_move_times=black_move_times,
                white_flags=1 if white_flagged else 0,
                black_flags=1 if black_flagged else 0,
                white_illegal_attempts=white_illegal_attempts,
                black_illegal_attempts=black_illegal_attempts,
                ply_count=ply
            )
        
        # Select bot
        current_bot = white_bot if board.turn == chess.WHITE else black_bot
        
        # Deliver position (idempotent)
        now_ns = time.monotonic_ns()
        clock = deliver_position(clock, now_ns, ply)
        
        # Build ClockView for bot
        if board.turn == chess.WHITE:
            clock_view = ClockView(
                my_ms=ns_to_ms(clock.white_ns),
                opponent_ms=ns_to_ms(clock.black_ns),
                increment_ms=ns_to_ms(increment_ns),
                ply=ply
            )
        else:
            clock_view = ClockView(
                my_ms=ns_to_ms(clock.black_ns),
                opponent_ms=ns_to_ms(clock.white_ns),
                increment_ms=ns_to_ms(increment_ns),
                ply=ply
            )
        
        # Call bot and measure time
        start_ns = time.monotonic_ns()
        try:
            move = current_bot(board, clock_view)
        except Exception as e:
            # Bot crashed - forfeit
            if verbose:
                print(f"{white_name if board.turn == chess.WHITE else black_name} crashed: {e}")
            
            return GameResult(
                white_name=white_name,
                black_name=black_name,
                result="black_win" if board.turn == chess.WHITE else "white_win",
                termination="illegal_forfeit",
                moves_san=moves_san,
                moves_uci=moves_uci,
                white_time_ms=ns_to_ms(clock.white_ns),
                black_time_ms=ns_to_ms(clock.black_ns),
                white_move_times=white_move_times,
                black_move_times=black_move_times,
                white_flags=0,
                black_flags=0,
                white_illegal_attempts=white_illegal_attempts + (1 if board.turn == chess.WHITE else 0),
                black_illegal_attempts=black_illegal_attempts + (0 if board.turn == chess.WHITE else 1),
                ply_count=ply
            )
        
        end_ns = time.monotonic_ns()
        elapsed_ms = (end_ns - start_ns) // 1_000_000
        
        # Record move time
        if board.turn == chess.WHITE:
            white_move_times.append(elapsed_ms)
        else:
            black_move_times.append(elapsed_ms)
        
        # Account for time and check for flag
        clock_result = account_move_and_switch(clock, end_ns, end_ns)
        
        if clock_result.flagged:
            # Bot flagged
            if verbose:
                print(f"{white_name if board.turn == chess.WHITE else black_name} flagged!")
            
            if board.turn == chess.WHITE:
                white_flagged = True
            else:
                black_flagged = True
            
            return GameResult(
                white_name=white_name,
                black_name=black_name,
                result="black_win" if board.turn == chess.WHITE else "white_win",
                termination="flag",
                moves_san=moves_san,
                moves_uci=moves_uci,
                white_time_ms=ns_to_ms(clock_result.new_clock.white_ns),
                black_time_ms=ns_to_ms(clock_result.new_clock.black_ns),
                white_move_times=white_move_times,
                black_move_times=black_move_times,
                white_flags=1 if white_flagged else 0,
                black_flags=1 if black_flagged else 0,
                white_illegal_attempts=white_illegal_attempts,
                black_illegal_attempts=black_illegal_attempts,
                ply_count=ply
            )
        
        # Validate and apply move
        move_uci = move.uci()
        outcome = validate_and_apply_move(fen, move_uci)
        
        if not outcome.accepted:
            # Illegal move - increment counter
            if board.turn == chess.WHITE:
                white_illegal_attempts += 1
                if white_illegal_attempts >= 3:
                    # Three strikes - forfeit
                    return GameResult(
                        white_name=white_name,
                        black_name=black_name,
                        result="black_win",
                        termination="illegal_forfeit",
                        moves_san=moves_san,
                        moves_uci=moves_uci,
                        white_time_ms=ns_to_ms(clock_result.new_clock.white_ns),
                        black_time_ms=ns_to_ms(clock_result.new_clock.black_ns),
                        white_move_times=white_move_times,
                        black_move_times=black_move_times,
                        white_flags=0,
                        black_flags=0,
                        white_illegal_attempts=white_illegal_attempts,
                        black_illegal_attempts=black_illegal_attempts,
                        ply_count=ply
                    )
            else:
                black_illegal_attempts += 1
                if black_illegal_attempts >= 3:
                    return GameResult(
                        white_name=white_name,
                        black_name=black_name,
                        result="white_win",
                        termination="illegal_forfeit",
                        moves_san=moves_san,
                        moves_uci=moves_uci,
                        white_time_ms=ns_to_ms(clock_result.new_clock.white_ns),
                        black_time_ms=ns_to_ms(clock_result.new_clock.black_ns),
                        white_move_times=white_move_times,
                        black_move_times=black_move_times,
                        white_flags=0,
                        black_flags=0,
                        white_illegal_attempts=white_illegal_attempts,
                        black_illegal_attempts=black_illegal_attempts,
                        ply_count=ply
                    )
            
            # Log but continue (not three strikes yet)
            if verbose:
                print(f"Illegal move attempt: {move_uci}")
            continue  # Don't increment ply
        
        # Move accepted - update state
        move_result = outcome.move_result
        fen = move_result.fen_after
        board = chess.Board(fen)
        history_fens.append(fen)
        moves_san.append(move_result.san)
        moves_uci.append(move_uci)
        clock = clock_result.new_clock
        ply += 1
        
        if verbose:
            print(f"{ply}. {move_result.san}")
    
    # Hit ply cap - adjudicated draw
    return GameResult(
        white_name=white_name,
        black_name=black_name,
        result="draw",
        termination="adjudicated",
        moves_san=moves_san,
        moves_uci=moves_uci,
        white_time_ms=ns_to_ms(clock.white_ns),
        black_time_ms=ns_to_ms(clock.black_ns),
        white_move_times=white_move_times,
        black_move_times=black_move_times,
        white_flags=0,
        black_flags=0,
        white_illegal_attempts=white_illegal_attempts,
        black_illegal_attempts=black_illegal_attempts,
        ply_count=ply
    )
