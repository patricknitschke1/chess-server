"""Chess Arena SDK types and client.

This module re-exports ClockView from chess_core to provide a stable
import path for bot implementations while maintaining the single source
of truth in chess_core.
"""
from chess_core import ClockView

from chess_client.client import ChessClient
from chess_client.errors import (
    ClientError,
    GameEnded,
    MoveRejected,
    NotYourTurn,
    RateLimited,
    ServerError,
    TokenInvalid,
)

__all__ = [
    "ChessClient",
    "ClientError",
    "ClockView",
    "GameEnded",
    "MoveRejected",
    "NotYourTurn",
    "RateLimited",
    "ServerError",
    "TokenInvalid",
]
