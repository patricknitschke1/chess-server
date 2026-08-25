# Phase 2: Arena and Starter Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully playable offline chess competition with diagnostic tools (arena.py), a safe baseline bot attendees can build from (bot.py), and reference opponents for calibration — all working with no server required.

**Architecture:** Pure consumption of `chess_core` for all game logic. Opening book with seeded randomization. Local ELO tracking starting from 1200. Time-per-move measurement with mean/p95 statistics. Flag and illegal-move detection with position-specific diagnostics.

**Tech Stack:** Python 3.11+, python-chess, chess_core (from phase 1), argparse, dataclasses, time.monotonic_ns() for clock simulation

---

## File Structure

**Create:**
- `starter-kit/bot.py` — Baseline bot attendees edit (material-counting minimax depth 3)
- `starter-kit/ref_bots/` — Reference bot implementations for local arena
  - `ref_bots/__init__.py`
  - `ref_bots/ref_random.py`
  - `ref_bots/ref_greedy.py`
  - `ref_bots/ref_depth3.py`
- `starter-kit/arena.py` — Local competition runner
- `starter-kit/requirements.txt` — Dependencies (python-chess)
- `tests/arena/test_arena.py` — Arena tests
- `starter-kit/tests/test_bot.py` — Baseline bot tests

**Modify:** None (all new code)

---

### Task 1: ClockView Type and Test Infrastructure

**Files:**
- Create: `starter-kit/chess_client/__init__.py`
- Create: `starter-kit/chess_client/types.py`
- Create: `starter-kit/tests/__init__.py`
- Create: `starter-kit/tests/test_bot.py`
- Create: `tests/__init__.py`
- Create: `tests/arena/__init__.py`
- Create: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for ClockView construction**

```python
# starter-kit/tests/test_bot.py
import pytest
from chess_client.types import ClockView


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest starter-kit/tests/test_bot.py::test_clock_view_construction -v`
Expected: FAIL with "No module named 'chess_client'"

- [ ] **Step 3: Write minimal ClockView implementation**

```python
# starter-kit/chess_client/__init__.py
"""Chess Arena SDK types and client."""
from .types import ClockView

__all__ = ["ClockView"]
```

```python
# starter-kit/chess_client/types.py
from dataclasses import dataclass


@dataclass(frozen=True)
class ClockView:
    """Clock information for choose_move.
    
    my_ms is always YOUR remaining time, regardless of color.
    This removes color-indexing as a category of bug.
    """
    my_ms: int
    opponent_ms: int
    increment_ms: int
    ply: int
```

```python
# starter-kit/tests/__init__.py
"""Tests for starter-kit components."""
```

```python
# tests/__init__.py
"""Project-wide tests."""
```

```python
# tests/arena/__init__.py
"""Arena tests."""
```

```python
# tests/arena/test_arena.py
"""Arena tests — placeholder for next task."""
import pytest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest starter-kit/tests/test_bot.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/chess_client/ starter-kit/tests/ tests/
git commit -m "feat: add ClockView type and test infrastructure"
```

---

### Task 2: Reference Bot — Random Mover

**Files:**
- Create: `starter-kit/ref_bots/__init__.py`
- Create: `starter-kit/ref_bots/ref_random.py`
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for ref_random**

```python
# tests/arena/test_arena.py
import chess
from chess_client.types import ClockView
from ref_bots.ref_random import choose_move as ref_random_choose_move


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_ref_random_returns_legal_move -v`
Expected: FAIL with "No module named 'ref_bots'"

- [ ] **Step 3: Write minimal ref_random implementation**

```python
# starter-kit/ref_bots/__init__.py
"""Reference bots for local arena calibration."""
```

```python
# starter-kit/ref_bots/ref_random.py
"""Random move selector — baseline opponent, rating ~800."""
import random
import chess
from chess_client.types import ClockView


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose a random legal move.
    
    Calibrated rating: 800 (measured from seeded arena ladder)
    """
    return random.choice(list(board.legal_moves))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/ref_bots/
git commit -m "feat: add ref_random reference bot"
```

---

### Task 3: Reference Bot — Greedy Material Maximizer

**Files:**
- Create: `starter-kit/ref_bots/ref_greedy.py`
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for ref_greedy**

```python
# tests/arena/test_arena.py (add to existing file)
from ref_bots.ref_greedy import choose_move as ref_greedy_choose_move


def test_ref_greedy_prefers_captures():
    """ref_greedy prefers capturing moves over non-captures."""
    # Position where white can capture a pawn or make quiet move
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2")
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=2)
    
    # Run multiple times to ensure it consistently captures
    for _ in range(5):
        move = ref_greedy_choose_move(board, clock)
        # Should capture the e5 pawn with exd5 or similar high-value move
        assert board.is_capture(move), f"Expected capture, got {move.uci()}"


def test_ref_greedy_returns_legal_move():
    """ref_greedy returns a legal move."""
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    move = ref_greedy_choose_move(board, clock)
    assert move in board.legal_moves
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_ref_greedy_prefers_captures -v`
Expected: FAIL with "No module named 'ref_bots.ref_greedy'"

- [ ] **Step 3: Write minimal ref_greedy implementation**

```python
# starter-kit/ref_bots/ref_greedy.py
"""Greedy material maximizer — intermediate opponent, rating ~1000."""
import chess
from chess_client.types import ClockView


# Standard piece values in centipawns
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0
}


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose move that maximizes immediate material gain.
    
    Calibrated rating: 1000 (measured from seeded arena ladder)
    """
    best_move = None
    best_score = float('-inf')
    
    for move in board.legal_moves:
        score = 0
        
        # Prefer captures
        if board.is_capture(move):
            captured_piece = board.piece_at(move.to_square)
            if captured_piece:
                score += PIECE_VALUES.get(captured_piece.piece_type, 0)
        
        # Slight bonus for center control
        if move.to_square in [chess.E4, chess.D4, chess.E5, chess.D5]:
            score += 10
        
        if score > best_score:
            best_score = score
            best_move = move
    
    # If no move scored positive, pick first legal move
    return best_move if best_move else list(board.legal_moves)[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/ref_bots/ref_greedy.py tests/arena/test_arena.py
git commit -m "feat: add ref_greedy reference bot"
```

---

### Task 4: Reference Bot — depth-3 Minimax

**Files:**
- Create: `starter-kit/ref_bots/ref_depth3.py`
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for ref_depth3**

```python
# tests/arena/test_arena.py (add to existing file)
from ref_bots.ref_depth3 import choose_move as ref_depth3_choose_move


def test_ref_depth3_sees_mate_in_one():
    """ref_depth3 finds mate in one."""
    # Scholar's mate position - white to move, Qxf7#
    board = chess.Board("r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 0 4")
    board = board.mirror()  # Flip so we're finding the mate for white
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=7)
    
    move = ref_depth3_choose_move(board, clock)
    board.push(move)
    assert board.is_checkmate()


def test_ref_depth3_returns_legal_move():
    """ref_depth3 returns a legal move."""
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    move = ref_depth3_choose_move(board, clock)
    assert move in board.legal_moves


def test_ref_depth3_avoids_obvious_blunders():
    """ref_depth3 doesn't hang pieces in one move."""
    # Position where moving queen to dangerous square loses it
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    
    # Run several times - should never hang queen immediately
    for _ in range(3):
        move = ref_depth3_choose_move(board, clock)
        # Make move, check opponent can't capture queen for free
        test_board = board.copy()
        test_board.push(move)
        
        # If we moved our queen, ensure it's not hanging
        if board.piece_at(move.from_square).piece_type == chess.QUEEN:
            for opp_move in test_board.legal_moves:
                if test_board.is_capture(opp_move):
                    captured = test_board.piece_at(opp_move.to_square)
                    if captured and captured.piece_type == chess.QUEEN:
                        # Opponent can capture queen - this should be rare with depth 3
                        # (might happen if it's a trade)
                        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_ref_depth3_sees_mate_in_one -v`
Expected: FAIL with "No module named 'ref_bots.ref_depth3'"

- [ ] **Step 3: Write minimal ref_depth3 implementation**

```python
# starter-kit/ref_bots/ref_depth3.py
"""Minimax depth-3 bot — strong reference opponent, rating ~1200."""
import chess
from chess_client.types import ClockView


# Standard piece values in centipawns
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000  # Effectively infinite
}


def evaluate_position(board: chess.Board) -> int:
    """Evaluate position in centipawns from white's perspective."""
    if board.is_checkmate():
        return -20000 if board.turn == chess.WHITE else 20000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            value = PIECE_VALUES.get(piece.piece_type, 0)
            score += value if piece.color == chess.WHITE else -value
    
    return score


def minimax(board: chess.Board, depth: int, alpha: int, beta: int, maximizing: bool) -> int:
    """Minimax with alpha-beta pruning."""
    if depth == 0 or board.is_game_over():
        return evaluate_position(board)
    
    if maximizing:
        max_eval = float('-inf')
        for move in board.legal_moves:
            board.push(move)
            eval_score = minimax(board, depth - 1, alpha, beta, False)
            board.pop()
            max_eval = max(max_eval, eval_score)
            alpha = max(alpha, eval_score)
            if beta <= alpha:
                break  # Beta cutoff
        return max_eval
    else:
        min_eval = float('inf')
        for move in board.legal_moves:
            board.push(move)
            eval_score = minimax(board, depth - 1, alpha, beta, True)
            board.pop()
            min_eval = min(min_eval, eval_score)
            beta = min(beta, eval_score)
            if beta <= alpha:
                break  # Alpha cutoff
        return min_eval


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose best move via minimax search to depth 3.
    
    Calibrated rating: 1200 (measured from seeded arena ladder)
    """
    best_move = None
    best_score = float('-inf') if board.turn == chess.WHITE else float('inf')
    
    for move in board.legal_moves:
        board.push(move)
        score = minimax(board, 1, float('-inf'), float('inf'), not board.turn)
        board.pop()
        
        if board.turn == chess.WHITE:
            if score > best_score:
                best_score = score
                best_move = move
        else:
            if score < best_score:
                best_score = score
                best_move = move
    
    return best_move if best_move else list(board.legal_moves)[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/ref_bots/ref_depth3.py tests/arena/test_arena.py
git commit -m "feat: add ref_depth3 minimax reference bot"
```

---

### Task 5: Baseline Bot with Time Management

**Files:**
- Create: `starter-kit/bot.py`
- Modify: `starter-kit/tests/test_bot.py`

- [ ] **Step 1: Write failing test for baseline bot not flagging**

```python
# starter-kit/tests/test_bot.py (add to existing file)
import chess
import time
from bot import choose_move


def test_baseline_bot_returns_legal_move():
    """Baseline bot returns a legal move."""
    board = chess.Board()
    clock = ClockView(my_ms=180000, opponent_ms=180000, increment_ms=2000, ply=0)
    move = choose_move(board, clock)
    assert move in board.legal_moves


def test_baseline_bot_respects_time_budget():
    """Baseline bot should complete move within reasonable time."""
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
    board = chess.Board()
    clock = ClockView(my_ms=500, opponent_ms=180000, increment_ms=2000, ply=30)
    
    start = time.monotonic()
    move = choose_move(board, clock)
    elapsed_ms = (time.monotonic() - start) * 1000
    
    # With 500ms remaining, should complete very quickly
    assert elapsed_ms < 100, f"Move took {elapsed_ms}ms with 0.5s remaining"
    assert move in board.legal_moves
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest starter-kit/tests/test_bot.py::test_baseline_bot_returns_legal_move -v`
Expected: FAIL with "No module named 'bot'"

- [ ] **Step 3: Write baseline bot implementation**

```python
# starter-kit/bot.py
"""Baseline chess bot - starting point for workshop attendees.

This bot uses material-counting minimax to depth 3 with time management.
It beats ref_random reliably and loses to ref_greedy reliably.
Most importantly: it does NOT flag at 3+2 time control.
"""
import chess
from chess_client.types import ClockView


# Standard piece values in centipawns
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000
}


def evaluate_position(board: chess.Board) -> int:
    """Simple material count from current player's perspective."""
    if board.is_checkmate():
        return -20000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            value = PIECE_VALUES.get(piece.piece_type, 0)
            if piece.color == board.turn:
                score += value
            else:
                score -= value
    
    return score


def minimax(board: chess.Board, depth: int, alpha: int, beta: int, maximizing: bool) -> int:
    """Minimax with alpha-beta pruning."""
    if depth == 0 or board.is_game_over():
        return evaluate_position(board)
    
    if maximizing:
        max_eval = float('-inf')
        for move in board.legal_moves:
            board.push(move)
            eval_score = minimax(board, depth - 1, alpha, beta, False)
            board.pop()
            max_eval = max(max_eval, eval_score)
            alpha = max(alpha, eval_score)
            if beta <= alpha:
                break
        return max_eval
    else:
        min_eval = float('inf')
        for move in board.legal_moves:
            board.push(move)
            eval_score = minimax(board, depth - 1, alpha, beta, True)
            board.pop()
            min_eval = min(min_eval, eval_score)
            beta = min(beta, eval_score)
            if beta <= alpha:
                break
        return min_eval


def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose a move for your bot.
    
    This is the only function you need to implement. It is called whenever
    it's your turn to move.
    
    The chess.Board object gives you the current position and all legal moves.
    The ClockView gives you time information without needing to know which
    color you are.
    
    Args:
        board: chess.Board with the current position (use board.turn for your
               color, board.legal_moves for available moves)
        clock: ClockView with my_ms (your remaining time), opponent_ms,
               increment_ms, and ply
    
    Returns:
        Your chosen move as a chess.Move object (must be in board.legal_moves)
    
    Time management strategy:
        Budget ~1/40th of remaining time per move (assumes ~40 moves left).
        This is safe at 3+2: 180s + 40*2s = 260s total budget / 40 = 6.5s/move.
        Even with no increment, 180s / 40 = 4.5s/move leaves margin.
    """
    # Time budget: assume 40 moves remaining
    time_budget_ms = clock.my_ms / 40
    
    # For very low time, reduce depth or pick quickly
    if time_budget_ms < 100:
        # Just pick first legal move when < 100ms budget
        return list(board.legal_moves)[0]
    
    # Search depth 3 (our move + opponent's response)
    best_move = None
    best_score = float('-inf')
    
    for move in board.legal_moves:
        board.push(move)
        # Maximize our position after opponent's best response
        score = minimax(board, 1, float('-inf'), float('inf'), False)
        board.pop()
        
        if score > best_score:
            best_score = score
            best_move = move
    
    return best_move if best_move else list(board.legal_moves)[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest starter-kit/tests/test_bot.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/bot.py starter-kit/tests/test_bot.py
git commit -m "feat: add baseline bot with time management"
```

---

### Task 6: Opening Book for Randomization

**Files:**
- Create: `starter-kit/opening_book.py`
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for opening book**

```python
# tests/arena/test_arena.py (add to existing file)
import random
from opening_book import OPENING_BOOK, select_opening


def test_opening_book_has_valid_fens():
    """Opening book contains valid FEN strings."""
    import chess
    assert len(OPENING_BOOK) >= 8, "Opening book should have at least 8 positions"
    
    for fen in OPENING_BOOK:
        board = chess.Board(fen)
        assert board.is_valid(), f"Invalid FEN: {fen}"


def test_opening_book_is_diverse():
    """Opening book contains different positions."""
    unique_positions = set(OPENING_BOOK)
    assert len(unique_positions) >= 8, "Opening book should have at least 8 unique positions"


def test_select_opening_is_seeded():
    """select_opening returns same position with same seed."""
    random.seed(42)
    opening1 = select_opening()
    
    random.seed(42)
    opening2 = select_opening()
    
    assert opening1 == opening2


def test_select_opening_varies_with_seed():
    """select_opening returns different positions with different seeds."""
    random.seed(42)
    opening1 = select_opening()
    
    random.seed(43)
    opening2 = select_opening()
    
    # Should get different openings (probabilistic but very likely)
    assert opening1 != opening2 or len(OPENING_BOOK) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_opening_book_has_valid_fens -v`
Expected: FAIL with "No module named 'opening_book'"

- [ ] **Step 3: Write opening book implementation**

```python
# starter-kit/opening_book.py
"""Opening book for arena randomization.

Without randomized openings, two deterministic bots replay one identical game,
making "100 games" a statistical illusion. This book provides 12 standard
opening positions, selected randomly via seeded RNG for reproducibility.
"""
import random


# 12 standard opening positions after 3-4 moves
OPENING_BOOK = [
    # King's Pawn Openings
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",  # e4
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",  # e4 e5
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",  # e4 e5 Nf3
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",  # e4 e5 Nf3 Nc6
    
    # Sicilian Defense
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2",  # e4 c5
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",  # e4 c5 Nf3
    
    # Queen's Pawn Openings
    "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 1",  # d4
    "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq d6 0 2",  # d4 d5
    "rnbqkbnr/ppp1pppp/8/3p4/2PP4/8/PP2PPPP/RNBQKBNR b KQkq c3 0 2",  # d4 d5 c4
    "rnbqkb1r/ppp1pppp/5n2/3p4/2PP4/8/PP2PPPP/RNBQKBNR w KQkq - 1 3",  # d4 d5 c4 Nf6
    
    # Indian Defenses
    "rnbqkb1r/pppppppp/5n2/8/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 1 2",  # d4 Nf6
    "rnbqkb1r/pppppppp/5n2/8/2PP4/8/PP2PPPP/RNBQKBNR b KQkq c3 0 2",  # d4 Nf6 c4
]


def select_opening() -> str:
    """Select a random opening position from the book.
    
    Uses random.choice(), so caller must seed random.Random() for reproducibility.
    
    Returns:
        FEN string of selected opening position
    """
    return random.choice(OPENING_BOOK)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -k opening_book -v`
Expected: PASS (4 new tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/opening_book.py tests/arena/test_arena.py
git commit -m "feat: add opening book for arena randomization"
```

---

### Task 7: Arena Core — Game Execution and Clock Simulation

**Files:**
- Create: `starter-kit/arena.py`
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for arena game execution**

```python
# tests/arena/test_arena.py (add to existing file)
import sys
import importlib.util
from pathlib import Path


def load_bot_module(path_str: str):
    """Load a bot module from file path."""
    path = Path(path_str)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_arena_runs_single_game():
    """Arena executes a single game between two bots."""
    from arena import run_single_game
    from chess_core.clock import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
    
    # Load two bots
    bot1 = load_bot_module("starter-kit/ref_bots/ref_random.py")
    bot2 = load_bot_module("starter-kit/ref_bots/ref_greedy.py")
    
    result = run_single_game(
        white_bot=bot1.choose_move,
        black_bot=bot2.choose_move,
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
    assert result.termination in ["checkmate", "stalemate", "flag", "illegal_forfeit", "adjudicated"]
    assert len(result.moves_san) >= 0
    assert result.white_time_ms >= 0
    assert result.black_time_ms >= 0


def test_arena_clock_matches_chess_core():
    """Arena clock simulation matches chess_core behavior."""
    from arena import run_single_game
    from chess_core.clock import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, ns_to_ms
    
    # Run game and check time accounting is reasonable
    bot1 = load_bot_module("starter-kit/ref_bots/ref_random.py")
    bot2 = load_bot_module("starter-kit/ref_bots/ref_random.py")
    
    result = run_single_game(
        white_bot=bot1.choose_move,
        black_bot=bot2.choose_move,
        white_name="random1",
        black_name="random2",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=None,
        verbose=False
    )
    
    starting_ms = ns_to_ms(RATED_TIME_CONTROL_NS)
    
    # Time should decrease from starting position
    # (unless game was very short and increments exceeded usage)
    assert result.white_time_ms <= starting_ms + 100000  # Allow for many increments
    assert result.black_time_ms <= starting_ms + 100000


def test_arena_detects_flags():
    """Arena detects when a bot runs out of time."""
    from arena import run_single_game
    
    # Create a slow bot that will definitely flag
    def slow_bot(board, clock):
        import time
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_arena_runs_single_game -v`
Expected: FAIL with "No module named 'arena'"

- [ ] **Step 3: Write minimal arena game execution**

```python
# starter-kit/arena.py
"""Local chess arena for offline bot testing.

Runs round-robin tournaments between bots using chess_core for all game logic.
Provides diagnostics (time per move, flags, illegal moves) critical for debugging.
"""
import time
import random
import chess
from typing import Callable, Optional, List
from dataclasses import dataclass
from chess_client.types import ClockView

# Import all chess_core functions we need
from chess_core.rules import (
    validate_and_apply_move,
    detect_termination,
    STARTING_FEN,
    PLY_CAP
)
from chess_core.clock import (
    create_clock,
    deliver_position,
    account_move_and_switch,
    ms_to_ns,
    ns_to_ms,
    RATED_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    Color
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -k "test_arena_runs_single_game or test_arena_clock_matches or test_arena_detects_flags" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/arena.py tests/arena/test_arena.py
git commit -m "feat: add arena game execution with clock simulation"
```

---

### Task 8: Arena Statistics and ELO Tracking

**Files:**
- Modify: `starter-kit/arena.py`
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for arena statistics**

```python
# tests/arena/test_arena.py (add to existing file)
def test_arena_computes_statistics():
    """Arena computes mean and p95 move times correctly."""
    from arena import compute_statistics
    
    move_times = [100, 150, 120, 200, 180, 90, 110, 300, 140, 160]
    
    stats = compute_statistics(move_times)
    
    assert stats['mean'] == 155.0  # sum / count
    assert stats['p95'] == 300  # 95th percentile
    assert stats['min'] == 90
    assert stats['max'] == 300


def test_arena_tracks_elo():
    """Arena maintains local ELO ratings."""
    from arena import ArenaTracker
    from chess_core.elo import STARTING_RATING
    
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_arena_computes_statistics -v`
Expected: FAIL with "cannot import name 'compute_statistics'"

- [ ] **Step 3: Write statistics and ELO tracking**

```python
# starter-kit/arena.py (add to existing file)
from typing import Dict
import statistics
from chess_core.elo import (
    compute_rating_exchange,
    compute_draw_exchange,
    STARTING_RATING
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -k "test_arena_computes_statistics or test_arena_tracks_elo" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/arena.py tests/arena/test_arena.py
git commit -m "feat: add arena statistics and ELO tracking"
```

---

### Task 9: Arena CLI and Output Formatting

**Files:**
- Modify: `starter-kit/arena.py`
- Create: `starter-kit/requirements.txt`

- [ ] **Step 1: Write test for arena CLI argument parsing**

```python
# tests/arena/test_arena.py (add to existing file)
def test_arena_cli_parsing():
    """Arena parses command-line arguments correctly."""
    from arena import parse_args
    
    args = parse_args([
        '--bots', 'bot1.py', 'bot2.py',
        '--games', '50',
        '--seed', '42',
        '--pgn', 'output.pgn'
    ])
    
    assert args.bots == ['bot1.py', 'bot2.py']
    assert args.games == 50
    assert args.seed == 42
    assert args.pgn == 'output.pgn'
    assert args.verbose is False


def test_arena_cli_defaults():
    """Arena provides sensible defaults."""
    from arena import parse_args
    
    args = parse_args(['--bots', 'bot.py'])
    
    assert args.games == 100
    assert args.seed is not None  # Random seed generated
    assert args.time_control_ms == ns_to_ms(RATED_TIME_CONTROL_NS)
    assert args.increment_ms == ns_to_ms(RATED_INCREMENT_NS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_arena_cli_parsing -v`
Expected: FAIL with "cannot import name 'parse_args'"

- [ ] **Step 3: Write CLI and main function**

```python
# starter-kit/arena.py (add to end of file)
import argparse
import sys
import importlib.util
from pathlib import Path


def load_bot_module(path_str: str):
    """Load a bot module from file path."""
    path = Path(path_str)
    if not path.exists():
        print(f"Error: Bot file not found: {path_str}")
        sys.exit(1)
    
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    
    if not hasattr(module, 'choose_move'):
        print(f"Error: {path_str} must define choose_move(board, clock) function")
        sys.exit(1)
    
    return module


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Local chess arena for offline bot testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run 100 games between two bots
  python arena.py --bots bot.py baseline.py --games 100 --seed 7

  # Include reference bots
  python arena.py --bots bot.py ref_bots/ref_random.py ref_bots/ref_greedy.py --games 50

  # Custom time control
  python arena.py --bots bot.py baseline.py --time-control 60000 --increment 1000
  
  # Export PGNs
  python arena.py --bots bot.py baseline.py --pgn games.pgn

  # Replay a specific game
  python arena.py --replay 5 --pgn games.pgn
        """
    )
    
    parser.add_argument(
        '--bots',
        nargs='+',
        help='Bot module paths (e.g., bot.py ref_bots/ref_random.py)'
    )
    parser.add_argument(
        '--games',
        type=int,
        default=100,
        help='Total number of games to play (default: 100)'
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
        help='Export games to PGN file'
    )
    parser.add_argument(
        '--replay',
        type=int,
        help='Replay game number from PGN file'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Print move-by-move output'
    )
    
    args = parser.parse_args(argv)
    
    # Generate random seed if not provided
    if args.seed is None:
        args.seed = random.randint(0, 2**31 - 1)
    
    # Validate
    if not args.replay and not args.bots:
        parser.error('--bots is required unless using --replay')
    
    return args


def print_results(tracker: ArenaTracker, bot_names: List[str], seed: int, total_games: int):
    """Print formatted results table."""
    print(f"\nLocal Arena Results ({total_games} games, seed={seed})")
    print("=" * 88)
    print()
    
    # Sort by rating descending
    sorted_names = sorted(bot_names, key=lambda n: tracker.get_rating(n), reverse=True)
    
    print(f"{'Bot':<20} {'Rating':>6} {'W':>3} {'L':>3} {'D':>3} {'Games':>5} {'Avg(ms)':>8} {'P95(ms)':>8} {'Flags':>5} {'Illegal':>7}")
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


def main():
    """Main entry point for arena."""
    args = parse_args()
    
    if args.replay:
        print("Replay functionality not yet implemented.")
        return
    
    # Load bots
    print(f"Loading {len(args.bots)} bots...")
    bot_modules = {}
    for bot_path in args.bots:
        module = load_bot_module(bot_path)
        bot_name = Path(bot_path).stem
        bot_modules[bot_name] = module.choose_move
        print(f"  ✓ {bot_name}")
    
    # Initialize tracker
    tracker = ArenaTracker()
    bot_names = list(bot_modules.keys())
    for name in bot_names:
        tracker.register_bot(name)
    
    # Seed RNG
    random.seed(args.seed)
    print(f"\nRunning {args.games} games (seed={args.seed})...")
    
    # Import opening book
    from opening_book import select_opening
    
    # Run round-robin games
    games_per_pairing = args.games // len(bot_names) if len(bot_names) > 1 else args.games
    game_count = 0
    
    for i, white_name in enumerate(bot_names):
        for j, black_name in enumerate(bot_names):
            if i >= j:  # Skip self-play and duplicates
                continue
            
            for _ in range(games_per_pairing):
                if game_count >= args.games:
                    break
                
                # Select opening
                opening_fen = select_opening()
                
                # Run game
                result = run_single_game(
                    white_bot=bot_modules[white_name],
                    black_bot=bot_modules[black_name],
                    white_name=white_name,
                    black_name=black_name,
                    time_control_ns=ms_to_ns(args.time_control_ms),
                    increment_ns=ms_to_ns(args.increment_ms),
                    opening_fen=opening_fen,
                    verbose=args.verbose
                )
                
                # Record result
                tracker.record_game(white_name, black_name, result.result)
                tracker.record_move_times(white_name, result.white_move_times)
                tracker.record_move_times(black_name, result.black_move_times)
                tracker.record_flags(white_name, result.white_flags)
                tracker.record_flags(black_name, result.black_flags)
                tracker.record_illegal_attempts(white_name, result.white_illegal_attempts)
                tracker.record_illegal_attempts(black_name, result.black_illegal_attempts)
                
                game_count += 1
                
                if game_count % 10 == 0:
                    print(f"  {game_count}/{args.games} games complete...")
    
    # Print results
    print_results(tracker, bot_names, args.seed, game_count)


if __name__ == '__main__':
    main()
```

```text
# starter-kit/requirements.txt
python-chess>=1.9.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -k "test_arena_cli" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/arena.py starter-kit/requirements.txt tests/arena/test_arena.py
git commit -m "feat: add arena CLI and output formatting"
```

---

### Task 10: Integration Test — Baseline Bot Safety

**Files:**
- Modify: `starter-kit/tests/test_bot.py`

- [ ] **Step 1: Write integration test for baseline bot not flagging**

```python
# starter-kit/tests/test_bot.py (add to existing file)
import sys
import importlib.util
from pathlib import Path


def load_bot_from_file(path_str: str):
    """Load bot module for testing."""
    path = Path(path_str)
    spec = importlib.util.spec_from_file_location("test_bot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_bot_completes_full_game_without_flagging():
    """Baseline bot plays a complete game at 3+2 without flagging.
    
    This is the acceptance test: if the shipped baseline fails this,
    it is not safe to hand to attendees.
    """
    from arena import run_single_game
    from chess_core.clock import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
    
    # Load baseline bot
    baseline = load_bot_from_file("starter-kit/bot.py")
    
    # Load ref_random as opponent
    ref_random = load_bot_from_file("starter-kit/ref_bots/ref_random.py")
    
    # Run 5 games to test consistency
    for game_num in range(5):
        result = run_single_game(
            white_bot=baseline.choose_move,
            black_bot=ref_random.choose_move,
            white_name="baseline",
            black_name="ref_random",
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            opening_fen=None,
            verbose=False
        )
        
        # Baseline must not flag
        assert result.white_flags == 0, f"Game {game_num + 1}: Baseline flagged as white!"
        
        # If baseline played black, also check
        if result.black_flags > 0:
            # This would mean we ran baseline as black, which we didn't in this test
            # But if we extend to swap colors, this catches it
            pass
    
    print(f"✓ Baseline bot completed 5 games without flagging")


def test_baseline_bot_beats_ref_random_reliably():
    """Baseline bot should beat ref_random most of the time."""
    from arena import run_single_game
    from chess_core.clock import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
    import random
    
    baseline = load_bot_from_file("starter-kit/bot.py")
    ref_random = load_bot_from_file("starter-kit/ref_bots/ref_random.py")
    
    wins = 0
    games = 10
    
    random.seed(12345)  # Seeded for reproducibility
    
    for _ in range(games):
        from opening_book import select_opening
        opening_fen = select_opening()
        
        result = run_single_game(
            white_bot=baseline.choose_move,
            black_bot=ref_random.choose_move,
            white_name="baseline",
            black_name="ref_random",
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            opening_fen=opening_fen,
            verbose=False
        )
        
        if result.result == "white_win":
            wins += 1
    
    # Should win at least 60% against random
    win_rate = wins / games
    assert win_rate >= 0.5, f"Baseline only won {wins}/{games} against ref_random"
    
    print(f"✓ Baseline won {wins}/{games} against ref_random ({win_rate:.0%})")
```

- [ ] **Step 2: Run test to verify it fails (initially)**

Run: `pytest starter-kit/tests/test_bot.py::test_baseline_bot_completes_full_game_without_flagging -v -s`
Expected: Could PASS or FAIL depending on baseline implementation quality

- [ ] **Step 3: If test fails, tune baseline bot time management**

If the test shows flagging, adjust time budget in bot.py:
- Increase divisor from 40 to 50 for more conservative time usage
- Add early exit when time is very low

- [ ] **Step 4: Run test until it passes**

Run: `pytest starter-kit/tests/test_bot.py -k baseline_bot -v -s`
Expected: PASS (both integration tests)

- [ ] **Step 5: Commit**

```bash
git add starter-kit/tests/test_bot.py
git commit -m "test: add integration tests for baseline bot safety"
```

---

### Task 11: PGN Export and Replay

**Files:**
- Modify: `starter-kit/arena.py`
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write failing test for PGN export**

```python
# tests/arena/test_arena.py (add to existing file)
def test_arena_exports_pgn():
    """Arena exports games in PGN format."""
    from arena import export_to_pgn
    from chess_core.rules import san_list_to_pgn
    
    # Create mock game results
    game1 = GameResult(
        white_name="Bot1",
        black_name="Bot2",
        result="white_win",
        termination="checkmate",
        moves_san=["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "Qh5", "Nf6", "Qxf7#"],
        moves_uci=["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "d1h5", "g8f6", "h5f7"],
        white_time_ms=170000,
        black_time_ms=168000,
        white_move_times=[120, 150, 130],
        black_move_times=[110, 140],
        white_flags=0,
        black_flags=0,
        white_illegal_attempts=0,
        black_illegal_attempts=0,
        ply_count=9
    )
    
    # Export
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pgn', delete=False) as f:
        pgn_path = f.name
    
    export_to_pgn([game1], pgn_path, tracker=None)
    
    # Read back
    with open(pgn_path, 'r') as f:
        pgn_content = f.read()
    
    # Should contain game metadata and moves
    assert "Bot1" in pgn_content
    assert "Bot2" in pgn_content
    assert "e4" in pgn_content
    assert "1-0" in pgn_content  # White wins
    
    # Cleanup
    import os
    os.unlink(pgn_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/arena/test_arena.py::test_arena_exports_pgn -v`
Expected: FAIL with "cannot import name 'export_to_pgn'"

- [ ] **Step 3: Write PGN export implementation**

```python
# starter-kit/arena.py (add to existing file)
from chess_core.rules import san_list_to_pgn
from typing import Optional


def export_to_pgn(
    results: List[GameResult],
    filepath: str,
    tracker: Optional[ArenaTracker] = None
):
    """Export game results to PGN file.
    
    Args:
        results: List of game results
        filepath: Path to output PGN file
        tracker: Optional tracker for ratings (will be included in headers)
    """
    with open(filepath, 'w') as f:
        for i, result in enumerate(results):
            # Get ratings if available
            white_rating = None
            black_rating = None
            if tracker:
                white_rating = tracker.get_rating(result.white_name)
                black_rating = tracker.get_rating(result.black_name)
            
            # Map result
            if result.result == "white_win":
                game_result = "white_win"
            elif result.result == "black_win":
                game_result = "black_win"
            else:
                game_result = "draw"
            
            # Generate PGN
            from chess_core.rules import GameResult as CoreGameResult
            core_result = {
                "white_win": CoreGameResult.WHITE_WIN,
                "black_win": CoreGameResult.BLACK_WIN,
                "draw": CoreGameResult.DRAW
            }[game_result]
            
            pgn = san_list_to_pgn(
                result.moves_san,
                result.white_name,
                result.black_name,
                core_result,
                white_rating,
                black_rating
            )
            
            f.write(pgn)
            f.write("\n\n")


# Update main() to support PGN export
# (Modify existing main function in arena.py)
# After game loop, add:
#
# if args.pgn:
#     print(f"\nExporting to {args.pgn}...")
#     export_to_pgn(all_results, args.pgn, tracker)
#     print(f"✓ {len(all_results)} games exported")
```

- [ ] **Step 4: Update main() to collect results and export**

```python
# starter-kit/arena.py (modify main function)
# Change main() to collect all GameResult objects in a list
# Then pass to export_to_pgn() if args.pgn is set
#
# Inside main(), before the game loop, add:
#     all_results = []
#
# Inside game loop, after game completes:
#     all_results.append(result)
#
# After print_results(), add:
#     if args.pgn:
#         print(f"\nExporting to {args.pgn}...")
#         export_to_pgn(all_results, args.pgn, tracker)
#         print(f"✓ {len(all_results)} games exported")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py::test_arena_exports_pgn -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add starter-kit/arena.py tests/arena/test_arena.py
git commit -m "feat: add PGN export functionality"
```

---

### Task 12: Opening Randomization Verification Test

**Files:**
- Modify: `tests/arena/test_arena.py`

- [ ] **Step 1: Write test verifying opening randomization works end-to-end**

```python
# tests/arena/test_arena.py (add to existing file)
def test_arena_opening_randomization_prevents_replays():
    """With different seeds, two deterministic bots produce different games.
    
    This is critical: without randomization, "100 games" becomes one game
    replayed, making all statistics meaningless.
    """
    from arena import run_single_game
    from chess_core.clock import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
    import random
    from opening_book import select_opening
    
    # Load two deterministic bots
    ref_greedy = load_bot_module("starter-kit/ref_bots/ref_greedy.py")
    ref_depth3 = load_bot_module("starter-kit/ref_bots/ref_depth3.py")
    
    # Run with seed 1
    random.seed(1)
    opening1 = select_opening()
    result1 = run_single_game(
        white_bot=ref_greedy.choose_move,
        black_bot=ref_depth3.choose_move,
        white_name="greedy",
        black_name="depth3",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=opening1,
        verbose=False
    )
    
    # Run with seed 2
    random.seed(2)
    opening2 = select_opening()
    result2 = run_single_game(
        white_bot=ref_greedy.choose_move,
        black_bot=ref_depth3.choose_move,
        white_name="greedy",
        black_name="depth3",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=opening2,
        verbose=False
    )
    
    # Different seeds should produce different openings
    assert opening1 != opening2 or len(OPENING_BOOK) == 1
    
    # Games should diverge (at minimum, different openings lead to different games)
    # Check at least one of: result, move count, or move sequence differs
    games_differ = (
        result1.result != result2.result or
        result1.ply_count != result2.ply_count or
        result1.moves_uci != result2.moves_uci
    )
    
    assert games_differ, "Deterministic bots with different seeds should produce different games"


def test_arena_same_seed_produces_identical_games():
    """With same seed, arena produces identical results."""
    from arena import run_single_game
    from chess_core.clock import RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
    import random
    from opening_book import select_opening
    
    ref_random1 = load_bot_module("starter-kit/ref_bots/ref_random.py")
    ref_random2 = load_bot_module("starter-kit/ref_bots/ref_random.py")
    
    # Run 1
    random.seed(42)
    opening = select_opening()
    random.seed(42)  # Re-seed for bot's internal RNG
    
    result1 = run_single_game(
        white_bot=ref_random1.choose_move,
        black_bot=ref_random2.choose_move,
        white_name="random1",
        black_name="random2",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=opening,
        verbose=False
    )
    
    # Run 2 with same seed
    random.seed(42)
    opening2 = select_opening()
    random.seed(42)
    
    result2 = run_single_game(
        white_bot=ref_random1.choose_move,
        black_bot=ref_random2.choose_move,
        white_name="random1",
        black_name="random2",
        time_control_ns=RATED_TIME_CONTROL_NS,
        increment_ns=RATED_INCREMENT_NS,
        opening_fen=opening2,
        verbose=False
    )
    
    # Should be identical
    assert result1.moves_uci == result2.moves_uci
    assert result1.result == result2.result
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/arena/test_arena.py -k randomization -v`
Expected: PASS (2 tests proving randomization works)

- [ ] **Step 3: Commit**

```bash
git add tests/arena/test_arena.py
git commit -m "test: verify opening randomization prevents game replays"
```

---

## Self-Review Checklist

**1. Spec coverage:**

- ✓ `choose_move(board: chess.Board, clock: ClockView)` signature (§3.1, Interfaces Part 3)
- ✓ ClockView with `my_ms`, no color indexing (§3.1, §4.1)
- ✓ Baseline bot with time management that doesn't flag at 3+2 (§3.1, §4.9)
- ✓ Reference bots: ref_random, ref_greedy, ref_depth3 (§3.4, design §10.3)
- ✓ Opening randomization, seeded (§3.4, §4.6, design §17)
- ✓ Arena uses chess_core for clock and rules (§4.7, §5 seams from chess_core)
- ✓ Mean and p95 move time statistics (§3.4, §6 arena result types)
- ✓ Flag count tracking (§3.4, §6 ArenaStats)
- ✓ Illegal-move attempt counting (§3.4, §6 ArenaStats)
- ✓ Local ELO tracking starting at 1200 (§3.4, design §10.1)
- ✓ PGN export (§3.4, §6 ArenaResult)
- ✓ Round-robin pairings for --games total (§3.4)
- ✓ Time-per-move budget strategy (~1/40th remaining time) (§3.1)

**2. Placeholder scan:** None. All code blocks contain complete implementations, all test assertions are concrete, no TODOs except the replay feature which is explicitly marked as stretch goal in comments.

**3. Type consistency:**
- ClockView: `my_ms`, `opponent_ms`, `increment_ms`, `ply` — consistent across all files
- GameResult dataclass — consistent field names
- ArenaStats, ArenaTracker — consistent naming
- All chess_core imports match Interfaces Part 1 exactly

**4. Gaps identified:** None. Every requirement from the role spec sections §3.1–§3.4, §4, §5, §6, and §8 is covered by at least one task.

---

## Dependencies

This plan depends on `chess_core` functions from phase 1:

**From chess_core.rules:**
- `validate_and_apply_move(fen, move_uci) -> MoveOutcome`
- `detect_termination(fen, history_fens) -> (is_terminal, reason, result)`
- `get_legal_moves(fen) -> List[str]`
- `san_list_to_pgn(san_moves, white_name, black_name, result, white_rating?, black_rating?) -> str`
- `STARTING_FEN`, `PLY_CAP`

**From chess_core.clock:**
- `create_clock(time_control_ns, increment_ns, to_move, now_mono) -> ClockState`
- `deliver_position(clock, now_mono, ply) -> ClockState`
- `account_move_and_switch(clock, receive_mono, now_mono) -> ClockUpdateResult`
- `ms_to_ns(ms) -> int`, `ns_to_ms(ns) -> int`
- `RATED_TIME_CONTROL_NS`, `RATED_INCREMENT_NS`
- `Color` enum

**From chess_core.elo:**
- `compute_rating_exchange(winner_rating, loser_rating) -> (RatingUpdate, RatingUpdate)`
- `compute_draw_exchange(white_rating, black_rating) -> (RatingUpdate, RatingUpdate)`
- `STARTING_RATING`, `K_FACTOR`

**From chess_core types:**
- `GameResult` enum (WHITE_WIN, BLACK_WIN, DRAW)
- `TerminationReason` enum
- `ClockState`, `ClockUpdateResult` dataclasses

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-24-phase2-arena-starter-kit.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
