"""Game creation and the terminal transitions (role spec §6.5, §7.2)."""
from chess_core import STARTING_FEN, ns_to_ms

from chess_server.engine import state
from chess_server.engine.deps import EngineDeps
from chess_server.engine.wall import utc_now_iso
from chess_server.store.repositories import GameRepo, SeatRepo
from chess_server.store.rows import BotRow
from chess_server.store.txn import Txn


async def create_game_locked(
    deps: EngineDeps,
    txn: Txn,
    white: BotRow,
    black: BotRow,
    *,
    time_control_ns: int,
    increment_ns: int,
    source: str,
    now_mono: int,
) -> int:
    """Insert a game and its two seats. The caller supplies the savepoint.

    Takes whole rows, not ids: `rated_at_creation` and the event payload both
    need the owner, role and name.
    """
    games = GameRepo(txn.conn, txn.executor)
    seats = SeatRepo(txn.conn, txn.executor)

    game_id = await games.insert_game(
        white=white,
        black=black,
        time_control_ns=time_control_ns,
        increment_ns=increment_ns,
        source=source,
        now_mono=now_mono,
        created_at=utc_now_iso(),
    )
    await seats.insert_seat(white.id, game_id)
    await seats.insert_seat(black.id, game_id)

    game = await games.get_by_id(game_id)
    txn.defer(lambda: state.history.setdefault(game_id, [STARTING_FEN]))
    txn.emit("game_created", {
        "game_id": game_id,
        "white_bot_id": white.id,
        "white_bot_name": white.name,
        "black_bot_id": black.id,
        "black_bot_name": black.name,
        "status": game.status,
        "rated": bool(game.rated),
        "source": source,
        "time_control_ms": ns_to_ms(time_control_ns),
        "increment_ms": ns_to_ms(increment_ns),
    })
    txn.defer(lambda: deps.wake(white.id))
    txn.defer(lambda: deps.wake(black.id))
    return game_id
