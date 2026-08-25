"""App construction and the lifespan (role spec §8.6; design §7.1).

`create_app` performs no I/O so a route test can hand it a pre-built `AppState`
and skip the lifespan entirely. The lifespan is what makes recovery the last
write before the listening socket accepts: a bot that reconnects fast must not
be paired into a game recovery is about to abort.
"""
import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from chess_server.api import routes_bots, routes_play
from chess_server.api.errors import ApiError
from chess_server.api.settings import Settings
from chess_server.api.state import AppState
from chess_server.engine import state as engine_state
from chess_server.engine.reference_bots import seed_anchors
from chess_server.engine.supervisor import Supervisor, run_supervisor
from chess_server.engine.ticker import run_ticker
from chess_server.engine.wall import utc_now_iso
from chess_server.store.db import open_store
from chess_server.store.recovery import recover


def build_state(settings: Settings) -> AppState:
    """The one place that opens the database."""
    return AppState(store=open_store(settings.db_path), settings=settings)


def start_ticker(app_state: AppState) -> asyncio.Task:
    app_state.ticker_task = asyncio.create_task(
        run_ticker(app_state.deps, app_state.metrics), name="ticker"
    )
    return app_state.ticker_task


def start_supervisor(app_state: AppState) -> asyncio.Task:
    supervisor = Supervisor(
        deps=app_state.deps,
        metrics=app_state.metrics,
        spawn=lambda: start_ticker(app_state),
        task=app_state.ticker_task,
    )
    app_state.supervisor_task = asyncio.create_task(
        run_supervisor(supervisor), name="supervisor"
    )
    return app_state.supervisor_task


async def _stop(task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app_state: AppState = app.state.arena
    # Anchors are seeded before recovery so that step 4's clear_monotonic_state
    # covers them too, and so recovery is the last write of the startup path —
    # which is what makes server_run_started genuinely seq 0.
    await seed_anchors(app_state.store.writer, app_state.store.executor)
    await recover(
        app_state.store.writer,
        app_state.store.executor,
        utc_now_iso(),
        engine_state.clear_all,
        app_state.deps.sink,
    )
    app_state.metrics.last_tick_mono = app_state.deps.now_mono()
    start_ticker(app_state)
    start_supervisor(app_state)
    try:
        yield
    finally:
        # The supervisor first: cancelling the ticker under a live supervisor
        # invites it to spawn a replacement during shutdown.
        await _stop(app_state.supervisor_task)
        await _stop(app_state.ticker_task)
        app_state.store.close()


def create_app(app_state: AppState) -> FastAPI:
    app = FastAPI(title="Chess Arena", lifespan=lifespan)
    app.state.arena = app_state
    app.include_router(routes_bots.router)
    app.include_router(routes_play.router)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "details": exc.details},
            headers=exc.headers,
        )

    return app
