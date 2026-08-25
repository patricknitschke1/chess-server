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


class ResignRequest(BaseModel):
    ply: int


class ResignResponse(BaseModel):
    game_id: int
    status: str
    result: str
    termination: str


class SetControlRequest(BaseModel):
    action: str


class SetControlResponse(BaseModel):
    controller: str
    message: str


class LegalMovesResponse(BaseModel):
    game_id: int
    ply: int
    legal_moves: List[str]
    fen: str


class CreateChallengeRequest(BaseModel):
    opponent: str
    time_control: str = "rated"


class CreateChallengeResponse(BaseModel):
    challenge_id: int
    challenger_bot_id: int
    opponent_bot_id: int
    status: str
    time_control_ms: int
    increment_ms: int


class AcceptChallengeResponse(BaseModel):
    challenge_id: int
    status: str


class DeclineChallengeResponse(BaseModel):
    challenge_id: int
    status: str


class ChallengeEntry(BaseModel):
    challenge_id: int
    challenger_bot_id: int
    challenger_bot_name: str
    opponent_bot_id: int
    opponent_bot_name: str
    status: str
    time_control_ms: int
    increment_ms: int
    created_at: str


class ChallengesInboxResponse(BaseModel):
    incoming: List[ChallengeEntry]
    outgoing: List[ChallengeEntry]
