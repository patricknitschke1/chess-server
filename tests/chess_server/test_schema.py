import sqlite3

import pytest

from chess_server.store.schema import apply_schema


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "arena.db")


@pytest.fixture
def conn(db_path):
    """Schema applied, foreign keys OFF so constraint tests isolate one constraint."""
    c = sqlite3.connect(db_path)
    apply_schema(c)
    yield c
    c.close()


@pytest.fixture
def fk_conn(db_path):
    c = sqlite3.connect(db_path)
    c.execute("PRAGMA foreign_keys = ON")
    apply_schema(c)
    yield c
    c.close()


def _insert_bot(conn, name="alpha", owner="ada"):
    cur = conn.execute(
        "INSERT INTO bots (name, owner, token_hash, role, created_at)"
        " VALUES (?, ?, 'h', 'competitor', '2026-08-24T00:00:00Z')",
        (name, owner),
    )
    return cur.lastrowid


def test_seats_null_bot_id_is_rejected(conn):
    # WITHOUT ROWID is the only thing that rejects this: on a rowid table the
    # auto rowid is substituted before constraint checking and NULL becomes 1.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO seats (bot_id, game_id) VALUES (NULL, 1)")


def test_seats_second_seat_for_same_bot_is_rejected(conn):
    conn.execute("INSERT INTO seats (bot_id, game_id) VALUES (7, 1)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO seats (bot_id, game_id) VALUES (7, 2)")


def test_seats_unknown_bot_id_is_rejected(fk_conn):
    with pytest.raises(sqlite3.IntegrityError):
        fk_conn.execute("INSERT INTO seats (bot_id, game_id) VALUES (999, 1)")


def test_moves_duplicate_ply_is_rejected(conn):
    args = (1, 0, "e2e4", "e4", "fen", 100)
    conn.execute(
        "INSERT INTO moves (game_id, ply, uci, san, fen_after, server_elapsed_ms)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        args,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO moves (game_id, ply, uci, san, fen_after, server_elapsed_ms)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            args,
        )


def test_rating_history_double_rating_one_game_is_rejected(conn):
    args = (1, 1, 1200, 1216, 16, "2026-08-24T00:00:00Z")
    sql = (
        "INSERT INTO rating_history (bot_id, game_id, rating_before, rating_after,"
        " delta, ts) VALUES (?, ?, ?, ?, ?, ?)"
    )
    conn.execute(sql, args)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, args)


def test_bots_duplicate_name_is_rejected(conn):
    _insert_bot(conn, name="alpha")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_bot(conn, name="alpha", owner="bob")


def test_deferred_tables_are_absent(conn):
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "mailbox" not in names
    assert "arena_reports" not in names


@pytest.mark.parametrize(
    "table,column",
    [
        ("games", "to_move"),
        ("challenges", "reason"),
        ("challenges", "created_mono"),
        ("bots", "last_color"),
        ("bots", "white_count"),
        ("bots", "last_opponent_id"),
    ],
)
def test_required_columns_exist(conn, table, column):
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    assert column in cols


def test_indexes_exist(conn):
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_games_status" in names
    assert "idx_bots_token_hash" in names


def test_apply_schema_is_idempotent(conn, db_path):
    second = sqlite3.connect(db_path)
    try:
        apply_schema(second)
    finally:
        second.close()
