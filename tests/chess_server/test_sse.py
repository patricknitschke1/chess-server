"""The SSE hub and GET /events (role spec §8.4; design §14; interfaces Part 2).

Nothing here sleeps to observe behaviour. The hub wakes a stream through an
asyncio.Event, so every test drives the wake-up it is asserting on.
"""
import asyncio
import json

import pytest
from starlette.requests import Request

from chess_core import (
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    GameResult,
    TerminationReason,
)
from chess_server.api.app import create_app
from chess_server.api.routes_public import events
from chess_server.api.settings import Settings
from chess_server.api.sse import CLIENT_QUEUE_MAX, Hub, format_sse
from chess_server.api.state import AppState
from chess_server.engine.deps import EngineDeps
from chess_server.engine.games import create_game_locked, finalise_game_locked
from chess_server.engine.mailbox import deliver_for_poll
from chess_server.engine.runner import apply_move
from chess_server.engine.ticker import step_presence
from chess_server.store.recovery import recover
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.run import current_run_id
from chess_server.store.txn import (
    critical_section,
    current_seq,
    reset_seq,
)

from tests.chess_server.conftest import ADMIN_TOKEN, JOIN_CODE, WALL

# Interfaces Part 2, field for field. A catalogue entry with no producer is a
# promise to the dashboard that nothing keeps, so the covered set is asserted too.
PART2_FIELDS = {
    "server_run_started": {"run_id", "started_at"},
    "game_created": {
        "game_id", "white_bot_id", "white_bot_name", "black_bot_id",
        "black_bot_name", "status", "rated", "source", "time_control_ms",
        "increment_ms",
    },
    "game_started": {
        "game_id", "white_bot_id", "white_bot_name", "black_bot_id",
        "black_bot_name", "started_at",
    },
    "move_played": {
        "game_id", "ply", "uci", "san", "fen", "to_move", "white_ms", "black_ms",
        "turn_elapsed_ms", "server_elapsed_ms", "is_featured",
    },
    "game_ended": {
        "game_id", "white_bot_id", "white_bot_name", "black_bot_id",
        "black_bot_name", "status", "result", "termination", "rated",
        "final_ply", "ended_at",
    },
    "rating_changed": {
        "bot_id", "bot_name", "rating_before", "rating_after", "delta", "game_id",
    },
    "bot_registered": {"bot_id", "bot_name", "role", "rating"},
    "bot_connected": {"bot_id", "bot_name"},
    "bot_disconnected": {"bot_id", "bot_name"},
}


@pytest.fixture
def hub():
    return Hub()


@pytest.fixture
def hub_state(store, clock):
    """An AppState with no injected sink, so the hub is the real EventSink."""
    return AppState(
        store=store,
        settings=Settings(
            db_path=store.path, join_code=JOIN_CODE, admin_token=ADMIN_TOKEN
        ),
        now_mono=clock,
    )


def _seqs(client):
    return [envelope["seq"] for envelope in client.queue]


# --- 1. bounded, drop-oldest -------------------------------------------------

# Game 1 is featured throughout this section: coalescing (test_featured.py) would
# otherwise swallow the flood these tests exist to produce.

def test_a_stalled_client_never_blocks_a_publisher(hub):
    hub.featured_game_id = 1
    client = hub.subscribe()

    for seq in range(300):
        hub.publish(seq, "move_played", {"game_id": 1})

    assert len(client.queue) == CLIENT_QUEUE_MAX


def test_the_surviving_window_is_the_newest_not_the_oldest(hub):
    hub.featured_game_id = 1
    client = hub.subscribe()

    for seq in range(300):
        hub.publish(seq, "move_played", {"game_id": 1})

    assert _seqs(client) == list(range(300 - CLIENT_QUEUE_MAX, 300))


def test_one_stalled_client_does_not_starve_another(hub):
    hub.featured_game_id = 1
    stalled = hub.subscribe()
    for seq in range(300):
        hub.publish(seq, "move_played", {"game_id": 1})

    fresh = hub.subscribe()
    hub.publish(300, "move_played", {"game_id": 1})

    assert _seqs(fresh) == [300]
    assert _seqs(stalled)[-1] == 300


# --- 2. nothing visible before commit ----------------------------------------

async def test_a_rolled_back_unit_publishes_nothing_and_consumes_no_seq(store, hub):
    client = hub.subscribe()
    before = current_seq()

    with pytest.raises(RuntimeError):
        async with critical_section(
            store.writer, store.executor, hub.publish
        ) as txn:
            txn.emit("game_created", {"game_id": 1})
            raise RuntimeError("the unit fails after emitting")

    assert list(client.queue) == []
    assert current_seq() == before


async def test_a_committed_unit_publishes_after_the_commit(store, hub):
    client = hub.subscribe()

    async with critical_section(store.writer, store.executor, hub.publish) as txn:
        txn.emit("game_created", {"game_id": 1})
        assert list(client.queue) == []  # still inside the transaction

    assert [envelope["event_type"] for envelope in client.queue] == ["game_created"]


# --- 3. the Part 2 catalogue --------------------------------------------------

async def _play_everything(deps, store, hub) -> None:
    """Drive one real emission of every Part 2 event that has a producer."""
    bots = BotRepo(store.writer, store.executor)
    games = GameRepo(store.writer, store.executor)

    async with critical_section(store.writer, store.executor, hub.publish) as txn:
        ids = [
            await bots.insert_bot(
                name=name, owner=f"{name}-owner", token_hash=f"hash-{name}",
                role="competitor", rating=1200, is_anchor=0, created_at=WALL,
            )
            for name in ("alpha", "beta")
        ]
        for bot_id in ids:
            txn.emit("bot_registered", {
                "bot_id": bot_id, "bot_name": f"bot-{bot_id}",
                "role": "competitor", "rating": 1200,
            })
        await bots.update_last_poll(ids[0], WALL, deps.now_mono())
        await bots.update_last_poll(ids[1], WALL, deps.now_mono())

    white, black = [await bots.get_by_id(bot_id) for bot_id in ids]

    async with critical_section(store.writer, store.executor, hub.publish) as txn:
        game_id = await create_game_locked(
            deps, txn, white, black,
            time_control_ns=RATED_TIME_CONTROL_NS,
            increment_ns=RATED_INCREMENT_NS,
            source="matchmaker",
            now_mono=deps.now_mono(),
        )

    await deliver_for_poll(deps, white.id)          # game_started
    await apply_move(deps, game_id, 0, "e2e4")      # move_played

    async with critical_section(store.writer, store.executor, hub.publish) as txn:
        await finalise_game_locked(
            deps, txn, await games.get_by_id(game_id),
            GameResult.WHITE_WIN, TerminationReason.CHECKMATE,
        )                                            # game_ended + rating_changed

    async with critical_section(store.writer, store.executor, hub.publish) as txn:
        await step_presence(deps, txn, deps.now_mono())        # bot_connected


async def test_every_produced_part2_event_carries_exactly_its_pinned_fields(
    store, clock, hub, wake
):
    deps = EngineDeps(
        conn=store.writer, executor=store.executor, sink=hub.publish,
        wake=wake, now_mono=clock,
    )
    client = hub.subscribe()
    await recover(store.writer, store.executor, WALL, lambda: None, hub.publish)
    await _play_everything(deps, store, hub)

    seen = {}
    for envelope in client.queue:
        seen.setdefault(envelope["event_type"], set(envelope["data"]))

    for event_type, fields in seen.items():
        assert fields == PART2_FIELDS[event_type], event_type
    assert seen.keys() >= {
        "server_run_started", "bot_registered", "game_created", "game_started",
        "move_played", "game_ended", "rating_changed", "bot_connected",
    }


async def test_server_run_started_carries_run_id_and_started_at(store, hub):
    client = hub.subscribe()

    report = await recover(store.writer, store.executor, WALL, lambda: None, hub.publish)

    envelope = client.queue[0]
    assert envelope["event_type"] == "server_run_started"
    assert envelope["data"] == {"run_id": report.run, "started_at": WALL}
    assert envelope["run"] == report.run  # the run also rides the envelope


# --- 4. {run, seq} ------------------------------------------------------------

def test_seq_rides_the_envelope_and_strictly_increases(hub, store):
    client = hub.subscribe()

    for seq in range(5):
        hub.publish(seq, "health_tick", {})

    assert _seqs(client) == [0, 1, 2, 3, 4]


async def test_a_new_run_restarts_seq_at_zero_with_a_different_run_id(store, hub):
    client = hub.subscribe()
    async with critical_section(store.writer, store.executor, hub.publish) as txn:
        txn.emit("health_tick", {})
    first_run = current_run_id()

    await recover(store.writer, store.executor, WALL, lambda: None, hub.publish)

    restart = [e for e in client.queue if e["event_type"] == "server_run_started"][0]
    assert restart["seq"] == 0
    assert restart["run"] != first_run


def test_reset_seq_alone_does_not_forge_a_new_run(hub):
    """A client that sees seq restart under an unchanged run has been lied to."""
    client = hub.subscribe()
    hub.publish(0, "health_tick", {})
    reset_seq()
    hub.publish(0, "health_tick", {})

    runs = {envelope["run"] for envelope in client.queue}
    assert len(runs) == 1


# --- 5. no tokens, no owners --------------------------------------------------

async def test_no_published_payload_carries_a_token_or_an_owner(
    store, clock, hub, wake
):
    deps = EngineDeps(
        conn=store.writer, executor=store.executor, sink=hub.publish,
        wake=wake, now_mono=clock,
    )
    client = hub.subscribe()
    await recover(store.writer, store.executor, WALL, lambda: None, hub.publish)
    await _play_everything(deps, store, hub)

    assert client.queue
    for envelope in client.queue:
        blob = json.dumps(envelope)
        assert "owner" not in envelope["data"]
        assert "token" not in blob
        assert "hash-" not in blob


# --- 6. streaming and client accounting ---------------------------------------

def test_format_sse_writes_event_data_and_id_lines(hub):
    frame = format_sse({"run": "r1", "seq": 7, "event_type": "game_ended", "data": {}})

    lines = frame.split("\n")
    assert lines[0] == "event: game_ended"
    assert json.loads(lines[1].removeprefix("data: "))["seq"] == 7
    assert lines[2] == "id: 7"
    assert frame.endswith("\n\n")


async def test_a_closed_stream_unsubscribes_and_sse_clients_drops(hub):
    client = hub.subscribe()
    stream = hub.stream(client)
    hub.publish(0, "health_tick", {})
    assert "health_tick" in await anext(stream)
    assert hub.sse_clients() == 1

    await stream.aclose()

    assert hub.sse_clients() == 0


async def test_get_events_streams_the_hub_and_releases_its_slot(hub_state):
    """Driven through the route's body_iterator, not httpx: ASGITransport runs the
    app to completion before returning, so an endless stream never yields to it."""
    hub = hub_state.hub
    response = await events(Request(
        {"type": "http", "method": "GET", "path": "/events", "headers": [],
         "app": create_app(hub_state)}
    ))
    assert response.media_type == "text/event-stream"
    assert hub.sse_clients() == 1

    hub.publish(3, "game_ended", {"game_id": 9})
    async with asyncio.timeout(5):
        frame = await anext(response.body_iterator)
    assert "event: game_ended" in frame
    assert "id: 3" in frame

    await response.body_iterator.aclose()

    assert hub.sse_clients() == 0


async def test_the_hub_is_the_sink_the_engine_publishes_through(hub_state):
    hub = hub_state.hub
    client = hub.subscribe()

    async with critical_section(
        hub_state.store.writer, hub_state.store.executor, hub_state.deps.sink
    ) as txn:
        txn.emit("game_created", {"game_id": 1})

    assert [envelope["event_type"] for envelope in client.queue] == ["game_created"]
