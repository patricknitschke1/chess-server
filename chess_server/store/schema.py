"""DDL per server role spec §3.1. Pragmas live in db.py — they are per-connection."""
import sqlite3

# IF NOT EXISTS is the only addition to §3.1's normative text: apply_schema runs
# on every start, not only the first.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bots (
  id                     INTEGER PRIMARY KEY,
  name                   TEXT    NOT NULL UNIQUE,
  owner                  TEXT    NOT NULL,
  token_hash             TEXT    NOT NULL,
  role                   TEXT    NOT NULL,             -- 'competitor' | 'benchmark' | 'anchor'
  rating                 INTEGER NOT NULL DEFAULT 1200,
  is_anchor              INTEGER NOT NULL DEFAULT 0,
  wins                   INTEGER NOT NULL DEFAULT 0,
  losses                 INTEGER NOT NULL DEFAULT 0,
  draws                  INTEGER NOT NULL DEFAULT 0,
  games_played           INTEGER NOT NULL DEFAULT 0,
  last_poll_at           TEXT,
  last_poll_mono         INTEGER,
  last_color             TEXT,                          -- 'white' | 'black' | NULL
  white_count            INTEGER NOT NULL DEFAULT 0,
  last_opponent_id       INTEGER REFERENCES bots(id),
  created_at             TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bots_token_hash ON bots(token_hash);

CREATE TABLE IF NOT EXISTS games (
  id                   INTEGER PRIMARY KEY,
  white_bot_id         INTEGER NOT NULL REFERENCES bots(id),
  black_bot_id         INTEGER NOT NULL REFERENCES bots(id),
  status               TEXT    NOT NULL,   -- 'pending'|'active'|'finished'|'aborted'
  result               TEXT,               -- 'white_win'|'black_win'|'draw'
  termination          TEXT,
  fen                  TEXT    NOT NULL,
  ply                  INTEGER NOT NULL,
  to_move              TEXT    NOT NULL,   -- 'white' | 'black', derived from fen (§3.2)
  white_ms             INTEGER NOT NULL,
  black_ms             INTEGER NOT NULL,
  time_control_ms      INTEGER NOT NULL,
  increment_ms         INTEGER NOT NULL,
  to_move_since_mono   INTEGER NOT NULL,
  turn_started_mono    INTEGER,
  delivered_to_mover   INTEGER NOT NULL DEFAULT 0,
  rated                INTEGER NOT NULL,
  source               TEXT    NOT NULL,   -- 'matchmaker'
  white_strikes        INTEGER NOT NULL DEFAULT 0,
  black_strikes        INTEGER NOT NULL DEFAULT 0,
  created_at           TEXT    NOT NULL,
  started_at           TEXT,
  ended_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);

CREATE TABLE IF NOT EXISTS seats (
  bot_id  INTEGER PRIMARY KEY NOT NULL REFERENCES bots(id),
  game_id INTEGER NOT NULL REFERENCES games(id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS moves (
  game_id            INTEGER NOT NULL REFERENCES games(id),
  ply                INTEGER NOT NULL,
  uci                TEXT    NOT NULL,
  san                TEXT    NOT NULL,
  fen_after          TEXT    NOT NULL,
  server_elapsed_ms  INTEGER NOT NULL,
  client_reported_ms INTEGER,
  -- Both clocks as at this ply. GET /games/{id}/moves must answer for finished
  -- games, and re-deriving them from server_elapsed_ms would put increment and
  -- flag arithmetic in api/, where a second implementation of it would drift.
  white_ms_after     INTEGER NOT NULL,
  black_ms_after     INTEGER NOT NULL,
  PRIMARY KEY (game_id, ply)
);

CREATE TABLE IF NOT EXISTS rating_history (
  bot_id        INTEGER NOT NULL REFERENCES bots(id),
  game_id       INTEGER NOT NULL REFERENCES games(id),
  rating_before INTEGER NOT NULL,
  rating_after  INTEGER NOT NULL,
  delta         INTEGER NOT NULL,
  ts            TEXT    NOT NULL,
  UNIQUE (game_id, bot_id)
);
"""


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
