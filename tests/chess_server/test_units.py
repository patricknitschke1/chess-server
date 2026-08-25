import pathlib
import re

import pytest

from chess_core import (
    AGENT_AUTO_RELEASE_NS,
    AGENT_DELIVERY_GRACE_NS,
    ANCHOR_RATING_WINDOW,
    CHALLENGE_TTL_NS,
    DELIVERY_GRACE_NS,
    EXHIBITION_INCREMENT_NS,
    EXHIBITION_TIME_CONTROL_NS,
    K_FACTOR,
    PLY_CAP,
    POLL_HOLD_NS,
    POLL_RECENCY_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    STARTING_RATING,
    TICK_INTERVAL_NS,
    Color,
    create_clock,
    ns_to_ms,
)
from chess_server.store.repositories import _clock_from_game, _clock_to_game_fields
from chess_server.store.rows import GameRow

_NS_CONSTANTS = (
    RATED_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    EXHIBITION_TIME_CONTROL_NS,
    EXHIBITION_INCREMENT_NS,
    DELIVERY_GRACE_NS,
    AGENT_DELIVERY_GRACE_NS,
    AGENT_AUTO_RELEASE_NS,
    POLL_RECENCY_NS,
    CHALLENGE_TTL_NS,
    POLL_HOLD_NS,
    TICK_INTERVAL_NS,
)

FORBIDDEN_LITERALS = (
    set(_NS_CONSTANTS)
    | {ns_to_ms(value) for value in _NS_CONSTANTS}
    | {STARTING_RATING, K_FACTOR, ANCHOR_RATING_WINDOW, PLY_CAP}
)

# Auditable by module name, per plan task 7: SQL text and a SQLite pragma value.
ALLOWED = {
    "schema.py": {STARTING_RATING},
    "db.py": {ns_to_ms(POLL_RECENCY_NS)},
    # Centipawn magnitudes and placeholder anchor ratings that happen to equal
    # PLY_CAP, ns_to_ms(TICK_INTERVAL_NS), STARTING_RATING and ns_to_ms(POLL_HOLD_NS).
    # Numerically equal, semantically unrelated: importing any of them here would
    # assert a dependency that does not exist.
    "reference_bots.py": {
        PLY_CAP, STARTING_RATING, ns_to_ms(TICK_INTERVAL_NS), ns_to_ms(POLL_HOLD_NS),
    },
    # Ops thresholds for tick staleness, numerically equal to DELIVERY_GRACE_NS
    # and POLL_RECENCY_NS and unrelated to either. They decide no game outcome.
    "supervisor.py": {DELIVERY_GRACE_NS, POLL_RECENCY_NS},
    # How long a board stays on the projector, numerically POLL_HOLD_NS and
    # semantically unrelated to it. A display choice, not a game deadline.
    "featured.py": {POLL_HOLD_NS},
}


def _game_row(clock, **overrides):
    fields = {
        "id": 1,
        "white_bot_id": 1,
        "black_bot_id": 2,
        "status": "active",
        "result": None,
        "termination": None,
        "fen": "startpos",
        "ply": 0,
        "time_control_ms": ns_to_ms(clock.time_control_ns),
        "increment_ms": ns_to_ms(clock.increment_ns),
        "rated": 1,
        "source": "matchmaker",
        "white_strikes": 0,
        "black_strikes": 0,
        "created_at": "2026-08-24T00:00:00Z",
        "started_at": None,
        "ended_at": None,
    }
    fields.update(_clock_to_game_fields(clock))
    fields.update(overrides)
    return GameRow(**fields)


def test_fresh_rated_clock_is_stored_as_180000_ms():
    clock = create_clock(RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, Color.WHITE, 0)
    fields = _clock_to_game_fields(clock)
    assert fields["white_ms"] == ns_to_ms(RATED_TIME_CONTROL_NS)
    # Stated twice on purpose: a broken ns_to_ms cannot satisfy both.
    assert fields["white_ms"] == 180_000
    assert fields["black_ms"] == 180_000


@pytest.mark.parametrize("turn_started_mono", [None, 987_654_321_123])
def test_monotonic_fields_survive_the_round_trip_bit_identical(turn_started_mono):
    clock = create_clock(RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, Color.WHITE, 42_000_000_007)
    clock = type(clock)(
        **{**vars(clock), "turn_started_mono": turn_started_mono,
           "delivered_to_mover": 0 if turn_started_mono is None else 1}
    )
    back = _clock_from_game(_game_row(clock))
    assert back.to_move_since_mono == clock.to_move_since_mono
    assert back.turn_started_mono == clock.turn_started_mono


@pytest.mark.parametrize("to_move", [Color.WHITE, Color.BLACK])
def test_round_trip_loses_under_one_ms_symmetrically(to_move):
    clock = create_clock(RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, to_move, 0)
    clock = type(clock)(
        **{**vars(clock),
           "white_ns": clock.white_ns - 999_999,
           "black_ns": clock.black_ns - 999_999}
    )
    back = _clock_from_game(_game_row(clock))
    for lost in (clock.white_ns - back.white_ns, clock.black_ns - back.black_ns):
        assert 0 <= lost < 1_000_000


def _server_sources():
    root = pathlib.Path(__file__).resolve().parents[2] / "chess_server"
    return sorted(root.rglob("*.py"))


def test_no_named_constant_appears_as_a_literal_in_chess_server():
    offenders = []
    for path in _server_sources():
        allowed = ALLOWED.get(path.name, set())
        text = re.sub(r"(?<=\d)_(?=\d)", "", path.read_text())
        for value in FORBIDDEN_LITERALS - allowed:
            if re.search(rf"(?<![\w.]){value}(?![\d])", text):
                offenders.append(f"{path.name}: {value}")
    assert offenders == []
