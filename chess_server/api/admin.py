"""The admin router (role spec §10, §8.5; design §15).

Never exposed to attendees, and never reachable without `ADMIN_TOKEN`: the
dependency is on the router, so a route added here is authenticated by default
rather than by whoever remembers.
"""
import logging

from fastapi import APIRouter, Depends, Request, status

from chess_core import STARTING_RATING, TerminationReason

from chess_server.api.auth import require_admin
from chess_server.api.errors import (
    BOT_NAME_NOT_FOUND,
    GAME_ALREADY_TERMINAL,
    GAME_NOT_FOUND,
    ApiError,
)
from chess_server.api.models import (
    AbortGameResponse,
    ConsistencyCheckResponse,
    ConsistencyViolation,
    PauseMatchmakingResponse,
    ResumeMatchmakingResponse,
)
from chess_server.api.state import AppState, get_state
from chess_server.engine.games import abort_game_locked
from chess_server.store.repositories import (
    NON_TERMINAL,
    BotRepo,
    GameRepo,
    RatingHistoryRepo,
)
from chess_server.store.txn import critical_section

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Matches routes_bots: a re-issued token is the same kind of secret as the first.
TOKEN_BYTES = 32


async def _bot_by_name_or_404(app_state: AppState, name: str):
    bot = await BotRepo(
        app_state.store.reader, app_state.store.reader_executor
    ).get_by_name(name)
    if bot is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, BOT_NAME_NOT_FOUND.format(name=name))
    return bot


@router.post("/games/{game_id}/abort", response_model=AbortGameResponse)
async def abort_game(request: Request, game_id: int) -> AbortGameResponse:
    """Racing the ticker is fine: exactly one CAS gets `rowcount == 1`."""
    app_state = get_state(request)
    async with critical_section(
        app_state.store.writer, app_state.store.executor, app_state.deps.sink
    ) as txn:
        game = await GameRepo(txn.conn, txn.executor).get_by_id(game_id)
        if game is None:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, GAME_NOT_FOUND.format(game_id=game_id)
            )
        if game.status not in NON_TERMINAL:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                GAME_ALREADY_TERMINAL.format(game_id=game_id),
            )
        await abort_game_locked(
            app_state.deps, txn, game, TerminationReason.ADMIN_ABORT
        )
    return AbortGameResponse(
        game_id=game_id,
        status="aborted",
        termination=TerminationReason.ADMIN_ABORT.value,
    )


@router.post("/matchmaking/pause", response_model=PauseMatchmakingResponse)
async def pause_matchmaking(request: Request) -> PauseMatchmakingResponse:
    get_state(request).matchmaking_paused = True
    return PauseMatchmakingResponse(paused=True)


@router.post("/matchmaking/resume", response_model=ResumeMatchmakingResponse)
async def resume_matchmaking(request: Request) -> ResumeMatchmakingResponse:
    get_state(request).matchmaking_paused = False
    return ResumeMatchmakingResponse(paused=False)


async def check_consistency(app_state: AppState) -> ConsistencyCheckResponse:
    """`rating == STARTING_RATING + sum(deltas)` for competitors only.

    Runs at startup only — §15's route was cut. Anchors hold fixed ratings away
    from `STARTING_RATING` and accrue no history rows, so including them would
    leave the one alarm that catches double-rating permanently red — the same as
    having no alarm at all.
    """
    bots = BotRepo(app_state.store.reader, app_state.store.reader_executor)
    history = RatingHistoryRepo(app_state.store.reader, app_state.store.reader_executor)
    violations = []
    for bot in await bots.list_leaderboard():
        if bot.role != "competitor":
            continue
        delta_sum = await history.sum_deltas_by_bot(bot.id)
        expected = STARTING_RATING + delta_sum
        if expected != bot.rating:
            violations.append(
                ConsistencyViolation(
                    bot_id=bot.id,
                    bot_name=bot.name,
                    expected_rating=expected,
                    actual_rating=bot.rating,
                    delta_sum=delta_sum,
                )
            )
    if violations:
        logger.error(
            "rating consistency check failed for %d bot(s): %s",
            len(violations),
            [violation.bot_name for violation in violations],
        )
    return ConsistencyCheckResponse(consistent=not violations, violations=violations)
