"""Every attendee-facing string, in one place (role spec §8.1).

Routes never inline prose. An attendee reading a `409` at 2pm on workshop day has
to be told what to do next, and prose scattered across handlers drifts.
"""
from typing import Optional

NO_BOT_FOR_TOKEN = "No bot registered for this token. Call register_bot first."
RATE_LIMITED = "Rate limit exceeded."
RETRY_AFTER_SECONDS = 3

CONTROLLER_IS_AGENT = (
    "Controller is 'agent'. Call release_control() before moving from your client."
)
CAS_CONFLICT = (
    "The position has changed since ply {ply}. Discard this move and poll"
    " GET /bots/me/turn again."
)
NOT_DELIVERED = (
    "This position has not been delivered to you. Call GET /bots/me/turn first."
)
SEAT_HELD = "Either you or your opponent is already in a game."
TAKE_CONTROL_WHILE_SEATED = (
    "Cannot take control while your bot is in a game. Wait for it to finish, or resign."
)
ILLEGAL_MOVE = (
    "Illegal move '{move}'. Legal moves: {legal_moves}. Current position: {fen}"
)

ADMIN_REQUIRED = "Admin token required."


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
    return ApiError(401, NO_BOT_FOR_TOKEN)


def rate_limited() -> ApiError:
    return ApiError(
        429, RATE_LIMITED, headers={"Retry-After": str(RETRY_AFTER_SECONDS)}
    )
