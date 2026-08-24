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
    """Flagged move does not receive increment per §6.4.

    Asserts the exact remainder. A bound like `< increment_ns` is satisfied by
    the buggy value too (0.5s − 1s + 2s = 1.5s < 2s), so it cannot detect the
    rule it is named for.
    """
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
    # 0.5s remaining − 1s elapsed = −0.5s, and no increment is added on top.
    assert result.new_clock.white_ns == -500_000_000


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
    receive_time = delivered_at + 9_000_000_000
    result = clock.account_move_and_switch(after_rejection, receive_time, receive_time)
    assert result.elapsed_ms == 9000
    assert result.flagged is False
    # 180s - 9s + 2s increment
    assert result.new_clock.white_ns == 173_000_000_000


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
    assert clock.AGENT_AUTO_RELEASE_NS == 45_000_000_000
    assert clock.POLL_RECENCY_NS == 5_000_000_000
    assert clock.CHALLENGE_TTL_NS == 60_000_000_000
    assert clock.POLL_HOLD_NS == 20_000_000_000
    assert clock.TICK_INTERVAL_NS == 1_000_000_000


def test_clock_is_the_sole_declaration_site_for_its_constants():
    """§5.2: clock.py declares, everything else imports.

    Pinned by identity against the package re-export so a second declaration
    elsewhere cannot quietly become the one people use.
    """
    import chess_core

    for name in (
        "RATED_TIME_CONTROL_NS",
        "RATED_INCREMENT_NS",
        "EXHIBITION_TIME_CONTROL_NS",
        "EXHIBITION_INCREMENT_NS",
        "DELIVERY_GRACE_NS",
        "AGENT_DELIVERY_GRACE_NS",
        "AGENT_AUTO_RELEASE_NS",
        "POLL_RECENCY_NS",
        "CHALLENGE_TTL_NS",
        "POLL_HOLD_NS",
        "TICK_INTERVAL_NS",
    ):
        assert getattr(chess_core, name) == getattr(clock, name), name


# has_flagged / remaining_ns — §6.4's predicate asked as a separate question

def _delivered_clock(white_ns, black_ns, to_move, started=1_000_000):
    return ClockState(
        white_ns=white_ns,
        black_ns=black_ns,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=to_move,
        to_move_since_mono=started,
        turn_started_mono=started,
        delivered_to_mover=1
    )


def test_has_flagged_agrees_with_account_move_and_switch():
    """The predicate must not drift from the atomic function that also applies it.

    This helper exists so the server does not hand-roll `<= 0` in the ticker and
    again at the move endpoint. It earns that only by matching exactly.
    """
    started = 1_000_000
    for remaining in (5_000_000_000, 2_000_000_000, 1, 0):
        for elapsed in (0, 1_999_999_999, 2_000_000_000, 2_000_000_001):
            for color in (Color.WHITE, Color.BLACK):
                white = remaining if color == Color.WHITE else 180_000_000_000
                black = remaining if color == Color.BLACK else 180_000_000_000
                state = _delivered_clock(white, black, color, started)
                now = started + elapsed

                predicted = clock.has_flagged(state, now)
                applied = clock.account_move_and_switch(state, now, now).flagged

                assert predicted is applied, (
                    f"remaining={remaining} elapsed={elapsed} {color}: "
                    f"has_flagged={predicted} but account_move_and_switch={applied}"
                )


def test_has_flagged_is_true_at_exactly_zero():
    """§6.4: reaching exactly zero is a flag, not a reprieve."""
    started = 1_000_000
    state = _delivered_clock(2_000_000_000, 180_000_000_000, Color.WHITE, started)

    assert clock.has_flagged(state, started + 1_999_999_999) is False
    assert clock.has_flagged(state, started + 2_000_000_000) is True


def test_remaining_ns_charges_only_the_side_to_move():
    """The waiting side's clock is frozen; charging both is the classic bug."""
    started = 1_000_000
    state = _delivered_clock(10_000_000_000, 20_000_000_000, Color.WHITE, started)
    now = started + 3_000_000_000

    assert clock.remaining_ns(state, Color.WHITE, now) == 7_000_000_000
    assert clock.remaining_ns(state, Color.BLACK, now) == 20_000_000_000


def test_remaining_ns_does_not_run_before_delivery():
    """An undelivered position has started nobody's turn (§6.2)."""
    state = ClockState(
        white_ns=10_000_000_000,
        black_ns=20_000_000_000,
        time_control_ns=180_000_000_000,
        increment_ns=2_000_000_000,
        to_move=Color.WHITE,
        to_move_since_mono=1_000_000,
        turn_started_mono=None,
        delivered_to_mover=0
    )
    far_future = 1_000_000 + 999_000_000_000

    assert clock.remaining_ns(state, Color.WHITE, far_future) == 10_000_000_000
    assert clock.has_flagged(state, far_future) is False


def test_is_within_boundary_is_inclusive():
    """Poll recency and challenge TTL both hinge on this boundary."""
    now = 10_000_000_000
    window = 5_000_000_000

    assert clock.is_within(now - window + 1, now, window) is True
    assert clock.is_within(now - window, now, window) is True
    assert clock.is_within(now - window - 1, now, window) is False


def test_window_start_mono_agrees_with_is_within():
    """A SQL bound and the in-process predicate must not disagree at the edge."""
    now = 10_000_000_000
    window = 5_000_000_000
    cutoff = clock.window_start_mono(now, window)

    for value in (cutoff - 1, cutoff, cutoff + 1, now):
        assert (value >= cutoff) is clock.is_within(value, now, window)


def test_elapsed_ms_floors_like_every_other_boundary_conversion():
    """One rounding direction across the whole ns->ms boundary, or clocks drift."""
    start = 1_000_000_000

    assert clock.elapsed_ms(start, start) == 0
    assert clock.elapsed_ms(start, start + 999_999) == 0
    assert clock.elapsed_ms(start, start + 1_000_000) == 1
    assert clock.elapsed_ms(start, start + 1_999_999) == 1
    assert clock.elapsed_ms(start, start + 15_000_000_000) == 15_000
