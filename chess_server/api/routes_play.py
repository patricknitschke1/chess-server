"""Play routes: move submission (role spec §6.1, §8.1; design §8.3).

One outer `apply_move` per request. The route opens no critical section of its
own — a second transaction here would be a second chance for the position to
move underneath the first.
"""
from fastapi import APIRouter, Depends, Request, status

from chess_core import Color

from chess_server.api.auth import require_bot
from chess_server.api.errors import (
    CAS_CONFLICT,
    FLAGGED,
    GAME_ALREADY_ENDED,
    GAME_NOT_FOUND,
    ILLEGAL_MOVE,
    NOT_DELIVERED,
    NOT_IN_GAME,
    NOT_TO_MOVE,
    ApiError,
)
from chess_server.api.models import (
    ResignRequest,
    ResignResponse,
    SubmitMoveRequest,
    SubmitMoveResponse,
)
from chess_server.api.state import AppState, get_state
from chess_server.engine.games import ResignRefused, resign_game
from chess_server.engine.runner import (
    Applied,
    Flagged,
    MoveOutcome,
    NotDelivered,
    Rejected,
    apply_move,
)
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import NON_TERMINAL, GameRepo, SeatRepo
from chess_server.store.rows import BotRow, GameRow

router = APIRouter()


async def _load_game(app_state: AppState, game_id: int) -> GameRow:
    game = await GameRepo(
        app_state.store.reader, app_state.store.reader_executor
    ).get_by_id(game_id)
    if game is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, GAME_NOT_FOUND.format(game_id=game_id)
        )
    return game


async def _mover_seat(
    app_state: AppState, bot: BotRow, game_id: int, from_ply: int
) -> None:
    """Refuse a caller who is not the side to move at the ply it is claiming.

    Read outside the transaction, and sound there because `ply` is part of the CAS:
    a caller who was the mover at ply P and whose CAS at ply P succeeds is still the
    mover. When the game has moved on this says nothing and defers to the CAS, so a
    move that raced the opponent's — or arrived after the game ended, when the seats
    are already gone — gets design §8.3's `409` (discard and re-poll) rather than a
    `403` that reads like an authorisation fault.
    """
    game = await _load_game(app_state, game_id)
    if game.ply != from_ply or game.status not in NON_TERMINAL:
        return
    seat = await SeatRepo(
        app_state.store.reader, app_state.store.reader_executor
    ).get_seat(bot.id)
    if seat is None or seat.game_id != game_id:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, NOT_IN_GAME.format(game_id=game_id)
        )
    mover_id = game.white_bot_id if game.to_move == Color.WHITE.value else game.black_bot_id
    if mover_id != bot.id:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, NOT_TO_MOVE.format(game_id=game_id)
        )


def _cas_error(game: GameRow) -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        CAS_CONFLICT.format(ply=game.ply),
        {"ply": game.ply, "fen": game.fen, "status": game.status},
    )


def _outcome_response(
    outcome: MoveOutcome, game: GameRow, attempted: str
) -> SubmitMoveResponse:
    if isinstance(outcome, Rejected):
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            ILLEGAL_MOVE.format(
                move=attempted, legal_moves=outcome.legal_moves, fen=outcome.fen
            ),
            {"legal_moves": outcome.legal_moves, "fen": outcome.fen,
             "strikes": outcome.strikes, "forfeited": outcome.forfeited},
        )
    if isinstance(outcome, NotDelivered):
        raise ApiError(
            status.HTTP_409_CONFLICT, NOT_DELIVERED,
            {"ply": outcome.ply, "fen": outcome.fen, "status": outcome.status},
        )
    if isinstance(outcome, Flagged):
        raise ApiError(
            status.HTTP_409_CONFLICT, FLAGGED,
            {"ply": game.ply, "fen": game.fen, "status": game.status,
             "result": outcome.result.value, "termination": game.termination},
        )
    assert isinstance(outcome, Applied)
    return SubmitMoveResponse(
        game_id=game.id,
        ply=game.ply,
        fen=game.fen,
        status=game.status,
        result=game.result,
        termination=game.termination,
    )


@router.post("/games/{game_id}/moves", response_model=SubmitMoveResponse)
async def submit_move(
    game_id: int,
    payload: SubmitMoveRequest,
    request: Request,
    bot: BotRow = Depends(require_bot),
):
    app_state = get_state(request)
    await _mover_seat(app_state, bot, game_id, payload.ply)
    try:
        outcome = await apply_move(
            app_state.deps, game_id, payload.ply, payload.move,
            client_reported_ms=payload.client_reported_ms,
        )
    except CASConflict:
        raise _cas_error(await _load_game(app_state, game_id))
    # Re-read after the commit: `Applied` carries the row as it was inside the
    # transaction, and a finalisation that followed the move is not on it.
    return _outcome_response(outcome, await _load_game(app_state, game_id), payload.move)


@router.post("/games/{game_id}/resign", response_model=ResignResponse)
async def resign(
    game_id: int,
    payload: ResignRequest,
    request: Request,
    bot: BotRow = Depends(require_bot),
):
    app_state = get_state(request)
    game = await _load_game(app_state, game_id)
    try:
        outcome = await resign_game(app_state.deps, game_id, bot.id, payload.ply)
    except CASConflict:
        game = await _load_game(app_state, game_id)
        raise ApiError(
            status.HTTP_409_CONFLICT,
            GAME_ALREADY_ENDED.format(game_id=game_id, ply=payload.ply),
            {"ply": game.ply, "fen": game.fen, "status": game.status},
        )
    if isinstance(outcome, ResignRefused):
        raise ApiError(
            status.HTTP_403_FORBIDDEN, NOT_IN_GAME.format(game_id=game_id)
        )
    return ResignResponse(
        game_id=outcome.id,
        status=outcome.status,
        result=outcome.result,
        termination=outcome.termination,
    )
