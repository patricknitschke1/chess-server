"""The unauthenticated read surface (role spec §8.1, §8.4; design §10.4, §14).

Every route here reads on the reader connection and executor, outside
`write_lock`: a dashboard refresh must never queue behind the game loop.

None of them deliver. `GET /games/{id}` in particular looks like the agent's
`legal_moves` route and is not — delivery starts a clock, and a spectator
opening a board must never start one.
"""
from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from chess_core import STARTING_FEN

from chess_server.api.errors import BOT_NOT_FOUND, GAME_NOT_FOUND, ApiError
from chess_server.api.models import (
    PROVISIONAL_GAMES,
    GameDetailResponse,
    GameMoveEntry,
    GameMovesResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    RatingHistoryResponse,
    RatingPoint,
)
from chess_server.api.state import AppState, get_state
from chess_server.store.repositories import (
    BotRepo,
    GameRepo,
    MoveRepo,
    RatingHistoryRepo,
)
from chess_server.store.rows import BotRow, GameRow

router = APIRouter()


def _bots(app_state: AppState) -> BotRepo:
    return BotRepo(app_state.store.reader, app_state.store.reader_executor)


def _games(app_state: AppState) -> GameRepo:
    return GameRepo(app_state.store.reader, app_state.store.reader_executor)


def _moves(app_state: AppState) -> MoveRepo:
    return MoveRepo(app_state.store.reader, app_state.store.reader_executor)


async def _game_or_404(app_state: AppState, game_id: int) -> GameRow:
    game = await _games(app_state).get_by_id(game_id)
    if game is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, GAME_NOT_FOUND.format(game_id=game_id)
        )
    return game


async def _bot_or_404(app_state: AppState, bot_id: int) -> BotRow:
    bot = await _bots(app_state).get_by_id(bot_id)
    if bot is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND.format(bot_id=bot_id))
    return bot


def to_leaderboard_entry(bot: BotRow) -> LeaderboardEntry:
    return LeaderboardEntry(
        bot_id=bot.id,
        bot_name=bot.name,
        owner=bot.owner,
        rating=bot.rating,
        wins=bot.wins,
        losses=bot.losses,
        draws=bot.draws,
        games_played=bot.games_played,
        is_provisional=bot.games_played < PROVISIONAL_GAMES,
        role=bot.role,
        is_anchor=bool(bot.is_anchor),
    )


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    app_state: AppState = get_state(request)
    hub = app_state.hub
    return StreamingResponse(
        hub.stream(hub.subscribe()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def leaderboard(request: Request) -> LeaderboardResponse:
    rows = await _bots(get_state(request)).list_leaderboard()
    return LeaderboardResponse(
        bots=[to_leaderboard_entry(bot) for bot in rows], total_bots=len(rows)
    )


@router.get("/games/{game_id}", response_model=GameDetailResponse)
async def game_detail(request: Request, game_id: int) -> GameDetailResponse:
    app_state = get_state(request)
    game = await _game_or_404(app_state, game_id)
    white = await _bots(app_state).get_by_id(game.white_bot_id)
    black = await _bots(app_state).get_by_id(game.black_bot_id)
    # From `moves`, not state.history_san: finalisation drops the cache entry and
    # this route must still answer for a game that ended an hour ago.
    rows = await _moves(app_state).list_moves_for_game(game_id)
    return GameDetailResponse(
        game_id=game.id,
        white_bot_id=white.id,
        white_bot_name=white.name,
        black_bot_id=black.id,
        black_bot_name=black.name,
        status=game.status,
        result=game.result,
        termination=game.termination,
        fen=game.fen,
        ply=game.ply,
        history_san=[row.san for row in rows],
        white_ms=game.white_ms,
        black_ms=game.black_ms,
        time_control_ms=game.time_control_ms,
        increment_ms=game.increment_ms,
        rated=bool(game.rated),
        source=game.source,
        created_at=game.created_at,
        started_at=game.started_at,
        ended_at=game.ended_at,
    )


@router.get("/games/{game_id}/moves", response_model=GameMovesResponse)
async def game_moves(request: Request, game_id: int) -> GameMovesResponse:
    app_state = get_state(request)
    game = await _game_or_404(app_state, game_id)
    white = await _bots(app_state).get_by_id(game.white_bot_id)
    black = await _bots(app_state).get_by_id(game.black_bot_id)
    rows = await _moves(app_state).list_moves_for_game(game_id)
    return GameMovesResponse(
        game_id=game.id,
        white_bot_name=white.name,
        black_bot_name=black.name,
        white_rating=white.rating,
        black_rating=black.rating,
        status=game.status,
        result=game.result,
        termination=game.termination,
        # Every server-created game starts here; create_game_locked seeds
        # state.history with the same constant. The arena randomises openings
        # locally, never on the server.
        starting_fen=STARTING_FEN,
        final_ply=game.ply,
        moves=[
            GameMoveEntry(
                ply=row.ply,
                uci=row.uci,
                san=row.san,
                fen_after=row.fen_after,
                server_elapsed_ms=row.server_elapsed_ms,
                client_reported_ms=row.client_reported_ms,
                white_ms_after=row.white_ms_after,
                black_ms_after=row.black_ms_after,
            )
            for row in rows
        ],
        white_strikes=game.white_strikes,
        black_strikes=game.black_strikes,
    )


@router.get("/bots/{bot_id}/rating_history", response_model=RatingHistoryResponse)
async def rating_history(request: Request, bot_id: int) -> RatingHistoryResponse:
    app_state = get_state(request)
    bot = await _bot_or_404(app_state, bot_id)
    points = await RatingHistoryRepo(
        app_state.store.reader, app_state.store.reader_executor
    ).list_points_for_bot(bot_id)
    return RatingHistoryResponse(
        bot_id=bot.id,
        bot_name=bot.name,
        points=[
            RatingPoint(
                game_id=point.game_id,
                rating_after=point.rating_after,
                delta=point.delta,
                ts=point.ts,
            )
            for point in points
        ],
    )
