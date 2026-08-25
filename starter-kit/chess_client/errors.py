"""Errors an attendee can see.

Every message is prose that says what to do next. A status code on its own is
not an error message.
"""


class ClientError(Exception):
    """Base class for everything this SDK raises."""


class MoveRejected(ClientError):
    """The server refused the move — illegal, or for a position that has moved on."""


class NotYourTurn(ClientError):
    """The move arrived when it was not this bot's turn."""


class GameEnded(ClientError):
    """The game is over. Stop working on it and poll for the next one."""


class TokenInvalid(ClientError):
    """The bot token is not registered on this server."""


class ServerError(ClientError):
    """The server failed (5xx). Retry with backoff."""


class RateLimited(ClientError):
    """Too many requests. `retry_after_seconds` says how long to wait."""

    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
