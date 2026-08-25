"""The mailbox and the waiters: how a delivery reaches the poll that collects it.

Role spec §5. The mailbox is process state on purpose (§5.1): a table would buy a
write on the hottest path under `write_lock`, in exchange for durability that §7.1
throws away on every start.

The delivery lives in the mailbox and the mailbox outlives the request, which is
what makes a superseded poll a cancelled *waiter* and never a lost *delivery*.
"""
import asyncio
from dataclasses import dataclass
from typing import Callable, List, Optional

from chess_core import Color, get_legal_moves

from chess_server.engine import state
from chess_server.engine.deps import EngineDeps
from chess_server.engine.runner import deliver_position_locked
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.rows import BotRow, GameRow
from chess_server.store.txn import Txn, critical_section


@dataclass(frozen=True)
class TurnPayload:
    """Exactly the `TurnResponse` wire fields, plus the bot it was built for."""

    bot_id: int
    game_id: int
    ply: int
    color: str
    fen: str
    legal_moves: List[str]
    history_san: List[str]
    white_ms: int
    black_ms: int
    time_control_ms: int
    increment_ms: int


def color_of(bot_id: int, game: GameRow) -> str:
    return Color.WHITE.value if game.white_bot_id == bot_id else Color.BLACK.value


def build_payload(bot: BotRow, game: GameRow) -> TurnPayload:
    return TurnPayload(
        bot_id=bot.id,
        game_id=game.id,
        ply=game.ply,
        color=color_of(bot.id, game),
        fen=game.fen,
        legal_moves=get_legal_moves(game.fen),
        # From the cache, never from `moves`: an O(ply) read here would run on the
        # hottest path in the server, inside the writer's lock.
        history_san=list(state.history_san.get(game.id, [])),
        white_ms=game.white_ms,
        black_ms=game.black_ms,
        time_control_ms=game.time_control_ms,
        increment_ms=game.increment_ms,
    )


def fill_mailbox_locked(txn: Txn, bot: BotRow, game: GameRow) -> TurnPayload:
    """Deferred, so a rolled-back delivery cannot hand a bot a position the
    database does not believe was delivered."""
    payload = build_payload(bot, game)
    txn.defer(lambda: state.mailbox.__setitem__(bot.id, payload))
    return payload


async def deliver_for_poll(deps: EngineDeps, bot_id: int) -> Optional[GameRow]:
    """The outer form, and the only delivery both HTTP call sites use (§5.2).

    Returns the post-delivery row, or None when the bot holds no seat — a game is
    reachable only through `seats`.
    """
    async with critical_section(deps.conn, deps.executor, deps.sink) as txn:
        games = GameRepo(txn.conn, txn.executor)
        game = await games.get_for_bot(bot_id)
        if game is None:
            return None
        await deliver_position_locked(deps, txn, game, deps.now_mono())
        delivered = await games.get_by_id(game.id)
        bot = await BotRepo(txn.conn, txn.executor).get_by_id(bot_id)
        fill_mailbox_locked(txn, bot, delivered)
        return delivered


def take_payload(bot_id: int, game: GameRow) -> Optional[TurnPayload]:
    """The payload for this exact position, or None — and a stale entry is dropped.

    A served payload **stays** in the mailbox. It is the record of the position
    currently delivered to this bot, which is what makes re-reading it free and
    identical (§5.2); it is removed when the side switches or the game ends.
    Draining it here instead would make the clear in `apply_move_locked`
    unreachable, and §5.3's second layer the only one left standing.

    `game_id` is compared as well as `ply`: a bot paired again sits at ply 0,
    where a stale ply-0 payload from the previous game would otherwise match.
    """
    payload = state.mailbox.get(bot_id)
    if payload is None:
        return None
    if payload.game_id != game.id or payload.ply != game.ply:
        state.mailbox.pop(bot_id, None)
        return None
    return payload


@dataclass
class Waiter:
    event: asyncio.Event
    superseded: bool = False


class WaiterRegistry:
    """One waiter per bot (§5.4). `EngineDeps.wake` binds to `wake`."""

    def __init__(self, on_register: Optional[Callable[[int], None]] = None):
        self._waiters: dict[int, Waiter] = {}
        self.on_register = on_register

    def register(self, bot_id: int) -> Waiter:
        """The newcomer evicts the incumbent. The flag is set before the event so
        the loser can never observe a wake it would mistake for a delivery."""
        incumbent = self._waiters.get(bot_id)
        if incumbent is not None:
            incumbent.superseded = True
            incumbent.event.set()
        waiter = Waiter(event=asyncio.Event())
        self._waiters[bot_id] = waiter
        if self.on_register is not None:
            self.on_register(bot_id)
        return waiter

    def wake(self, bot_id: int) -> None:
        """A no-op for a bot that is not polling: the ticker wakes both seats on
        every pairing, and most bots are not held at that moment."""
        waiter = self._waiters.get(bot_id)
        if waiter is not None:
            waiter.event.set()

    def discard(self, bot_id: int, waiter: Waiter) -> None:
        """Only if it is still the registered one — a superseded waiter cleaning
        up must not evict the successor that replaced it."""
        if self._waiters.get(bot_id) is waiter:
            del self._waiters[bot_id]

    def held_count(self) -> int:
        return len(self._waiters)
