"""Play routes: move submission (role spec §6.1, §8.1; design §8.3, §13.3).

One outer `apply_move` per request. The route opens no critical section of its
own — a second transaction here would be a second chance for the position to
move underneath the first.
"""
from fastapi import APIRouter, Depends, Request, status

from chess_core import Color, get_legal_moves

from chess_server.api.auth import require_bot
from chess_server.api.errors import (
    CAS_CONFLICT,
    CONTROLLER_IS_AGENT,
    CONTROLLER_IS_CLIENT,
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
    LegalMovesResponse,
    ResignRequest,
    ResignResponse,
    SubmitMoveRequest,
    SubmitMoveResponse,
)
from chess_server.api.state import AppState, get_state
from chess_server.engine import games as games_module
from chess_server.engine.games import ResignRefused, resign_game
from chess_server.engine.mailbox import deliver_for_poll
from chess_server.engine.runner import (
    Applied,
    Flagged,
    MoveOutcome,
    NotDelivered,
    Rejected,
    WrongController,
    apply_move,
)
from chess_server.store.cas import CASConflict
from chess_server.store.repositories import NON_TERMINAL, BotRepo, GameRepo, SeatRepo
from chess_server.store.rows import BotRow, GameRow
from chess_server.store.txn import critical_section

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
    `403` that reads like an authorisation fault. `controller` gets no such pin,
    which is why it is checked inside the transaction (design §13.3).
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
    if isinstance(outcome, WrongController):
        raise ApiError(status.HTTP_403_FORBIDDEN, CONTROLLER_IS_AGENT)
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
            controller="client",
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
        outcome = await resign_game(
            app_state.deps, game_id, bot.id, payload.ply, controller="client"
        )
    except CASConflict:
        game = await _load_game(app_state, game_id)
        raise ApiError(
            status.HTTP_409_CONFLICT,
            GAME_ALREADY_ENDED.format(game_id=game_id, ply=payload.ply),
            {"ply": game.ply, "fen": game.fen, "status": game.status},
        )
    if isinstance(outcome, ResignRefused):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            NOT_IN_GAME.format(game_id=game_id)
            if outcome.reason == games_module.NOT_IN_GAME
            else CONTROLLER_IS_AGENT,
        )
    return ResignResponse(
        game_id=outcome.id,
        status=outcome.status,
        result=outcome.result,
        termination=outcome.termination,
    )


@router.get("/games/{game_id}/legal_moves", response_model=LegalMovesResponse)
async def get_legal_moves_route(
    game_id: int, request: Request, bot: BotRow = Depends(require_bot)
):
    """The agent's delivery site (role spec §5.2, design §6.2).

    Read-only in name only: while `controller='agent'` this is where the position
    is delivered, and delivery starts a clock. `GET /games/{id}` is the read that
    never delivers.
    """
    app_state = get_state(request)
    game = await _load_game(app_state, game_id)
    if bot.controller != "agent":
        raise ApiError(status.HTTP_403_FORBIDDEN, CONTROLLER_IS_CLIENT)
    seat = await SeatRepo(
        app_state.store.reader, app_state.store.reader_executor
    ).get_seat(bot.id)
    if seat is None or seat.game_id != game_id:
        raise ApiError(status.HTTP_403_FORBIDDEN, NOT_IN_GAME.format(game_id=game_id))

    mover_id = game.white_bot_id if game.to_move == Color.WHITE.value else game.black_bot_id
    if game.status in NON_TERMINAL and mover_id == bot.id:
        # Idempotent by §6.2's `delivered_to_mover = 0` predicate: a second call
        # returns the identical position and does not restart the clock.
        game = await deliver_for_poll(app_state.deps, bot.id) or game
    await _record_agent_action(app_state, bot.id)
    return LegalMovesResponse(
        game_id=game.id,
        ply=game.ply,
        legal_moves=get_legal_moves(game.fen),
        fen=game.fen,
    )


async def _record_agent_action(app_state: AppState, bot_id: int) -> None:
    """§7.5's auto-release is keyed on this, so an agent that is working must not
    be released mid-thought."""
    async with critical_section(
        app_state.store.writer, app_state.store.executor, app_state.deps.sink
    ) as txn:
        await BotRepo(txn.conn, txn.executor).update_last_agent_action(
            bot_id, app_state.deps.now_mono()
        )
