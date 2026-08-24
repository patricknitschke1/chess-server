"""`POST /bots`, `GET /bots/me` (role spec §8.2; design §10.4, §14, §16.2)."""
import secrets

from fastapi import APIRouter, Depends, Request, status

from chess_core import STARTING_RATING

from chess_server.api.auth import enforce_register_limit, hash_token, require_bot
from chess_server.api.errors import (
    INVALID_JOIN_CODE,
    INVALID_ROLE,
    NAME_TAKEN,
    SECOND_COMPETITOR,
    ApiError,
)
from chess_server.api.models import MyBotResponse, RegisterBotRequest, RegisterBotResponse
from chess_server.api.state import get_state
from chess_server.api.validation import validate_identity
from chess_server.engine.wall import utc_now_iso
from chess_server.store.repositories import BotRepo, SeatRepo
from chess_server.store.rows import BotRow
from chess_server.store.txn import critical_section

router = APIRouter()

# `anchor` is deliberately absent: anchors are seeded at startup, and role='anchor'
# is what keeps them off the leaderboard and out of the one-per-owner rule.
REGISTRABLE_ROLES = ("competitor", "benchmark")

PROVISIONAL_GAMES = 10
TOKEN_BYTES = 32


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/bots", status_code=status.HTTP_201_CREATED, response_model=RegisterBotResponse)
async def register_bot(payload: RegisterBotRequest, request: Request):
    app_state = get_state(request)
    enforce_register_limit(app_state, _client_ip(request))
    if not secrets.compare_digest(payload.join_code, app_state.settings.join_code):
        raise ApiError(status.HTTP_400_BAD_REQUEST, INVALID_JOIN_CODE)
    validate_identity(payload.name, payload.owner)
    if payload.role not in REGISTRABLE_ROLES:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, INVALID_ROLE.format(role=payload.role)
        )

    token = secrets.token_urlsafe(TOKEN_BYTES)
    # Both checks and the insert are one transaction, so two simultaneous
    # registrations from one owner cannot both find no existing competitor.
    async with critical_section(
        app_state.store.writer, app_state.store.executor, app_state.deps.sink
    ) as txn:
        bots = BotRepo(txn.conn, txn.executor)
        if await bots.get_by_name(payload.name) is not None:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST, NAME_TAKEN.format(name=payload.name)
            )
        if payload.role == "competitor":
            existing = await bots.get_competitor_for_owner(payload.owner)
            if existing is not None:
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    SECOND_COMPETITOR.format(existing_name=existing.name),
                )
        bot_id = await bots.insert_bot(
            name=payload.name,
            owner=payload.owner,
            token_hash=hash_token(token),
            role=payload.role,
            rating=STARTING_RATING,
            is_anchor=0,
            created_at=utc_now_iso(),
        )
        txn.emit("bot_registered", {
            "bot_id": bot_id,
            "bot_name": payload.name,
            "role": payload.role,
        })
    # The plaintext token exists in exactly one response, and nowhere else.
    return RegisterBotResponse(bot_id=bot_id, name=payload.name, token=token)


@router.get("/bots/me", response_model=MyBotResponse)
async def get_my_bot(request: Request, bot: BotRow = Depends(require_bot)):
    app_state = get_state(request)
    seats = SeatRepo(app_state.store.reader, app_state.store.reader_executor)
    seat = await seats.get_seat(bot.id)
    return MyBotResponse(
        bot_id=bot.id,
        name=bot.name,
        owner=bot.owner,
        role=bot.role,
        rating=bot.rating,
        wins=bot.wins,
        losses=bot.losses,
        draws=bot.draws,
        games_played=bot.games_played,
        is_provisional=bot.games_played < PROVISIONAL_GAMES,
        controller=bot.controller,
        # Through `seats`, never by scanning `games`: a game is reachable only
        # through the seat that makes it exclusive (§7.1).
        current_game_id=seat.game_id if seat is not None else None,
    )
