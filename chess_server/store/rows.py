"""Plain dataclasses mirroring the §3.1 columns, name for name."""
import sqlite3
from dataclasses import dataclass, fields
from typing import Optional, TypeVar

T = TypeVar("T")


@dataclass
class BotRow:
    id: int
    name: str
    owner: str
    token_hash: str
    role: str
    rating: int
    is_anchor: int
    wins: int
    losses: int
    draws: int
    games_played: int
    controller: str
    last_agent_action_mono: Optional[int]
    last_poll_at: Optional[str]
    last_poll_mono: Optional[int]
    last_color: Optional[str]
    white_count: int
    last_opponent_id: Optional[int]
    created_at: str


@dataclass
class GameRow:
    id: int
    white_bot_id: int
    black_bot_id: int
    status: str
    result: Optional[str]
    termination: Optional[str]
    fen: str
    ply: int
    to_move: str
    white_ms: int
    black_ms: int
    time_control_ms: int
    increment_ms: int
    to_move_since_mono: int
    turn_started_mono: Optional[int]
    delivered_to_mover: int
    rated: int
    source: str
    white_strikes: int
    black_strikes: int
    created_at: str
    started_at: Optional[str]
    ended_at: Optional[str]


@dataclass
class SeatRow:
    bot_id: int
    game_id: int


@dataclass
class MoveRow:
    game_id: int
    ply: int
    uci: str
    san: str
    fen_after: str
    server_elapsed_ms: int
    client_reported_ms: Optional[int]
    white_ms_after: int
    black_ms_after: int


@dataclass
class RatingHistoryRow:
    bot_id: int
    game_id: int
    rating_before: int
    rating_after: int
    delta: int
    ts: str


@dataclass
class ChallengeRow:
    id: int
    challenger_bot_id: int
    opponent_bot_id: int
    status: str
    reason: Optional[str]
    time_control_ms: int
    increment_ms: int
    created_at: str
    created_mono: int
    resolved_at: Optional[str]
    game_id: Optional[int]


def from_row(cls: type[T], row: Optional[sqlite3.Row]) -> Optional[T]:
    if row is None:
        return None
    return cls(**{f.name: row[f.name] for f in fields(cls)})
