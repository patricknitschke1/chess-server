"""Challenge routes (role spec §7.2, §8.1; design §12; interfaces Part 5).

A challenge is an intent, not a game. Accepting queues it; the ticker consumes it
before pairing. Every transition here buffers `challenge_updated` — design §12's
"no silent drop" applies to the routes as much as to the sweep.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, status

from chess_core import (
    EXHIBITION_INCREMENT_NS,
    EXHIBITION_TIME_CONTROL_NS,
    RATED_INCREMENT_NS,
    RATED_TIME_CONTROL_NS,
    ns_to_ms,
)

from chess_server.api.auth import require_bot
from chess_server.api.errors import (
    AGENT_IN_RATED_CHALLENGE,
    CHALLENGE_ALREADY_RESOLVED,
    CHALLENGE_NOT_FOUND,
    NOT_THE_OPPONENT,
    OPEN_OUTGOING_CHALLENGE,
    OPPONENT_NOT_FOUND,
    SEAT_HELD,
    SELF_CHALLENGE,
    UNKNOWN_TIME_CONTROL,
    ApiError,
)
from chess_server.api.models import (
    AcceptChallengeResponse,
    ChallengeEntry,
    ChallengesInboxResponse,
    CreateChallengeRequest,
    CreateChallengeResponse,
    DeclineChallengeResponse,
)
from chess_server.api.state import AppState, get_state
from chess_server.engine.ticker import challenge_event
from chess_server.engine.wall import utc_now_iso
from chess_server.store.repositories import BotRepo, ChallengeRepo, SeatRepo
from chess_server.store.rows import BotRow, ChallengeRow
from chess_server.store.txn import Txn, critical_section

router = APIRouter()

RATED = "rated"
EXHIBITION = "exhibition"
# The two named controls of design §11, and nothing else: an unrecognised string
# would otherwise silently become 3+2 and quietly count for rating.
TIME_CONTROLS = {
    RATED: (RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS),
    EXHIBITION: (EXHIBITION_TIME_CONTROL_NS, EXHIBITION_INCREMENT_NS),
}

OPEN = "open"
QUEUED = "queued"
DECLINED = "declined"


async def _entry(bots: BotRepo, challenge: ChallengeRow) -> ChallengeEntry:
    challenger = await bots.get_by_id(challenge.challenger_bot_id)
    opponent = await bots.get_by_id(challenge.opponent_bot_id)
    return ChallengeEntry(
        challenge_id=challenge.id,
        challenger_bot_id=challenger.id,
        challenger_bot_name=challenger.name,
        opponent_bot_id=opponent.id,
        opponent_bot_name=opponent.name,
        status=challenge.status,
        time_control_ms=challenge.time_control_ms,
        increment_ms=challenge.increment_ms,
        created_at=challenge.created_at,
    )


async def _emit(txn: Txn, challenges: ChallengeRepo, challenge_id: int) -> None:
    txn.emit("challenge_updated", await challenge_event(
        txn, await challenges.get_by_id(challenge_id)
    ))


@router.post("/challenges", status_code=status.HTTP_201_CREATED,
             response_model=CreateChallengeResponse)
async def create_challenge(
    payload: CreateChallengeRequest,
    request: Request,
    bot: BotRow = Depends(require_bot),
):
    app_state = get_state(request)
    if payload.time_control not in TIME_CONTROLS:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            UNKNOWN_TIME_CONTROL.format(time_control=payload.time_control),
        )
    time_control_ns, increment_ns = TIME_CONTROLS[payload.time_control]
    rated = payload.time_control == RATED

    async with critical_section(
        app_state.store.writer, app_state.store.executor, app_state.deps.sink
    ) as txn:
        bots = BotRepo(txn.conn, txn.executor)
        opponent = await bots.get_by_name(payload.opponent)
        if opponent is None:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST,
                OPPONENT_NOT_FOUND.format(name=payload.opponent),
            )
        # Bad request, not a conflict: left to the ticker, the `seats` primary key
        # kills it as `seat_unavailable`, which reads as a server fault (§11.12).
        if opponent.id == bot.id:
            raise ApiError(status.HTTP_400_BAD_REQUEST, SELF_CHALLENGE)

        challenges = ChallengeRepo(txn.conn, txn.executor)
        if await challenges.get_open_outgoing(bot.id) is not None:
            raise ApiError(status.HTTP_409_CONFLICT, OPEN_OUTGOING_CHALLENGE)
        seats = SeatRepo(txn.conn, txn.executor)
        for participant in (bot, opponent):
            if await seats.get_seat(participant.id) is not None:
                raise ApiError(status.HTTP_409_CONFLICT, SEAT_HELD)
            # Design §13.3: an agent may only be handed the controls in an
            # exhibition, and the ticker would expire this anyway at consumption.
            if rated and participant.controller != "client":
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    AGENT_IN_RATED_CHALLENGE.format(name=participant.name),
                )

        challenge_id = await challenges.insert_challenge(
            challenger_bot_id=bot.id,
            opponent_bot_id=opponent.id,
            status=OPEN,
            time_control_ns=time_control_ns,
            increment_ns=increment_ns,
            created_at=utc_now_iso(),
            created_mono=app_state.deps.now_mono(),
        )
        await _emit(txn, challenges, challenge_id)

    return CreateChallengeResponse(
        challenge_id=challenge_id,
        challenger_bot_id=bot.id,
        opponent_bot_id=opponent.id,
        status=OPEN,
        time_control_ms=ns_to_ms(time_control_ns),
        increment_ms=ns_to_ms(increment_ns),
    )


async def _resolve(
    app_state: AppState, bot: BotRow, challenge_id: int, to_status: str
) -> None:
    """The read, the CAS off `open` and the event, in one transaction."""
    async with critical_section(
        app_state.store.writer, app_state.store.executor, app_state.deps.sink
    ) as txn:
        challenges = ChallengeRepo(txn.conn, txn.executor)
        challenge = await challenges.get_by_id(challenge_id)
        if challenge is None:
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                CHALLENGE_NOT_FOUND.format(challenge_id=challenge_id),
            )
        if challenge.opponent_bot_id != bot.id:
            raise ApiError(status.HTTP_403_FORBIDDEN, NOT_THE_OPPONENT)
        if challenge.status != OPEN:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                CHALLENGE_ALREADY_RESOLVED.format(status=challenge.status),
            )
        await challenges.cas_set_status(
            challenge_id, OPEN, to_status, resolved_at=utc_now_iso()
        )
        await _emit(txn, challenges, challenge_id)


@router.post("/challenges/{challenge_id}/accept", response_model=AcceptChallengeResponse)
async def accept_challenge(
    challenge_id: int, request: Request, bot: BotRow = Depends(require_bot)
):
    """`queued`, never `accepted` — that status does not exist. The ticker is what
    turns it into a game, and only if both seats are still free."""
    await _resolve(get_state(request), bot, challenge_id, QUEUED)
    return AcceptChallengeResponse(challenge_id=challenge_id, status=QUEUED)


@router.post("/challenges/{challenge_id}/decline", response_model=DeclineChallengeResponse)
async def decline_challenge(
    challenge_id: int, request: Request, bot: BotRow = Depends(require_bot)
):
    await _resolve(get_state(request), bot, challenge_id, DECLINED)
    return DeclineChallengeResponse(challenge_id=challenge_id, status=DECLINED)


@router.get("/challenges", response_model=ChallengesInboxResponse)
async def list_challenges(request: Request, bot: BotRow = Depends(require_bot)):
    app_state = get_state(request)
    conn, executor = app_state.store.reader, app_state.store.reader_executor
    challenges = ChallengeRepo(conn, executor)
    bots = BotRepo(conn, executor)
    incoming = [
        await _entry(bots, challenge) for challenge in await challenges.list_inbox(bot.id)
    ]
    # At most one by the rule above, so the list is exact rather than truncated.
    outgoing: List[ChallengeEntry] = []
    mine: Optional[ChallengeRow] = await challenges.get_open_outgoing(bot.id)
    if mine is not None:
        outgoing.append(await _entry(bots, mine))
    return ChallengesInboxResponse(incoming=incoming, outgoing=outgoing)
