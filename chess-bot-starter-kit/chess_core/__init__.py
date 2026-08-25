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
    DELIVERY_GRACE_NS,
    POLL_RECENCY_NS,
    POLL_HOLD_NS,
    TICK_INTERVAL_NS,
    create_clock,
    deliver_position,
    is_within,
    elapsed_ms,
    window_start_mono,
    remaining_ns,
    has_flagged,
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
    "DELIVERY_GRACE_NS",
    "POLL_RECENCY_NS",
    "POLL_HOLD_NS",
    "TICK_INTERVAL_NS",
    "create_clock",
    "deliver_position",
    "is_within",
    "elapsed_ms",
    "window_start_mono",
    "remaining_ns",
    "has_flagged",
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
