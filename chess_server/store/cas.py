"""Compare-and-swap assertions for every game-state transition (role spec §3.10)."""
import sqlite3


class CASConflict(Exception):
    """The row was not in the state we were transitioning from. A lost race: 409."""


class InvariantViolation(Exception):
    """More rows moved than should exist. Corruption, never a 409."""


def assert_cas(cursor: sqlite3.Cursor, expected: int = 1) -> None:
    if cursor.rowcount < expected:
        raise CASConflict(
            f"expected {expected} row(s) in the from-state, matched {cursor.rowcount}"
        )
    if cursor.rowcount > expected:
        raise InvariantViolation(
            f"expected {expected} row(s), matched {cursor.rowcount}"
        )
