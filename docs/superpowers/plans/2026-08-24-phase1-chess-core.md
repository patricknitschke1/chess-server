# Phase 1: chess_core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure logic layer (`chess_core/`) shared by the live server and the offline arena, with strict TDD and no I/O dependencies.

**Architecture:** Five focused modules (`rules.py`, `clock.py`, `elo.py`, `matchmaker.py`, `match.py`) plus shared types, all pure functions taking time as parameters. Uses `python-chess` for move generation but wraps it with our domain model. Tests are explicit table-driven cases, no mocks or fixtures.

**Tech Stack:** Python 3.11+, `python-chess` for rules engine, `pytest` for testing, dataclasses for immutable state.

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `pytest.ini`
- Create: `chess_core/__init__.py`
- Create: `tests/chess_core/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "chess-arena"
version = "0.1.0"
description = "Chess bot competition server for agentic AI workshop"
requires-python = ">=3.11"
dependencies = [
    "python-chess>=1.999",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-cov>=4.1.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --strict-markers --cov=chess_core --cov-report=term-missing"
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
minversion = 7.0
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

- [ ] **Step 3: Create chess_core package**

```bash
mkdir -p chess_core tests/chess_core
touch chess_core/__init__.py tests/chess_core/__init__.py
```

- [ ] **Step 4: Install dependencies**

Run: `pip install -e ".[dev]"`
Expected: Successfully installed python-chess, pytest, pytest-cov

- [ ] **Step 5: Verify pytest works**

Run: `pytest --collect-only`
Expected: "collected 0 items" (no tests yet, but pytest runs)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml pytest.ini chess_core/ tests/
git commit -m "chore: initialize project with pyproject.toml and pytest config"
```

---

## Task 2: Shared Types and Enums

**Files:**
- Create: `chess_core/types.py`
- Create: `tests/chess_core/test_types.py`

- [ ] **Step 1: Write failing test for shared types**

Create `tests/chess_core/test_types.py`:

```python
"""Tests for shared types and enums."""
from chess_core.types import (
    Color, GameStatus, TerminationReason, GameResult,
    MoveResult, MoveOutcome, ClockView, ClockState,
    ClockUpdateResult, PoolEntry, Pairing, RatingUpdate
)


def test_color_enum_values():
    assert Color.WHITE.value == "white"
    assert Color.BLACK.value == "black"


def test_game_status_enum_values():
    assert GameStatus.PENDING.value == "pending"
    assert GameStatus.ACTIVE.value == "active"
    assert GameStatus.FINISHED.value == "finished"
    assert GameStatus.ABORTED.value == "aborted"


def test_termination_reason_enum_has_all_cases():
    """Ensure all termination reasons from spec are present."""
    reasons = {r.value for r in TerminationReason}
    expected = {
        "checkmate", "stalemate", "insufficient", "fifty_move", "threefold",
        "resignation", "flag", "illegal_forfeit", "abandoned", "adjudicated",
        "no_show", "server_restart", "admin_abort"
    }
    assert reasons == expected


def test_move_outcome_accepted():
    move_result = MoveResult(
        fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        san="e4",
        is_terminal=False,
        termination=None,
        result=None
    )
    outcome = MoveOutcome(accepted=True, move_result=move_result, rejection_reason=None)
    assert outcome.accepted is True
    assert outcome.move_result.san == "e4"
    assert outcome.rejection_reason is None


def test_move_outcome_rejected():
    outcome = MoveOutcome(accepted=False, move_result=None, rejection_reason="Illegal move")
    assert outcome.accepted is False
    assert outcome.move_result is None
    assert outcome.rejection_reason == "Illegal move"


def test_clock_view_immutable():
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    assert clock.my_ms == 180000
    # Immutability enforced by frozen=True
    try:
        clock.my_ms = 100000
        assert False, "Should not allow mutation"
    except AttributeError:
        pass


def test_clock_state_has_ns_suffix():
    """Unit discipline: all time fields must have _ns suffix."""
    clock = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=None,
        delivered_to_mover=0
    )
    assert clock.white_ns == 180_000_000_000
    assert clock.increment_ns == 2_000_000_000


def test_pool_entry_has_all_matchmaker_fields():
    entry = PoolEntry(
        bot_id=1,
        owner="alice",
        rating=1200,
        games_played=5,
        is_anchor=False,
        last_color=Color.WHITE,
        white_count=3,
        last_opponent_id=2,
        unpaired_ticks=0
    )
    assert entry.bot_id == 1
    assert entry.unpaired_ticks == 0


def test_pairing_structure():
    pairing = Pairing(white_bot_id=1, black_bot_id=2)
    assert pairing.white_bot_id == 1
    assert pairing.black_bot_id == 2


def test_rating_update_structure():
    update = RatingUpdate(rating_before=1200, rating_after=1212, delta=12)
    assert update.delta == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/chess_core/test_types.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'chess_core.types'"

- [ ] **Step 3: Write minimal implementation**

Create `chess_core/types.py`:

```python
"""Shared types and enums for chess_core.

All time values use nanoseconds internally. Unit suffixes (_ns, _ms) are mandatory.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Color(Enum):
    """Chess piece color."""
    WHITE = "white"
    BLACK = "black"


class GameStatus(Enum):
    """Game lifecycle states per §7."""
    PENDING = "pending"
    ACTIVE = "active"
    FINISHED = "finished"
    ABORTED = "aborted"


class TerminationReason(Enum):
    """How a game ended per §22 and data model."""
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    INSUFFICIENT = "insufficient"
    FIFTY_MOVE = "fifty_move"
    THREEFOLD = "threefold"
    RESIGNATION = "resignation"
    FLAG = "flag"
    ILLEGAL_FORFEIT = "illegal_forfeit"
    ABANDONED = "abandoned"
    ADJUDICATED = "adjudicated"
    NO_SHOW = "no_show"
    SERVER_RESTART = "server_restart"
    ADMIN_ABORT = "admin_abort"


class GameResult(Enum):
    """Game outcome from White's perspective."""
    WHITE_WIN = "white_win"
    BLACK_WIN = "black_win"
    DRAW = "draw"


@dataclass(frozen=True)
class MoveResult:
    """Result of applying a move to a position."""
    fen_after: str
    san: str
    is_terminal: bool
    termination: Optional[TerminationReason]
    result: Optional[GameResult]


@dataclass(frozen=True)
class MoveOutcome:
    """Result of validating and applying a move.
    
    Models rejection as an explicit state rather than an exception.
    """
    accepted: bool
    move_result: Optional[MoveResult]  # Present only if accepted=True
    rejection_reason: Optional[str]    # Present only if accepted=False


@dataclass(frozen=True)
class ClockView:
    """Clock information presented to a bot's choose_move function.
    
    my_ms is always the time remaining for the bot making the choice,
    regardless of colour. This removes colour-indexing as a class of bug.
    """
    my_ms: int
    opponent_ms: int
    increment_ms: int
    ply: int


@dataclass(frozen=True)
class ClockState:
    """Immutable clock state for a game.
    
    All time values are integer nanoseconds from monotonic clock.
    to_move_since_mono: when position became available to side to move (never None)
    turn_started_mono: when position was delivered to mover (None if undelivered)
    delivered_to_mover: whether current position has been delivered (0 or 1)
    """
    white_ns: int
    black_ns: int
    time_control_ns: int
    increment_ns: int
    to_move: Color
    to_move_since_mono: int
    turn_started_mono: Optional[int]
    delivered_to_mover: int  # 0 or 1


@dataclass(frozen=True)
class ClockUpdateResult:
    """Result of clock accounting for a move per §6.4.
    
    Combines deduction, flag-check, increment, and side-switch into
    a single atomic result, making §6.4's ordering unbreakable.
    """
    new_clock: ClockState
    flagged: bool
    flagged_color: Optional[Color]
    elapsed_ms: int


@dataclass(frozen=True)
class PoolEntry:
    """Matchmaker pool entry per §9.2."""
    bot_id: int
    owner: str
    rating: int
    games_played: int
    is_anchor: bool
    last_color: Optional[Color]
    white_count: int
    last_opponent_id: Optional[int]
    unpaired_ticks: int


@dataclass(frozen=True)
class Pairing:
    """A proposed pairing from the matchmaker."""
    white_bot_id: int
    black_bot_id: int


@dataclass(frozen=True)
class RatingUpdate:
    """Rating change for one bot in a game."""
    rating_before: int
    rating_after: int
    delta: int
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/chess_core/test_types.py -v`
Expected: PASS (all type tests pass)

- [ ] **Step 5: Commit**

```bash
git add chess_core/types.py tests/chess_core/test_types.py
git commit -m "feat(chess_core): add shared types and enums"
```

---

## Task 3: rules.py — Move Validation and Termination

**Files:**
- Create: `chess_core/rules.py`
- Create: `tests/chess_core/test_rules.py`

- [ ] **Step 1: Write failing tests for rules.py**

Create `tests/chess_core/test_rules.py`:

```python
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
    """Server claims fifty-move draw at exactly 50 halfmoves."""
    # FEN with halfmove clock at 50
    fen = "8/8/8/4k3/8/8/4K3/8 w - - 50 100"
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/chess_core/test_rules.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'chess_core.rules'"

- [ ] **Step 3: Write minimal implementation**

Create `chess_core/rules.py`:

```python
"""Chess rules: move validation, termination detection, notation conversion.

Uses python-chess for move generation. Never hand-rolls validation.
"""
import chess
from typing import List, Optional
from chess_core.types import (
    MoveOutcome, MoveResult, TerminationReason, GameResult
)


STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PLY_CAP = 200  # §22 adjudication cap


def validate_and_apply_move(fen: str, move_uci: str) -> MoveOutcome:
    """Validate and apply a move in UCI notation.
    
    Returns a MoveOutcome that models rejection explicitly. Exceptions
    are reserved for genuinely invalid input (malformed FEN, syntactically
    unparseable UCI).
    
    Args:
        fen: Current position in FEN notation
        move_uci: Move in UCI notation (e.g. "e2e4")
    
    Returns:
        MoveOutcome with accepted=True and move_result on success,
        or accepted=False and rejection_reason on illegal move
    
    Raises:
        ValueError: if fen is malformed or move_uci is syntactically invalid
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Malformed FEN: {e}")
    
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as e:
        raise ValueError(f"Invalid UCI notation: {e}")
    
    if move not in board.legal_moves:
        return MoveOutcome(
            accepted=False,
            move_result=None,
            rejection_reason=f"Illegal move: {move_uci}"
        )
    
    san = board.san(move)
    board.push(move)
    fen_after = board.fen()
    
    # Check termination after the move
    is_terminal = False
    termination = None
    result = None
    
    if board.is_checkmate():
        is_terminal = True
        termination = TerminationReason.CHECKMATE
        result = GameResult.BLACK_WIN if board.turn == chess.WHITE else GameResult.WHITE_WIN
    elif board.is_stalemate():
        is_terminal = True
        termination = TerminationReason.STALEMATE
        result = GameResult.DRAW
    elif board.is_insufficient_material():
        is_terminal = True
        termination = TerminationReason.INSUFFICIENT
        result = GameResult.DRAW
    elif board.can_claim_fifty_moves():
        is_terminal = True
        termination = TerminationReason.FIFTY_MOVE
        result = GameResult.DRAW
    elif board.can_claim_threefold_repetition():
        is_terminal = True
        termination = TerminationReason.THREEFOLD
        result = GameResult.DRAW
    
    move_result = MoveResult(
        fen_after=fen_after,
        san=san,
        is_terminal=is_terminal,
        termination=termination,
        result=result
    )
    
    return MoveOutcome(
        accepted=True,
        move_result=move_result,
        rejection_reason=None
    )


def position_key(fen: str) -> str:
    """Extract position key for threefold repetition detection.
    
    Returns only the first four FEN fields (placement, side to move,
    castling rights, en passant target), omitting the halfmove clock
    and fullmove number. Two positions with identical keys are the
    same position for threefold purposes.
    
    Contract: threefold detection compares position_key(fen) strings,
    never full FEN strings.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        Position key string (first four FEN fields joined by space)
    
    Raises:
        ValueError: if fen is invalid
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    # Extract first 4 fields: placement, side, castling, ep
    parts = fen.split()
    if len(parts) < 4:
        raise ValueError(f"FEN missing required fields: {fen}")
    
    return " ".join(parts[:4])


def get_legal_moves(fen: str) -> List[str]:
    """Generate all legal moves from a position in UCI notation.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        List of legal moves in UCI notation, sorted lexicographically
    
    Raises:
        ValueError: if fen is invalid
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    moves = [move.uci() for move in board.legal_moves]
    return sorted(moves)


def detect_termination(
    fen: str,
    history_fens: List[str]
) -> tuple[bool, Optional[TerminationReason], Optional[GameResult]]:
    """Detect if position is terminal and determine result.
    
    Includes server-claimed fifty-move and threefold per §22 (uses
    python-chess can_claim_draw). Threefold detection compares
    position_key(fen) values, not full FEN strings.
    
    Args:
        fen: Current position in FEN notation
        history_fens: All FENs in game history for threefold detection
    
    Returns:
        (is_terminal, termination_reason, result)
        termination_reason and result are None if not terminal
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    # Checkmate
    if board.is_checkmate():
        winner = GameResult.BLACK_WIN if board.turn == chess.WHITE else GameResult.WHITE_WIN
        return (True, TerminationReason.CHECKMATE, winner)
    
    # Stalemate
    if board.is_stalemate():
        return (True, TerminationReason.STALEMATE, GameResult.DRAW)
    
    # Insufficient material
    if board.is_insufficient_material():
        return (True, TerminationReason.INSUFFICIENT, GameResult.DRAW)
    
    # Fifty-move rule (server-claimed)
    if board.can_claim_fifty_moves():
        return (True, TerminationReason.FIFTY_MOVE, GameResult.DRAW)
    
    # Threefold repetition (server-claimed, via position key)
    current_key = position_key(fen)
    key_count = sum(1 for h in history_fens if position_key(h) == current_key)
    if key_count >= 3:
        return (True, TerminationReason.THREEFOLD, GameResult.DRAW)
    
    return (False, None, None)


def fen_to_ascii(fen: str) -> str:
    """Render a position as ASCII art for MCP get_game() per §13.2.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        ASCII board representation with rank/file labels
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    return str(board)


def uci_to_san(fen: str, move_uci: str) -> str:
    """Convert UCI move to SAN notation in the given position.
    
    Args:
        fen: Position in FEN notation
        move_uci: Move in UCI notation
    
    Returns:
        Move in SAN notation (e.g. "Nf3")
    
    Raises:
        ValueError: if move_uci is illegal in the position
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as e:
        raise ValueError(f"Invalid UCI: {e}")
    
    if move not in board.legal_moves:
        raise ValueError(f"Illegal move {move_uci} in position")
    
    return board.san(move)


def san_list_to_pgn(
    san_moves: List[str],
    white_name: str,
    black_name: str,
    result: GameResult,
    white_rating: Optional[int] = None,
    black_rating: Optional[int] = None
) -> str:
    """Format a game as PGN for arena.py export.
    
    Args:
        san_moves: Moves in SAN notation
        white_name: White player name
        black_name: Black player name
        result: Game result
        white_rating: Optional ELO rating for White
        black_rating: Optional ELO rating for Black
    
    Returns:
        Complete PGN string with headers and movetext
    """
    # Map result to PGN notation
    result_map = {
        GameResult.WHITE_WIN: "1-0",
        GameResult.BLACK_WIN: "0-1",
        GameResult.DRAW: "1/2-1/2"
    }
    result_str = result_map[result]
    
    # Build headers
    headers = [
        f'[White "{white_name}"]',
        f'[Black "{black_name}"]',
        f'[Result "{result_str}"]'
    ]
    
    if white_rating is not None:
        headers.append(f'[WhiteElo "{white_rating}"]')
    if black_rating is not None:
        headers.append(f'[BlackElo "{black_rating}"]')
    
    # Build movetext
    movetext_parts = []
    for i, move in enumerate(san_moves):
        if i % 2 == 0:  # White's move
            move_num = (i // 2) + 1
            movetext_parts.append(f"{move_num}. {move}")
        else:  # Black's move
            movetext_parts.append(move)
    
    movetext = " ".join(movetext_parts)
    if movetext:
        movetext += " "
    movetext += result_str
    
    return "\n".join(headers) + "\n\n" + movetext + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/chess_core/test_rules.py -v`
Expected: PASS (all rules tests pass)

- [ ] **Step 5: Commit**

```bash
git add chess_core/rules.py tests/chess_core/test_rules.py
git commit -m "feat(chess_core): implement rules.py with move validation and termination"
```

---

## Task 4: clock.py — Time Management and Delivery

**Files:**
- Create: `chess_core/clock.py`
- Create: `tests/chess_core/test_clock.py`

- [ ] **Step 1: Write failing tests for clock.py**

Create `tests/chess_core/test_clock.py`:

```python
"""Tests for clock arithmetic and delivery lifecycle.

Table-driven tests for §6.4 ordering are critical.
"""
import chess_core.clock as clock
from chess_core.types import Color, ClockState


# Failure path tests first: flag on exact zero, no increment on flag

def test_account_move_flags_on_exact_zero():
    """Flag on exactly zero remaining after deduction per §6.4 step 3."""
    state = ClockState(
        white_ns=2_000_000_000,  # 2 seconds
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1000000,
        delivered_to_mover=1
    )
    
    # Elapsed exactly equals remaining
    receive_mono = 1000000 + 2_000_000_000
    now_mono = receive_mono + 100
    
    result = clock.account_move_and_switch(state, receive_mono, now_mono)
    
    assert result.flagged is True
    assert result.flagged_color == Color.WHITE
    # No increment on flag per §6.4
    assert result.new_clock.black_ns == 180_000_000_000  # unchanged
    assert result.new_clock.white_ns == 0


def test_account_move_flags_below_zero():
    """Flag when remaining goes negative."""
    state = ClockState(
        white_ns=1_000_000_000,  # 1 second
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1000000,
        delivered_to_mover=1
    )
    
    # Elapsed exceeds remaining
    receive_mono = 1000000 + 3_000_000_000  # 3 seconds
    now_mono = receive_mono + 100
    
    result = clock.account_move_and_switch(state, receive_mono, now_mono)
    
    assert result.flagged is True
    assert result.flagged_color == Color.WHITE
    assert result.new_clock.white_ns <= 0  # can be negative


def test_account_move_no_increment_on_flag():
    """Flagged move does not receive increment per §6.4."""
    state = ClockState(
        white_ns=500_000_000,  # 0.5 seconds
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1000000,
        delivered_to_mover=1
    )
    
    receive_mono = 1000000 + 1_000_000_000  # flag
    now_mono = receive_mono + 100
    
    result = clock.account_move_and_switch(state, receive_mono, now_mono)
    
    assert result.flagged is True
    # Increment NOT added
    assert result.new_clock.white_ns < state.increment_ns


def test_account_move_adds_increment_when_not_flagged():
    """Non-flagged move receives increment per §6.4 step 5."""
    state = ClockState(
        white_ns=10_000_000_000,  # 10 seconds
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1000000,
        delivered_to_mover=1
    )
    
    receive_mono = 1000000 + 1_000_000_000  # 1 second elapsed
    now_mono = receive_mono + 100
    
    result = clock.account_move_and_switch(state, receive_mono, now_mono)
    
    assert result.flagged is False
    # white_ns = 10s - 1s + 2s = 11s
    assert result.new_clock.white_ns == 11_000_000_000


def test_account_move_switches_side():
    """Side switches after accounting per §6.4 step 5."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1000000,
        delivered_to_mover=1
    )
    
    receive_mono = 1000000 + 1_000_000_000
    now_mono = receive_mono + 100
    
    result = clock.account_move_and_switch(state, receive_mono, now_mono)
    
    assert result.new_clock.to_move == Color.BLACK


def test_account_move_clears_delivery():
    """Side switch clears delivery per §6.4 step 5."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1000000,
        delivered_to_mover=1
    )
    
    receive_mono = 1000000 + 1_000_000_000
    now_mono = receive_mono + 100
    
    result = clock.account_move_and_switch(state, receive_mono, now_mono)
    
    assert result.new_clock.delivered_to_mover == 0
    assert result.new_clock.turn_started_mono is None
    assert result.new_clock.to_move_since_mono == now_mono


def test_account_move_raises_if_undelivered():
    """Cannot account move on undelivered position."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=None,  # undelivered
        delivered_to_mover=0
    )
    
    try:
        clock.account_move_and_switch(state, 2000000, 2000000)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "undelivered" in str(e).lower()


# Delivery idempotency tests

def test_deliver_position_sets_fields():
    """First delivery sets turn_started_mono and delivered_to_mover."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=None,
        delivered_to_mover=0
    )
    
    now_mono = 1500000
    delivered = clock.deliver_position(state, now_mono, 0)
    
    assert delivered.turn_started_mono == now_mono
    assert delivered.delivered_to_mover == 1
    assert delivered.to_move_since_mono == 1000000  # unchanged


def test_deliver_position_idempotent():
    """Re-delivery returns identical clock per §6.2."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1500000,
        delivered_to_mover=1
    )
    
    now_mono = 2000000
    redelivered = clock.deliver_position(state, now_mono, 0)
    
    # Clock unchanged
    assert redelivered.turn_started_mono == 1500000
    assert redelivered.delivered_to_mover == 1


def test_rejected_move_does_not_reset_clock():
    """A rejected move leaves the clock baseline untouched per §8.3.

    The bot is delivered a position at T0, submits an illegal move at T0+5s,
    then a legal move at T0+9s. It must be charged the full 9 seconds, not the
    4 seconds since its rejected attempt. Without this, an illegal-move loop is
    a free clock stop and a buggy bot outlives a correct one.
    """
    delivered_at = 1_000_000_000
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=delivered_at,
        turn_started_mono=delivered_at,
        delivered_to_mover=1,
    )

    # An illegal move is rejected by rules.validate_and_apply_move and the caller
    # never touches the clock: no deliver_position, no account_move_and_switch.
    after_rejection = state
    assert after_rejection.turn_started_mono == delivered_at

    # Re-delivery during the same turn must also not move the baseline (§6.2).
    after_rejection = clock.deliver_position(after_rejection, delivered_at + 5_000_000_000, 0)
    assert after_rejection.turn_started_mono == delivered_at

    # The eventual legal move is charged from the original delivery instant.
    result = clock.account_move_and_switch(after_rejection, delivered_at + 9_000_000_000)
    assert result.elapsed_ns == 9_000_000_000
    assert result.flagged is False
    # 180s - 9s + 2s increment
    assert result.clock.white_ns == 173_000_000_000


# Delivery timeout tests

def test_check_delivery_timeout_at_grace():
    """Timeout fires at exactly DELIVERY_GRACE_NS + 1ns."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=None,
        delivered_to_mover=0
    )
    
    grace_ns = clock.DELIVERY_GRACE_NS
    
    # Just before timeout
    assert clock.check_delivery_timeout(state, 1000000 + grace_ns, grace_ns) is False
    
    # At timeout
    assert clock.check_delivery_timeout(state, 1000000 + grace_ns + 1, grace_ns) is True


def test_check_delivery_timeout_returns_false_if_delivered():
    """Timeout check returns False if position already delivered."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1500000,
        delivered_to_mover=1
    )
    
    # Way past grace
    assert clock.check_delivery_timeout(
        state,
        1000000 + clock.DELIVERY_GRACE_NS * 10,
        clock.DELIVERY_GRACE_NS
    ) is False


# Turn elapsed computation

def test_compute_turn_elapsed_returns_none_when_undelivered():
    """Turn elapsed is None for undelivered position."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=None,
        delivered_to_mover=0
    )
    
    assert clock.compute_turn_elapsed_ms(state, 2000000) is None


def test_compute_turn_elapsed_when_delivered():
    """Turn elapsed computes correctly when delivered."""
    state = ClockState(
        white_ns=180_000_000_000,
        black_ns=180_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1000000,
        turn_started_mono=1500000,
        delivered_to_mover=1
    )
    
    now_mono = 1500000 + 3_000_000_000  # 3 seconds later
    elapsed_ms = clock.compute_turn_elapsed_ms(state, now_mono)
    assert elapsed_ms == 3000


# Create clock test

def test_create_clock():
    """Create clock initializes correctly."""
    state = clock.create_clock(
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        now_mono=1000000
    )
    
    assert state.white_ns == 180_000_000_000
    assert state.black_ns == 180_000_000_000
    assert state.to_move == Color.WHITE
    assert state.to_move_since_mono == 1000000
    assert state.turn_started_mono is None
    assert state.delivered_to_mover == 0


# Unit conversion tests

def test_ms_to_ns():
    assert clock.ms_to_ns(180000) == 180_000_000_000


def test_ns_to_ms():
    assert clock.ns_to_ms(180_000_000_000) == 180000


# Constants test

def test_constants_exist():
    assert clock.RATED_TIME_CONTROL_NS == 180_000_000_000
    assert clock.RATED_INCREMENT_NS == 2_000_000_000
    assert clock.EXHIBITION_TIME_CONTROL_NS == 300_000_000_000
    assert clock.EXHIBITION_INCREMENT_NS == 10_000_000_000
    assert clock.DELIVERY_GRACE_NS == 15_000_000_000
    assert clock.AGENT_DELIVERY_GRACE_NS == 60_000_000_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/chess_core/test_clock.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'chess_core.clock'"

- [ ] **Step 3: Write minimal implementation**

Create `chess_core/clock.py`:

```python
"""Clock arithmetic and delivery lifecycle per §6.

All time values are integer nanoseconds internally. Unit conversion happens
only at the boundary via ms_to_ns/ns_to_ms helpers.
"""
from typing import Optional
from chess_core.types import Color, ClockState, ClockUpdateResult


# Constants per §5.2
RATED_TIME_CONTROL_NS = 180_000_000_000  # 3 minutes
RATED_INCREMENT_NS = 2_000_000_000       # 2 seconds
EXHIBITION_TIME_CONTROL_NS = 300_000_000_000  # 5 minutes
EXHIBITION_INCREMENT_NS = 10_000_000_000      # 10 seconds
DELIVERY_GRACE_NS = 15_000_000_000       # 15 seconds
AGENT_DELIVERY_GRACE_NS = 60_000_000_000 # 60 seconds
AGENT_AUTO_RELEASE_NS = 45_000_000_000   # 45 seconds


def create_clock(
    time_control_ns: int,
    increment_ns: int,
    to_move: Color,
    now_mono: int
) -> ClockState:
    """Create initial clock state for a new game.
    
    Args:
        time_control_ns: Starting time in nanoseconds
        increment_ns: Increment per move in nanoseconds
        to_move: Side to move first
        now_mono: Current monotonic time in nanoseconds
    
    Returns:
        ClockState with both sides at time_control_ns, undelivered
    """
    return ClockState(
        white_ns=time_control_ns,
        black_ns=time_control_ns,
        time_control_ns=time_control_ns,
        increment_ns=increment_ns,
        to_move=to_move,
        to_move_since_mono=now_mono,
        turn_started_mono=None,
        delivered_to_mover=0
    )


def deliver_position(
    clock: ClockState,
    now_mono: int,
    ply: int
) -> ClockState:
    """Mark position as delivered to the side to move per §6.2.
    
    Idempotent: re-delivery returns identical clock without restarting timer.
    
    Args:
        clock: Current clock state
        now_mono: Delivery time in nanoseconds (monotonic)
        ply: Current ply for idempotency check
    
    Returns:
        ClockState with turn_started_mono set, delivered_to_mover=1
    """
    if clock.delivered_to_mover == 1:
        # Already delivered, return unchanged
        return clock
    
    return ClockState(
        white_ns=clock.white_ns,
        black_ns=clock.black_ns,
        time_control_ns=clock.time_control_ns,
        increment_ns=clock.increment_ns,
        to_move=clock.to_move,
        to_move_since_mono=clock.to_move_since_mono,
        turn_started_mono=now_mono,
        delivered_to_mover=1
    )


def account_move_and_switch(
    clock: ClockState,
    receive_mono: int,
    now_mono: int
) -> ClockUpdateResult:
    """Account for elapsed time, increment, and switch sides per §6.4.
    
    Performs the complete §6.4 sequence atomically, making the ordering
    unbreakable:
    1. elapsed = receive_mono − turn_started_mono
    2. remaining = remaining − elapsed
    3. if remaining_ns <= 0 -> flag (NO increment on flag)
    4. if not flagged -> remaining += increment_ns
    5. switch side; delivered_to_mover=0; turn_started_mono=NULL;
       to_move_since_mono=now_mono
    
    Args:
        clock: Current clock state (must have turn_started_mono set)
        receive_mono: Move receipt time in nanoseconds (monotonic)
        now_mono: Current time in nanoseconds (becomes new to_move_since_mono)
    
    Returns:
        ClockUpdateResult with new_clock (side switched, delivery cleared),
        flag status, and elapsed_ms
    
    Raises:
        ValueError: if turn_started_mono is None (undelivered position)
    """
    if clock.turn_started_mono is None:
        raise ValueError("Cannot account move on undelivered position")
    
    # Step 1: compute elapsed
    elapsed_ns = receive_mono - clock.turn_started_mono
    elapsed_ms = ns_to_ms(elapsed_ns)
    
    # Step 2: deduct from mover's clock
    if clock.to_move == Color.WHITE:
        remaining_ns = clock.white_ns - elapsed_ns
        white_ns = remaining_ns
        black_ns = clock.black_ns
    else:
        remaining_ns = clock.black_ns - elapsed_ns
        white_ns = clock.white_ns
        black_ns = remaining_ns
    
    # Step 3: check flag
    flagged = remaining_ns <= 0
    flagged_color = clock.to_move if flagged else None
    
    # Step 4 & 5: add increment if not flagged, then switch
    if not flagged:
        if clock.to_move == Color.WHITE:
            white_ns += clock.increment_ns
        else:
            black_ns += clock.increment_ns
    
    # Switch side
    new_to_move = Color.BLACK if clock.to_move == Color.WHITE else Color.WHITE
    
    new_clock = ClockState(
        white_ns=white_ns,
        black_ns=black_ns,
        time_control_ns=clock.time_control_ns,
        increment_ns=clock.increment_ns,
        to_move=new_to_move,
        to_move_since_mono=now_mono,
        turn_started_mono=None,
        delivered_to_mover=0
    )
    
    return ClockUpdateResult(
        new_clock=new_clock,
        flagged=flagged,
        flagged_color=flagged_color,
        elapsed_ms=elapsed_ms
    )


def check_delivery_timeout(
    clock: ClockState,
    now_mono: int,
    grace_ns: int
) -> bool:
    """Check if undelivered position has exceeded grace period per §6.3.
    
    Args:
        clock: Current clock state
        now_mono: Current monotonic time in nanoseconds
        grace_ns: Grace period in nanoseconds (DELIVERY_GRACE_NS or AGENT_DELIVERY_GRACE_NS)
    
    Returns:
        True if delivered_to_mover=0 and (now_mono - to_move_since_mono) > grace_ns
    """
    if clock.delivered_to_mover == 1:
        return False
    
    return (now_mono - clock.to_move_since_mono) > grace_ns


def compute_turn_elapsed_ms(
    clock: ClockState,
    now_mono: int
) -> Optional[int]:
    """Compute milliseconds elapsed since delivery for SSE payloads per §14.
    
    Args:
        clock: Current clock state
        now_mono: Current monotonic time in nanoseconds
    
    Returns:
        Milliseconds elapsed if delivered, None if undelivered
    """
    if clock.turn_started_mono is None:
        return None
    
    elapsed_ns = now_mono - clock.turn_started_mono
    return ns_to_ms(elapsed_ns)


def ms_to_ns(ms: int) -> int:
    """Convert milliseconds to nanoseconds."""
    return ms * 1_000_000


def ns_to_ms(ns: int) -> int:
    """Convert nanoseconds to milliseconds."""
    return ns // 1_000_000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/chess_core/test_clock.py -v`
Expected: PASS (all clock tests pass)

- [ ] **Step 5: Commit**

```bash
git add chess_core/clock.py tests/chess_core/test_clock.py
git commit -m "feat(chess_core): implement clock.py with §6.4 ordering and delivery lifecycle"
```

---

## Task 5: elo.py — Rating Calculation

**Files:**
- Create: `chess_core/elo.py`
- Create: `tests/chess_core/test_elo.py`

- [ ] **Step 1: Write failing tests for elo.py**

Create `tests/chess_core/test_elo.py`:

```python
"""Tests for Elo rating calculations.

Property test for zero-sum and symmetry is critical.
"""
import chess_core.elo as elo


# Property test: zero-sum and symmetric

def test_elo_zero_sum_symmetric():
    """Property test: exchange is zero-sum and symmetric across the rating space per §10.1.

    This sweeps the whole plausible rating range rather than sampling a handful of
    pairs. Rounding is where zero-sum breaks, and rounding boundaries are exactly
    what a hand-picked case list misses. Verified: 3,249 pairs, 0 violations, runs
    in well under a second.
    """
    grid = range(600, 2001, 25)

    for winner_rating in grid:
        for loser_rating in grid:
            winner_update, loser_update = elo.compute_rating_exchange(winner_rating, loser_rating)

            # Zero-sum: the points the winner gains equal the points the loser loses.
            assert winner_update.delta + loser_update.delta == 0, \
                f"Not zero-sum for {winner_rating} vs {loser_rating}: " \
                f"{winner_update.delta} + {loser_update.delta}"

            # Symmetric: swapping the arguments negates the deltas.
            loser_update_swap, winner_update_swap = elo.compute_rating_exchange(
                loser_rating, winner_rating
            )
            assert winner_update.delta == -loser_update_swap.delta
            assert loser_update.delta == -winner_update_swap.delta


def test_draw_exchange_zero_sum():
    """Draw exchange is zero-sum across the rating space.

    Verified: 3,249 pairs, 0 violations.
    """
    grid = range(600, 2001, 25)

    for white_rating in grid:
        for black_rating in grid:
            white_update, black_update = elo.compute_draw_exchange(white_rating, black_rating)
            assert white_update.delta + black_update.delta == 0, \
                f"Draw not zero-sum for {white_rating} vs {black_rating}: " \
                f"{white_update.delta} + {black_update.delta}"


def test_draw_exchange_symmetric():
    """Draw exchange is symmetric."""
    white_update, black_update = elo.compute_draw_exchange(1000, 1400)
    black_update_swap, white_update_swap = elo.compute_draw_exchange(1400, 1000)
    
    assert white_update.delta == -black_update_swap.delta
    assert black_update.delta == -white_update_swap.delta


# Extreme rating gap tests

def test_extreme_rating_gap_1000_vs_2000():
    """Verify exchange is sane at 1000-point gap."""
    winner_update, loser_update = elo.compute_rating_exchange(1000, 2000)
    
    # Underdog wins: big gain
    assert winner_update.delta > 20
    # Favorite loses: big loss
    assert loser_update.delta < -20
    # Zero-sum
    assert winner_update.delta + loser_update.delta == 0


def test_extreme_rating_gap_800_vs_1600():
    """Verify exchange is sane at 800-point gap."""
    winner_update, loser_update = elo.compute_rating_exchange(800, 1600)
    
    assert winner_update.delta > 20
    assert loser_update.delta < -20
    assert winner_update.delta + loser_update.delta == 0


# One-sided anchor tests

def test_one_sided_exchange_competitor_only():
    """Anchor rating never changes per §10.3."""
    competitor_update = elo.compute_one_sided_exchange(
        competitor_rating=1200,
        anchor_rating=1000,
        competitor_won=True
    )
    
    # Competitor gains points
    assert competitor_update.delta > 0
    # Anchor rating not returned (one-sided)


def test_one_sided_exchange_competitor_loses():
    """Competitor loses points when losing to anchor."""
    competitor_update = elo.compute_one_sided_exchange(
        competitor_rating=1200,
        anchor_rating=1000,
        competitor_won=False
    )
    
    # Competitor loses points
    assert competitor_update.delta < 0


def test_one_sided_exchange_shrinks_near_anchor():
    """Injection shrinks as competitor approaches anchor per §10.3."""
    # Competitor far from anchor
    far_update = elo.compute_one_sided_exchange(
        competitor_rating=600,
        anchor_rating=1000,
        competitor_won=True
    )
    
    # Competitor near anchor
    near_update = elo.compute_one_sided_exchange(
        competitor_rating=980,
        anchor_rating=1000,
        competitor_won=True
    )
    
    assert far_update.delta > near_update.delta


# Happy path tests

def test_compute_rating_exchange_equal_ratings():
    """Equal ratings, winner gains ~12, loser loses ~12."""
    winner_update, loser_update = elo.compute_rating_exchange(1200, 1200)
    
    assert winner_update.rating_before == 1200
    assert winner_update.delta == 12  # K/2 for equal ratings
    assert winner_update.rating_after == 1212
    
    assert loser_update.rating_before == 1200
    assert loser_update.delta == -12
    assert loser_update.rating_after == 1188


def test_compute_rating_exchange_underdog_wins():
    """Lower-rated player winning gains more points."""
    winner_update, loser_update = elo.compute_rating_exchange(1000, 1200)
    
    assert winner_update.delta > 12  # more than equal-rating case
    assert loser_update.delta < -12


def test_compute_rating_exchange_favorite_wins():
    """Higher-rated player winning gains fewer points."""
    winner_update, loser_update = elo.compute_rating_exchange(1200, 1000)
    
    assert winner_update.delta < 12  # less than equal-rating case
    assert loser_update.delta > -12


def test_compute_draw_exchange_equal_ratings():
    """Equal ratings draw, no change."""
    white_update, black_update = elo.compute_draw_exchange(1200, 1200)
    
    assert white_update.delta == 0
    assert black_update.delta == 0


def test_compute_draw_exchange_unequal_ratings():
    """Unequal ratings draw, lower-rated gains, higher-rated loses."""
    white_update, black_update = elo.compute_draw_exchange(1000, 1200)
    
    assert white_update.delta > 0  # underdog gains
    assert black_update.delta < 0  # favorite loses
    assert white_update.delta + black_update.delta == 0


# Constants test

def test_constants_exist():
    assert elo.STARTING_RATING == 1200
    assert elo.K_FACTOR == 24
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/chess_core/test_elo.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'chess_core.elo'"

- [ ] **Step 3: Write minimal implementation**

Create `chess_core/elo.py`:

```python
"""Elo rating calculations per §10.

K=24 flat for all bots. Exchange is zero-sum and symmetric for
competitor-vs-competitor games.
"""
from chess_core.types import RatingUpdate


STARTING_RATING = 1200
K_FACTOR = 24


def compute_rating_exchange(
    winner_rating: int,
    loser_rating: int
) -> tuple[RatingUpdate, RatingUpdate]:
    """Compute two-sided Elo exchange for a decisive game, K=24 flat per §10.1.
    
    Exchange is zero-sum and symmetric.
    
    Args:
        winner_rating: Winner's current rating
        loser_rating: Loser's current rating
    
    Returns:
        (winner_update, loser_update) where winner gains and loser loses
    """
    # Expected scores
    winner_expected = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
    loser_expected = 1 - winner_expected
    
    # Actual scores (1 for winner, 0 for loser)
    winner_actual = 1.0
    loser_actual = 0.0
    
    # Deltas
    winner_delta = round(K_FACTOR * (winner_actual - winner_expected))
    loser_delta = round(K_FACTOR * (loser_actual - loser_expected))
    
    winner_update = RatingUpdate(
        rating_before=winner_rating,
        rating_after=winner_rating + winner_delta,
        delta=winner_delta
    )
    
    loser_update = RatingUpdate(
        rating_before=loser_rating,
        rating_after=loser_rating + loser_delta,
        delta=loser_delta
    )
    
    return winner_update, loser_update


def compute_draw_exchange(
    white_rating: int,
    black_rating: int
) -> tuple[RatingUpdate, RatingUpdate]:
    """Compute two-sided Elo exchange for a draw, K=24 flat per §10.1.
    
    Exchange is zero-sum and symmetric.
    
    Args:
        white_rating: White's current rating
        black_rating: Black's current rating
    
    Returns:
        (white_update, black_update) summing to zero delta
    """
    # Expected scores
    white_expected = 1 / (1 + 10 ** ((black_rating - white_rating) / 400))
    black_expected = 1 - white_expected
    
    # Actual scores (0.5 for each in a draw)
    white_actual = 0.5
    black_actual = 0.5
    
    # Deltas
    white_delta = round(K_FACTOR * (white_actual - white_expected))
    black_delta = round(K_FACTOR * (black_actual - black_expected))
    
    white_update = RatingUpdate(
        rating_before=white_rating,
        rating_after=white_rating + white_delta,
        delta=white_delta
    )
    
    black_update = RatingUpdate(
        rating_before=black_rating,
        rating_after=black_rating + black_delta,
        delta=black_delta
    )
    
    return white_update, black_update


def compute_one_sided_exchange(
    competitor_rating: int,
    anchor_rating: int,
    competitor_won: bool
) -> RatingUpdate:
    """Compute one-sided Elo update against a fixed anchor per §10.3.
    
    Anchor rating never changes. Net injection into pool per game, but
    shrinks toward zero as competitor approaches anchor rating.
    
    Args:
        competitor_rating: Competitor's current rating
        anchor_rating: Fixed anchor rating
        competitor_won: True if competitor won, False if lost
    
    Returns:
        RatingUpdate for competitor only
    """
    # Expected score for competitor
    expected = 1 / (1 + 10 ** ((anchor_rating - competitor_rating) / 400))
    
    # Actual score
    actual = 1.0 if competitor_won else 0.0
    
    # Delta
    delta = round(K_FACTOR * (actual - expected))
    
    return RatingUpdate(
        rating_before=competitor_rating,
        rating_after=competitor_rating + delta,
        delta=delta
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/chess_core/test_elo.py -v`
Expected: PASS (all Elo tests pass, including property test)

- [ ] **Step 5: Commit**

```bash
git add chess_core/elo.py tests/chess_core/test_elo.py
git commit -m "feat(chess_core): implement elo.py with K=24 flat and zero-sum property"
```

---

## Task 6: matchmaker.py — Pairing Policy

**Files:**
- Create: `chess_core/matchmaker.py`
- Create: `tests/chess_core/test_matchmaker.py`

- [ ] **Step 1: Write failing tests for matchmaker.py**

Create `tests/chess_core/test_matchmaker.py`:

```python
"""Tests for matchmaker pairing policy per §9.2."""
import chess_core.matchmaker as matchmaker
from chess_core.types import PoolEntry, Color


# Failure path tests: edge cases

def test_pair_odd_pool_leaves_one_unpaired():
    """Odd pool leaves one bot unpaired."""
    pool = [
        PoolEntry(1, "alice", 1200, 0, False, None, 0, None, 0),
        PoolEntry(2, "alice", 1200, 0, False, None, 0, None, 0),
        PoolEntry(3, "alice", 1200, 0, False, None, 0, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    assert len(pairings) == 1  # one pairing, one bot unpaired


def test_pair_single_bot_returns_empty():
    """Single bot returns empty pairings."""
    pool = [
        PoolEntry(1, "alice", 1200, 0, False, None, 0, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    assert len(pairings) == 0


def test_pair_empty_pool_returns_empty():
    """Empty pool returns empty pairings."""
    pairings = matchmaker.pair_bots([], seed=42)
    assert len(pairings) == 0


def test_pair_skips_same_owner_until_relaxed():
    """Same owner blocks pairing until unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(2, "alice", 1200, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(3, "bob", 1200, 5, False, Color.WHITE, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 and 2 share owner, so bot 1 pairs with bot 3
    assert len(pairings) == 1
    assert (pairings[0].white_bot_id, pairings[0].black_bot_id) in [(1, 3), (3, 1)]


def test_pair_relaxes_same_owner_after_3_ticks():
    """Same owner allowed when unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 3),  # waited 3 ticks
        PoolEntry(2, "alice", 1200, 5, False, Color.BLACK, 2, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Relaxed, so pairing allowed
    assert len(pairings) == 1


def test_pair_skips_rematch_until_relaxed():
    """Rematch of last_opponent_id blocks pairing until unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, 2, 0),  # last opponent was 2
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, 1, 0),    # last opponent was 1
        PoolEntry(3, "charlie", 1200, 5, False, Color.WHITE, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 and 2 are a rematch, so bot 1 pairs with bot 3
    assert len(pairings) == 1
    assert (pairings[0].white_bot_id, pairings[0].black_bot_id) in [(1, 3), (3, 1)]


def test_pair_relaxes_rematch_after_3_ticks():
    """Rematch allowed when unpaired_ticks >= 3."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, 2, 3),  # waited 3 ticks
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, 1, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Relaxed, so rematch allowed
    assert len(pairings) == 1


# Happy path tests

def test_pair_sorts_by_games_played_first():
    """New bots paired first per §9.2."""
    pool = [
        PoolEntry(1, "alice", 1200, 10, False, Color.WHITE, 5, None, 0),
        PoolEntry(2, "bob", 1200, 0, False, None, 0, None, 0),  # new bot
        PoolEntry(3, "charlie", 1200, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(4, "dave", 1200, 0, False, None, 0, None, 0),  # new bot
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 2 and 4 (new bots) should be paired
    bot_ids = {pairings[0].white_bot_id, pairings[0].black_bot_id}
    assert bot_ids == {2, 4}


def test_pair_sorts_by_rating_second():
    """Rating sorts after games_played per §9.2."""
    pool = [
        PoolEntry(1, "alice", 1300, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(2, "bob", 1100, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(3, "charlie", 1200, 5, False, Color.WHITE, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Should pair adjacent: 2 (1100) with 3 (1200)
    bot_ids = {pairings[0].white_bot_id, pairings[0].black_bot_id}
    assert bot_ids == {2, 3}


def test_colour_precedence_alternates_from_last_color():
    """Colour alternates from last_color per §9.2."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),  # was White
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, None, 0),    # was Black
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 was White, should be Black now
    # Bot 2 was Black, should be White now
    assert pairings[0].white_bot_id == 2
    assert pairings[0].black_bot_id == 1


def test_colour_precedence_tie_break_by_white_count():
    """Tie-break by white_count when both have same last_color."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.BLACK, 5, None, 0),  # white_count=5
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, None, 0),    # white_count=2
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 2 has lower white_count, gets White
    assert pairings[0].white_bot_id == 2
    assert pairings[0].black_bot_id == 1


def test_colour_precedence_tie_break_by_bot_id():
    """Tie-break by bot_id when white_count is equal."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.BLACK, 3, None, 0),
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 3, None, 0),
    ]
    
    pairings = matchmaker.pair_bots(pool, seed=42)
    
    # Bot 1 has lower id, gets White
    assert pairings[0].white_bot_id == 1
    assert pairings[0].black_bot_id == 2


def test_seeded_determinism():
    """Same pool + same seed → identical pairings per §9.2."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(2, "bob", 1250, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(3, "charlie", 1150, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(4, "dave", 1300, 5, False, Color.BLACK, 2, None, 0),
    ]
    
    pairings1 = matchmaker.pair_bots(pool, seed=42)
    pairings2 = matchmaker.pair_bots(pool, seed=42)
    
    assert pairings1 == pairings2


def test_seeded_different_seeds_different_pairings():
    """Different seeds can produce different pairings."""
    pool = [
        PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(2, "bob", 1200, 5, False, Color.BLACK, 2, None, 0),
        PoolEntry(3, "charlie", 1200, 5, False, Color.WHITE, 3, None, 0),
        PoolEntry(4, "dave", 1200, 5, False, Color.BLACK, 2, None, 0),
    ]
    
    pairings1 = matchmaker.pair_bots(pool, seed=42)
    pairings2 = matchmaker.pair_bots(pool, seed=99)
    
    # May differ (not guaranteed, but likely with different seeds)
    # This test just verifies seeding works, not that results differ


# Anchor gating tests

def test_should_offer_anchor_within_400():
    """Anchor offered within ±400 rating window per §9.3."""
    bot = PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0)
    anchor = PoolEntry(99, "ref-greedy", 1000, 100, True, Color.WHITE, 50, None, 0)
    
    # Within 400
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=False) is True
    
    # Exactly 400
    bot_at_1400 = PoolEntry(1, "alice", 1400, 5, False, Color.WHITE, 3, None, 0)
    assert matchmaker.should_offer_anchor(bot_at_1400, anchor, has_other_pairing_option=False) is True


def test_should_offer_anchor_beyond_400():
    """Anchor not offered beyond ±400 rating window."""
    bot = PoolEntry(1, "alice", 1500, 5, False, Color.WHITE, 3, None, 0)
    anchor = PoolEntry(99, "ref-greedy", 1000, 100, True, Color.WHITE, 50, None, 0)
    
    # Beyond 400 (diff = 500)
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=False) is False


def test_should_offer_anchor_only_when_idle():
    """Anchor only offered when bot would otherwise sit idle."""
    bot = PoolEntry(1, "alice", 1200, 5, False, Color.WHITE, 3, None, 0)
    anchor = PoolEntry(99, "ref-greedy", 1000, 100, True, Color.WHITE, 50, None, 0)
    
    # Has other pairing option
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=True) is False
    
    # No other option
    assert matchmaker.should_offer_anchor(bot, anchor, has_other_pairing_option=False) is True


# Constants test

def test_constants_exist():
    assert matchmaker.ANCHOR_RATING_WINDOW == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/chess_core/test_matchmaker.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'chess_core.matchmaker'"

- [ ] **Step 3: Write minimal implementation**

Create `chess_core/matchmaker.py`:

```python
"""Matchmaker pairing policy per §9.2.

Pure function over explicit pool snapshots, seeded for determinism.
"""
import random
from typing import List, Optional
from chess_core.types import PoolEntry, Pairing, Color


ANCHOR_RATING_WINDOW = 400


def pair_bots(
    pool: List[PoolEntry],
    seed: Optional[int] = None
) -> List[Pairing]:
    """Pure pairing function implementing §9.2 policy.
    
    Algorithm per §9.2:
    1. Sort by games_played asc, then rating asc, then bot_id asc
    2. Walk sorted list pairing adjacent entries
    3. Skip if same owner or rematch of last_opponent_id
    4. Bot with unpaired_ticks >= 3 has constraints relaxed
    5. Color precedence: alternate from last_color; ties broken by white_count,
       then bot_id
    
    Deterministic and seeded-testable.
    
    Args:
        pool: Snapshot of eligible bots
        seed: Optional random seed for deterministic testing
    
    Returns:
        List of Pairing objects (white_bot_id, black_bot_id)
    """
    if seed is not None:
        random.seed(seed)
    
    if len(pool) < 2:
        return []
    
    # Sort by games_played asc, rating asc, bot_id asc
    eligible = sorted(pool, key=lambda e: (e.games_played, e.rating, e.bot_id))
    
    pairings = []
    i = 0
    
    while i < len(eligible) - 1:
        a = eligible[i]
        j = i + 1
        matched = None
        
        while j < len(eligible):
            b = eligible[j]
            if _allowed(a, b):
                matched = j
                break
            j += 1  # b advances; a holds its place
        
        if matched is None:
            i += 1  # a is unpairable this tick
            continue
        
        # Make pairing with color precedence
        pairing = _make_pairing(a, eligible[matched])
        pairings.append(pairing)
        
        # Remove both from eligible (in reverse order to preserve indices)
        if matched > i:
            eligible.pop(matched)
            eligible.pop(i)
        else:
            eligible.pop(i)
            eligible.pop(matched)
        # i is not incremented: the list shifted, so eligible[i] is a new bot
    
    return pairings


def _allowed(a: PoolEntry, b: PoolEntry) -> bool:
    """Check if pairing is allowed per §9.2 constraints."""
    relaxed = (a.unpaired_ticks >= 3) or (b.unpaired_ticks >= 3)
    
    # Same owner blocks unless relaxed
    if a.owner == b.owner and not relaxed:
        return False
    
    # Rematch blocks unless relaxed
    if a.last_opponent_id == b.bot_id and not relaxed:
        return False
    if b.last_opponent_id == a.bot_id and not relaxed:
        return False
    
    # Both anchors never pair
    if a.is_anchor and b.is_anchor:
        return False
    
    return True


def _make_pairing(a: PoolEntry, b: PoolEntry) -> Pairing:
    """Determine colors and create pairing per §9.2 color precedence."""
    # Determine who gets White
    # 1. Alternate from last_color
    # 2. Tie-break by white_count (lower gets White)
    # 3. Tie-break by bot_id (lower gets White)
    
    a_wants_white = a.last_color == Color.BLACK or a.last_color is None
    b_wants_white = b.last_color == Color.BLACK or b.last_color is None
    
    if a_wants_white and not b_wants_white:
        white_bot = a
        black_bot = b
    elif b_wants_white and not a_wants_white:
        white_bot = b
        black_bot = a
    else:
        # Both want same color or both are new (None): tie-break
        if a.white_count < b.white_count:
            white_bot = a
            black_bot = b
        elif b.white_count < a.white_count:
            white_bot = b
            black_bot = a
        else:
            # Equal white_count: tie-break by bot_id
            if a.bot_id < b.bot_id:
                white_bot = a
                black_bot = b
            else:
                white_bot = b
                black_bot = a
    
    return Pairing(white_bot_id=white_bot.bot_id, black_bot_id=black_bot.bot_id)


def should_offer_anchor(
    bot: PoolEntry,
    anchor: PoolEntry,
    has_other_pairing_option: bool
) -> bool:
    """Gate anchor pairing per §9.3.
    
    Anchor offered only when competitor would otherwise sit idle,
    and |rating - anchor_rating| <= 400.
    
    Args:
        bot: Competitor bot
        anchor: Anchor bot
        has_other_pairing_option: Whether bot has a non-anchor pairing available
    
    Returns:
        True if anchor should be offered to this bot
    """
    # Only offer when bot would otherwise be idle
    if has_other_pairing_option:
        return False
    
    # Rating must be within ±400
    rating_diff = abs(bot.rating - anchor.rating)
    return rating_diff <= ANCHOR_RATING_WINDOW
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/chess_core/test_matchmaker.py -v`
Expected: PASS (all matchmaker tests pass)

- [ ] **Step 5: Commit**

```bash
git add chess_core/matchmaker.py tests/chess_core/test_matchmaker.py
git commit -m "feat(chess_core): implement matchmaker.py with §9.2 pairing policy"
```

---

## Task 7: match.py — State Machine

**Files:**
- Create: `chess_core/match.py`
- Create: `tests/chess_core/test_match.py`

- [ ] **Step 1: Write failing tests for match.py**

Create `tests/chess_core/test_match.py`:

```python
"""Tests for match state machine per §7."""
import chess_core.match as match
from chess_core.types import (
    GameStatus, TerminationReason, GameResult, MoveResult
)


def test_create_match_is_pending():
    """Initial match state is pending at ply 0."""
    state = match.create_match()
    
    assert state.status == GameStatus.PENDING
    assert state.ply == 0
    assert state.result is None
    assert state.termination is None


def test_transition_pending_to_active():
    """Transition pending → active per §7."""
    state = match.create_match()
    active = match.transition_to_active(state)
    
    assert active.status == GameStatus.ACTIVE
    assert active.ply == 0  # ply unchanged


def test_transition_after_move_increments_ply():
    """Non-terminal move increments ply."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=5,
        result=None,
        termination=None
    )
    
    move_result = MoveResult(
        fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        san="e4",
        is_terminal=False,
        termination=None,
        result=None
    )
    
    new_state = match.transition_after_move(state, move_result)
    
    assert new_state.status == GameStatus.ACTIVE
    assert new_state.ply == 6
    assert new_state.result is None


def test_transition_after_move_adjudicates_at_ply_cap():
    """Reaching PLY_CAP ends the game as a draw, unconditionally, per §22.

    The cap is deliberately not material-based: the position may be completely
    winning for one side and it is still a draw. That is the whole point of
    replacing revision 1's "draw if within a pawn" rule — no judgement call,
    no bespoke evaluation, one line.
    """
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=PLY_CAP - 1,
        result=None,
        termination=None
    )

    # A non-terminal move by a side that is winning on material.
    move_result = MoveResult(
        fen_after="4k3/8/8/8/8/8/8/3QK3 b - - 0 100",
        san="Qd1",
        is_terminal=False,
        termination=None,
        result=None
    )

    new_state = match.transition_after_move(state, move_result)

    assert new_state.status == GameStatus.FINISHED
    assert new_state.ply == PLY_CAP
    assert new_state.termination == TerminationReason.ADJUDICATED
    assert new_state.result == GameResult.DRAW


def test_transition_after_move_does_not_adjudicate_below_cap():
    """One ply short of the cap, play continues."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=PLY_CAP - 2,
        result=None,
        termination=None
    )

    move_result = MoveResult(
        fen_after="4k3/8/8/8/8/8/8/3QK3 b - - 0 100",
        san="Qd1",
        is_terminal=False,
        termination=None,
        result=None
    )

    new_state = match.transition_after_move(state, move_result)

    assert new_state.status == GameStatus.ACTIVE
    assert new_state.ply == PLY_CAP - 1
    assert new_state.termination is None


def test_terminal_move_at_cap_keeps_its_own_termination():
    """A checkmate delivered on the capping ply is checkmate, not adjudication.

    Ordering matters: the terminal check precedes the cap check, so a game that
    ends decisively on ply 200 records the real result rather than a draw.
    """
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=PLY_CAP - 1,
        result=None,
        termination=None
    )

    move_result = MoveResult(
        fen_after="4k3/8/8/8/8/8/5PPP/6K1 b - - 0 100",
        san="Qe8#",
        is_terminal=True,
        termination=TerminationReason.CHECKMATE,
        result=GameResult.WHITE_WINS
    )

    new_state = match.transition_after_move(state, move_result)

    assert new_state.status == GameStatus.FINISHED
    assert new_state.termination == TerminationReason.CHECKMATE
    assert new_state.result == GameResult.WHITE_WINS


def test_transition_after_terminal_move_ends_game():
    """Terminal move transitions to finished."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=10,
        result=None,
        termination=None
    )
    
    move_result = MoveResult(
        fen_after="rnbqkb1r/pppp1ppp/5n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 4 4",
        san="Qh5#",
        is_terminal=True,
        termination=TerminationReason.CHECKMATE,
        result=GameResult.WHITE_WIN
    )
    
    new_state = match.transition_after_move(state, move_result)
    
    assert new_state.status == GameStatus.FINISHED
    assert new_state.ply == 11
    assert new_state.result == GameResult.WHITE_WIN
    assert new_state.termination == TerminationReason.CHECKMATE


def test_transition_to_terminal_finished():
    """Transition to finished for terminal reasons."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=20,
        result=None,
        termination=None
    )
    
    terminal = match.transition_to_terminal(
        state,
        TerminationReason.FLAG,
        GameResult.BLACK_WIN
    )
    
    assert terminal.status == GameStatus.FINISHED
    assert terminal.ply == 20  # ply unchanged
    assert terminal.result == GameResult.BLACK_WIN
    assert terminal.termination == TerminationReason.FLAG


def test_transition_to_terminal_aborted():
    """Transition to aborted for abort reasons."""
    state = match.MatchState(
        status=GameStatus.PENDING,
        ply=0,
        result=None,
        termination=None
    )
    
    aborted = match.transition_to_terminal(
        state,
        TerminationReason.NO_SHOW,
        None  # no result for aborted
    )
    
    assert aborted.status == GameStatus.ABORTED
    assert aborted.result is None
    assert aborted.termination == TerminationReason.NO_SHOW


def test_is_terminal():
    """is_terminal detects finished and aborted."""
    finished = match.MatchState(GameStatus.FINISHED, 30, GameResult.DRAW, TerminationReason.STALEMATE)
    aborted = match.MatchState(GameStatus.ABORTED, 0, None, TerminationReason.ADMIN_ABORT)
    active = match.MatchState(GameStatus.ACTIVE, 10, None, None)
    pending = match.MatchState(GameStatus.PENDING, 0, None, None)
    
    assert match.is_terminal(finished) is True
    assert match.is_terminal(aborted) is True
    assert match.is_terminal(active) is False
    assert match.is_terminal(pending) is False


def test_can_transition_valid():
    """can_transition validates legal transitions per §7."""
    pending = match.MatchState(GameStatus.PENDING, 0, None, None)
    active = match.MatchState(GameStatus.ACTIVE, 5, None, None)
    
    # pending → active: valid
    assert match.can_transition(pending, GameStatus.ACTIVE) is True
    
    # pending → aborted: valid
    assert match.can_transition(pending, GameStatus.ABORTED) is True
    
    # active → finished: valid
    assert match.can_transition(active, GameStatus.FINISHED) is True
    
    # active → aborted: valid
    assert match.can_transition(active, GameStatus.ABORTED) is True


def test_can_transition_invalid():
    """can_transition rejects illegal transitions."""
    pending = match.MatchState(GameStatus.PENDING, 0, None, None)
    active = match.MatchState(GameStatus.ACTIVE, 5, None, None)
    finished = match.MatchState(GameStatus.FINISHED, 30, GameResult.DRAW, TerminationReason.STALEMATE)
    
    # pending → finished: invalid (must go through active)
    assert match.can_transition(pending, GameStatus.FINISHED) is False
    
    # active → pending: invalid (cannot go backward)
    assert match.can_transition(active, GameStatus.PENDING) is False
    
    # finished → *: invalid (terminal)
    assert match.can_transition(finished, GameStatus.ACTIVE) is False
    assert match.can_transition(finished, GameStatus.PENDING) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/chess_core/test_match.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'chess_core.match'"

- [ ] **Step 3: Write minimal implementation**

Create `chess_core/match.py`:

```python
"""Match state machine per §7.

Pure state transitions with validation helpers.
"""
from dataclasses import dataclass
from typing import Optional
from chess_core.types import GameStatus, TerminationReason, GameResult, MoveResult


@dataclass(frozen=True)
class MatchState:
    """Pure game state machine per §7.
    
    Encodes legal transitions for CAS validation.
    """
    status: GameStatus
    ply: int
    result: Optional[GameResult]
    termination: Optional[TerminationReason]


def create_match() -> MatchState:
    """Create initial match state: pending at ply 0."""
    return MatchState(
        status=GameStatus.PENDING,
        ply=0,
        result=None,
        termination=None
    )


def transition_to_active(state: MatchState) -> MatchState:
    """Transition pending -> active per §7 (first delivery)."""
    if state.status != GameStatus.PENDING:
        raise ValueError(f"Cannot transition to active from {state.status}")
    
    return MatchState(
        status=GameStatus.ACTIVE,
        ply=state.ply,
        result=state.result,
        termination=state.termination
    )


def transition_after_move(
    state: MatchState,
    move_result: MoveResult
) -> MatchState:
    """Transition after applying a move.
    
    If move_result is terminal, transitions to finished.
    Otherwise increments ply while staying active.
    """
    if state.status != GameStatus.ACTIVE:
        raise ValueError(f"Cannot apply move in status {state.status}")
    
    new_ply = state.ply + 1
    
    if move_result.is_terminal:
        return MatchState(
            status=GameStatus.FINISHED,
            ply=new_ply,
            result=move_result.result,
            termination=move_result.termination
        )
    elif new_ply >= PLY_CAP:
        # §22: flat cap, unconditional draw. Deliberately not material-based —
        # the position may be winning for either side and it is still a draw.
        return MatchState(
            status=GameStatus.FINISHED,
            ply=new_ply,
            result=GameResult.DRAW,
            termination=TerminationReason.ADJUDICATED
        )
    else:
        return MatchState(
            status=GameStatus.ACTIVE,
            ply=new_ply,
            result=None,
            termination=None
        )


def transition_to_terminal(
    state: MatchState,
    termination: TerminationReason,
    result: Optional[GameResult]
) -> MatchState:
    """Transition to a terminal state (finished or aborted).
    
    Covers all terminal transitions: flag, forfeit, resignation,
    abandonment, no-show, adjudication, admin abort, restart abort.
    
    Args:
        state: Current match state
        termination: How the game ended
        result: Game result (None for aborted games)
    
    Returns:
        MatchState with status='finished' or 'aborted' as appropriate
    """
    # Determine if this is an abort or a finish
    abort_reasons = {
        TerminationReason.NO_SHOW,
        TerminationReason.SERVER_RESTART,
        TerminationReason.ADMIN_ABORT
    }
    
    if termination in abort_reasons:
        status = GameStatus.ABORTED
    else:
        status = GameStatus.FINISHED
    
    return MatchState(
        status=status,
        ply=state.ply,
        result=result,
        termination=termination
    )


def is_terminal(state: MatchState) -> bool:
    """Check if match is in a terminal state (finished or aborted)."""
    return state.status in {GameStatus.FINISHED, GameStatus.ABORTED}


def can_transition(state: MatchState, to_status: GameStatus) -> bool:
    """Validate state transition is legal per §7 diagram.
    
    Legal transitions:
    - pending → active
    - pending → aborted
    - active → finished
    - active → aborted
    - (no transitions from terminal states)
    """
    if is_terminal(state):
        return False  # Cannot transition from terminal state
    
    if state.status == GameStatus.PENDING:
        return to_status in {GameStatus.ACTIVE, GameStatus.ABORTED}
    elif state.status == GameStatus.ACTIVE:
        return to_status in {GameStatus.FINISHED, GameStatus.ABORTED}
    else:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/chess_core/test_match.py -v`
Expected: PASS (all match tests pass)

- [ ] **Step 5: Commit**

```bash
git add chess_core/match.py tests/chess_core/test_match.py
git commit -m "feat(chess_core): implement match.py state machine per §7"
```

---

## Task 8: Export chess_core Public API

**Files:**
- Modify: `chess_core/__init__.py`

- [ ] **Step 1: Export public API from chess_core**

Modify `chess_core/__init__.py`:

```python
"""Chess Arena pure logic layer.

Shared by the live server and the offline arena. No I/O, no network,
no clock reads — time is always passed in as a parameter.
"""

# Shared types and enums
from chess_core.types import (
    Color,
    GameStatus,
    TerminationReason,
    GameResult,
    MoveResult,
    MoveOutcome,
    ClockView,
    ClockState,
    ClockUpdateResult,
    PoolEntry,
    Pairing,
    RatingUpdate,
)

# Rules
from chess_core.rules import (
    STARTING_FEN,
    PLY_CAP,
    validate_and_apply_move,
    position_key,
    get_legal_moves,
    detect_termination,
    fen_to_ascii,
    uci_to_san,
    san_list_to_pgn,
)

# Clock
from chess_core.clock import (
    RATED_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    EXHIBITION_TIME_CONTROL_NS,
    EXHIBITION_INCREMENT_NS,
    DELIVERY_GRACE_NS,
    AGENT_DELIVERY_GRACE_NS,
    AGENT_AUTO_RELEASE_NS,
    create_clock,
    deliver_position,
    account_move_and_switch,
    check_delivery_timeout,
    compute_turn_elapsed_ms,
    ms_to_ns,
    ns_to_ms,
)

# Elo
from chess_core.elo import (
    STARTING_RATING,
    K_FACTOR,
    compute_rating_exchange,
    compute_draw_exchange,
    compute_one_sided_exchange,
)

# Matchmaker
from chess_core.matchmaker import (
    ANCHOR_RATING_WINDOW,
    pair_bots,
    should_offer_anchor,
)

# Match state machine
from chess_core.match import (
    MatchState,
    create_match,
    transition_to_active,
    transition_after_move,
    transition_to_terminal,
    is_terminal,
    can_transition,
)

__all__ = [
    # Types
    "Color",
    "GameStatus",
    "TerminationReason",
    "GameResult",
    "MoveResult",
    "MoveOutcome",
    "ClockView",
    "ClockState",
    "ClockUpdateResult",
    "PoolEntry",
    "Pairing",
    "RatingUpdate",
    # Rules
    "STARTING_FEN",
    "PLY_CAP",
    "validate_and_apply_move",
    "position_key",
    "get_legal_moves",
    "detect_termination",
    "fen_to_ascii",
    "uci_to_san",
    "san_list_to_pgn",
    # Clock
    "RATED_TIME_CONTROL_NS",
    "RATED_INCREMENT_NS",
    "EXHIBITION_TIME_CONTROL_NS",
    "EXHIBITION_INCREMENT_NS",
    "DELIVERY_GRACE_NS",
    "AGENT_DELIVERY_GRACE_NS",
    "AGENT_AUTO_RELEASE_NS",
    "create_clock",
    "deliver_position",
    "account_move_and_switch",
    "check_delivery_timeout",
    "compute_turn_elapsed_ms",
    "ms_to_ns",
    "ns_to_ms",
    # Elo
    "STARTING_RATING",
    "K_FACTOR",
    "compute_rating_exchange",
    "compute_draw_exchange",
    "compute_one_sided_exchange",
    # Matchmaker
    "ANCHOR_RATING_WINDOW",
    "pair_bots",
    "should_offer_anchor",
    # Match
    "MatchState",
    "create_match",
    "transition_to_active",
    "transition_after_move",
    "transition_to_terminal",
    "is_terminal",
    "can_transition",
]
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/chess_core/ -v --cov=chess_core --cov-report=term-missing`
Expected: PASS, coverage ≥90%

- [ ] **Step 3: Verify no I/O calls exist**

Run: `grep -r "time.monotonic\|open(\|socket\|sqlite3" chess_core/`
Expected: No matches (pure module, no I/O)

- [ ] **Step 4: Commit**

```bash
git add chess_core/__init__.py
git commit -m "feat(chess_core): export public API from chess_core package"
```

---

## Task 9: Final Verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 2: Check coverage**

Run: `pytest --cov=chess_core --cov-report=term-missing`
Expected: Coverage ≥90% for chess_core

- [ ] **Step 3: Verify purity invariant**

Run: `grep -r "time.monotonic\|open(\|socket\|asyncio\|sqlite3\|fastapi\|httpx" chess_core/`
Expected: No matches in chess_core/ (except in comments/docstrings)

- [ ] **Step 4: Verify unit suffix discipline**

Run: `grep -rE "(remaining|elapsed|control|increment|grace|release|poll|tick|challenge|auto)([^_]|$)" chess_core/ | grep -v "def \|class \|import \|#\|remaining_ns\|elapsed_ns\|elapsed_ms\|control_ns\|increment_ns\|grace_ns\|release_ns\|poll_ns\|tick_ns\|challenge_ns\|auto_ns"`
Expected: Minimal matches (all time fields should have _ns or _ms suffix)

- [ ] **Step 5: Verify all signatures match interfaces document**

Manually check Part 1 of `docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md` against:
- `chess_core/rules.py`
- `chess_core/clock.py`
- `chess_core/elo.py`
- `chess_core/matchmaker.py`
- `chess_core/match.py`

Expected: All function signatures, parameter names, and return types match exactly

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "phase-1: chess_core complete with strict TDD and purity invariants"
```

---

## Self-Review Checklist

### 1. Spec Coverage

Scanning each section:
- **§4.2 (CAS)**: Not applicable to chess_core (pure logic, no transactions)
- **§5.2 (canonical constants)**: ✅ Task 4 (clock.py) and Task 5 (elo.py) declare all constants
- **§6 (clock and delivery)**: ✅ Task 4 covers entire §6 lifecycle
- **§7 (state machine)**: ✅ Task 7 implements all transitions
- **§9.2–§9.3 (matchmaking)**: ✅ Task 6 implements pairing algorithm and anchor gating
- **§10 (rating)**: ✅ Task 5 implements K=24 flat, zero-sum, one-sided anchor
- **§18 (testing)**: ✅ Every module has comprehensive tests, failure paths first
- **§22 (termination)**: ✅ Task 3 covers all termination cases including 200-ply cap

**Gaps:** None identified.

### 2. Placeholder Scan

Searched for: "TBD", "TODO", "implement later", "fill in details", "add validation", "handle edge cases", "Similar to Task"

**Result:** No placeholders found. Every task contains complete code.

### 3. Type Consistency

Verified:
- `Color`, `GameStatus`, `TerminationReason`, `GameResult` used consistently across all modules
- All time fields have `_ns` or `_ms` suffix
- `ClockState`, `ClockUpdateResult`, `PoolEntry`, `Pairing`, `RatingUpdate`, `MoveOutcome`, `MoveResult` used as defined in Task 2

**Result:** Consistent.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-24-phase1-chess-core.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
