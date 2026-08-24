"""Connections and pragmas. One writer on one thread, one reader for display queries."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from chess_server.store.schema import apply_schema

# Applied to every connection, not only the writer. 5000 is a SQLite pragma value,
# not a chess_core constant, and has no name to import.
PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous  = NORMAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA foreign_keys = ON",
)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        conn.execute(pragma)
    return conn


@dataclass
class Store:
    path: str
    writer: sqlite3.Connection
    reader: sqlite3.Connection
    executor: ThreadPoolExecutor
    reader_executor: ThreadPoolExecutor

    def close(self) -> None:
        self.executor.shutdown(wait=True)
        self.reader_executor.shutdown(wait=True)
        self.reader.close()
        self.writer.close()


def open_store(path: str) -> Store:
    writer = _connect(path)
    apply_schema(writer)
    return Store(
        path=path,
        writer=writer,
        reader=_connect(path),
        # max_workers=1 is the invariant: one connection, one thread, one writer.
        executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite-writer"),
        # Its own thread, so a display read never queues behind the writer; still
        # one thread, so nothing concurrently uses the single reader connection.
        reader_executor=ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sqlite-reader"
        ),
    )
