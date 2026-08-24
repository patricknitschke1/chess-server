"""`POST /bots`, `GET /bots/me`, `GET /bots/me/turn` (role spec §5, §8.2)."""
import asyncio
import secrets
from typing import Optional, Union

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
from chess_server.api.models import (
    MyBotResponse,
    NoGameResponse,
    RegisterBotRequest,
    RegisterBotResponse,
    TurnResponse,
)
from chess_server.api.state import AppState, get_state
from chess_server.api.validation import validate_identity
from chess_server.engine.mailbox import (
    TurnPayload,
    color_of,
    deliver_for_poll,
    take_payload,
)
from chess_server.engine.wall import utc_now_iso
from chess_server.store.repositories import NON_TERMINAL, BotRepo, GameRepo, SeatRepo
from chess_server.store.rows import BotRow
from chess_server.store.txn import critical_section

router = APIRouter()

# `anchor` is deliberately absent: anchors are seeded at startup, and role='anchor'
# is what keeps them off the leaderboard and out of the one-per-owner rule.
REGISTRABLE_ROLES = ("competitor", "benchmark")

PROVISIONAL_GAMES = 10
TOKEN_BYTES = 32

# Design §8.2 gives six, and the handler covers six (role spec §5.5).
WAITING_FOR_PAIRING = "waiting_for_pairing"
NO_SEAT = "no_seat"
NOT_YOUR_TURN = "not_your_turn"
AGENT_HAS_CONTROL = "agent_has_control"
PAUSED = "paused"
SUPERSEDED = "superseded"


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


def _turn_response(payload: TurnPayload) -> TurnResponse:
    return TurnResponse(
        game_id=payload.game_id,
        ply=payload.ply,
        color=payload.color,
        fen=payload.fen,
        legal_moves=payload.legal_moves,
        history_san=payload.history_san,
        white_ms=payload.white_ms,
        black_ms=payload.black_ms,
        time_control_ms=payload.time_control_ms,
        increment_ms=payload.increment_ms,
        controller=payload.controller,
    )


async def _record_poll(app_state: AppState, bot_id: int) -> None:
    """§5.6: this endpoint and no other. Pool eligibility means "the bot is
    actually running", so a dashboard refresh must never refresh these."""
    async with critical_section(
        app_state.store.writer, app_state.store.executor, app_state.deps.sink
    ) as txn:
        await BotRepo(txn.conn, txn.executor).update_last_poll(
            bot_id, utc_now_iso(), app_state.deps.now_mono()
        )


async def _no_seat_reason(app_state: AppState, bot: BotRow) -> NoGameResponse:
    if bot.role == "benchmark":
        # Matchmaking will never consider it, so "waiting" would be a promise
        # the server does not keep. Checked before `paused` for that reason.
        return NoGameResponse(reason=NO_SEAT)
    if app_state.matchmaking_paused:
        return NoGameResponse(reason=PAUSED)
    return NoGameResponse(reason=WAITING_FOR_PAIRING)


async def _resolve_turn(
    app_state: AppState, bot_id: int
) -> Optional[Union[TurnResponse, NoGameResponse]]:
    """None means "nothing to say yet" — the caller holds. Reads on the reader
    connection; the one write is inside `deliver_for_poll`'s critical section."""
    conn, executor = app_state.store.reader, app_state.store.reader_executor
    # Re-read the bot: `controller` can flip to 'agent' while a poll is held.
    bot = await BotRepo(conn, executor).get_by_id(bot_id)
    seat = await SeatRepo(conn, executor).get_seat(bot_id)
    if bot is None or seat is None:
        return None
    games = GameRepo(conn, executor)
    game = await games.get_by_id(seat.game_id)
    if game is None or game.status not in NON_TERMINAL:
        return None
    if bot.controller != "client":
        return NoGameResponse(reason=AGENT_HAS_CONTROL)

    # Before the turn check, so a payload left over from an earlier ply is
    # discarded rather than left to be drained by the next poll (§5.3).
    payload = take_payload(bot_id, game)
    if payload is not None:
        return _turn_response(payload)
    if game.to_move != color_of(bot_id, game):
        return NoGameResponse(reason=NOT_YOUR_TURN)

    delivered = await deliver_for_poll(app_state.deps, bot_id)
    if delivered is None:
        return None
    payload = take_payload(bot_id, delivered)
    if payload is None:
        return NoGameResponse(reason=NOT_YOUR_TURN)
    return _turn_response(payload)


@router.get("/bots/me/turn")
async def get_turn(request: Request, bot: BotRow = Depends(require_bot)):
    """Record the poll, **register the waiter**, then read. Reading first loses
    any wake that fires in the gap and hangs for the whole hold with a delivered
    position sitting in the mailbox (§5.4)."""
    app_state = get_state(request)
    await _record_poll(app_state, bot.id)
    waiter = app_state.waiters.register(bot.id)
    try:
        answer = await _resolve_turn(app_state, bot.id)
        if answer is not None:
            return answer
        try:
            async with asyncio.timeout(app_state.settings.poll_hold_seconds):
                await waiter.event.wait()
        except TimeoutError:
            return await _no_seat_reason(app_state, bot)
        if waiter.superseded:
            # Without touching the mailbox: supersede cancels a waiter, and the
            # delivery outlives the request that was holding for it.
            return NoGameResponse(reason=SUPERSEDED)
        answer = await _resolve_turn(app_state, bot.id)
        return answer if answer is not None else await _no_seat_reason(app_state, bot)
    finally:
        app_state.waiters.discard(bot.id, waiter)
