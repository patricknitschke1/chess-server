"""Design §7.1 restart recovery. Runs before the listening socket accepts (§8.6)."""
import sqlite3
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Callable

from chess_core import TerminationReason

from chess_server.store.repositories import BotRepo, GameRepo, SeatRepo
from chess_server.store.run import new_run_id, set_run_id
from chess_server.store.txn import EventSink, Txn, _drop, critical_section, reset_seq

RESTART = TerminationReason.SERVER_RESTART.value


@dataclass
class RecoveryReport:
    run: str
    games_aborted: int
    seats_freed: int
    bots_cleared: int


async def recover_locked(
    txn: Txn, now_wall: str, clear_process_state: Callable[[], None]
) -> RecoveryReport:
    conn, executor = txn.conn, txn.executor
    games_aborted = await GameRepo(conn, executor).abort_all_non_terminal(RESTART, now_wall)
    seats_freed = await SeatRepo(conn, executor).delete_all_seats()
    bots_cleared = await BotRepo(conn, executor).clear_monotonic_state()

    run = new_run_id()
    reset_seq()  # before the flush that assigns seq, so server_run_started is 0
    txn.defer(lambda: set_run_id(run))
    txn.defer(clear_process_state)
    txn.emit("server_run_started", {"run_id": run, "started_at": now_wall})
    return RecoveryReport(
        run=run,
        games_aborted=games_aborted,
        seats_freed=seats_freed,
        bots_cleared=bots_cleared,
    )


async def recover(
    conn: sqlite3.Connection,
    executor: Executor,
    now_wall: str,
    clear_process_state: Callable[[], None],
    sink: EventSink = _drop,
) -> RecoveryReport:
    async with critical_section(conn, executor, sink) as txn:
        return await recover_locked(txn, now_wall, clear_process_state)
