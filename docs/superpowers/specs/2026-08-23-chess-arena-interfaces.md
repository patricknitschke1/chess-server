# Chess Arena — Interfaces Draft

**Date:** 2026-08-23
**Purpose:** Precise interface contracts for parallel implementation by five independent planning agents

This document defines exact type signatures, dataclasses, and contracts for all public interfaces in the Chess Arena system. These interfaces match the approved spec (revision 4) exactly and are designed to prevent independent agents from inventing conflicting interfaces.

---

## Part 1 — `chess_core` Public API

`chess_core` is a **pure** package: no I/O, no clock reads, no network. Time is always passed in as integer nanoseconds from `time.monotonic_ns()`.

**Units convention:** `chess_core` uses **nanoseconds** internally; the database and wire format use **milliseconds**. Conversion happens only at the boundary via two named helpers defined in `chess_core/clock.py`: `ms_to_ns(ms: int) -> int` and `ns_to_ms(ns: int) -> int`. Every signature and dataclass field name carries its unit suffix (`_ns` or `_ms`) consistently, and a field without a suffix is a bug. **`_mono` counts as a nanosecond suffix**: it marks a `time.monotonic_ns()` reading, which is always nanoseconds and never a wall-clock time. `to_move_since_mono`, `turn_started_mono`, `now_mono` and `receive_mono` are conformant; anything bare is not.

**`owner` is a public display handle**, chosen at registration and shown on the leaderboard and dashboard — it is how the room knows whose bot is winning. It must never be an email address, and registration says so. §14's "no owner identifiers in SSE payloads" is therefore narrowed to: **no tokens anywhere**; `owner` may appear wherever `bot_name` does.

### Shared Types and Enums

```python
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
    CRASH = "crash"  # bot raised; distinct from illegal_forfeit so the attendee
                      # is sent to the traceback, not to their move generation

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

### `chess_core/rules.py`

```python
from typing import List
import chess

def validate_and_apply_move(
    fen: str,
    move_uci: str
) -> MoveOutcome:
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
    ...

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
    ...

def get_legal_moves(fen: str) -> List[str]:
    """Generate all legal moves from a position in UCI notation.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        List of legal moves in UCI notation, sorted lexicographically
    
    Raises:
        ValueError: if fen is invalid
    """
    ...

def detect_termination(fen: str, history_fens: List[str]) -> tuple[bool, Optional[TerminationReason], Optional[GameResult]]:
    """Detect if position is terminal and determine result.
    
    Includes server-claimed fifty-move and threefold per §22 (uses
    python-chess can_claim_draw). Threefold detection compares
    position_key(fen) values, not full FEN strings.
    
    This answers "is this POSITION terminal?" only. The §22 200-ply cap is a
    game-length rule, not a position rule, and lives in match.py where ply is
    part of the state — see transition_after_move. Keeping it out of here is
    what lets this function stay a pure function of the position.
    
    Args:
        fen: Current position in FEN notation
        history_fens: All FENs in game history for threefold detection.
            **Contract, binding on every caller:** `[starting_fen] + [fen_after
            for each ply in order]` — it includes the position the game began
            from AND the current position. Threefold counts occurrences in this
            list and needs `>= 3`, so omitting either end silently under-counts.
            A server building it from `SELECT fen_after FROM moves ORDER BY ply`
            drops ply 0 and will not claim the commonest repetition of all
            (Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8 returns to the start three times).
            `starter-kit/arena.py` is the reference implementation.
    
    Returns:
        (is_terminal, termination_reason, result)
        termination_reason and result are None if not terminal
    """
    ...

def fen_to_ascii(fen: str) -> str:
    """Render a position as ASCII art for MCP get_game() per §13.2.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        ASCII board representation with rank/file labels
    """
    ...

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
    ...

def san_list_to_pgn(
    san_moves: List[str],
    white_name: str,
    black_name: str,
    result: GameResult,
    white_rating: Optional[int] = None,
    black_rating: Optional[int] = None,
    starting_fen: Optional[str] = None
) -> str:
    """Format a game as PGN for arena.py export.
    
    Args:
        san_moves: Moves in SAN notation
        white_name: White player name
        black_name: Black player name
        result: Game result
        white_rating: Optional ELO rating for White
        black_rating: Optional ELO rating for Black
        starting_fen: Position the game began from; omit for the standard start.
            When it is not the standard start, `[SetUp "1"]` and `[FEN ...]`
            headers are written and movetext is numbered from that position.
            Without them a game played from an opening cannot be read back.
    
    Returns:
        Complete PGN string with headers and movetext
    """
    ...

STARTING_FEN: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PLY_CAP: int = 200  # §22 adjudication cap
```

### `chess_core/clock.py`

```python
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
    ...

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
    ...

def window_start_mono(now_mono: int, window_ns: int) -> int:
    """The oldest monotonic timestamp still inside `window_ns`.
    
    `is_within` cannot serve a SQL filter, which needs a bound to compare a
    column against. Repositories use this so `chess_server` never subtracts
    monotonic timestamps itself.
    """
    ...

def is_within(earlier_mono: int, now_mono: int, window_ns: int) -> bool:
    """Whether `earlier_mono` is no more than `window_ns` ago.
    
    Poll recency (§9.1) and challenge TTL (§12) are elapsed arithmetic and belong
    here, not as a subtraction in `chess_server`. Inclusive boundary.
    """
    ...

def remaining_ns(clock: ClockState, color: Color, now_mono: int) -> int:
    """Nanoseconds left on one side's clock, counting time burnt this turn.
    
    Only the side to move burns time, and only once the position has been
    delivered. May go negative; that is what flag-fall looks like.
    """
    ...

def has_flagged(clock: ClockState, now_mono: int) -> bool:
    """Whether the side to move has run out of time, per §6.4's `<= 0`.
    
    Exists so §6.4's "flag precedes illegal-move validation" ordering can be
    asked as a separate question before validating a move.
    `account_move_and_switch` is deliberately atomic and cannot answer it
    without also mutating.
    
    **`chess_server` must never subtract monotonic timestamps itself.** Both the
    ticker and the move endpoint call this. The predicate living in two
    hand-written places is how it came to be stated two ways once already.
    """
    ...

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
    ...

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
    ...

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
    ...

# Constants
RATED_TIME_CONTROL_NS: int = 180_000_000_000  # 3 minutes
RATED_INCREMENT_NS: int = 2_000_000_000       # 2 seconds
EXHIBITION_TIME_CONTROL_NS: int = 300_000_000_000  # 5 minutes
EXHIBITION_INCREMENT_NS: int = 10_000_000_000      # 10 seconds
DELIVERY_GRACE_NS: int = 15_000_000_000       # 15 seconds
AGENT_DELIVERY_GRACE_NS: int = 60_000_000_000 # 60 seconds
AGENT_AUTO_RELEASE_NS: int = 45_000_000_000   # 45 seconds
POLL_RECENCY_NS: int = 5_000_000_000          # pool eligibility window (§9.1)
CHALLENGE_TTL_NS: int = 60_000_000_000        # open challenge lifetime (§12)
POLL_HOLD_NS: int = 20_000_000_000            # server long-poll hold (§8.4)
TICK_INTERVAL_NS: int = 1_000_000_000         # ticker period (§4.6)
```

This block is the complete `clock.py` set from design §5.2. `clock.py` is the sole
declaration site; `chess_server` imports these and declares none of them itself.

### `chess_core/elo.py`

```python
def compute_rating_exchange(
    winner_rating: int,
    loser_rating: int
) -> tuple[RatingUpdate, RatingUpdate]:
    """Compute two-sided Elo exchange for a decisive game, K=24 flat per §10.1.
    
    Exchange is zero-sum. It is NOT swap-symmetric: an upset (1000 beats 1400)
    moves 22 points, the expected result (1400 beats 1000) moves 2. The underdog
    always gains more than the favourite.
    
    Args:
        winner_rating: Winner's current rating
        loser_rating: Loser's current rating
    
    Returns:
        (winner_update, loser_update) where winner gains and loser loses
    """
    ...

def compute_draw_exchange(
    white_rating: int,
    black_rating: int
) -> tuple[RatingUpdate, RatingUpdate]:
    """Compute two-sided Elo exchange for a draw, K=24 flat per §10.1.
    
    Exchange is zero-sum. Equal ratings move nothing; otherwise the favourite
    loses points to the underdog.
    
    Args:
        white_rating: White's current rating
        black_rating: Black's current rating
    
    Returns:
        (white_update, black_update) summing to zero delta
    """
    ...

def compute_one_sided_exchange(
    competitor_rating: int,
    anchor_rating: int,
    competitor_score: float
) -> RatingUpdate:
    """Compute one-sided Elo update against a fixed anchor per §10.3.
    
    Anchor rating never changes. Net injection into pool per game, but
    shrinks toward zero as competitor approaches anchor rating.
    
    Args:
        competitor_rating: Competitor's current rating
        anchor_rating: Fixed anchor rating
        competitor_score: 1.0 win, 0.5 draw, 0.0 loss — the S term of
            R' = R + K(S - E). Raises ValueError on any other value.
    
    Returns:
        RatingUpdate for competitor only
    """
    ...

STARTING_RATING: int = 1200
K_FACTOR: int = 24
```

### `chess_core/matchmaker.py`

```python
import random
from typing import List

def pair_bots(
    pool: List[PoolEntry],
    seed: Optional[int] = None  # INERT. Pairing is fully deterministic; see below.
) -> List[Pairing]:
    """Pure pairing function implementing §9.2 policy.
    
    Algorithm per §9.2:
    1. Sort by games_played asc, then rating asc
    2. Pair adjacent entries, skipping same owner or rematch of last_opponent_id
    3. Bot with unpaired_ticks >= 3 has constraints dropped in order:
       - same owner, then rematch
    4. Color precedence: alternate from last_color; ties broken by white_count,
       then bot_id
    
    Deterministic: the same pool always produces the same pairings.
    
    Args:
        pool: Snapshot of eligible bots
        seed: Unused. The §9.2 algorithm has no random component, so this
            changes nothing and a caller must not rely on it for
            reproducibility. It once held a `random.seed()` call, which
            silently reset the process-global RNG on every tick and broke
            `chess_core` purity. Kept only because it is a pinned seam.
    
    Returns:
        List of Pairing objects (white_bot_id, black_bot_id)
    """
    ...

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
    ...

ANCHOR_RATING_WINDOW: int = 400
```

### `chess_core/match.py`

```python
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
    ...

def transition_to_active(state: MatchState) -> MatchState:
    """Transition pending -> active per §7 (first delivery)."""
    ...

def transition_after_move(
    state: MatchState,
    move_result: MoveResult
) -> MatchState:
    """Transition after applying a move.
    
    If move_result is terminal, transitions to finished.
    Otherwise increments ply while staying active.
    """
    ...

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
    ...

def is_terminal(state: MatchState) -> bool:
    """Check if match is in a terminal state (finished or aborted)."""
    ...

def can_transition(state: MatchState, to_status: GameStatus) -> bool:
    """Validate state transition is legal per §7 diagram."""
    ...
```

---

## Part 2 — SSE Event Catalog

All events carry `{"run": str, "seq": int}` per §14. Events contain **no tokens and no owner identifiers** — bot id and name only. Clocks include `turn_elapsed_ms` computed at emit time, never `turn_started_mono`.

### Event Types

```python
@dataclass
class SSEEvent:
    """Base SSE event structure."""
    run: str
    seq: int
    event_type: str
    data: dict

# Event type constants
EVENT_SERVER_RUN_STARTED = "server_run_started"
EVENT_GAME_CREATED = "game_created"
EVENT_GAME_STARTED = "game_started"
EVENT_MOVE_PLAYED = "move_played"
EVENT_GAME_ENDED = "game_ended"
EVENT_RATING_CHANGED = "rating_changed"
EVENT_BOT_REGISTERED = "bot_registered"
EVENT_BOT_CONNECTED = "bot_connected"
EVENT_BOT_DISCONNECTED = "bot_disconnected"
EVENT_CHALLENGE_UPDATED = "challenge_updated"
EVENT_HEALTH_TICK = "health_tick"
EVENT_ARENA_REPORT_POSTED = "arena_report_posted"  # DEFERRED - no producer in this build
```

### Event Payloads

#### `server_run_started`
```json
{
  "run": "abc123",
  "seq": 0,
  "event_type": "server_run_started",
  "data": {
    "run_id": "abc123",
    "started_at": "2026-08-23T10:00:00Z"
  }
}
```

#### `game_created`
```json
{
  "run": "abc123",
  "seq": 1,
  "event_type": "game_created",
  "data": {
    "game_id": 42,
    "white_bot_id": 1,
    "white_bot_name": "AlphaBot",
    "black_bot_id": 2,
    "black_bot_name": "BetaBot",
    "status": "pending",
    "rated": true,
    "source": "matchmaker",
    "time_control_ms": 180000,
    "increment_ms": 2000
  }
}
```

#### `game_started`
Fired when status transitions pending -> active (first delivery).
```json
{
  "run": "abc123",
  "seq": 2,
  "event_type": "game_started",
  "data": {
    "game_id": 42,
    "white_bot_id": 1,
    "white_bot_name": "AlphaBot",
    "black_bot_id": 2,
    "black_bot_name": "BetaBot",
    "started_at": "2026-08-23T10:00:05Z"
  }
}
```

#### `move_played`
Non-featured moves coalesced to ≤2 Hz per §14. Featured game moves sent immediately.
```json
{
  "run": "abc123",
  "seq": 3,
  "event_type": "move_played",
  "data": {
    "game_id": 42,
    "ply": 1,
    "uci": "e2e4",
    "san": "e4",
    "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "to_move": "black",
    "white_ms": 178300,
    "black_ms": 180000,
    "turn_elapsed_ms": 1700,
    "server_elapsed_ms": 1700,
    "is_featured": false
  }
}
```

#### `game_ended`
```json
{
  "run": "abc123",
  "seq": 4,
  "event_type": "game_ended",
  "data": {
    "game_id": 42,
    "white_bot_id": 1,
    "white_bot_name": "AlphaBot",
    "black_bot_id": 2,
    "black_bot_name": "BetaBot",
    "status": "finished",
    "result": "white_win",
    "termination": "checkmate",
    "rated": true,
    "final_ply": 45,
    "ended_at": "2026-08-23T10:08:23Z"
  }
}
```

#### `rating_changed`
Sent for each bot in a rated game. Clients recompute leaderboard ordering
locally from rating_changed events rather than receiving a separate
leaderboard_updated event.
```json
{
  "run": "abc123",
  "seq": 5,
  "event_type": "rating_changed",
  "data": {
    "bot_id": 1,
    "bot_name": "AlphaBot",
    "rating_before": 1200,
    "rating_after": 1212,
    "delta": 12,
    "game_id": 42
  }
}
```

#### `bot_registered`
```json
{
  "run": "abc123",
  "seq": 6,
  "event_type": "bot_registered",
  "data": {
    "bot_id": 3,
    "bot_name": "GammaBot",
    "role": "competitor",
    "rating": 1200
  }
}
```

#### `bot_connected`
Fired when a bot polls for the first time or after prolonged absence.
```json
{
  "run": "abc123",
  "seq": 7,
  "event_type": "bot_connected",
  "data": {
    "bot_id": 3,
    "bot_name": "GammaBot"
  }
}
```

#### `bot_disconnected`
Fired when a bot hasn't polled for 30+ seconds.
```json
{
  "run": "abc123",
  "seq": 8,
  "event_type": "bot_disconnected",
  "data": {
    "bot_id": 3,
    "bot_name": "GammaBot"
  }
}
```

#### `challenge_updated`
Unified event for all challenge state changes. The status field carries
the transition: created, accepted, queued, consumed, declined, expired,
or cancelled.
```json
{
  "run": "abc123",
  "seq": 9,
  "event_type": "challenge_updated",
  "data": {
    "challenge_id": 5,
    "status": "created",
    "challenger_bot_id": 1,
    "challenger_bot_name": "AlphaBot",
    "opponent_bot_id": 2,
    "opponent_bot_name": "BetaBot",
    "time_control_ms": 180000,
    "increment_ms": 2000,
    "game_id": null,
    "reason": null
  }
}
```

When `status` is `consumed`, `game_id` is populated. When `status` is
`expired` or `cancelled`, `reason` may carry context (e.g.,
"seat_unavailable", "timeout").

#### `arena_report_posted`
**DEFERRED — no producer in this build** (design §21). Its producer, `arena.py --report`, is deferred with the arena surface, so nothing emits this today. The payload stays recorded so the shape survives the deferral.

Fired when a local arena report is posted via `arena.py --report`.
```json
{
  "run": "abc123",
  "seq": 11,
  "event_type": "arena_report_posted",
  "data": {
    "bot_id": 1,
    "bot_name": "AlphaBot",
    "candidate_name": "AlphaBot v2",
    "opponent_name": "baseline",
    "games": 100,
    "wins": 67,
    "draws": 15,
    "losses": 18,
    "win_rate": 0.67,
    "mean_move_ms": 45,
    "p95_move_ms": 120,
    "flags": 2
  }
}
```
No tokens. `win_rate` is computed as `wins / games` at emit time.

#### `health_tick`
Sent every ~3-5 seconds as a stale-tick signal per §4.6.
```json
{
  "run": "abc123",
  "seq": 10,
  "event_type": "health_tick",
  "data": {
    "last_tick_age_ms": 1234,
    "last_tick_duration_ms": 56,
    "active_games": 5,
    "pending_games": 2,
    "pooled_bots": 8,
    "held_polls": 12,
    "sse_clients": 3
  }
}
```

## Part 3 — Bot and SDK Surface

### Attendee `bot.py` Signature

The single most-read line in the workshop per spec requirements. Must be obvious and hard to misuse.

```python
import chess

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
    
    Example:
        >>> def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
        ...     return random.choice(list(board.legal_moves))
    """
    ...
```

### `chess_client` SDK Public API

The SDK converts between wire format (FEN strings, UCI strings, milliseconds)
and attendee-facing types (chess.Board, chess.Move, ClockView). Attendees
never see the wire.

```python
from typing import Optional, Callable
from dataclasses import dataclass
import chess

class ChessClient:
    """SDK client for Chess Arena server.
    
    Handles registration, polling, move submission, and error recovery.
    Attendees should never need to touch HTTP details.
    """
    
    def __init__(self, server_url: str, token: Optional[str] = None):
        """Initialize client.
        
        Args:
            server_url: Base URL of Chess Arena server
            token: Bot token (if already registered)
        """
        ...
    
    def register(
        self,
        name: str,
        owner: str,
        join_code: str,
        role: str = "competitor"
    ) -> str:
        """Register a new bot.
        
        Args:
            name: Bot name (must be unique)
            owner: Owner identifier (email or username)
            join_code: Workshop join code
            role: "competitor" or "benchmark"
        
        Returns:
            Bot token (store this!)
        
        Raises:
            ClientError: If registration fails (name taken, invalid join code, etc.)
        """
        ...
    
    def run(
        self,
        choose_move_fn: Callable[[chess.Board, ClockView], chess.Move],
        idle_on_control_handoff: bool = True
    ) -> None:
        """Run the bot's main loop.
        
        Polls for turns, calls choose_move_fn, submits moves, handles errors.
        Runs until interrupted (Ctrl-C) or fatal error.
        
        The SDK measures elapsed time around the choose_move call and includes
        client_reported_ms automatically in the POST payload.
        
        Args:
            choose_move_fn: The bot's choose_move function
            idle_on_control_handoff: If True, idle when controller='agent'
        
        Raises:
            ClientError: On unrecoverable errors (invalid token, server down)
        """
        ...
    
    def challenge(
        self,
        opponent_name: str,
        time_control: str = "rated"
    ) -> int:
        """Challenge another bot to a game.
        
        Args:
            opponent_name: Name of bot to challenge
            time_control: "rated" (3+2) or "exhibition" (5+10)
        
        Returns:
            Challenge ID
        
        Raises:
            ClientError: If opponent not found, either bot already playing, etc.
        """
        ...
    
    def resign(self, game_id: int, ply: int) -> None:
        """Resign the current game.
        
        Args:
            game_id: Game ID
            ply: Current ply (for CAS)
        
        Raises:
            ClientError: If not your turn or game already ended
        """
        ...

class ClientError(Exception):
    """Base exception for client errors with actionable messages."""
    pass

class MoveRejected(ClientError):
    """Move was rejected (illegal, wrong ply, etc.). Re-poll."""
    pass

class NotYourTurn(ClientError):
    """Submitted move but it's not your turn. Re-poll."""
    pass

class GameEnded(ClientError):
    """Game has ended. Stop polling for this game."""
    pass

class TokenInvalid(ClientError):
    """Bot token is invalid. Re-register."""
    pass

class ServerError(ClientError):
    """Server returned 5xx. Retry with backoff."""
    pass
```

### `arena.py` CLI and Results

> **DEFERRED — not built, and not currently pinned.** `run_arena()`, `ArenaStats`, `HeadToHead` and `ArenaResult` below are a design sketch, not a seam any track may bind to today.
>
> Their only consumer is `arena.py --report` posting to `POST /arena-reports`. That route does not exist, and because `arena_reports` is display-only its shape follows what the dashboard renders — also unbuilt. Pinning these dataclasses now means guessing a wire format twice and reconciling later.
>
> The same applies to the remaining §17 arena items: head-to-head win rates in the output, reporting illegal-move attempts with the offending FEN, and `--replay` stepping with a clock display. All three are cheap and server-independent, but all three exist to compare and debug bots, which is deferred (design §21). They have no consumer today.
>
> **What `arena.py` actually ships** and what other tracks may rely on: `run_single_game()`, `ArenaTracker`, `GameResult`, `export_to_pgn()`, `replay_game()`, `build_schedule()`, `parse_args()`, `main()`. Read the module, not this block.
>
> Revisit when the server track builds `POST /arena-reports`, or when bot development resumes. Recorded rather than deleted so the requirement survives the deferral.

```python
from dataclasses import dataclass
from typing import List, Dict, Optional

@dataclass
class ArenaStats:
    """Per-bot statistics from local arena run."""
    bot_name: str
    rating: int
    wins: int
    losses: int
    draws: int
    games_played: int
    mean_move_time_ms: float
    p95_move_time_ms: float
    flags: int
    illegal_attempts: int

@dataclass
class HeadToHead:
    """Head-to-head record between two bots."""
    bot1_name: str
    bot2_name: str
    bot1_wins: int
    bot2_wins: int
    draws: int

@dataclass
class ArenaResult:
    """Complete result from arena.py run."""
    stats: List[ArenaStats]
    head_to_head: List[HeadToHead]
    total_games: int
    seed: int
    pgns: List[str]

def run_arena(
    bot_modules: List[str],
    num_games: int,
    seed: int,
    time_control_ms: int = 180000,
    increment_ms: int = 2000,
    verbose: bool = False
) -> ArenaResult:
    """Run local arena competition.
    
    Args:
        bot_modules: Paths to bot.py files
        num_games: Total games to play (distributed across pairings)
        seed: Random seed for opening book and pairing order
        time_control_ms: Time control in milliseconds
        increment_ms: Increment in milliseconds
        verbose: Print game-by-game results
    
    Returns:
        ArenaResult with ELO table, statistics, and PGNs
    """
    ...

# CLI interface
# $ python arena.py --bots bot.py baseline.py ref_greedy.py --games 100 --seed 7
# $ python arena.py --replay <game_id> --pgn <file>
```

---

## Part 4 — Test Conventions

### Directory Layout

```
tests/
  chess_core/
    test_rules.py
    test_clock.py
    test_elo.py
    test_matchmaker.py
    test_match.py
  chess_server/
    test_store.py
    test_engine.py
    test_api.py
    test_mcp.py
    integration/
      test_concurrency.py
      test_seats.py
      test_recovery.py
      test_fake_bot_harness.py
  arena/
    test_arena.py
```

### Naming Conventions

- **Unit tests:** `test_<function_name>_<scenario>` (e.g. `test_account_move_flags_on_timeout`)
- **Integration tests:** `test_<workflow>_<scenario>` (e.g. `test_concurrent_flag_and_move_cas`)
- **Fixtures:** Avoided in `chess_core` tests — prefer explicit test data
- **Seeded tests:** All `chess_core/matchmaker.py` tests use explicit seeds

### Pure vs Integration Split

**`chess_core` tests are pure unit tests:**
- No mocks, no fixtures, no database
- Direct function calls with explicit inputs
- Table-driven tests for combinatorial cases (clock §6.4, termination §22)
- Property tests for Elo zero-sum (**not** symmetry — Elo is not swap-symmetric; 1000 beating 1400 gains 22, 1400 beating 1000 gains 2)
- Seeded tests for matchmaker determinism

**`chess_server` integration tests:**
- **A real SQLite file under pytest's `tmp_path`, never `:memory:`.** The store opens separate reader and writer connections, and two connections to `:memory:` are two unrelated databases — the reader would never see the writer's rows. WAL, `BEGIN IMMEDIATE` lock contention and `PRAGMA foreign_keys` are also unobservable in memory, and those are exactly what §4 requires the tests to exercise.
- Full FastAPI test client for API tests
- Concurrent execution tests for CAS validation per §4.2
- Recovery tests that simulate server restart
- Fake bot harness plays complete games over real endpoints

### Critical Test Coverage

Per §18, these are the highest-priority tests:

1. **Clock ordering per §6.4:** Flag on exact zero, no increment on flag, rejected move does not reset, flag precedes illegal-move validation
2. **Delivery idempotency:** Re-delivery does not restart clock, mailbox drained by any poll
3. **CAS concurrency:** Concurrent move + flag resolves to exactly one terminal transition, exactly one `rating_history` row
4. **Seats enforcement:** Attempting second game for seated bot raises, challenge and pairing racing same seat yields exactly one game
5. **Recovery:** Restart mid-game aborts unrated, frees seats, reconnecting bot re-paired
6. **Failure paths:** Illegal-move strikes, flag-fall, mid-game disconnect (abandoned), no-show, superseded poll, admin abort

### Test Execution

```bash
# Fast pure tests only
pytest tests/chess_core/

# Integration tests (slower, need --integration flag)
pytest tests/chess_server/integration/ --integration

# Full suite
pytest

# Coverage requirement: 90% for chess_core, 80% for chess_server
pytest --cov=chess_core --cov=chess_server --cov-report=term-missing
```

---

## Part 5 — HTTP API Models

All authenticated endpoints use `Authorization: Bearer <token>`. Models are defined Pydantic-style with precise field types.

### Common Models

```python
class ErrorResponse(BaseModel):
    """Standard error response shape."""
    error: str
    details: Optional[dict] = None

class TurnResponse(BaseModel):
    """Response from GET /bots/me/turn when game available."""
    game_id: int
    ply: int
    color: str  # "white" or "black"
    fen: str
    legal_moves: List[str]  # UCI notation, sorted
    history_san: List[str]
    white_ms: int
    black_ms: int
    time_control_ms: int
    increment_ms: int
    controller: str  # "client" or "agent"

class NoGameResponse(BaseModel):
    """Response from GET /bots/me/turn when no game available."""
    game_id: None
    reason: str  # "waiting_for_pairing" | "not_your_turn" | "agent_has_control" | "superseded" | "paused" | "no_seat"
```

### Bot Registration

**POST /bots**
- **Unauthenticated**
- **Request:**
  ```python
  class RegisterBotRequest(BaseModel):
      name: str
      owner: str
      join_code: str
      role: str = "competitor"  # "competitor" or "benchmark"
  ```
- **Response (201):**
  ```python
  class RegisterBotResponse(BaseModel):
      bot_id: int
      name: str
      token: str
  ```
- **Errors:**
  - `400` — ErrorResponse: "Name already taken" | "Invalid role" | "Invalid join code"
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

### Bot Identity and Control

**GET /bots/me**
- **Authenticated**
- **Response (200):**
  ```python
  class MyBotResponse(BaseModel):
      bot_id: int
      name: str
      owner: str
      role: str            # "competitor" | "benchmark" | "anchor"
      rating: int
      wins: int
      losses: int
      draws: int
      games_played: int
      is_provisional: bool  # games_played < 10
      controller: str       # "client" | "agent"
      current_game_id: Optional[int]  # resolved through `seats`, never by scanning games
  ```
- **Errors:**
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

  Implemented by `server-engineer`. The MCP `get_my_bot()` tool is a consumer of this
  route and its `MyBotResult` is this shape; it implements nothing.

**POST /bots/me/control**
- **Authenticated**
- **Request:**
  ```python
  class SetControlRequest(BaseModel):
      action: str  # "take" | "release"
  ```
- **Response (200):**
  ```python
  class SetControlResponse(BaseModel):
      controller: str  # "agent" after take, "client" after release
      message: str     # actionable prose for the agent transcript
  ```
- **Errors:**
  - `400` — ErrorResponse: "Invalid action '{action}'. Must be 'take' or 'release'."
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."
  - `409` — ErrorResponse: "Cannot take control while your bot is in a game. Wait for it to finish, or resign."
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

  The field is `action`, not `mode`, matching design §8.1. `take` is refused whenever
  the bot holds a `seats` row (§13.3), and it wakes any held poll with
  `NoGameResponse(reason="agent_has_control")`.

### Turn Polling

**GET /bots/me/turn**
- **Authenticated**
- **Response (200):** `TurnResponse | NoGameResponse`
- **Errors:**
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

### Move Submission

**POST /games/{id}/moves**
- **Authenticated**
- **Request:**
  ```python
  class SubmitMoveRequest(BaseModel):
      ply: int
      move: str  # UCI notation
      client_reported_ms: Optional[int] = None
  ```
- **Response (200):**
  ```python
  class SubmitMoveResponse(BaseModel):
      game_id: int
      ply: int
      fen: str
      status: str  # "active" | "finished"
      result: Optional[str]  # "white_win" | "black_win" | "draw"
      termination: Optional[str]
  ```
- **Errors:**
  - `400` — ErrorResponse: "Illegal move. Legal moves: [...]", includes `legal_moves: List[str]` and `fen: str` in details
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."
  - `403` — ErrorResponse: "Controller is 'agent'. Only agent tools may move."
  - `409` — ErrorResponse: "CAS conflict. Position has changed.", includes `ply: int`, `fen: str`, `status: str` in details
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

**GET /games/{id}/moves**
- **Unauthenticated**
- **Response (200):**
  ```python
  class GameMovesResponse(BaseModel):
      game_id: int
      white_bot_name: str
      black_bot_name: str
      white_rating: Optional[int]
      black_rating: Optional[int]
      status: str
      result: Optional[str]
      termination: Optional[str]
      starting_fen: str          # feeds san_list_to_pgn's starting_fen argument
      final_ply: int
      moves: List[GameMoveEntry]
      white_strikes: int
      black_strikes: int

  class GameMoveEntry(BaseModel):
      ply: int
      uci: str
      san: str
      fen_after: str
      server_elapsed_ms: int              # delivery -> receipt, network included
      client_reported_ms: Optional[int]   # self-reported compute time, diagnostics only
      white_ms_after: int
      black_ms_after: int
  ```
- **Errors:**
  - `404` — ErrorResponse: "Game not found"

  Implemented by `server-engineer`. The MCP `analyze_game()` tool consumes this and
  builds its own PGN, timing table and event log; the server returns data, not Markdown.
  The strike counts are what the event log's "illegal move strike (n/3)" lines are built
  from, and `server_elapsed_ms` versus `client_reported_ms` is what distinguishes a slow
  bot from a slow network.

### Resignation

**POST /games/{id}/resign**
- **Authenticated**
- **Request:**
  ```python
  class ResignRequest(BaseModel):
      ply: int
  ```
- **Response (200):**
  ```python
  class ResignResponse(BaseModel):
      game_id: int
      status: str  # "finished"
      result: str  # opposite color wins
      termination: str  # "resignation"
  ```
- **Errors:**
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."
  - `403` — ErrorResponse: "Not your turn" | "Controller is 'agent'. Only agent tools may resign."
  - `409` — ErrorResponse: "Game already ended"
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

### Challenges

**POST /challenges**
- **Authenticated**
- **Request:**
  ```python
  class CreateChallengeRequest(BaseModel):
      opponent: str  # bot name
      time_control: str = "rated"  # "rated" (3+2) or "exhibition" (5+10)
  ```
- **Response (201):**
  ```python
  class CreateChallengeResponse(BaseModel):
      challenge_id: int
      challenger_bot_id: int
      opponent_bot_id: int
      status: str  # "open"
      time_control_ms: int
      increment_ms: int
  ```
- **Errors:**
  - `400` — ErrorResponse: "Opponent bot not found: {name}"
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."
  - `409` — ErrorResponse: "You already have an open outgoing challenge" | "Either you or opponent is already in a game"
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

**POST /challenges/{id}/accept**
- **Authenticated**
- **Response (200):**
  ```python
  class AcceptChallengeResponse(BaseModel):
      challenge_id: int
      status: str  # "queued"
  ```
- **Errors:**
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."
  - `403` — ErrorResponse: "You are not the opponent of this challenge"
  - `404` — ErrorResponse: "Challenge not found"
  - `409` — ErrorResponse: "Challenge already {status}" | "You are already in a game"
  - `429` — ErrorResponse: "Rate limit exceeded", Retry-After header

**POST /challenges/{id}/decline**
- **Authenticated**
- **Response (200):**
  ```python
  class DeclineChallengeResponse(BaseModel):
      challenge_id: int
      status: str  # "declined"
  ```
- **Errors:** same as accept

**GET /challenges**
- **Authenticated**
- **Response (200):**
  ```python
  class ChallengesInboxResponse(BaseModel):
      incoming: List[ChallengeEntry]
      outgoing: List[ChallengeEntry]
  
  class ChallengeEntry(BaseModel):
      challenge_id: int
      challenger_bot_id: int
      challenger_bot_name: str
      opponent_bot_id: int
      opponent_bot_name: str
      status: str
      time_control_ms: int
      increment_ms: int
      created_at: str  # ISO 8601
  ```
- **Errors:**
  - `401` — ErrorResponse: "No bot registered for this token. Call register_bot first."

### Leaderboard and Game Info

**GET /leaderboard**
- **Unauthenticated**
- **Response (200):**
  ```python
  class LeaderboardResponse(BaseModel):
      bots: List[LeaderboardEntry]
      total_bots: int
  
  class LeaderboardEntry(BaseModel):
      bot_id: int
      bot_name: str
      owner: str
      rating: int
      wins: int
      losses: int
      draws: int
      games_played: int
      is_provisional: bool  # games_played < 10
      role: str  # "competitor" | "benchmark"
      is_anchor: bool  # ref-* bots; fixed rating, never changes
  ```

**GET /games/{id}**
- **Unauthenticated**
- **Response (200):**
  ```python
  class GameDetailResponse(BaseModel):
      game_id: int
      white_bot_id: int
      white_bot_name: str
      black_bot_id: int
      black_bot_name: str
      status: str
      result: Optional[str]
      termination: Optional[str]
      fen: str
      ply: int
      history_san: List[str]
      white_ms: int
      black_ms: int
      time_control_ms: int
      increment_ms: int
      rated: bool
      source: str  # "matchmaker" | "challenge"
      created_at: str  # ISO 8601
      started_at: Optional[str]
      ended_at: Optional[str]
  ```
- **Errors:**
  - `404` — ErrorResponse: "Game not found"

**GET /state**
- **Unauthenticated**
- **Response (200):**
  ```python
  class DashboardStateResponse(BaseModel):
      run_id: str
      event_id: int  # last emitted SSE seq
      active_games: List[ActiveGameSummary]
      leaderboard: List[LeaderboardEntry]
      featured_game_id: Optional[int]
  
  class ActiveGameSummary(BaseModel):
      game_id: int
      white_bot_id: int
      white_bot_name: str
      white_rating: int
      black_bot_id: int
      black_bot_name: str
      black_rating: int
      status: str                     # "pending" | "active"
      fen: str
      to_move: str                    # "white" | "black"
      ply: int
      white_ms: int
      black_ms: int
      turn_elapsed_ms: Optional[int]  # None while undelivered
      is_featured: bool
      rated: bool
  ```

  Ratings are included because featured-game selection ranks by the sum of participant ratings; without them the dashboard would have to join against the leaderboard on every tick.

  `fen`, `to_move` and `status` are included so the dashboard can render every board in the live grid from `/state` alone. Without them a page load mid-workshop must issue one `GET /games/{id}` per active game before it can draw anything, and `status` is what distinguishes a paired-but-undelivered game from one in play.

**GET /bots/{bot_id}/rating_history**
- **Unauthenticated**
- **Response (200):**
  ```python
  class RatingHistoryResponse(BaseModel):
      bot_id: int
      bot_name: str
      points: List[RatingPoint]

  class RatingPoint(BaseModel):
      game_id: int
      rating_after: int
      delta: int
      ts: str  # ISO 8601
  ```
- **Errors:** `404` — ErrorResponse: "Bot not found: {bot_id}"

  Needed for the My Bot sparkline. Accumulating from `rating_changed` events alone breaks when a page is loaded mid-workshop, since the client has no backlog. Unbounded is acceptable at ~20 bots playing a single day; if that assumption changes, add a `limit`.

**GET /events**
- **Unauthenticated**
- **SSE stream**, payloads defined in Part 2
- **Headers:** `Last-Event-ID` optional (not implemented)

**GET /health**
- **Unauthenticated**
- **Response (200):**
  ```python
  class HealthResponse(BaseModel):
      last_tick_age_ms: int
      last_tick_duration_ms: int
      active_games: int
      pending_games: int
      stalled_games: int
      pooled_bots: int
      held_polls: int
      sse_clients: int
      db_writable: bool
      consecutive_tick_errors: int
      ticker_restarts: int
  ```

  `db_writable` is a real probe, not a constant: the server opens and immediately closes
  a write transaction (§4.6). `ticker_restarts` counts supervisor-initiated restarts —
  a detector wired to no remediation is what a stalled ticker costs.

### Admin Endpoints

All require `Authorization: Bearer <ADMIN_TOKEN>`.

**POST /admin/games/{id}/abort**
- **Request:** empty body
- **Response (200):**
  ```python
  class AbortGameResponse(BaseModel):
      game_id: int
      status: str  # "aborted"
      termination: str  # "admin_abort"
  ```
- **Errors:**
  - `401` — ErrorResponse: "Invalid admin token"
  - `404` — ErrorResponse: "Game not found"
  - `409` — ErrorResponse: "Game already terminal"

**POST /admin/matchmaking/pause**
- **Request:** empty body
- **Response (200):**
  ```python
  class PauseMatchmakingResponse(BaseModel):
      paused: bool  # true
  ```
- **Errors:**
  - `401` — ErrorResponse: "Invalid admin token"

**POST /admin/matchmaking/resume**
- **Request:** empty body
- **Response (200):**
  ```python
  class ResumeMatchmakingResponse(BaseModel):
      paused: bool  # false
  ```
- **Errors:**
  - `401` — ErrorResponse: "Invalid admin token"

**POST /admin/bots/{name}/token**
- **Request:** empty body
- **Response (200):**
  ```python
  class ReissueTokenResponse(BaseModel):
      bot_id: int
      bot_name: str
      token: str
  ```
- **Errors:**
  - `401` — ErrorResponse: "Invalid admin token"
  - `404` — ErrorResponse: "Bot not found"
  - `409` — ErrorResponse: "Cannot reissue token while bot holds a seat"

**POST /admin/reset**
- **Request:** empty body
- **Response (200):**
  ```python
  class ResetResponse(BaseModel):
      wiped_games: int
      wiped_moves: int
      wiped_rating_history: int
      wiped_seats: int
      wiped_challenges: int
      reset_bots: int
      run_id: str  # regenerated; SSE clients refetch /state on the run change
  ```
- **Errors:**
  - `401` — ErrorResponse: "Invalid admin token"
  - `409` — ErrorResponse: "Pause matchmaking before resetting. POST /admin/matchmaking/pause, then retry."

  There is no `wiped_mailboxes` count: the mailbox is process state, not a table (§5).
  Bot identities, tokens and anchor ratings survive; competitor and benchmark ratings
  return to `STARTING_RATING`. See `roles/server-engineer-spec.md` §10.1 for the full
  list of what is wiped and what survives.

### Arena Reports

**Deferred — no models in this build.** Design §21 defers the whole `arena_reports`
vertical, including `POST /arena-reports`, `GET /bots/{bot_id}/arena-reports` and the
`arena_report_posted` SSE event, because its only producer (`arena.py --report`) is
deferred with it. The models return with the dashboard panel that renders them.

**GET /admin/consistency**
- **Response (200):**
  ```python
  class ConsistencyCheckResponse(BaseModel):
      consistent: bool
      violations: List[ConsistencyViolation]
  
  class ConsistencyViolation(BaseModel):
      bot_id: int
      bot_name: str
      expected_rating: int
      actual_rating: int
      delta_sum: int
  ```
- **Errors:**
  - `401` — ErrorResponse: "Invalid admin token"

---

## Part 6 — MCP Tool Signatures

All tools use `Authorization: Bearer <token>` forwarded from `.mcp.json`.

### Observe Tools (readOnlyHint)

**get_leaderboard()**
- **Parameters:** none
- **Returns:**
  ```python
  class LeaderboardResult(BaseModel):
      bots: List[LeaderboardEntry]
      total_bots: int
  ```
  Same structure as GET /leaderboard response.
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- **Annotation:** `readOnlyHint`

**get_my_bot()**
- **Parameters:** none
- **Returns:**
  ```python
  class MyBotResult(BaseModel):
      bot_id: int
      name: str
      owner: str
      rating: int
      wins: int
      losses: int
      draws: int
      games_played: int
      controller: str  # "client" or "agent"
      current_game_id: Optional[int]
      role: str
  ```
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- **Annotation:** `readOnlyHint`

**get_game(game_id: Optional[int] = None)**
- **Parameters:**
  - `game_id` (optional int): Game ID to retrieve. Defaults to caller's current game if omitted.
- **Returns:** Markdown-formatted text with:
  - ASCII board (via `fen_to_ascii`)
  - Current FEN
  - SAN move history
  - Clock state (white_ms, black_ms, to_move)
  - Game status and metadata
  
  Example format:
  ```
  Game #42 — AlphaBot (White) vs BetaBot (Black)
  Status: active, Ply: 12
  
    a b c d e f g h
  8 ♜ ♞ ♝ ♛ ♚ ♝ ♞ ♜
  7 ♟ ♟ ♟ ♟ ♟ ♟ ♟ ♟
  ...
  
  FEN: rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1
  
  Moves: 1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1
  
  Clock: White 152300ms, Black 161100ms (to move)
  ```
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
  - "No current game. Omit game_id to use your current game, or specify a game_id."
  - "Game {id} not found."
- **Annotation:** `readOnlyHint`

**analyze_game(game_id: int)**
- **Parameters:**
  - `game_id` (int, required): Game ID to analyze
- **Returns:** Markdown-formatted text with three sections:
  1. PGN with standard headers
  2. Timing table:
     ```
     Ply | Move | Server ms | Client ms | White remaining | Black remaining
     1   | e4   | 1200      | 1150      | 178800         | 180000
     2   | e5   | 1350      | 1300      | 178800         | 178650
     ...
     ```
  3. Event log (flags, strikes, forfeits with ply numbers):
     ```
     Ply 15: White illegal move strike (1/3)
     Ply 23: Black flagged at 0ms
     ```
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
  - "Game {id} not found."
- **Annotation:** `readOnlyHint`

### Act Tools (destructiveHint)

**register_bot(name: str, owner: str, role: str = "competitor")**
- **Parameters:**
  - `name` (str, required): Bot name (must be unique)
  - `owner` (str, required): Owner identifier (email or username)
  - `role` (str, optional): "competitor" or "benchmark", default "competitor"
- **Returns:**
  ```python
  class RegisterBotResult(BaseModel):
      bot_id: int
      name: str
      token: str  # IMPORTANT: Store this token in .mcp.json!
  ```
  Plus instructional text explaining how to add the token to `.mcp.json`.
- **Error messages:**
  - "Name '{name}' is already taken. Choose a different name."
  - "Invalid role '{role}'. Must be 'competitor' or 'benchmark'."
  - "Invalid join code. Ask the workshop organizer for the correct code."
  - "Rate limit exceeded. Wait 60 seconds and try again."
- **Annotation:** `destructiveHint`

**challenge(opponent: str, time_control: str = "rated")**
- **Parameters:**
  - `opponent` (str, required): Name of bot to challenge
  - `time_control` (str, optional): "rated" (3+2) or "exhibition" (5+10), default "rated"
- **Returns:**
  ```python
  class ChallengeResult(BaseModel):
      challenge_id: int
      challenger_bot_name: str
      opponent_bot_name: str
      status: str  # "open"
      time_control_ms: int
      increment_ms: int
  ```
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
  - "Opponent bot '{opponent}' not found."
  - "You already have an open outgoing challenge. Decline or wait for it to be resolved."
  - "Either you or {opponent} is already in a game. Wait for the current game to finish."
  - "Invalid time_control '{time_control}'. Must be 'rated' or 'exhibition'."
- **Annotation:** `destructiveHint`

**make_move(game_id: int, ply: int, move: str)**
- **Parameters:**
  - `game_id` (int, required): Game ID
  - `ply` (int, required): Current ply (for CAS)
  - `move` (str, required): Move in UCI notation (e.g., "e2e4")
- **Returns:**
  ```python
  class MakeMoveResult(BaseModel):
      game_id: int
      ply: int  # new ply after move
      fen: str  # position after move
      status: str
      result: Optional[str]
      termination: Optional[str]
  ```
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
  - "Illegal move '{move}'. Legal moves: {legal_moves}. Current position: {fen}"
  - "CAS conflict. The position has changed since ply {ply}. Call get_game() to see the current position."
  - "Controller is 'client'. Call release_control() before using agent tools."
  - "Game {game_id} not found or already ended."
- **Annotation:** `destructiveHint`

**get_legal_moves(game_id: int)**
- **Parameters:**
  - `game_id` (int, required): Game ID
- **Returns:**
  ```python
  class LegalMovesResult(BaseModel):
      game_id: int
      ply: int
      legal_moves: List[str]  # UCI notation, sorted
      fen: str
  ```
  Triggers delivery per §6.2 if undelivered and controller='agent'.
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
  - "Game {game_id} not found or already ended."
  - "Controller is 'client'. Call take_control() before using agent tools."
- **Annotation:** `destructiveHint` (triggers delivery)

**take_control()**
- **Parameters:** none
- **Returns:**
  ```python
  class TakeControlResult(BaseModel):
      controller: str  # "agent"
      message: str  # "Control transferred to agent. Client polling is now idle."
  ```
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
  - "Cannot take control while bot holds a seat (is in an active or pending game). Wait for the current game to finish or resign."
- **Annotation:** `destructiveHint`

**release_control()**
- **Parameters:** none
- **Returns:**
  ```python
  class ReleaseControlResult(BaseModel):
      controller: str  # "client"
      message: str  # "Control released to client. Resume polling."
  ```
- **Error messages:**
  - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- **Annotation:** `destructiveHint`

---

## Decisions (All Resolved in Revision 4)

The following design decisions have been resolved during spec harmonization. Cross-references indicate which role spec owns implementation:

### 1. Opening book format for `arena.py`

**Issue:** §17 requires opening randomisation and states "drawn from a small book, seeded for reproducibility," but does not specify the book format or size.

**Recommendation:** Use a list of 20-30 FEN positions 3-4 moves deep, hardcoded in `arena.py`. Selecting by `random.Random(seed).choice()` makes it deterministic. Alternative: ECO opening names mapped to starting FENs, but that adds complexity for marginal gain.

**Why it matters:** Affects `arena.py` interface and test reproducibility.

### 2. `client_reported_ms` semantics in move submission

**Issue:** §5 data model includes `client_reported_ms` as "optional self-reported compute time" in `moves` table, and §8.3 shows it in the submission payload as `{ply, move, client_reported_ms?}`.

**Resolution (applied in this revision):** The SDK measures elapsed time around the `choose_move` call and includes it automatically in the POST payload. Attendees never see this field. The MCP `analyze_game` tool displays both server and client elapsed times per move. This keeps the attendee-facing `choose_move` signature simple (returns only a `chess.Move` object).

**Why it mattered:** Affected the attendee `choose_move` signature and SDK implementation contract.

### 3. `controller` field initial value

**Resolution:** `controller TEXT NOT NULL DEFAULT 'client'` added to `bots` table schema. Indexed for pool eligibility queries. Makes control state durable across restarts.

**Owner:** server-engineer (schema), design spec §5 updated.

### 4. Exact SSE coalescing window for non-featured moves

**Issue:** §14 states "non-featured move events are coalesced to ≤2 Hz" but does not specify the buffering/throttle mechanism (time-based batching vs. event count vs. per-game throttle).

**Resolution:** Per-game 500ms throttle. After emitting a `move_played` event for a non-featured game, suppress further events for that game for 500ms. Featured games bypass throttling entirely. Simple, prevents fast games from flooding the SSE stream.

**Owner:** server-engineer (SSE emitter)

**Issue:** §13.2 lists `analyze_game(game_id)` as returning "PGN, per-move server_elapsed_ms, and explicit flag / strike / forfeit markers" but does not specify whether this is structured JSON, annotated PGN with comments, or a plain-text report.

**Resolution:** Markdown with three sections: (1) PGN with headers, (2) timing table (ply | move | server_ms | client_ms | remaining_ms), (3) event log (flags, strikes, forfeits with ply numbers). Most readable in Claude transcripts, no PGN parser needed.

**Owner:** mcp-engineer (§5 specifies this in detail)

**Issue:** §8.3 states "three strikes in one game forfeits" and mentions incrementing "the mover's strike counter," but does not specify when strikes reset (per-game vs. per-bot-lifetime) or which table column holds them.

**Recommendation:** Strikes are per-game columns `white_strikes` and `black_strikes` in the `games` table (already present in §5 data model). Reset to 0 at game creation. This matches "in one game" semantics and avoids a bot being penalised in game N+1 for mistakes in game N.

**Why it matters:** Affects move validation logic and testing.

### 7. Leaderboard provisional annotation threshold

**Issue:** §10.1 states "'Provisional' survives as a leaderboard annotation for bots under 10 games — display only, never arithmetic" but does not specify the exact threshold or whether it applies to the dashboard, MCP `get_leaderboard()`, or both.
Resolution:** Per-game columns `white_strikes` and `black_strikes` in `games` table (already in §5 schema). Reset to 0 at game creation. Matches "three strikes in one game" semantics—mistakes in game N don't affect game N+1.

**Owner:** server-engineer (move validation), chess-domain-engineer (strike counting logic if needed)
### 8. Dashboard featured game selection policy

**Issue:** §11 states "the dashboard holds a featured game for at least 20s before switching" but does not define the selection policy (highest-rated participants, longest-running game, round-robin, random, etc.).

**Recommendation:** Feature the active game with the highest sum of participant ratings, held for at least 20s. Ties broken by lowest `game_id` (oldest). This ensures high-stakes games are featured and is deterministic for testing.
Resolution:** Bots with `games_played < 10` are annotated `"is_provisional": true` in all leaderboard responses (HTTP API, MCP, SSE `rating_changed`). Computed field, not a column. Consistent across all surfaces.

**Owner:** server-engineer (API/SSE responses), mcp-engineer (MCP tool responses)Resolution:** Feature the active game with highest sum of participant ratings (white_rating + black_rating), held for at least 20s. Ties broken by lowest `game_id` (oldest). Deterministic, ensures high-stakes games are featured.

**Owner:** dashboard-engineer (client-side selection logic). Server provides white_rating/black_rating in ActiveGameSummary (already in schema above)