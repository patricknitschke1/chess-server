# Server Engineer Role Specification

> **Revision 5 errata — binding, and they override anything below that disagrees.**
> Applied from [the round-4 review](../../../../agent-reports/2026-08-24-spec-review-round4.md). Where this spec and design spec revision 5 conflict, **the design spec wins**.
>
> 1. **Delivery has exactly two call sites, and you own both.** `GET /bots/me/turn` delivers for `controller='client'`; `get_legal_moves()` (via its route) delivers for `controller='agent'`. `get_game()` never delivers. Nothing in the ticker delivers. Revision 4 defined `deliver_position()` and called it from nowhere — every paired game would have died `no_show` at ply 0.
> 2. **`write_lock` is acquired at exactly one place per call stack.** Every mutating helper needs an inner `*_locked` form; the ticker calls only those. A nested `async with` on an `asyncio.Lock` wedges the coroutine forever, silently, and §4.6's error counter stays at zero. Verified by execution.
> 3. **Buffer SSE events inside the transaction; flush after commit, discard on rollback.**
> 4. **You own these routes**, which revision 4 left described only in the MCP and interfaces documents: `POST /bots/me/control`, `GET /bots/me`, `GET /games/{id}/moves`, `GET /bots/{bot_id}/rating_history`. `mcp-engineer` consumes them and implements none of them.
> 5. **`rated` is written at creation** from §5.3 rules 2–6; only rule 1's terminations may flip it to 0.
> 6. **Restore the `controller='client'` check** on challenge creation *and* consumption.
> 7. **`seats` must be declared `WITHOUT ROWID`.** `NOT NULL` alone does not work: in a rowid table the rowid is substituted before constraint checking, so `INSERT (NULL, 1)` is accepted and silently stored as `bot_id=1` — a phantom row holding bot 1's seat forever, with `foreign_key_check` clean. Verified across four DDL variants; `WITHOUT ROWID` still enforces uniqueness and the foreign key.
> 8. **Arena report retention orders by `id DESC`, never `created_at`.** With tied timestamps, `created_at` ordering deletes the *newest* rows. Verified.
> 9. **Validate `name`, `owner`, `candidate_name`, `opponent_name` against `^[A-Za-z0-9 _-]{1,32}$`**, and bound the arena-report numerics semantically (`wins + draws + losses == games`, non-negative, `games <= 10000`).
> 10. Use the canonical constants in design §5.2. There is no `TIME_CONTROL_MS`.

**Date:** 2026-08-24  
**Role:** server-engineer  
**Owns:** `chess_server/store/`, `chess_server/engine/`, `chess_server/api/`, `tests/chess_server/`  
**Revision:** 1

This is the authoritative specification for the server track. It distils all server responsibilities from the design spec and interfaces document into a single buildable document. Every behaviour described here is normative and must be implemented as specified.

---

## 1. Scope and Boundaries

### 1.1 What You Own

You own the complete server implementation:

- **`chess_server/store/`** — SQLite schema, repositories, write lock, CAS helpers, transaction discipline
- **`chess_server/engine/`** — move application, ticker loop, ticker supervision, reference bots, mailbox, delivery
- **`chess_server/api/`** — FastAPI routes, long-poll mechanics, SSE emission, authentication, admin endpoints
- **`tests/chess_server/`** — all server tests including concurrency, recovery, and fake-bot harness integration tests

### 1.2 What You Delegate to `chess_core`

You **never** implement:
- Chess rules (move validation, legality, termination detection)
- Clock arithmetic (delivery, elapsed time accounting, flag detection)
- Elo rating calculations
- Pairing policy (matchmaking algorithm, colour assignment)
- Game state transitions (the pure state machine)

For all of these, you **call** `chess_core` functions per Part 1 of the interfaces document. If you find yourself writing `if board.is_checkmate()` or computing Elo deltas, **stop** — you are duplicating logic that belongs in `chess_core`.

### 1.3 Who Your Consumers Are

Your HTTP API and SSE stream are consumed by:
- **`chess_client` SDK** (client-engineer's track) — registration, polling, move submission
- **`mcp/` server** (mcp-engineer's track) — an HTTP client with no privileged access
- **`web/` dashboard** (dashboard-engineer's track) — `/state`, `/events`, `/leaderboard`, `/games/{id}`

You expose a public wire contract per Part 5 of interfaces; your internals are private.

---

## 2. What You Build

### 2.1 `chess_server/store/`

**`schema.py`** — complete SQLite DDL, executed on first launch:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE bots (
  id                     INTEGER PRIMARY KEY,
  name                   TEXT NOT NULL UNIQUE,
  owner                  TEXT NOT NULL,
  token_hash             TEXT NOT NULL,  -- indexed separately
  role                   TEXT NOT NULL,  -- 'competitor' | 'benchmark'
  rating                 INTEGER NOT NULL DEFAULT 1200,
  is_anchor              INTEGER NOT NULL DEFAULT 0,
  wins                   INTEGER NOT NULL DEFAULT 0,
  losses                 INTEGER NOT NULL DEFAULT 0,
  draws                  INTEGER NOT NULL DEFAULT 0,
  games_played           INTEGER NOT NULL DEFAULT 0,
  controller             TEXT NOT NULL DEFAULT 'client',  -- 'client' | 'agent'
  last_agent_action_mono INTEGER,
  last_poll_at           TEXT,     -- UTC timestamp for display
  last_poll_mono         INTEGER,  -- monotonic ns for pool eligibility
  created_at             TEXT NOT NULL
);
CREATE INDEX idx_bots_token_hash ON bots(token_hash);

CREATE TABLE games (
  id                   INTEGER PRIMARY KEY,
  white_bot_id         INTEGER NOT NULL REFERENCES bots(id),
  black_bot_id         INTEGER NOT NULL REFERENCES bots(id),
  status               TEXT NOT NULL,  -- 'pending'|'active'|'finished'|'aborted'
  result               TEXT,           -- 'white_win'|'black_win'|'draw'
  termination          TEXT,           -- see TerminationReason enum
  fen                  TEXT NOT NULL,
  ply                  INTEGER NOT NULL,
  white_ms             INTEGER NOT NULL,
  black_ms             INTEGER NOT NULL,
  time_control_ms      INTEGER NOT NULL,
  increment_ms         INTEGER NOT NULL,
  to_move_since_mono   INTEGER NOT NULL,
  turn_started_mono    INTEGER,
  delivered_to_mover   INTEGER NOT NULL DEFAULT 0,
  rated                INTEGER NOT NULL,
  source               TEXT NOT NULL,  -- 'matchmaker' | 'challenge'
  white_strikes        INTEGER NOT NULL DEFAULT 0,
  black_strikes        INTEGER NOT NULL DEFAULT 0,
  created_at           TEXT NOT NULL,
  started_at           TEXT,
  ended_at             TEXT
);
CREATE INDEX idx_games_status ON games(status);

CREATE TABLE seats (
  bot_id  INTEGER PRIMARY KEY REFERENCES bots(id),
  game_id INTEGER NOT NULL REFERENCES games(id)
);

CREATE TABLE moves (
  game_id              INTEGER NOT NULL REFERENCES games(id),
  ply                  INTEGER NOT NULL,
  uci                  TEXT NOT NULL,
  san                  TEXT NOT NULL,
  fen_after            TEXT NOT NULL,
  server_elapsed_ms    INTEGER NOT NULL,
  client_reported_ms   INTEGER,
  PRIMARY KEY (game_id, ply)
);

CREATE TABLE rating_history (
  bot_id         INTEGER NOT NULL REFERENCES bots(id),
  game_id        INTEGER NOT NULL REFERENCES games(id),
  rating_before  INTEGER NOT NULL,
  rating_after   INTEGER NOT NULL,
  delta          INTEGER NOT NULL,
  ts             TEXT NOT NULL,
  UNIQUE (game_id, bot_id)
);

CREATE TABLE challenges (
  id                INTEGER PRIMARY KEY,
  challenger_bot_id INTEGER NOT NULL REFERENCES bots(id),
  opponent_bot_id   INTEGER NOT NULL REFERENCES bots(id),
  status            TEXT NOT NULL,  -- 'open'|'accepted'|'queued'|'consumed'|'declined'|'expired'|'cancelled'
  time_control_ms   INTEGER NOT NULL,
  increment_ms      INTEGER NOT NULL,
  created_at        TEXT NOT NULL,
  resolved_at       TEXT,
  game_id           INTEGER REFERENCES games(id)
);

CREATE TABLE mailbox (
  bot_id          INTEGER PRIMARY KEY REFERENCES bots(id),
  payload_json    TEXT NOT NULL,
  delivered_mono  INTEGER NOT NULL
);

CREATE TABLE arena_reports (
  id                 INTEGER PRIMARY KEY,
  bot_id             INTEGER NOT NULL REFERENCES bots(id),
  created_at         TEXT NOT NULL,
  candidate_name     TEXT NOT NULL,
  opponent_name      TEXT NOT NULL,
  games              INTEGER NOT NULL,
  wins               INTEGER NOT NULL,
  draws              INTEGER NOT NULL,
  losses             INTEGER NOT NULL,
  mean_move_ms       INTEGER NOT NULL,
  p95_move_ms        INTEGER NOT NULL,
  flags              INTEGER NOT NULL,
  illegal_attempts   INTEGER NOT NULL,
  seed               INTEGER NOT NULL,
  time_control_ms    INTEGER NOT NULL,
  increment_ms       INTEGER NOT NULL
);
```

**INVARIANT: `arena_reports` is display-only.** No rating, matchmaking, leaderboard, seat, or game-finalisation code may ever read this table. This is an architectural constraint, not a preference. The table exists solely to store self-reported local arena results for display in the dashboard's My Bot panel.

**Retention:** Keep 20 most recent rows per `bot_id`. Pruning happens in the same transaction as the insert, under `write_lock`, using:
```sql
DELETE FROM arena_reports
 WHERE bot_id = ?
   AND id NOT IN (
     SELECT id FROM arena_reports
      WHERE bot_id = ?
      ORDER BY created_at DESC
      LIMIT 20
   )
```

**`repositories.py`** — typed data-access layer wrapping all SQL:

- `BotRepo` — `insert_bot`, `get_bot_by_token_hash`, `get_bot_by_name`, `get_bot_by_id`, `update_bot_controller`, `update_bot_rating`, `increment_bot_counters`, `update_last_poll`, `update_last_agent_action`, `list_all_bots_for_leaderboard`
- `GameRepo` — `insert_game`, `get_game_by_id`, `update_game_status`, `update_game_fen_and_ply`, `update_game_clocks`, `mark_game_delivered`, `get_active_games`, `get_pending_games`, `list_games_by_status`
- `SeatRepo` — `insert_seats`, `delete_seats`, `get_bot_seat`, `get_seated_bots`
- `MoveRepo` — `insert_move`, `get_moves_for_game`
- `RatingHistoryRepo` — `insert_rating_change`
- `ChallengeRepo` — `insert_challenge`, `update_challenge_status`, `get_challenge_by_id`, `get_open_challenges_for_bot`, `get_challenges_inbox`, `get_queued_challenges`, `get_expired_open_challenges`
- `MailboxRepo` — `write_mailbox`, `read_mailbox`, `clear_mailbox`, `clear_all_mailboxes`
- `ArenaReportRepo` — `insert_report`, `get_reports_for_bot`, `prune_old_reports`

Every repository method that writes wraps its SQL in a function that **expects to be called inside a transaction under `write_lock`**. No repository method calls `BEGIN` or `COMMIT` — that is the caller's responsibility.

**`lock.py`** — the single process-wide write lock:

```python
import asyncio
import sqlite3
from contextlib import asynccontextmanager

write_lock = asyncio.Lock()

@asynccontextmanager
async def critical_section(conn: sqlite3.Connection, executor):
    """
    Acquire the write lock, issue BEGIN IMMEDIATE, yield, then exactly one
    COMMIT or ROLLBACK before release. Entire block is asyncio.shield()ed.
    
    Usage:
        async with critical_section(writer_conn, writer_executor):
            # all mutations here
            # exactly one commit or rollback before exiting
    """
    async with write_lock:
        try:
            await asyncio.shield(_execute_in_thread(conn, "BEGIN IMMEDIATE", executor))
            yield conn
        except Exception:
            await _execute_in_thread(conn, "ROLLBACK", executor)
            raise
        else:
            await _execute_in_thread(conn, "COMMIT", executor)
```

**`connection.py`** — connection management:

- One **writer** connection on a dedicated single-thread executor, `check_same_thread=False`, used only under `write_lock`
- One **reader** connection pool with a small semaphore (5 permits), used for display-only reads outside the lock

**`cas.py`** — CAS helper for UPDATE validation:

```python
def assert_cas_succeeded(cursor: sqlite3.Cursor, expected: int = 1):
    """
    Assert cursor.rowcount == expected after an UPDATE with a CAS predicate.
    On 0, raise CASConflict. On > expected, raise InvariantViolation (should never happen).
    """
    if cursor.rowcount == 0:
        raise CASConflict("Position has changed; CAS predicate failed")
    if cursor.rowcount > expected:
        raise InvariantViolation(f"CAS updated {cursor.rowcount} rows, expected {expected}")
```

All transitions (move, flag, finalisation, abort, reset) call this after their UPDATE.

### 2.2 `chess_server/engine/`

**`runner.py`** — applies moves and transitions games:

- `apply_move(game_id, ply, move_uci, client_reported_ms, now_mono) -> MoveApplicationResult`
  - Validate ply matches `games.ply` (CAS)
  - Load game state and `chess_core.ClockState` from DB
  - Call `chess_core.rules.validate_and_apply_move(fen, move_uci)`
  - If rejected: increment strikes, return rejection; if 3rd strike, call `forfeit_game`
  - If accepted and delivered: call `chess_core.clock.account_move_and_switch(clock, now_mono, now_mono)`
  - If flagged: call `finalise_game(game_id, result, 'flag')` under same transaction
  - If not flagged: persist move, update `games` (fen, ply, clocks, side switch, clear delivery), check `chess_core.rules.detect_termination`
  - If terminal: call `finalise_game`
  - All under `critical_section`; CAS failure returns 409 to caller

- `finalise_game(game_id, result, termination) -> None`
  - CAS UPDATE from current status to 'finished' or 'aborted'
  - Determine `rated` per §5.1 rules (no_show/restart/admin_abort → 0; benchmark → 0; same owner → 0; non-standard time control → 0; one anchor → one-sided; else 1)
  - If rated: call `chess_core.elo.compute_rating_exchange` (or `compute_one_sided_exchange` for anchors)
  - Insert `rating_history` rows (guarded by UNIQUE constraint)
  - Update `bots` rating and counters
  - Delete both `seats` rows
  - Clear both mailboxes
  - All in one transaction

- `forfeit_game(game_id, forfeiter_color) -> None`
  - Wrapper calling `finalise_game(game_id, opposite_win(forfeiter_color), 'illegal_forfeit')`

- `abort_game(game_id, termination='admin_abort') -> None`
  - CAS UPDATE from current status to 'aborted'
  - Set `rated=0`, `result=NULL`, given termination
  - Delete seats, clear mailboxes
  - No rating updates

**`ticker.py`** — the single supervised background loop:

```python
async def ticker_loop(interval_ms: int = 1000):
    """
    The only creator of games. Runs every ~1s:
    1. Consume queued challenges
    2. Run matchmaking
    3. Check delivery grace expiry
    4. Check flag-fall
    
    Wrapped in try/except; logs errors and continues. Never exits.
    """
    tick_number = 0
    consecutive_errors = 0
    
    while True:
        tick_start = time.monotonic_ns()
        try:
            await _tick(tick_number)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Tick {tick_number} failed: {e}", exc_info=True)
        
        tick_duration_ns = time.monotonic_ns() - tick_start
        update_ticker_health(tick_duration_ns, consecutive_errors)
        
        tick_number += 1
        await asyncio.sleep(interval_ms / 1000.0)

async def _tick(tick_number: int):
    async with critical_section(writer_conn, writer_executor):
        # 1. Consume queued challenges
        queued = challenge_repo.get_queued_challenges()
        for ch in queued:
            white_seat = seat_repo.get_bot_seat(ch.challenger_bot_id)
            black_seat = seat_repo.get_bot_seat(ch.opponent_bot_id)
            if white_seat or black_seat:
                challenge_repo.update_challenge_status(ch.id, 'expired', reason='seat_unavailable')
                emit_sse(challenge_updated_event(ch.id, 'expired', reason='seat_unavailable'))
                continue
            
            game_id = _create_game(ch.challenger_bot_id, ch.opponent_bot_id, ch.time_control_ms, ch.increment_ms, 'challenge')
            challenge_repo.update_challenge_status(ch.id, 'consumed', game_id=game_id)
            emit_sse(challenge_updated_event(ch.id, 'consumed', game_id=game_id))
        
        # 2. Matchmaking
        if not is_matchmaking_paused():
            pool = _build_pool_snapshot()
            pairings = chess_core.matchmaker.pair_bots(pool)
            for pairing in pairings:
                cursor = conn.cursor()
                cursor.execute("SAVEPOINT pairing")
                try:
                    _create_game(pairing.white_bot_id, pairing.black_bot_id, RATED_TIME_CONTROL_MS, RATED_INCREMENT_MS, 'matchmaker')
                    cursor.execute("RELEASE SAVEPOINT pairing")
                except sqlite3.IntegrityError:
                    # Seat collision; rollback this pairing only
                    cursor.execute("ROLLBACK TO SAVEPOINT pairing")
        
        # 3. Delivery grace expiry
        now_mono = time.monotonic_ns()
        undelivered = game_repo.get_undelivered_games()
        for game in undelivered:
            grace_ns = AGENT_DELIVERY_GRACE_NS if game.controller == 'agent' else DELIVERY_GRACE_NS
            clock = _clock_from_game(game)
            if chess_core.clock.check_delivery_timeout(clock, now_mono, grace_ns):
                if game.ply == 0:
                    # No-show at ply 0
                    abort_game(game.id, termination='no_show')
                else:
                    # Abandonment mid-game
                    finalise_game(game.id, opposite_win(game.to_move), 'abandoned')
        
        # 4. Flag detection
        delivered_active = game_repo.get_delivered_active_games()
        for game in delivered_active:
            clock = _clock_from_game(game)
            now_mono = time.monotonic_ns()
            # Check if time expired
            mover_color = Color.WHITE if game.to_move == 'white' else Color.BLACK
            mover_ns = clock.white_ns if mover_color == Color.WHITE else clock.black_ns
            elapsed_ns = now_mono - clock.turn_started_mono
            remaining_ns = mover_ns - elapsed_ns
            if remaining_ns <= 0:
                # Flagged
                finalise_game(game.id, opposite_win(mover_color), 'flag')
        
        # 5. Agent auto-release
        agent_controlled = bot_repo.get_agent_controlled_bots()
        for bot in agent_controlled:
            if bot.last_agent_action_mono and (now_mono - bot.last_agent_action_mono) > AGENT_AUTO_RELEASE_NS:
                bot_repo.update_bot_controller(bot.id, 'client')
                wake_waiters(bot.id)
        
        # 6. Expire open challenges (60s timeout)
        expired = challenge_repo.get_expired_open_challenges(now_mono, 60_000_000_000)
        for ch in expired:
            challenge_repo.update_challenge_status(ch.id, 'expired', reason='timeout')
            emit_sse(challenge_updated_event(ch.id, 'expired', reason='timeout'))

def _create_game(white_bot_id, black_bot_id, time_control_ms, increment_ms, source) -> int:
    """
    Create game and insert two seats. Called under critical_section and SAVEPOINT.
    Raises sqlite3.IntegrityError on seat collision (handled by ticker).
    """
    now_mono = time.monotonic_ns()
    now_wall = datetime.utcnow().isoformat() + 'Z'
    
    game_id = game_repo.insert_game({
        'white_bot_id': white_bot_id,
        'black_bot_id': black_bot_id,
        'status': 'pending',
        'fen': STARTING_FEN,
        'ply': 0,
        'white_ms': time_control_ms,
        'black_ms': time_control_ms,
        'time_control_ms': time_control_ms,
        'increment_ms': increment_ms,
        'to_move_since_mono': now_mono,
        'delivered_to_mover': 0,
        'rated': 1,  # will be recomputed at finalisation
        'source': source,
        'created_at': now_wall
    })
    
    seat_repo.insert_seats(white_bot_id, game_id)
    seat_repo.insert_seats(black_bot_id, game_id)
    
    emit_sse(game_created_event(game_id, white_bot_id, black_bot_id, ...))
    wake_waiters(white_bot_id)
    wake_waiters(black_bot_id)
    
    return game_id

def _build_pool_snapshot() -> List[PoolEntry]:
    """
    Query eligible bots per §9.1: role='competitor', no seat, controller='client',
    matchmaking not paused, and (held poll OR last_poll_mono within 5s).
    """
    ...
```

**Ticker supervision** — separate supervisor coroutine:

```python
async def supervise_ticker():
    """
    Watches last_tick_age_ms. If > 5000ms, log CRITICAL.
    Dashboard reads /health and shows red banner if stale.
    """
    while True:
        await asyncio.sleep(2.0)
        age = get_last_tick_age_ms()
        if age > 5000:
            logger.critical(f"Ticker stalled: last tick {age}ms ago")
```

**`reference_bots.py`** — anchors, trusted in-process code:

- `RefRandomBot`, `RefGreedyBot`, `RefDepth2Bot`
- Each implements `choose_move(board, clock) -> chess.Move`
- Registered at startup with `is_anchor=1`, fixed ratings (measured from calibration), never change
- Paired only when a competitor would otherwise sit idle, and only within ±400 rating (§9.3)

**`mailbox.py`** — per-bot delivery mailbox and poll waiters:

```python
mailbox_waiters: Dict[int, asyncio.Event] = {}  # bot_id -> Event

async def deliver_position(bot_id: int, payload: dict, now_mono: int):
    """
    Write payload to mailbox under write_lock, mark game delivered (§6.2), wake waiter.
    Idempotent: if already delivered, do nothing.
    """
    async with critical_section(writer_conn, writer_executor):
        game = game_repo.get_game_for_bot(bot_id)
        if game.delivered_to_mover == 1:
            return  # already delivered; re-delivery is free
        
        mailbox_repo.write_mailbox(bot_id, json.dumps(payload), now_mono)
        
        # Idempotent delivery UPDATE per §6.2
        game_repo.mark_game_delivered(game.id, game.ply, now_mono)
        
        wake_waiters(bot_id)

async def wait_for_turn(bot_id: int, timeout: float = 20.0) -> Optional[dict]:
    """
    Check mailbox first; if empty, wait up to timeout for wake event.
    Supersede: only one waiter per bot; second call cancels first.
    """
    payload = mailbox_repo.read_mailbox(bot_id)
    if payload:
        return json.loads(payload)
    
    # Register waiter
    if bot_id in mailbox_waiters:
        mailbox_waiters[bot_id].set()  # supersede old waiter
    
    event = asyncio.Event()
    mailbox_waiters[bot_id] = event
    
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        payload = mailbox_repo.read_mailbox(bot_id)
        return json.loads(payload) if payload else None
    except asyncio.TimeoutError:
        return None
    finally:
        if mailbox_waiters.get(bot_id) is event:
            del mailbox_waiters[bot_id]

def wake_waiters(bot_id: int):
    """Set waiter event if present."""
    if bot_id in mailbox_waiters:
        mailbox_waiters[bot_id].set()
```

### 2.3 `chess_server/api/`

**`app.py`** — FastAPI application with lifespan:

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await run_recovery()  # §7.1 — before socket accepts connections
    start_ticker_loop()
    start_ticker_supervisor()
    yield
    # Shutdown
    stop_ticker_loop()

app = FastAPI(lifespan=lifespan)
```

**`recovery.py`** — restart recovery per §7.1:

```python
async def run_recovery():
    """
    Mark all pending/active games aborted with termination='server_restart', rated=0.
    Delete all seats, clear all mailboxes, regenerate run_id.
    """
    async with critical_section(writer_conn, writer_executor):
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE games SET status='aborted', termination='server_restart', rated=0
             WHERE status IN ('pending', 'active')
        """)
        cursor.execute("DELETE FROM seats")
        cursor.execute("DELETE FROM mailbox")
        
        new_run_id = secrets.token_urlsafe(8)
        set_run_id(new_run_id)
        
        logger.info(f"Recovery complete: {cursor.rowcount} games aborted, new run_id={new_run_id}")
```

**`auth.py`** — bearer token authentication:

```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthCredentials

bearer_scheme = HTTPBearer()

async def get_current_bot(credentials: HTTPAuthCredentials = Depends(bearer_scheme)) -> Bot:
    """
    Extract token, hash, lookup bot by token_hash. Constant-time compare.
    Return Bot or raise 401 with actionable message.
    """
    token = credentials.credentials
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    bot = bot_repo.get_bot_by_token_hash(token_hash)
    if not bot:
        raise HTTPException(401, detail="No bot registered for this token. Call register_bot first.")
    return bot

async def get_admin(credentials: HTTPAuthCredentials = Depends(bearer_scheme)):
    """
    Compare credentials.credentials against ADMIN_TOKEN with secrets.compare_digest.
    Raise 401 on mismatch.
    """
    if not secrets.compare_digest(credentials.credentials, os.getenv('ADMIN_TOKEN', '')):
        raise HTTPException(401, detail="Invalid admin token")
```

**`routes.py`** — all endpoints per §8.1 and §15, binding to Part 5 models:

Every route handler is `async def`.

**POST /bots** (unauthenticated, requires join code, rate-limited by IP):

```python
@app.post("/bots", response_model=RegisterBotResponse, status_code=201)
async def register_bot(req: RegisterBotRequest):
    # Validate join_code against JOIN_CODE env
    # Check name uniqueness
    # Enforce §10.4: one competitor per owner (see below)
    # Generate token with secrets.token_urlsafe(32)
    # Hash token with sha256, store hash
    # Insert bot with controller='client', rating=1200, role=req.role
    # Return bot_id, name, token (plaintext once only)
```

**§10.4 registration rules — one competitor per owner.**

This rule was added by the orchestrator after a coverage check found it claimed by no role. It is what closes the rating-farming vector: without it an attendee registers two competitors and feeds one wins.

Enforced inside the registration transaction, under `write_lock`:

- If `role='competitor'` and that `owner` already has a `competitor` bot, reject with `409`:
  `"You already have a competitor bot registered ({existing_name}). Register additional bots with role='benchmark' — they can spar with your competitor without affecting ratings."`
- Any number of `benchmark` bots per owner is allowed.
- `role` must be `competitor` or `benchmark`. `anchor` is not registrable over HTTP; anchors are seeded at startup with `is_anchor=1` and fixed ratings (§10.3).
- The check and the insert are one transaction — two simultaneous registrations from the same owner must not both succeed.

The error text matters more than the rule: an attendee hitting this is trying to do something reasonable, and the message has to point them at the benchmark role rather than just refusing.

**GET /bots/me/turn** (authenticated, long-poll up to 20s):

```python
@app.get("/bots/me/turn", response_model=TurnResponse | NoGameResponse)
async def get_turn(bot: Bot = Depends(get_current_bot)):
    # Update last_poll_at, last_poll_mono
    # Supersede any existing waiter
    # Check mailbox first; if present, return TurnResponse
    # Otherwise wait_for_turn(bot.id, timeout=20.0)
    # If timeout, return NoGameResponse with reason='waiting_for_pairing'
    # If superseded, return NoGameResponse with reason='superseded'
    # If agent has control, return NoGameResponse with reason='agent_has_control'
```

**POST /games/{id}/moves** (authenticated):

```python
@app.post("/games/{id}/moves", response_model=SubmitMoveResponse)
async def submit_move(id: int, req: SubmitMoveRequest, bot: Bot = Depends(get_current_bot)):
    now_mono = time.monotonic_ns()
    try:
        result = await apply_move(id, req.ply, req.move, req.client_reported_ms, now_mono)
        return SubmitMoveResponse(...)
    except CASConflict:
        # Load current game state
        raise HTTPException(409, detail="CAS conflict. Position has changed.", ...)
    except IllegalMove as e:
        raise HTTPException(400, detail=f"Illegal move. Legal moves: {e.legal_moves}", ...)
    except ControllerMismatch:
        raise HTTPException(403, detail="Controller is 'agent'. Only agent tools may move.")
```

**POST /games/{id}/resign** (authenticated):

```python
@app.post("/games/{id}/resign", response_model=ResignResponse)
async def resign_game(id: int, req: ResignRequest, bot: Bot = Depends(get_current_bot)):
    # CAS check ply, check bot is in game and it's their turn
    # Call finalise_game(id, opposite_win(bot_color), 'resignation')
    # Return ResignResponse
```

**POST /challenges** (authenticated):

```python
@app.post("/challenges", response_model=CreateChallengeResponse, status_code=201)
async def create_challenge(req: CreateChallengeRequest, bot: Bot = Depends(get_current_bot)):
    # Look up opponent by name
    # Check neither bot has a seat
    # Check challenger has no open outgoing challenge
    # Determine time_control_ms/increment_ms from req.time_control
    # Insert challenge with status='open'
    # emit_sse challenge_updated event
    # Return CreateChallengeResponse
```

**POST /challenges/{id}/accept** (authenticated):

```python
@app.post("/challenges/{id}/accept", response_model=AcceptChallengeResponse)
async def accept_challenge(id: int, bot: Bot = Depends(get_current_bot)):
    # Load challenge, verify bot is opponent
    # Check acceptor has no seat
    # Update status to 'queued'
    # emit_sse challenge_updated event
    # Return AcceptChallengeResponse
```

**POST /challenges/{id}/decline** (authenticated):

```python
@app.post("/challenges/{id}/decline", response_model=DeclineChallengeResponse)
async def decline_challenge(id: int, bot: Bot = Depends(get_current_bot)):
    # Load challenge, verify bot is opponent
    # Update status to 'declined'
    # emit_sse challenge_updated event
    # Return DeclineChallengeResponse
```

**GET /challenges** (authenticated):

```python
@app.get("/challenges", response_model=ChallengesInboxResponse)
async def get_challenges_inbox(bot: Bot = Depends(get_current_bot)):
    # Query challenges where bot is challenger or opponent
    # Separate into incoming (bot is opponent) and outgoing (bot is challenger)
    # Return ChallengesInboxResponse
```

**GET /leaderboard** (unauthenticated):

```python
@app.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard():
    # Query all bots with role='competitor', sorted by rating desc
    # Add is_provisional = (games_played < 10)
    # Return LeaderboardResponse
```

**GET /games/{id}** (unauthenticated):

```python
@app.get("/games/{id}", response_model=GameDetailResponse)
async def get_game(id: int):
    # Load game + moves
    # Return GameDetailResponse
```

**GET /state** (unauthenticated):

```python
@app.get("/state", response_model=DashboardStateResponse)
async def get_state():
    # Return snapshot: run_id, event_id (last SSE seq), active games, leaderboard, featured_game_id
```

**GET /events** (unauthenticated, SSE stream):

```python
@app.get("/events")
async def sse_stream(request: Request):
    # Register client in SSE client registry
    # Yield events from queue (bounded 256, drop-oldest)
    # 15s heartbeat comments
    # On disconnect, unregister
```

**GET /health** (unauthenticated):

```python
@app.get("/health", response_model=HealthResponse)
async def health():
    # Return last_tick_age_ms, last_tick_duration_ms, active_games, pending_games, pooled_bots, held_polls, sse_clients, db_writable, consecutive_tick_errors
```

**Admin endpoints** (all require `Depends(get_admin)`):

- **POST /admin/games/{id}/abort** — CAS from current status to aborted, termination='admin_abort', rated=0, delete seats, clear mailboxes, wake waiters
- **POST /admin/matchmaking/pause** — set global matchmaking_paused flag
- **POST /admin/matchmaking/resume** — clear global matchmaking_paused flag
- **POST /admin/bots/{name}/token** — refuse if bot holds seat; generate new token, hash, update, return plaintext
- **POST /admin/reset** — wipe games/moves/rating_history/seats/mailboxes, reset bot counters to zero, keep bot identities
- **GET /admin/consistency** — assert `bots.rating == 1200 + sum(rating_history.delta)` for all bots; return violations

**Arena reports endpoints** per design spec §8.1:

- **POST /arena-reports** (authenticated, bot token) — accepts local arena report payload per Part 5, inserts into `arena_reports`, prunes old reports (keep 20 most recent per bot), emits `arena_report_posted` SSE event, returns `201` with `{report_id}`. Errors: `401` (invalid token), `422` (malformed payload), `429` (rate limited).
- **GET /bots/{bot_id}/arena-reports** (unauthenticated) — returns most recent 20 reports for the bot, ordered by `created_at` descending, per Part 5 `BotArenaReportsResponse`. Returns `404` if bot not found.

**`sse.py`** — SSE emission per §14:

```python
sse_clients: List[asyncio.Queue] = []
run_id: str = secrets.token_urlsafe(8)
event_seq: int = 0

def emit_sse(event_type: str, data: dict):
    """
    Increment event_seq, construct SSE event with run_id and seq, enqueue to all clients.
    Per-client queue bounded at 256, drop-oldest on overflow.
    """
    global event_seq
    event_seq += 1
    event = {
        "run": run_id,
        "seq": event_seq,
        "event_type": event_type,
        "data": data
    }
    for queue in sse_clients:
        if queue.qsize() >= 256:
            queue.get_nowait()  # drop oldest
        queue.put_nowait(event)

# Event constructors per Part 2
def game_created_event(...): ...
def game_started_event(...): ...
def move_played_event(...): ...
def game_ended_event(...): ...
def rating_changed_event(...): ...
def arena_report_posted_event(bot_id, bot_name, candidate_name, opponent_name, games, wins, draws, losses, mean_move_ms, p95_move_ms, flags): ...
# etc.
```

**Non-featured move coalescing** per §14: per-game throttle; after emitting a `move_played` for a non-featured game, suppress further `move_played` for that game for 500ms.

**`rate_limit.py`** — token bucket per-token:

```python
from collections import defaultdict
import time

buckets: Dict[str, TokenBucket] = defaultdict(lambda: TokenBucket(rate=20, burst=40))

def check_rate_limit(token: str):
    if not buckets[token].consume():
        raise HTTPException(429, detail="Rate limit exceeded", headers={"Retry-After": "3"})
```

---

## 3. The Concurrency Contract (Normative)

This section restates §4 of the design spec with absolute precision, because getting it wrong corrupts state silently.

### 3.1 Single Writer, One Lock, One Transaction

**All mutation** of `games`, `moves`, `seats`, `bots`, `rating_history`, `challenges`, `mailbox` happens while holding one process-wide `asyncio.Lock` (`store.write_lock`).

**A critical section is a transaction.** Acquiring the lock issues `BEGIN IMMEDIATE`; the section ends with exactly one `COMMIT` or `ROLLBACK` before the lock is released.

The critical section is wrapped in `asyncio.shield` and no database call is cancellable. A client disconnecting mid-request must never abandon a half-finalised game.

Reads that inform writes happen inside the lock. Display-only reads may use a separate read connection outside it.

### 3.2 Compare-and-Swap on Every Transition

CAS applies to **every** game-state transition — move, flag, finalisation, abort, reset — not only move submission. The predicate names **the state being transitioned from**:

```sql
UPDATE games SET status='finished', result=?, termination=?
 WHERE id=? AND status='active' AND ply=?
```

`cursor.rowcount` **MUST** be asserted to be 1 after every such UPDATE. If it is 0, another path already transitioned the game: roll back the transaction and abandon the work silently (return 409 to the HTTP caller).

### 3.3 Seats — One Non-Terminal Game per Bot

An explicit `seats` table with `PRIMARY KEY (bot_id)` enforces "one non-terminal game per bot" at the storage layer.

Two rows are inserted in the **same transaction** as the game insert; both are deleted on any terminal transition.

**Ordering and the failure path:**

- `PRAGMA foreign_keys = ON` forces `games` to be inserted **before** its two `seats` rows.
- A `UNIQUE`/PK violation aborts only the **statement**, not the transaction. Left unhandled, that leaves an orphan `games` row and one stray seat committed.
- Therefore **each pairing in the ticker is wrapped in its own `SAVEPOINT`**. On violation, `ROLLBACK TO SAVEPOINT` discards the orphan game and the stray seat; the tick continues with the next pairing.

**A game is only reachable through `seats`.** Pool eligibility, delivery and the turn endpoint resolve a bot's current game by joining `seats`, never by scanning `games`. An orphan game row is therefore inert even if one were ever committed.

**Game creation has exactly one creator: the ticker.** Challenges do not create games; they enqueue an intent that the ticker consumes. A challenge whose seat is unavailable is rejected with 409 and prose explaining that the bot is already playing.

### 3.4 Storage-Level Backstops

- `moves`: `PRIMARY KEY (game_id, ply)` — prevents duplicate ply inserts
- `rating_history`: `UNIQUE (game_id, bot_id)` — prevents double-rating one game
- `seats`: `PRIMARY KEY (bot_id)` — enforces one seat per bot
- Index on `games(status)` — scanned every tick
- Index on `bots(token_hash)` — hit on every authenticated request

### 3.5 Execution Model

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```

- **Every route handler is `async def`.** Only `sqlite3` calls enter a thread. A `def` handler would run on the shared thread pool and can deadlock against the writer.
- The **writer** connection lives on a dedicated single-thread executor (`check_same_thread=False`), used only under `write_lock`. One connection, one thread, one writer — `SQLITE_BUSY` cannot occur between our own connections.
- **Reads** use a separate connection pool with its own small limiter (5 permits), so a burst of dashboard queries cannot starve the writer's thread.

### 3.6 The Ticker is Supervised

Its silent death stops pairing and flagging while the server still looks healthy — the highest-blast-radius failure.

- The tick body is wrapped in `try/except Exception`, logs with the tick number, and continues. The loop never exits.
- The supervisor watches **`last_tick_age_ms > 5000`**, not `task.done()`. A task wedged on an await is the more likely failure and `done()` never fires for it.
- `GET /health` returns `{last_tick_age_ms, last_tick_duration_ms, active_games, pending_games, stalled_games, pooled_bots, held_polls, sse_clients, db_writable, consecutive_tick_errors}`.
- The dashboard shows a red banner when `last_tick_age_ms > 5000`. The operator must see the heartbeat from the back of the room.

### 3.7 Delivery is Idempotent

Delivery writes the turn payload to the bot's mailbox under the lock, and:

```sql
UPDATE games
   SET turn_started_mono = :now,
       delivered_to_mover = 1,
       status     = CASE WHEN status='pending' THEN 'active' ELSE status END,
       started_at = CASE WHEN status='pending' THEN :now_wall ELSE started_at END
 WHERE id = :id AND ply = :ply
   AND delivered_to_mover = 0
   AND status IN ('pending','active')
```

The `delivered_to_mover=0` predicate is what makes re-delivery free. **Re-reading the position returns the identical payload and never touches the clock.**

`delivered_to_mover` is cleared to 0 **in the same UPDATE as the side switch** (inside `account_move_and_switch` persistence), along with `turn_started_mono = NULL` and a fresh `to_move_since_mono`.

### 3.8 Tokens are Hashed, Never Logged

Tokens are stored as `sha256(token)`, indexed, compared with `secrets.compare_digest`. They never appear in logs, error bodies, or SSE payloads.

---

## 4. Full Endpoint Inventory

Every route from §8.1 and §15 with auth requirement, request/response models (bound to Interfaces Part 5), and every status code with exact error prose.

### 4.1 Public Endpoints

| Endpoint | Auth | Method | Request Model | Response Model | Status Codes |
|---|---|---|---|---|---|
| `/bots` | None (join code) | POST | `RegisterBotRequest` | `RegisterBotResponse` (201) | 400 (name taken, invalid role, invalid join code), 429 (rate limit) |
| `/bots/me/turn` | Bearer | GET | — | `TurnResponse \| NoGameResponse` (200) | 401 (invalid token), 429 (rate limit) |
| `/games/{id}/moves` | Bearer | POST | `SubmitMoveRequest` | `SubmitMoveResponse` (200) | 400 (illegal move), 401 (invalid token), 403 (controller mismatch), 409 (CAS conflict), 429 (rate limit) |
| `/games/{id}/resign` | Bearer | POST | `ResignRequest` | `ResignResponse` (200) | 401 (invalid token), 403 (not your turn / controller mismatch), 409 (game ended), 429 (rate limit) |
| `/challenges` | Bearer | POST | `CreateChallengeRequest` | `CreateChallengeResponse` (201) | 400 (opponent not found), 401 (invalid token), 409 (open challenge exists / seat taken), 429 (rate limit) |
| `/challenges/{id}/accept` | Bearer | POST | — | `AcceptChallengeResponse` (200) | 401 (invalid token), 403 (not opponent), 404 (challenge not found), 409 (already resolved / seat taken), 429 (rate limit) |
| `/challenges/{id}/decline` | Bearer | POST | — | `DeclineChallengeResponse` (200) | 401 (invalid token), 403 (not opponent), 404 (challenge not found), 409 (already resolved), 429 (rate limit) |
| `/challenges` | Bearer | GET | — | `ChallengesInboxResponse` (200) | 401 (invalid token) |
| `/leaderboard` | None | GET | — | `LeaderboardResponse` (200) | — |
| `/games/{id}` | None | GET | — | `GameDetailResponse` (200) | 404 (game not found) |
| `/state` | None | GET | — | `DashboardStateResponse` (200) | — |
| `/events` | None | GET | — | SSE stream (200) | — |
| `/health` | None | GET | — | `HealthResponse` (200) | — |

**Error prose per §8:**

- `400` illegal move: `"Illegal move '{move}'. Legal moves: {legal_moves}. Current position: {fen}"`
- `401`: `"No bot registered for this token. Call register_bot first."`
- `403` controller mismatch: `"Controller is 'agent'. Only agent tools may move."` (or reverse)
- `409` CAS conflict: `"CAS conflict. Position has changed since ply {ply}."`
- `409` seat taken: `"Either you or opponent is already in a game."`
- `429`: `"Rate limit exceeded."` with `Retry-After: 3` header

### 4.2 Admin Endpoints (all require admin token)

| Endpoint | Method | Request | Response | Status Codes |
|---|---|---|---|---|
| `/admin/games/{id}/abort` | POST | — | `AbortGameResponse` (200) | 401 (invalid admin token), 404 (game not found), 409 (already terminal) |
| `/admin/matchmaking/pause` | POST | — | `PauseMatchmakingResponse` (200) | 401 (invalid admin token) |
| `/admin/matchmaking/resume` | POST | — | `ResumeMatchmakingResponse` (200) | 401 (invalid admin token) |
| `/admin/bots/{name}/token` | POST | — | `ReissueTokenResponse` (200) | 401 (invalid admin token), 404 (bot not found), 409 (bot holds seat) |
| `/admin/reset` | POST | — | `ResetResponse` (200) | 401 (invalid admin token) |
| `/admin/consistency` | GET | — | `ConsistencyCheckResponse` (200) | 401 (invalid admin token) |

---

## 5. The Ticker: Every Action Per Tick, In Order

Run every ~1000ms, under `critical_section`:

1. **Consume queued challenges** (status='queued')
   - For each: check both seats; if available, create game and mark challenge 'consumed'; if not, mark 'expired' with reason='seat_unavailable'
2. **Matchmaking** (if not globally paused)
   - Build pool snapshot: bots with role='competitor', no seat, controller='client', (held poll OR last_poll_mono within 5s)
   - Call `chess_core.matchmaker.pair_bots(pool)`
   - For each pairing: wrap in `SAVEPOINT`, create game, insert seats; on `IntegrityError` rollback savepoint only
3. **Delivery grace expiry**
   - Query games with `delivered_to_mover=0`
   - For each: check `check_delivery_timeout(clock, now_mono, grace_ns)`
   - If expired at ply 0: abort game, termination='no_show', rated=0
   - If expired mid-game: finalise game, termination='abandoned', loss for absent side
4. **Flag detection**
   - Query games with `delivered_to_mover=1`, status='active'
   - For each: compute elapsed = now_mono - turn_started_mono; remaining = mover_time - elapsed
   - If remaining_ns <= 0: finalise game, termination='flag', loss for flagged side
5. **Agent auto-release**
   - Query bots with controller='agent'
   - For each: if (now_mono - last_agent_action_mono) > AGENT_AUTO_RELEASE_NS, set controller='client', wake waiters
6. **Expire open challenges**
   - Query challenges with status='open', created_at older than 60s
   - Mark each 'expired', reason='timeout'

All actions emit appropriate SSE events.

### Ticker Supervision

Separate coroutine, runs every 2s, logs CRITICAL if `last_tick_age_ms > 5000`.

---

## 6. Seams You Produce

### 6.1 HTTP API

Consumed by:
- **`chess_client` SDK** — registration, polling, move submission, challenges
- **`mcp/` server** — an HTTP client forwarding `Authorization` header from `.mcp.json`, no privileged access
- **`web/` dashboard** — `/state`, `/events`, `/leaderboard`, `/games/{id}`

Contract: Part 5 of interfaces document (HTTP models).

### 6.2 SSE Event Stream

Consumed by:
- **`web/` dashboard** — live updates

Contract: Part 2 of interfaces document (SSE event catalog).

All events carry `{run, seq}` per §14. Events contain **no tokens**. Clocks include `turn_elapsed_ms` computed at emit time, never `turn_started_mono`.

---

## 7. Seams You Consume (from `chess_core`)

Every function call by name from Part 1 of interfaces:

### From `chess_core/rules.py`:
- `validate_and_apply_move(fen, move_uci) -> MoveOutcome`
- `detect_termination(fen, history_fens) -> (is_terminal, termination, result)`
- `get_legal_moves(fen) -> List[str]`
- `fen_to_ascii(fen) -> str` (for MCP `get_game()`)
- `uci_to_san(fen, move_uci) -> str`
- `san_list_to_pgn(...) -> str` (for MCP `analyze_game()`)
- `position_key(fen) -> str` (for threefold detection)
- `STARTING_FEN`, `PLY_CAP`

### From `chess_core/clock.py`:
- `create_clock(time_control_ns, increment_ns, to_move, now_mono) -> ClockState`
- `deliver_position(clock, now_mono, ply) -> ClockState`
- `account_move_and_switch(clock, receive_mono, now_mono) -> ClockUpdateResult`
- `check_delivery_timeout(clock, now_mono, grace_ns) -> bool`
- `compute_turn_elapsed_ms(clock, now_mono) -> Optional[int]`
- `ms_to_ns(ms) -> int`, `ns_to_ms(ns) -> int`
- `RATED_TIME_CONTROL_NS`, `RATED_INCREMENT_NS`, `EXHIBITION_TIME_CONTROL_NS`, `EXHIBITION_INCREMENT_NS`, `DELIVERY_GRACE_NS`, `AGENT_DELIVERY_GRACE_NS`, `AGENT_AUTO_RELEASE_NS`

### From `chess_core/elo.py`:
- `compute_rating_exchange(winner_rating, loser_rating) -> (RatingUpdate, RatingUpdate)`
- `compute_draw_exchange(white_rating, black_rating) -> (RatingUpdate, RatingUpdate)`
- `compute_one_sided_exchange(competitor_rating, anchor_rating, competitor_won) -> RatingUpdate`
- `STARTING_RATING`, `K_FACTOR`

### From `chess_core/matchmaker.py`:
- `pair_bots(pool: List[PoolEntry]) -> List[Pairing]`
- `should_offer_anchor(bot, anchor, has_other_option) -> bool`
- `ANCHOR_RATING_WINDOW`

### From `chess_core/match.py`:
- `create_match() -> MatchState`
- `transition_to_active(state) -> MatchState`
- `transition_after_move(state, move_result) -> MatchState`
- `transition_to_terminal(state, termination, result) -> MatchState`
- `is_terminal(state) -> bool`
- `can_transition(state, to_status) -> bool`

---

## 8. Failure Modes and Handling

Enumerate every anticipated failure and specify exact handling:

| Failure | Detection | Handling |
|---|---|---|
| **CAS conflict** (concurrent move submission) | `cursor.rowcount == 0` after UPDATE | Roll back transaction, return 409 with current game state |
| **Seat collision** (ticker pairing race) | `sqlite3.IntegrityError` on seat insert | `ROLLBACK TO SAVEPOINT` for that pairing only; continue tick with next pairing |
| **Illegal move** | `MoveOutcome.accepted == False` | Increment strike counter; if 3rd strike, forfeit game; else return 400 with legal moves |
| **Flag-fall** | Ticker detects remaining_ns <= 0 on delivered active game | Finalise game, termination='flag', loss for flagged side |
| **Delivery grace expiry at ply 0** | Ticker detects timeout on undelivered ply-0 game | Abort game, termination='no_show', rated=0, delete seats |
| **Delivery grace expiry mid-game** | Ticker detects timeout on undelivered mid-game | Finalise game, termination='abandoned', loss for absent side |
| **Superseded poll** | Second poll for same bot arrives | Cancel first waiter, return `NoGameResponse` with reason='superseded' |
| **Dropped delivery response** | Client never receives HTTP response | Mailbox persists payload; re-poll drains it; delivery idempotency prevents clock restart |
| **Server restart mid-game** | Startup lifespan hook | Recovery aborts all pending/active games, termination='server_restart', rated=0, delete all seats/mailboxes |
| **Ticker exception** | `try/except Exception` around tick body | Log error, increment `consecutive_errors`, continue loop |
| **Ticker stall** | Supervisor checks `last_tick_age_ms > 5000` | Log CRITICAL; dashboard shows red banner; operator intervenes |
| **SQLite lock timeout** | `SQLITE_BUSY` (should never occur with our connection model) | Log CRITICAL, raise exception; single writer prevents this |
| **Rate limit breach** | Token bucket exhausted | Return 429 with `Retry-After: 3` header |
| **Missing/invalid token** | Token hash lookup fails | Return 401 with actionable message |
| **Admin abort racing ticker** | Admin CAS from 'active', ticker CAS from 'active' simultaneously | One succeeds (rowcount==1), one fails (rowcount==0) and abandons silently |
| **Controller mismatch** | Move submission while controller='agent' (or vice versa) | Return 403 with actionable message explaining handoff |
| **Challenge seat collision** | Ticker consumes challenge but seat taken | Mark challenge 'expired', reason='seat_unavailable', emit SSE |

---

## 9. Test Obligations

### 9.1 Concurrency Test (Critical)

Simultaneous move submission and ticker flag pass fired at the same instant must yield:
- Exactly one terminal transition (rowcount assertions enforce this)
- Exactly one `rating_history` row per bot (UNIQUE constraint enforces this)
- No orphan seats (transaction rollback on CAS failure enforces this)

### 9.2 Seat Collision Test

Attempting to create a second game for a seated bot must:
- Raise `sqlite3.IntegrityError` on seat insert
- Ticker pairing: rollback savepoint, continue with next pairing
- Challenge consumption: mark challenge 'expired', emit SSE, do not create game

### 9.3 Restart Recovery Test

Restart mid-game must:
- Abort all pending/active games, termination='server_restart', rated=0
- Delete all seats
- Clear all mailboxes
- Regenerate run_id
- Reconnecting bot gets "no game", re-paired within one tick

### 9.4 Delivery Idempotency Test

Re-polling for the same position must:
- Return identical payload from mailbox
- Not touch `turn_started_mono`
- Not restart clock

Delivery after side switch must:
- Write fresh payload to mailbox
- Set `turn_started_mono` to now
- Clear `delivered_to_mover` to 0 in the side-switch UPDATE

### 9.5 Fake-Bot Harness (Integration)

In-process scripted bots play complete games over real endpoints:
- Happy path: legal moves, normal termination
- Illegal move: increment strikes, 3rd strike forfeits
- Flag: submit move after time expires, assert flagged
- Abandonment: stop polling mid-game, assert abandoned after delivery grace
- No-show: never poll after pairing, assert aborted at ply 0 after delivery grace
- Superseded poll: open two connections, assert second supersedes first
- Admin abort: admin aborts mid-game, assert unrated

### 9.6 Failure Paths First

Per §18, test failure paths **before** happy paths:
- Illegal-move strikes and forfeit
- Flag-fall (exact timing, no increment on flag)
- Mid-game disconnect → abandonment
- CAS conflict on concurrent move
- Controller handoff (take/release/auto-release)
- No-show at ply 0
- Superseded poll
- Admin abort racing finalisation

---

## 10. Acceptance Criteria

Your track is complete when:

1. **Schema applied** — `schema.py` runs on first launch, all indexes and constraints in place
2. **Lock discipline enforced** — all mutation under `write_lock`, `BEGIN IMMEDIATE` on acquire, exactly one commit/rollback before release, entire critical section is `asyncio.shield`ed
3. **CAS on every transition** — move, flag, finalisation, abort, all assert `rowcount == 1` and roll back on 0
4. **Seats enforced** — two rows per game, deleted on termination, PK violation caught and handled per §4.3
5. **Ticker runs** — pairing, challenge consumption, delivery grace, flag detection, agent auto-release, challenge expiry, all in order
6. **Ticker supervised** — supervisor logs CRITICAL if `last_tick_age_ms > 5000`
7. **Recovery runs** — before socket accepts connections, aborts all active games, clears seats/mailboxes, regenerates run_id
8. **All endpoints implemented** — per §4 inventory, bind to Part 5 models, return correct status codes and error prose
9. **SSE emits** — per Part 2 catalog, all events carry `{run, seq}`, no tokens in payloads, coalescing for non-featured moves
10. **Rate limiting works** — per-token token bucket, 429 with `Retry-After` on breach
11. **Authentication works** — bearer token hashed and compared with `secrets.compare_digest`, 401 with actionable prose on mismatch
12. **Admin endpoints gated** — require `ADMIN_TOKEN`, refuse token re-issue while bot holds seat
13. **Concurrency test passes** — simultaneous move + flag → exactly one terminal transition, one rating row
14. **Recovery test passes** — restart mid-game → aborted unrated, seats freed, bot re-paired
15. **Fake-bot harness passes** — complete games over real endpoints, including all failure paths
16. **Health endpoint works** — returns ticker age, duration, game counts, pooled bots, held polls, SSE clients
17. **Consistency check works** — `/admin/consistency` asserts `bots.rating == 1200 + sum(deltas)`, logs violations at startup
18. **No tokens logged** — grep codebase for token appearances; none in logs, errors, SSE

---

## 11. All Decisions Resolved (Harmonization Revision 4)

The following design decisions were previously marked "Requires Decision" and have now been resolved during spec harmonization:

### 11.1 `controller` Field Schema — **RESOLVED**

**Resolution:** `controller TEXT NOT NULL DEFAULT 'client'` added to `bots` table schema in design spec §5. Makes control state durable across restarts and queryable in a single pass for pool eligibility.

**Action:** Add `controller` column to `bots` table in `store/schema.py`, default `'client'`, indexed.

### 11.2 SSE Coalescing Mechanism — **RESOLVED**

**Resolution:** Per-game 500ms throttle. After emitting `move_played` for a non-featured game, suppress further events for that game for 500ms. Featured games bypass throttling.

**Action:** Implement per-game throttle map in `sse.py` emitter.

### 11.3 Illegal Move Strike Reset — **RESOLVED**

**Resolution:** Per-game columns `white_strikes` and `black_strikes` in `games` table (already in §5 schema). Reset to 0 at game creation. Mistakes in game N don't affect game N+1.

**Action:** No schema change needed (columns already exist). Implement strike counting in `engine/runner.py` move validation.

### 11.4 Featured Game Selection Policy — **DELEGATED TO DASHBOARD**

**Resolution:** Dashboard owns the selection policy (highest sum of participant ratings, held 20s, ties broken by lowest game_id). Server provides `white_rating` and `black_rating` in `ActiveGameSummary` for dashboard to compute the sum.

**Action:** Add `white_rating` and `black_rating` fields to `ActiveGameSummary` in `GET /state` response. Dashboard computes featured game client-side.

### 11.5 MCP `analyze_game` Response Format — **OWNED BY MCP-ENGINEER**

**Resolution:** Markdown with three sections: (1) PGN, (2) timing table, (3) event log. Specified in detail in mcp-engineer §5.

**Action:** No server API change needed. MCP tool constructs Markdown from existing game data.

### 11.6 Leaderboard Provisional Threshold — **RESOLVED**

**Resolution:** Computed field `is_provisional = (games_played < 10)` in all leaderboard responses (HTTP API, MCP, SSE `rating_changed`). No database column.

**Action:** Add `is_provisional` boolean to `LeaderboardEntry` model, computed as `games_played < 10`.

---

## 12. Summary Report

**File path:** `docs/superpowers/specs/roles/server-engineer-spec.md`

**Design spec sections claimed:**
- §4 (concurrency — normative)
- §5 (data model)
- §6 (clock and delivery — implementation only; arithmetic delegated to chess_core)
- §7 (game state machine)
- §7.1 (restart recovery)
- §8 (play protocol — all endpoints, long-poll, mailbox)
- §9.1 (pool eligibility — pairing policy delegated to chess_core)
- §12 (challenges — queue mechanics and ticker consumption)
- §15 (admin endpoints and observability)
- §16 (security and tokens)
- §4.6 (ticker supervision and `/health`)
- §10.2 (rating application — computation delegated to chess_core)
- Reference bots from §10.3 (in-process execution only)

**Seams produced:**
- **HTTP API** (Part 5 of interfaces) — consumed by SDK, MCP, dashboard
- **SSE event stream** (Part 2 of interfaces) — consumed by dashboard

**`chess_core` functions depended on:**
- `rules.validate_and_apply_move`, `detect_termination`, `get_legal_moves`, `fen_to_ascii`, `uci_to_san`, `san_list_to_pgn`, `position_key`, `STARTING_FEN`, `PLY_CAP`
- `clock.create_clock`, `deliver_position`, `account_move_and_switch`, `check_delivery_timeout`, `compute_turn_elapsed_ms`, `ms_to_ns`, `ns_to_ms`, all time control constants
- `elo.compute_rating_exchange`, `compute_draw_exchange`, `compute_one_sided_exchange`, `STARTING_RATING`, `K_FACTOR`
- `matchmaker.pair_bots`, `should_offer_anchor`, `ANCHOR_RATING_WINDOW`
- `match.create_match`, `transition_to_active`, `transition_after_move`, `transition_to_terminal`, `is_terminal`, `can_transition`

**Action items from resolved decisions:**
1. Add `controller TEXT NOT NULL DEFAULT 'client'` to `bots` table schema
2. Add `white_rating` and `black_rating` to `ActiveGameSummary` in GET /state
3. Add `is_provisional` computed field to `LeaderboardEntry` (games_played < 10)
4. Implement per-game 500ms SSE throttle for non-featured move events
5. Implement strike counting in move validation (per-game, already in schema)

**Document completeness:** This spec is self-contained and buildable without re-reading the full design spec. Every behaviour, constraint, and failure mode is specified.
