"""GET /health, the snapshot behind it, and the `health_tick` (role spec §7.6,
§8.1; design §4.6).

One builder for every `HealthResponse` field, so the route and the SSE signal
cannot drift into reporting different numbers for the same server.
"""
from fastapi import APIRouter, Request

from chess_core import elapsed_ms, window_start_mono, POLL_RECENCY_NS

from chess_server.api.models import HealthResponse
from chess_server.api.state import AppState, get_state
from chess_server.engine.supervisor import probe_db_writable
from chess_server.store.repositories import BotRepo, GameRepo
from chess_server.store.txn import take_seq

router = APIRouter()

# Part 2's subset: a liveness signal, not a diagnosis. `db_writable` is absent
# because the tick does not probe — only the route does.
HEALTH_TICK_FIELDS = {
    "last_tick_age_ms", "last_tick_duration_ms", "active_games", "pending_games",
    "pooled_bots", "held_polls", "sse_clients",
}


async def build_health(app_state: AppState, db_writable: bool) -> HealthResponse:
    """Reads on the reader connection: /health must answer while the writer works."""
    now_mono = app_state.now_mono()
    games = GameRepo(app_state.store.reader, app_state.store.reader_executor)
    bots = BotRepo(app_state.store.reader, app_state.store.reader_executor)

    summaries = await games.list_active_summaries()
    # Non-terminal and never handed to the mover — the shape a wedged delivery
    # sweep leaves behind. Defined here because no spec defines it (plan gap 4).
    stalled = await games.list_undelivered_non_terminal()
    pooled = await bots.list_pool_candidates(window_start_mono(now_mono, POLL_RECENCY_NS))
    metrics = app_state.metrics

    return HealthResponse(
        last_tick_age_ms=elapsed_ms(metrics.last_tick_mono, now_mono),
        last_tick_duration_ms=metrics.last_tick_duration_ms,
        active_games=sum(1 for row in summaries if row["status"] == "active"),
        pending_games=sum(1 for row in summaries if row["status"] == "pending"),
        stalled_games=len(stalled),
        pooled_bots=len(pooled),
        held_polls=app_state.waiters.held_count(),
        sse_clients=app_state.hub.sse_clients(),
        db_writable=db_writable,
        consecutive_tick_errors=metrics.consecutive_tick_errors,
        ticker_restarts=metrics.ticker_restarts,
    )


def health_tick_data(health: HealthResponse) -> dict:
    return {field: getattr(health, field) for field in HEALTH_TICK_FIELDS}


async def publish_health_tick(app_state: AppState) -> None:
    """Unbuffered on purpose. Routed through a `Txn` it would stay invisible until
    some unrelated transaction committed, which is the opposite of a liveness
    signal — and it probes nothing, so it never contends for the write lock."""
    health = await build_health(app_state, db_writable=True)
    app_state.sink(take_seq(), "health_tick", health_tick_data(health))


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    app_state = get_state(request)
    return await build_health(app_state, await probe_db_writable(app_state.deps))
