import sqlite3
import threading
import time

import pytest


def _scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_journal_mode_is_wal_on_every_connection(store):
    # The reader is asserted first: pragmas applied to the writer only is the defect.
    assert _scalar(store.reader, "PRAGMA journal_mode").lower() == "wal"
    assert _scalar(store.writer, "PRAGMA journal_mode").lower() == "wal"


def test_foreign_keys_enforced_on_every_connection(store):
    assert _scalar(store.reader, "PRAGMA foreign_keys") == 1
    assert _scalar(store.writer, "PRAGMA foreign_keys") == 1
    with pytest.raises(sqlite3.IntegrityError):
        store.reader.execute("INSERT INTO seats (bot_id, game_id) VALUES (999, 1)")


def _busy_ident():
    # The pause keeps every worker occupied; resolving each future before the next
    # submit lets a two-worker pool reuse one idle thread and look single-threaded.
    time.sleep(0.02)
    return threading.get_ident()


def test_writer_executor_is_a_single_thread(store):
    futures = [store.executor.submit(_busy_ident) for _ in range(10)]
    idents = {f.result() for f in futures}
    assert len(idents) == 1
    assert idents != {threading.get_ident()}


def test_reader_is_a_distinct_connection_that_sees_committed_writes(store):
    assert store.reader is not store.writer
    store.writer.execute(
        "INSERT INTO bots (name, owner, token_hash, role, created_at)"
        " VALUES ('alpha', 'ada', 'h', 'competitor', '2026-08-24T00:00:00Z')"
    )
    store.writer.commit()
    assert _scalar(store.reader, "SELECT count(*) FROM bots") == 1


def test_busy_timeout_is_set_on_every_connection(store):
    assert _scalar(store.reader, "PRAGMA busy_timeout") == 5000
    assert _scalar(store.writer, "PRAGMA busy_timeout") == 5000
