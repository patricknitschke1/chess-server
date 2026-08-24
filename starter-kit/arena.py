"""Local chess arena for offline bot testing.

Runs round-robin tournaments between bots using chess_core for all game logic.
Provides diagnostics (time per move, flags, illegal moves) critical for debugging.
"""
import argparse
import importlib.util
import logging
import random
import statistics
import sys
import time
from pathlib import Path
import chess
import chess.pgn
from typing import Callable, Optional, List, Dict, Tuple
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
    RATED_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    Color,
    TerminationReason,
    STARTING_RATING,
    compute_rating_exchange,
    compute_draw_exchange,
    fen_to_ascii,
    san_list_to_pgn,
    GameResult as CoreGameResult,
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
    # Openings are randomised (§17), so roughly half start with Black to move. The
    # clock must agree with the board or every move is charged to the wrong side.
    starting_side = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
    clock = create_clock(time_control_ns, increment_ns, starting_side, now_ns)
    
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
            # A raised exception is not an illegal move: it sends the attendee to a
            # traceback rather than to their move generation.
            if verbose:
                print(f"{white_name if board.turn == chess.WHITE else black_name} crashed: {e}")
            
            return GameResult(
                white_name=white_name,
                black_name=black_name,
                result="black_win" if board.turn == chess.WHITE else "white_win",
                termination=TerminationReason.CRASH.value,
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


def compute_statistics(values: List[int]) -> Dict[str, float]:
    """Compute mean, p95, min, max from a list of values."""
    if not values:
        return {'mean': 0.0, 'p95': 0, 'min': 0, 'max': 0}
    
    sorted_values = sorted(values)
    p95_index = int(len(sorted_values) * 0.95)
    
    return {
        'mean': statistics.mean(values),
        'p95': sorted_values[p95_index] if p95_index < len(sorted_values) else sorted_values[-1],
        'min': min(values),
        'max': max(values)
    }


class ArenaTracker:
    """Tracks ratings and statistics for local arena."""
    
    def __init__(self):
        self.ratings: Dict[str, int] = {}
        self.wins: Dict[str, int] = {}
        self.losses: Dict[str, int] = {}
        self.draws: Dict[str, int] = {}
        self.games_played: Dict[str, int] = {}
        self.move_times: Dict[str, List[int]] = {}
        self.flags: Dict[str, int] = {}
        self.illegal_attempts: Dict[str, int] = {}
    
    def register_bot(self, name: str):
        """Register a bot with starting rating."""
        if name not in self.ratings:
            self.ratings[name] = STARTING_RATING
            self.wins[name] = 0
            self.losses[name] = 0
            self.draws[name] = 0
            self.games_played[name] = 0
            self.move_times[name] = []
            self.flags[name] = 0
            self.illegal_attempts[name] = 0
    
    def get_rating(self, name: str) -> int:
        """Get current rating for a bot."""
        return self.ratings.get(name, STARTING_RATING)
    
    def record_game(self, white_name: str, black_name: str, result: str):
        """Record game result and update ratings."""
        white_rating = self.ratings[white_name]
        black_rating = self.ratings[black_name]
        
        if result == "draw":
            white_update, black_update = compute_draw_exchange(white_rating, black_rating)
            self.draws[white_name] += 1
            self.draws[black_name] += 1
        elif result == "white_win":
            white_update, black_update = compute_rating_exchange(white_rating, black_rating)
            self.wins[white_name] += 1
            self.losses[black_name] += 1
        else:  # black_win
            black_update, white_update = compute_rating_exchange(black_rating, white_rating)
            self.wins[black_name] += 1
            self.losses[white_name] += 1
        
        self.ratings[white_name] = white_update.rating_after
        self.ratings[black_name] = black_update.rating_after
        self.games_played[white_name] += 1
        self.games_played[black_name] += 1
    
    def record_move_times(self, bot_name: str, times: List[int]):
        """Record move times for a bot."""
        self.move_times[bot_name].extend(times)
    
    def record_flags(self, bot_name: str, count: int):
        """Record flag events."""
        self.flags[bot_name] += count
    
    def record_illegal_attempts(self, bot_name: str, count: int):
        """Record illegal move attempts."""
        self.illegal_attempts[bot_name] += count
    
    def get_stats(self, name: str) -> Dict:
        """Get full statistics for a bot."""
        move_stats = compute_statistics(self.move_times.get(name, []))
        
        return {
            'name': name,
            'rating': self.ratings.get(name, STARTING_RATING),
            'wins': self.wins.get(name, 0),
            'losses': self.losses.get(name, 0),
            'draws': self.draws.get(name, 0),
            'games_played': self.games_played.get(name, 0),
            'mean_move_ms': move_stats['mean'],
            'p95_move_ms': move_stats['p95'],
            'flags': self.flags.get(name, 0),
            'illegal_attempts': self.illegal_attempts.get(name, 0)
        }


_ARENA_RESULT_TO_CORE = {
    "white_win": CoreGameResult.WHITE_WIN,
    "black_win": CoreGameResult.BLACK_WIN,
    "draw": CoreGameResult.DRAW,
}


def export_to_pgn(
    results: List[GameResult],
    filepath: str,
    tracker: Optional['ArenaTracker'] = None
):
    """Write game results to a PGN file, one game after another.

    Ratings are included in the headers when a tracker is supplied.
    """
    with open(filepath, 'w') as f:
        for result in results:
            core_result = _ARENA_RESULT_TO_CORE.get(result.result)
            if core_result is None:
                raise ValueError(
                    f"Cannot export a game whose result is {result.result!r}. "
                    f"Expected one of: {', '.join(sorted(_ARENA_RESULT_TO_CORE))}."
                )

            white_rating = tracker.get_rating(result.white_name) if tracker else None
            black_rating = tracker.get_rating(result.black_name) if tracker else None

            f.write(san_list_to_pgn(
                result.moves_san,
                result.white_name,
                result.black_name,
                core_result,
                white_rating,
                black_rating,
            ))
            f.write("\n\n")


def replay_game(pgn_path: str, game_number: int) -> int:
    """Print the board after every move of the Nth (1-based) game in a PGN file.

    Returns the number of moves replayed.
    """
    path = Path(pgn_path)
    if not path.exists():
        raise ValueError(
            f"There is no PGN file at {pgn_path}. Record some games first with "
            f"--bots bot.py ref_bots/ref_greedy.py --pgn {pgn_path}"
        )

    # python-chess logs unreadable movetext at ERROR level; we report it as prose below.
    pgn_logger = logging.getLogger("chess.pgn")
    previous_level = pgn_logger.level
    pgn_logger.setLevel(logging.CRITICAL)
    try:
        with path.open() as handle:
            game = None
            found = 0
            while found < game_number:
                next_game = chess.pgn.read_game(handle)
                if next_game is None:
                    break
                found += 1
                game = next_game
    finally:
        pgn_logger.setLevel(previous_level)

    if game is None or found < game_number:
        if found == 0:
            raise ValueError(
                f"{pgn_path} contains no games. Record some first with "
                f"--bots bot.py ref_bots/ref_greedy.py --pgn {pgn_path}"
            )
        raise ValueError(
            f"{pgn_path} contains {found} game(s), so game {game_number} is not in it. "
            f"Pick a number from --replay 1 to --replay {found}."
        )

    if game.errors:
        raise ValueError(
            f"Game {game_number} in {pgn_path} could not be read back: {game.errors[0]}. "
            f"A game that started from an opening position needs a [FEN] header in its "
            f"PGN, which the exporter does not yet write."
        )

    board = game.board()
    print(
        f"Game {game_number}: {game.headers.get('White', '?')} (White) vs "
        f"{game.headers.get('Black', '?')} (Black)  {game.headers.get('Result', '*')}"
    )
    print()
    print("Starting position")
    print(fen_to_ascii(board.fen()))

    moves_replayed = 0
    for move in game.mainline_moves():
        separator = '.' if board.turn == chess.WHITE else '...'
        label = f"{board.fullmove_number}{separator} {board.san(move)}"
        board.push(move)
        moves_replayed += 1

        print()
        print(label)
        print(fen_to_ascii(board.fen()))

    print()
    print(f"{moves_replayed} moves replayed.")
    return moves_replayed


_load_counter = 0
def load_bot_module(path_str: str):
    """Load a bot module from a file path."""
    global _load_counter

    path = Path(path_str)
    if not path.exists():
        print(f"Error: Bot file not found: {path_str}")
        sys.exit(1)

    # Two bots may share a filename in different directories; keying sys.modules by
    # stem alone would silently hand back the first one for both.
    _load_counter += 1
    module_key = f"arena_bot_{_load_counter}_{path.stem}"

    spec = importlib.util.spec_from_file_location(module_key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)

    if not hasattr(module, 'choose_move'):
        print(f"Error: {path_str} must define choose_move(board, clock) function")
        sys.exit(1)

    return module


def build_schedule(bot_names: List[str], total_games: int) -> List[Tuple[str, str]]:
    """Build the (white, black) order for a round-robin of exactly total_games.

    Colours alternate within each pairing: every opening has White to move, so a
    fixed assignment would rate colour rather than skill. Games that do not divide
    evenly across pairings go to the earliest pairings, so the total is exact.
    """
    pairings = [
        (bot_names[i], bot_names[j])
        for i in range(len(bot_names))
        for j in range(i + 1, len(bot_names))
    ]
    if not pairings:
        return []

    base, remainder = divmod(total_games, len(pairings))

    schedule: List[Tuple[str, str]] = []
    for index, (first, second) in enumerate(pairings):
        count = base + (1 if index < remainder else 0)
        for game in range(count):
            schedule.append((first, second) if game % 2 == 0 else (second, first))
    return schedule


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog='arena.py',
        description="Local chess arena for offline bot testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run 100 games between two bots
  python arena.py --bots bot.py ref_bots/ref_greedy.py --games 100 --seed 7

  # Include reference bots
  python arena.py --bots bot.py ref_bots/ref_random.py ref_bots/ref_greedy.py --games 60

  # Custom time control
  python arena.py --bots bot.py ref_bots/ref_greedy.py --time-control 60000 --increment 1000

  # Export PGNs
  python arena.py --bots bot.py ref_bots/ref_greedy.py --pgn games.pgn

  # Replay a specific game
  python arena.py --replay 5 --pgn games.pgn
        """
    )

    parser.add_argument(
        '--bots',
        nargs='+',
        help='Bot module paths (e.g. bot.py ref_bots/ref_random.py)'
    )
    parser.add_argument(
        '--games',
        type=int,
        default=100,
        help='Total number of games to play across all pairings (default: 100)'
    )
    parser.add_argument(
        '--seed',
        type=int,
        default=None,
        help='Random seed for reproducibility (default: random)'
    )
    parser.add_argument(
        '--time-control',
        dest='time_control_ms',
        type=int,
        default=ns_to_ms(RATED_TIME_CONTROL_NS),
        help=f'Time control in milliseconds (default: {ns_to_ms(RATED_TIME_CONTROL_NS)})'
    )
    parser.add_argument(
        '--increment',
        dest='increment_ms',
        type=int,
        default=ns_to_ms(RATED_INCREMENT_NS),
        help=f'Increment in milliseconds (default: {ns_to_ms(RATED_INCREMENT_NS)})'
    )
    parser.add_argument(
        '--pgn',
        help='Export games to this PGN file'
    )
    parser.add_argument(
        '--replay',
        type=int,
        metavar='N',
        help='Replay game number N from the PGN file given by --pgn'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Print move-by-move output'
    )

    args = parser.parse_args(argv)

    if args.seed is None:
        args.seed = random.randint(0, 2**31 - 1)

    if args.replay is not None:
        if not args.pgn:
            parser.error(
                'Replaying a game needs the file to read it from. '
                'Add --pgn games.pgn to your command.'
            )
        if args.replay < 1:
            parser.error(
                f'Games are numbered from 1, so --replay {args.replay} cannot exist. '
                'Use --replay 1 for the first game in the file.'
            )
    elif not args.bots:
        parser.error(
            'No bots given. Add --bots bot.py ref_bots/ref_greedy.py to play some games, '
            'or --replay N --pgn games.pgn to watch one back.'
        )

    return args


def print_results(tracker: 'ArenaTracker', bot_names: List[str], seed: int, total_games: int):
    """Print the formatted results table."""
    print(f"\nLocal Arena Results ({total_games} games, seed={seed})")
    print("=" * 88)
    print()

    sorted_names = sorted(bot_names, key=lambda n: tracker.get_rating(n), reverse=True)

    print(
        f"{'Bot':<20} {'Rating':>6} {'W':>3} {'L':>3} {'D':>3} {'Games':>5} "
        f"{'Avg(ms)':>8} {'P95(ms)':>8} {'Flags':>5} {'Illegal':>7}"
    )
    print("-" * 88)

    for name in sorted_names:
        stats = tracker.get_stats(name)
        print(
            f"{name:<20} {stats['rating']:>6} "
            f"{stats['wins']:>3} {stats['losses']:>3} {stats['draws']:>3} "
            f"{stats['games_played']:>5} "
            f"{stats['mean_move_ms']:>8.0f} {stats['p95_move_ms']:>8.0f} "
            f"{stats['flags']:>5} {stats['illegal_attempts']:>7}"
        )

    print()
    flagged = [n for n in sorted_names if tracker.get_stats(n)['flags'] > 0]
    if flagged:
        for name in flagged:
            stats = tracker.get_stats(name)
            print(
                f"{name} ran out of time in {stats['flags']} game(s), "
                f"averaging {stats['mean_move_ms']:.0f}ms per move "
                f"({stats['p95_move_ms']:.0f}ms at p95). "
                f"Try reducing search depth in bot.py."
            )
        print()


def main(argv=None):
    """Main entry point for the arena."""
    args = parse_args(argv)

    if args.replay is not None:
        try:
            replay_game(args.pgn, args.replay)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        return

    if len(args.bots) < 2:
        print(
            "Error: an arena needs at least two bots to pair up. Try adding a "
            "reference opponent: --bots bot.py ref_bots/ref_greedy.py",
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"Loading {len(args.bots)} bots...")
    bot_moves: Dict[str, Callable] = {}
    for bot_path in args.bots:
        module = load_bot_module(bot_path)
        bot_name = Path(bot_path).stem
        bot_moves[bot_name] = module.choose_move
        print(f"  loaded {bot_name}")

    tracker = ArenaTracker()
    bot_names = list(bot_moves.keys())
    for name in bot_names:
        tracker.register_bot(name)

    # Openings take the explicit generator; reference bots reach for the global
    # `random` module, so both need seeding for a run to be reproducible.
    rng = random.Random(args.seed)
    random.seed(args.seed)

    from opening_book import select_opening

    schedule = build_schedule(bot_names, args.games)
    print(f"\nRunning {len(schedule)} games (seed={args.seed})...")

    all_results: List[GameResult] = []
    for game_number, (white_name, black_name) in enumerate(schedule, start=1):
        result = run_single_game(
            white_bot=bot_moves[white_name],
            black_bot=bot_moves[black_name],
            white_name=white_name,
            black_name=black_name,
            time_control_ns=ms_to_ns(args.time_control_ms),
            increment_ns=ms_to_ns(args.increment_ms),
            opening_fen=select_opening(rng),
            verbose=args.verbose,
        )
        all_results.append(result)

        tracker.record_game(white_name, black_name, result.result)
        tracker.record_move_times(white_name, result.white_move_times)
        tracker.record_move_times(black_name, result.black_move_times)
        tracker.record_flags(white_name, result.white_flags)
        tracker.record_flags(black_name, result.black_flags)
        tracker.record_illegal_attempts(white_name, result.white_illegal_attempts)
        tracker.record_illegal_attempts(black_name, result.black_illegal_attempts)

        if game_number % 10 == 0:
            print(f"  {game_number}/{len(schedule)} games complete...")

    print_results(tracker, bot_names, args.seed, len(all_results))

    if args.pgn:
        export_to_pgn(all_results, args.pgn, tracker)
        print(f"{len(all_results)} games written to {args.pgn}")
        print(f"Watch one back with: python arena.py --replay 1 --pgn {args.pgn}")


if __name__ == '__main__':
    main()
