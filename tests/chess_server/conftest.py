import asyncio

import pytest

from chess_server.store import txn
from chess_server.store.db import open_store


@pytest.fixture(autouse=True)
def _fresh_write_lock(monkeypatch):
    """asyncio.Lock binds to the first loop that *contends* it and raises in any
    other. The server has one loop for its lifetime; pytest-asyncio gives each test
    its own, so without this only the first contending test can ever deadlock."""
    monkeypatch.setattr(txn, "write_lock", asyncio.Lock())


@pytest.fixture
def store(tmp_path):
    """File-backed, never ':memory:' — two ':memory:' connections are two databases,
    so the reader/writer split, WAL and BEGIN IMMEDIATE contention are unobservable."""
    s = open_store(str(tmp_path / "arena.db"))
    try:
        yield s
    finally:
        s.close()
