import pytest

from chess_server.store.db import open_store


@pytest.fixture
def store(tmp_path):
    """File-backed, never ':memory:' — two ':memory:' connections are two databases,
    so the reader/writer split, WAL and BEGIN IMMEDIATE contention are unobservable."""
    s = open_store(str(tmp_path / "arena.db"))
    try:
        yield s
    finally:
        s.close()
