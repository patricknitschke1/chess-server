"""Arena tests — placeholder for next task."""
import pytest
import chess
from pathlib import Path
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
    """ref_depth2 must not give its queen away for a pawn.

    K+Q vs K+P, and the d7 pawn is defended by the king. Taking it is the only
    move that loses material, and a depth-2 search sees the recapture — unless
    the min/max flag is inverted, in which case the opponent's reply is searched
    as if it helped us and the bot goes hunting for the biggest available
    blunder. It played Qxd7+ and lost the queen to Kxd7.
    """
    board = chess.Board("4k3/3p4/8/8/8/8/3Q4/4K3 w - - 0 1")
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)

    move = ref_depth2_choose_move(board, clock)

    board.push(move)
    hangs_queen = [
        opponent_move.uci()
        for opponent_move in board.legal_moves
        if board.is_capture(opponent_move)
        and (captured := board.piece_at(opponent_move.to_square)) is not None
        and captured.piece_type == chess.QUEEN
    ]
    assert not hangs_queen, (
        f"ref_depth2 played {move.uci()}, losing the queen to {hangs_queen}"
    )


def test_bots_are_given_a_position_with_no_history():
    """A bot cannot see repetition, because it never receives a move history.

    Both reference bots once carried anti-repetition penalties keyed on
    `board.is_repetition(2)`. The arena rebuilds the board from a FEN every ply,
    so `move_stack` is empty and those penalties could never fire — they read as
    a mitigation that was not there. Attendees copy these bots.
    """
    seen = []

    def recording_bot(board, clock):
        seen.append(len(board.move_stack))
        return next(iter(board.legal_moves))

    from arena import run_single_game
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    run_single_game(
        recording_bot, ref_random_choose_move, "recorder", "ref_random",
        RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS,
    )

    assert seen, "the bot was never called"
    assert set(seen) == {0}, f"bot saw move history of lengths {sorted(set(seen))}"


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


def test_arena_records_a_mate_on_the_capping_ply_as_a_mate(monkeypatch):
    """A checkmate delivered on the last permitted ply is a win, not a draw.

    The loop used to test the cap before terminality, so the mating move was
    played, the game fell out of the loop, and the result was written as
    `adjudicated`/`draw`. chess_core.match checks terminality first, so the same
    game scored 1-0 on the server and 1/2-1/2 in the arena — and the arena is
    only useful if it predicts the server.
    """
    import arena
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    monkeypatch.setattr(arena, "PLY_CAP", 1)

    # Fool's mate, one ply from the end: White is to move and Qh4# ends it.
    one_from_mate = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"

    def mate_in_one(board, clock):
        for move in board.legal_moves:
            board.push(move)
            mates = board.is_checkmate()
            board.pop()
            if mates:
                return move
        raise AssertionError("no mate available in the test position")

    result = arena.run_single_game(
        ref_random_choose_move, mate_in_one, "white", "black",
        RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, opening_fen=one_from_mate,
    )

    assert result.termination == "checkmate", (
        f"mate on the capping ply recorded as {result.termination!r}"
    )
    assert result.result == "black_win"


def test_arena_still_adjudicates_a_draw_at_the_ply_cap(monkeypatch):
    """The cap still ends non-terminal games, and at the same ply as before."""
    import arena
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    monkeypatch.setattr(arena, "PLY_CAP", 4)

    def first_legal(board, clock):
        return next(iter(board.legal_moves))

    result = arena.run_single_game(
        first_legal, first_legal, "a", "b",
        RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS,
    )

    assert result.termination == "adjudicated"
    assert result.result == "draw"
    assert result.ply_count == 4


def test_arena_computes_statistics():
    """Arena computes mean and p95 move times correctly."""
    from arena import compute_statistics
    
    move_times = [100, 150, 120, 200, 180, 90, 110, 300, 140, 160]
    
    stats = compute_statistics(move_times)
    
    assert stats['mean'] == 155.0  # sum / count
    assert stats['p95'] == 300  # 95th percentile (9th value in sorted list)
    assert stats['min'] == 90
    assert stats['max'] == 300


def test_arena_tracks_elo():
    """Arena maintains local ELO ratings."""
    from arena import ArenaTracker
    from chess_core import STARTING_RATING
    
    tracker = ArenaTracker()
    tracker.register_bot("Bot1")
    tracker.register_bot("Bot2")
    
    # Both start at 1200
    assert tracker.get_rating("Bot1") == STARTING_RATING
    assert tracker.get_rating("Bot2") == STARTING_RATING
    
    # Record Bot1 win
    tracker.record_game("Bot1", "Bot2", "white_win")
    
    # Bot1 should gain rating, Bot2 should lose
    bot1_rating = tracker.get_rating("Bot1")
    bot2_rating = tracker.get_rating("Bot2")
    
    assert bot1_rating > STARTING_RATING
    assert bot2_rating < STARTING_RATING
    assert bot1_rating + bot2_rating == 2 * STARTING_RATING  # Zero-sum


def test_clock_charges_correct_side_from_black_to_move_opening():
    """Thinking time is charged to whoever actually moved, not always White.

    Openings are randomised (§17), so about half start with Black to move. The
    clock is created from the FEN's side to move; creating it as White regardless
    charged Black's thinking to White's clock, and the slow side finished with
    MORE time than the fast one. Silent, and invisible from the standard opening.
    """
    import time as _time
    import arena
    from ref_bots import ref_random
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    def slow_black(board, clock):
        _time.sleep(0.05)
        return ref_random.choose_move(board, clock)

    def fast_white(board, clock):
        return ref_random.choose_move(board, clock)

    black_to_move = "rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
    result = arena.run_single_game(
        fast_white, slow_black, "fast_white", "slow_black",
        RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, opening_fen=black_to_move,
    )

    assert result.black_time_ms < result.white_time_ms


def test_bot_crash_reports_crash_not_illegal_forfeit():
    """A raised exception is `crash`, not `illegal_forfeit`.

    Both forfeit the game, but the label is what an attendee reads when
    diagnosing: `illegal_forfeit` sends them to their move generation,
    `crash` sends them to the traceback.
    """
    import arena
    from ref_bots import ref_random
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    def exploding_bot(board, clock):
        raise ValueError("boom")

    result = arena.run_single_game(
        exploding_bot, ref_random.choose_move, "exploding", "ref_random",
        RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS,
    )

    assert result.termination == "crash"
    assert result.result == "black_win"
    assert result.white_illegal_attempts == 0


def test_every_opening_has_white_to_move():
    """White always starts; colour balance comes from matchmaking, not openings."""
    import chess
    from opening_book import OPENING_BOOK

    for fen in OPENING_BOOK:
        board = chess.Board(fen)
        assert board.turn == chess.WHITE, f"{fen} does not have White to move"
        assert not board.is_game_over()


def test_arena_cli_parsing():
    """Arena parses command-line arguments correctly."""
    from arena import parse_args

    args = parse_args([
        '--bots', 'bot1.py', 'bot2.py',
        '--games', '50',
        '--seed', '42',
        '--pgn', 'output.pgn',
    ])

    assert args.bots == ['bot1.py', 'bot2.py']
    assert args.games == 50
    assert args.seed == 42
    assert args.pgn == 'output.pgn'
    assert args.verbose is False


def test_arena_cli_defaults():
    """Arena provides sensible defaults."""
    from arena import parse_args
    from chess_core import ns_to_ms, RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    args = parse_args(['--bots', 'bot.py'])

    assert args.games == 100
    assert args.seed is not None
    assert args.time_control_ms == ns_to_ms(RATED_TIME_CONTROL_NS)
    assert args.increment_ms == ns_to_ms(RATED_INCREMENT_NS)


def test_arena_schedule_alternates_colours():
    """Every bot in a pairing plays both colours; openings are all White to move,
    so a fixed assignment would measure colour rather than skill."""
    from arena import build_schedule

    schedule = build_schedule(['a', 'b'], 2)

    whites = [white for white, _ in schedule]
    blacks = [black for _, black in schedule]
    for name in ('a', 'b'):
        assert name in whites, f"{name} never played White"
        assert name in blacks, f"{name} never played Black"


def test_arena_schedule_alternates_colours_in_every_pairing():
    """Colour alternation holds for each pairing of a 3-bot round robin."""
    from arena import build_schedule

    schedule = build_schedule(['a', 'b', 'c'], 12)

    for pair in (frozenset({'a', 'b'}), frozenset({'a', 'c'}), frozenset({'b', 'c'})):
        games = [g for g in schedule if frozenset(g) == pair]
        assert len(games) == 4
        whites = {white for white, _ in games}
        assert whites == set(pair), f"{pair} did not swap colours"


def test_arena_schedule_plays_exact_game_count():
    """--games is the total played, not the count per pairing."""
    from arena import build_schedule

    assert len(build_schedule(['a', 'b'], 100)) == 100
    assert len(build_schedule(['a', 'b', 'c'], 100)) == 100
    assert len(build_schedule(['a', 'b', 'c', 'd'], 7)) == 7


def test_arena_schedule_spreads_remainder_evenly():
    """The odd games out land on distinct pairings, not all on one."""
    from arena import build_schedule

    schedule = build_schedule(['a', 'b', 'c'], 100)

    counts = [len([g for g in schedule if frozenset(g) == pair])
              for pair in (frozenset({'a', 'b'}), frozenset({'a', 'c'}), frozenset({'b', 'c'}))]
    assert max(counts) - min(counts) <= 1


# Scholar's mate, from the standard starting position.
SCHOLARS_MATE_SAN = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
SCHOLARS_MATE_UCI = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]


def _scholars_mate_result():
    from arena import GameResult as ArenaGameResult

    return ArenaGameResult(
        white_name="Bot1",
        black_name="Bot2",
        result="white_win",
        termination="checkmate",
        moves_san=list(SCHOLARS_MATE_SAN),
        moves_uci=list(SCHOLARS_MATE_UCI),
        white_time_ms=170000,
        black_time_ms=168000,
        white_move_times=[120, 150, 130],
        black_move_times=[110, 140],
        white_flags=0,
        black_flags=0,
        white_illegal_attempts=0,
        black_illegal_attempts=0,
        ply_count=len(SCHOLARS_MATE_SAN),
    )


def test_arena_exports_pgn(tmp_path):
    """Arena exports games in PGN format."""
    from arena import export_to_pgn

    pgn_path = tmp_path / "games.pgn"
    export_to_pgn([_scholars_mate_result()], str(pgn_path), tracker=None)

    pgn_content = pgn_path.read_text()

    assert "Bot1" in pgn_content
    assert "Bot2" in pgn_content
    assert "e4" in pgn_content
    assert "1-0" in pgn_content


def test_export_to_pgn_includes_ratings_from_tracker(tmp_path):
    """Ratings reach the PGN headers when a tracker is supplied."""
    from arena import export_to_pgn, ArenaTracker

    tracker = ArenaTracker()
    tracker.register_bot("Bot1")
    tracker.register_bot("Bot2")

    pgn_path = tmp_path / "games.pgn"
    export_to_pgn([_scholars_mate_result()], str(pgn_path), tracker=tracker)

    assert "WhiteElo" in pgn_path.read_text()


def test_export_to_pgn_rejects_unknown_result(tmp_path):
    """An unrecognised result string names itself rather than raising a bare KeyError."""
    from arena import export_to_pgn
    from dataclasses import replace

    broken = replace(_scholars_mate_result(), result="white_resigned")

    with pytest.raises(ValueError) as excinfo:
        export_to_pgn([broken], str(tmp_path / "games.pgn"), tracker=None)

    assert "white_resigned" in str(excinfo.value)


def test_arena_replay_round_trips_exported_pgn(tmp_path, capsys):
    """A game exported to PGN can be replayed back move for move."""
    import re
    from arena import export_to_pgn, main

    pgn_path = tmp_path / "games.pgn"
    export_to_pgn([_scholars_mate_result()], str(pgn_path), tracker=None)

    main(['--replay', '1', '--pgn', str(pgn_path)])

    out = capsys.readouterr().out
    move_headers = re.findall(r"^\d+\.", out, flags=re.MULTILINE)
    assert len(move_headers) == len(SCHOLARS_MATE_SAN)
    assert "Qxf7#" in out
    assert "Bot1" in out and "Bot2" in out


def test_arena_replay_out_of_range_errors_actionably(tmp_path, capsys):
    """Asking for a game the file does not hold says how many it does hold."""
    from arena import export_to_pgn, main

    pgn_path = tmp_path / "games.pgn"
    export_to_pgn([_scholars_mate_result()], str(pgn_path), tracker=None)

    with pytest.raises(SystemExit):
        main(['--replay', '99', '--pgn', str(pgn_path)])

    err = capsys.readouterr().err
    assert "1 game" in err
    assert "--replay 1" in err


def test_arena_replay_without_pgn_errors_actionably(capsys):
    """--replay alone tells the attendee to add --pgn."""
    from arena import parse_args

    with pytest.raises(SystemExit):
        parse_args(['--replay', '1'])

    assert "--pgn" in capsys.readouterr().err


def test_arena_replay_missing_file_errors_actionably(tmp_path, capsys):
    """Replaying a file that does not exist explains how to create it."""
    from arena import main

    with pytest.raises(SystemExit):
        main(['--replay', '1', '--pgn', str(tmp_path / "nope.pgn")])

    assert "--pgn" in capsys.readouterr().err


STARTER_KIT = Path(__file__).resolve().parent.parent.parent / "starter-kit"


def _run_arena(seed: int, pgn_path) -> str:
    """Run a two-game arena through main() and return the exported PGN text."""
    from arena import main

    main([
        '--bots',
        str(STARTER_KIT / "ref_bots" / "ref_random.py"),
        str(STARTER_KIT / "ref_bots" / "ref_greedy.py"),
        '--games', '2',
        '--seed', str(seed),
        '--pgn', str(pgn_path),
    ])
    return Path(pgn_path).read_text()


def test_arena_same_seed_produces_identical_games(tmp_path):
    """One seed replays a whole arena run exactly.

    Openings take an explicit generator and ref_random reaches for the global
    random module, so this only holds if main seeds both.
    """
    first = _run_arena(4242, tmp_path / "a.pgn")
    second = _run_arena(4242, tmp_path / "b.pgn")

    assert first == second


def test_arena_different_seeds_produce_different_games(tmp_path):
    """Different seeds diverge, so "100 games" is 100 games and not one repeated."""
    first = _run_arena(1, tmp_path / "a.pgn")
    second = _run_arena(2, tmp_path / "b.pgn")

    assert first != second


def test_arena_seeded_run_reproduces_moves_and_results(tmp_path, capsys):
    """The determinism holds at the level attendees care about: moves and results."""
    import random
    from arena import run_single_game, build_schedule
    from opening_book import select_opening
    from ref_bots.ref_random import choose_move as random_move
    from ref_bots.ref_greedy import choose_move as greedy_move
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS

    bots = {'ref_random': random_move, 'ref_greedy': greedy_move}

    def play(seed):
        rng = random.Random(seed)
        random.seed(seed)
        games = []
        for white, black in build_schedule(list(bots), 2):
            games.append(run_single_game(
                white_bot=bots[white],
                black_bot=bots[black],
                white_name=white,
                black_name=black,
                time_control_ns=RATED_TIME_CONTROL_NS,
                increment_ns=RATED_INCREMENT_NS,
                opening_fen=select_opening(rng),
                verbose=False,
            ))
        return [(g.moves_uci, g.result) for g in games]

    assert play(77) == play(77)
    assert play(77) != play(78)


def test_arena_exports_and_replays_a_game_from_an_opening(tmp_path):
    """Every book opening is a non-start position, so the PGN needs a [FEN] header.

    Without one, `--replay` fails on every file the arena writes — including the
    one the CLI prints as a hint the moment it finishes a run.
    """
    from arena import run_single_game, export_to_pgn, replay_game
    from chess_core import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
    from opening_book import OPENING_BOOK

    opening = OPENING_BOOK[0]
    result = run_single_game(
        white_bot=ref_greedy_choose_move,
        black_bot=ref_random_choose_move,
        white_name="greedy",
        black_name="random",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=opening,
    )
    assert result.opening_fen == opening

    pgn_path = tmp_path / "games.pgn"
    export_to_pgn([result], str(pgn_path), tracker=None)

    text = pgn_path.read_text()
    assert '[SetUp "1"]' in text
    assert f'[FEN "{opening}"]' in text

    assert replay_game(str(pgn_path), 1) == len(result.moves_san)
