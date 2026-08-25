"""Request and response models, bound field-for-field to interfaces Part 5.

One model file. A second definition of the same wire shape is how two tracks end
up disagreeing about a field name that neither of them changed.
"""
from typing import List, Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    details: Optional[dict] = None


class TurnResponse(BaseModel):
    game_id: int
    ply: int
    color: str
    fen: str
    legal_moves: List[str]
    history_san: List[str]
    white_ms: int
    black_ms: int
    time_control_ms: int
    increment_ms: int
    controller: str


class NoGameResponse(BaseModel):
    game_id: None = None
    reason: str


class RegisterBotRequest(BaseModel):
    name: str
    owner: str
    join_code: str
    role: str = "competitor"


class RegisterBotResponse(BaseModel):
    bot_id: int
    name: str
    token: str


class MyBotResponse(BaseModel):
    bot_id: int
    name: str
    owner: str
    role: str
    rating: int
    wins: int
    losses: int
    draws: int
    games_played: int
    is_provisional: bool
    controller: str
    current_game_id: Optional[int]


class SubmitMoveRequest(BaseModel):
    ply: int
    move: str
    client_reported_ms: Optional[int] = None


class SubmitMoveResponse(BaseModel):
    game_id: int
    ply: int
    fen: str
    status: str
    result: Optional[str] = None
    termination: Optional[str] = None
