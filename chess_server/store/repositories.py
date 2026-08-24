"""Typed SQL wrappers. No method issues BEGIN, COMMIT, ROLLBACK or SAVEPOINT (§3.6)."""
from chess_core import ClockState, Color, ms_to_ns, ns_to_ms

from chess_server.store.rows import GameRow


def _clock_from_game(game: GameRow) -> ClockState:
    """The single ms -> ns boundary. Nothing else in chess_server/ calls ms_to_ns."""
    return ClockState(
        white_ns=ms_to_ns(game.white_ms),
        black_ns=ms_to_ns(game.black_ms),
        time_control_ns=ms_to_ns(game.time_control_ms),
        increment_ns=ms_to_ns(game.increment_ms),
        to_move=Color(game.to_move),
        to_move_since_mono=game.to_move_since_mono,
        turn_started_mono=game.turn_started_mono,
        delivered_to_mover=game.delivered_to_mover,
    )


def _clock_to_game_fields(clock: ClockState) -> dict:
    """The single ns -> ms boundary. Nothing else in chess_server/ calls ns_to_ms."""
    return {
        "white_ms": ns_to_ms(clock.white_ns),
        "black_ms": ns_to_ms(clock.black_ns),
        "to_move": clock.to_move.value,
        "to_move_since_mono": clock.to_move_since_mono,
        "turn_started_mono": clock.turn_started_mono,
        "delivered_to_mover": clock.delivered_to_mover,
    }
