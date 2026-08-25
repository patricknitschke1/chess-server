"""Server-side validation of attendee-controlled strings (role spec §8.2).

Independent of the dashboard's `textContent` rendering, and both are required:
this layer could be relaxed by someone who does not know the dashboard depends
on it, and an escape could be missed in one cell. A bot named
`<img src=x onerror=...>` has to be boring at both ends.
"""
import re

from fastapi import status

from chess_server.api.errors import (
    RESERVED_NAME,
    RESERVED_OWNER,
    STRING_SHAPE,
    ApiError,
)
from chess_server.engine.reference_bots import ANCHORS

IDENTIFIER = re.compile(r"^[A-Za-z0-9 _-]{1,32}$")

RESERVED_NAMES = frozenset(name.casefold() for name, _bot, _rating in ANCHORS)
RESERVED_OWNERS = frozenset({"server"})


def validate_identity(name: str, owner: str) -> None:
    """Case-folded reservations: without them an attendee registers as an anchor,
    and the leaderboard and the anchor gate both read a bot that is not what they
    think it is."""
    for field, value in (("name", name), ("owner", owner)):
        if not IDENTIFIER.match(value):
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                STRING_SHAPE.format(field=field, value=value),
            )
    if name.casefold() in RESERVED_NAMES:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, RESERVED_NAME.format(name=name)
        )
    if owner.casefold() in RESERVED_OWNERS:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, RESERVED_OWNER.format(owner=owner)
        )
