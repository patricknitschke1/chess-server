"""Every attendee-facing string, in one place (role spec §8.1).

Routes never inline prose. An attendee reading a `409` at 2pm on workshop day has
to be told what to do next, and prose scattered across handlers drifts.
"""
from typing import Optional

from fastapi import status

NO_BOT_FOR_TOKEN = "No bot registered for this token. Call register_bot first."
RATE_LIMITED = "Rate limit exceeded."
RETRY_AFTER_SECONDS = 3

GAME_ALREADY_ENDED = (
    "Game {game_id} is no longer at ply {ply}. It has moved on or already ended."
)
GAME_NOT_FOUND = "Game {game_id} not found."
BOT_NOT_FOUND = "Bot not found: {bot_id}. Check the id on /leaderboard."
CAS_CONFLICT = (
    "The position has changed since ply {ply}. Discard this move and poll"
    " GET /bots/me/turn again."
)
NOT_DELIVERED = (
    "This position has not been delivered to you. Call GET /bots/me/turn first."
)
NOT_IN_GAME = "Your bot is not a player in game {game_id}."
NOT_TO_MOVE = "It is not your turn in game {game_id}. Poll GET /bots/me/turn."
FLAGGED = (
    "Your clock ran out before this move arrived. The game is over."
)
SEAT_HELD = "Either you or your opponent is already in a game."
OPPONENT_NOT_FOUND = "Opponent bot not found: {name}. Check the name on /leaderboard."
ILLEGAL_MOVE = (
    "Illegal move '{move}'. Legal moves: {legal_moves}. Current position: {fen}"
)

ADMIN_REQUIRED = "Admin token required."
GAME_ALREADY_TERMINAL = "Game {game_id} has already ended; there is nothing to abort."
BOT_NAME_NOT_FOUND = "Bot not found: {name}."

INVALID_JOIN_CODE = "Invalid join code. Ask the workshop host for the current one."
INVALID_ROLE = "Invalid role '{role}'. Register with role 'competitor'."
NAME_TAKEN = "Name '{name}' is already taken. Pick another and register again."
STRING_SHAPE = (
    "Invalid {field} '{value}'. Use 1-32 characters: letters, digits, spaces,"
    " underscores or hyphens."
)
RESERVED_NAME = "The name '{name}' belongs to a reference bot. Pick another one."
RESERVED_OWNER = "The owner '{owner}' is reserved for the server. Use your own name."


class ApiError(Exception):
    """Carries the interfaces Part 5 `ErrorResponse` shape, which is `{error, details}`
    and not FastAPI's default `{detail}`."""

    def __init__(
        self,
        status_code: int,
        error: str,
        details: Optional[dict] = None,
        headers: Optional[dict] = None,
    ):
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.details = details
        self.headers = headers


def unauthorized() -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, NO_BOT_FOR_TOKEN)


def rate_limited() -> ApiError:
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        RATE_LIMITED,
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )
