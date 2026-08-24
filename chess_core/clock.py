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
POLL_RECENCY_NS = 5_000_000_000          # pool eligibility window (§9.1)
CHALLENGE_TTL_NS = 60_000_000_000        # open challenge lifetime (§12)
POLL_HOLD_NS = 20_000_000_000            # server long-poll hold (§8.4)
TICK_INTERVAL_NS = 1_000_000_000         # ticker period (§4.6)


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


def remaining_ns(
    clock: ClockState,
    color: Color,
    now_mono: int
) -> int:
    """Nanoseconds left on one side's clock, counting time burnt this turn.

    Only the side to move burns time, and only once the position has been
    delivered — an undelivered position has not started anyone's turn (§6.2).
    May go negative; that is what flag-fall looks like.
    """
    stored_ns = clock.white_ns if color == Color.WHITE else clock.black_ns
    if color != clock.to_move or clock.turn_started_mono is None:
        return stored_ns
    return stored_ns - (now_mono - clock.turn_started_mono)


def has_flagged(clock: ClockState, now_mono: int) -> bool:
    """Whether the side to move has run out of time, per §6.4's `<= 0`.

    Exists so that §6.4's "flag precedes illegal-move validation" ordering can be
    asked as a separate question. `account_move_and_switch` is deliberately
    atomic and cannot answer it without also mutating, so without this the
    server would hand-roll the predicate in the ticker and again at the move
    endpoint — the two-places-one-rule shape that got the predicate stated
    inconsistently once already.
    """
    return remaining_ns(clock, clock.to_move, now_mono) <= 0


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
