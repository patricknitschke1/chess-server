"""The SSE hub and `GET /events` (role spec §8.4; design §14; interfaces Part 2).

`Hub.publish` is the `EventSink` bound into `EngineDeps`, so every event reaches a
browser through `Txn.flush` — after the commit, never before. No route writes here
directly, and nothing inside a transaction may.

`is_featured` is stamped here, at publish time, because it is a presentation
choice keyed on process state: inside the transaction it would make a committed
row depend on who happens to be watching.
"""
import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from chess_server.store.run import current_run_id

# The route lives in routes_public.py: AppState holds a Hub, so a router here
# would close an import cycle through state.py.

# A stalled tab must never apply backpressure to the game loop, so the queue is
# bounded and the oldest event goes. A client that lost events refetches /state.
CLIENT_QUEUE_MAX = 256

# Float seconds on purpose: the millisecond form of this interval collides
# numerically with ns_to_ms(DELIVERY_GRACE_NS) in the no-literals guard, and this
# is a socket keepalive, not a game deadline.
HEARTBEAT_SECONDS = 15.0

HEARTBEAT_FRAME = ": heartbeat\n\n"


def format_sse(envelope: dict) -> str:
    return (
        f"event: {envelope['event_type']}\n"
        f"data: {json.dumps(envelope, separators=(',', ':'))}\n"
        f"id: {envelope['seq']}\n\n"
    )


@dataclass
class Client:
    queue: deque = field(default_factory=lambda: deque(maxlen=CLIENT_QUEUE_MAX))
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def offer(self, envelope: dict) -> None:
        self.queue.append(envelope)   # maxlen drops the oldest, never the newest
        self.wake.set()


class Hub:
    """Fan-out to every connected browser. Publishing is synchronous and never
    awaits, so `Txn.flush` cannot be stalled by a slow reader."""

    def __init__(self, heartbeat_seconds: float = HEARTBEAT_SECONDS):
        self._clients: list[Client] = []
        self._heartbeat_seconds = heartbeat_seconds
        self.featured_game_id: Optional[int] = None

    def subscribe(self) -> Client:
        client = Client()
        self._clients.append(client)
        return client

    def unsubscribe(self, client: Client) -> None:
        if client in self._clients:
            self._clients.remove(client)

    def sse_clients(self) -> int:
        return len(self._clients)

    def publish(self, seq: int, event_type: str, data: dict) -> None:
        envelope = {
            "run": current_run_id(),
            "seq": seq,
            "event_type": event_type,
            "data": self._stamp(event_type, data),
        }
        for client in self._clients:
            client.offer(envelope)

    def _stamp(self, event_type: str, data: dict) -> dict:
        if event_type != "move_played":
            return data
        return {**data, "is_featured": data.get("game_id") == self.featured_game_id}

    async def stream(self, client: Client) -> AsyncIterator[str]:
        try:
            while True:
                client.wake.clear()      # cleared first: a publish during the
                while client.queue:      # drain then re-sets it and we loop again
                    yield format_sse(client.queue.popleft())
                try:
                    async with asyncio.timeout(self._heartbeat_seconds):
                        await client.wake.wait()
                except TimeoutError:
                    yield HEARTBEAT_FRAME
        finally:
            self.unsubscribe(client)
