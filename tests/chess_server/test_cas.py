import pytest

from chess_server.store.cas import CASConflict, InvariantViolation, assert_cas


@pytest.fixture
def seeded(store):
    for name in ("alpha", "beta"):
        store.writer.execute(
            "INSERT INTO bots (name, owner, token_hash, role, created_at)"
            " VALUES (?, 'ada', 'h', 'competitor', '2026-08-24T00:00:00Z')",
            (name,),
        )
    return store


def test_zero_rows_is_a_lost_race(seeded):
    cur = seeded.writer.execute("UPDATE bots SET rating = 1300 WHERE name = 'nobody'")
    with pytest.raises(CASConflict):
        assert_cas(cur)


def test_too_many_rows_is_a_corrupted_invariant(seeded):
    cur = seeded.writer.execute("UPDATE bots SET rating = 1300")
    with pytest.raises(InvariantViolation):
        assert_cas(cur)


def test_exactly_one_row_returns(seeded):
    cur = seeded.writer.execute("UPDATE bots SET rating = 1300 WHERE name = 'alpha'")
    assert_cas(cur)


def test_the_two_exceptions_are_unrelated():
    # 3b maps CASConflict to 409; an InvariantViolation must never take that path.
    assert not issubclass(CASConflict, InvariantViolation)
    assert not issubclass(InvariantViolation, CASConflict)
