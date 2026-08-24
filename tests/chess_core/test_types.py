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
