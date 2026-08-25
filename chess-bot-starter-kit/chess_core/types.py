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
    CRASH = "crash"
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
