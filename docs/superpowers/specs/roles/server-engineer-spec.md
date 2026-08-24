# Server Engineer Role Specification

**Date:** 2026-08-24
**Role:** server-engineer
**Owns:** `chess_server/store/`, `chess_server/engine/`, `chess_server/api/`, `tests/chess_server/`
**Revision:** 2 — rewritten against the design spec to close [the round-6 review](../../../agent-reports/2026-08-24-server-role-spec-review.md)

Revision 1 carried a block of corrections at the top that fixed ten rules in prose while leaving in the body the defective code blocks those rules were correcting. A plan author copies the code block, not the prose. This revision has no correction block: every rule is stated once, in the body, and the code that contradicted it has been deleted.

Where this document and the design spec disagree, **the design spec wins** and this document is a bug. Where this document and the interfaces document disagree on a wire shape, the interfaces document wins.

**Style note for anyone editing this file.** Prefer a stated rule plus the failure it prevents over pseudocode. A rule that is wrong reads wrong; a code block that is wrong reads like a template. Where a code block survives here it is because the exact SQL or the exact control flow *is* the thing being specified, and it must be correct standing alone with no accompanying correction.

---

## 1. Scope and boundaries

### 1.1 What you own

- **`chess_server/store/`** — SQLite schema, repositories, the write lock, transaction and savepoint discipline, CAS helpers
- **`chess_server/engine/`** — move application, the single supervised ticker, anchor execution, reference bots, mailbox and poll waiters
- **`chess_server/api/`** — FastAPI routes, long-poll mechanics, SSE emission, authentication, rate limiting, the admin router
- **`tests/chess_server/`** — concurrency, recovery, seat-collision and fake-bot harness tests

### 1.2 What you delegate to `chess_core`

You never implement chess rules, clock arithmetic, Elo, pairing policy, or the ply cap. Concretely: **`chess_server/` never subtracts two monotonic timestamps and never asks whether a board is checkmate.** Both questions have a named `chess_core` answer (§12), and this is a hard rule rather than a preference because the flag predicate has already been stated two different ways in two different files, and neither raised.

If you are writing `if board.is_checkmate()`, or `now - turn_started_mono`, or an expected-score formula, stop.

### 1.3 Who your consumers are

The `chess_client` SDK, the MCP server, and the dashboard. All three bind to the wire contract — interfaces Part 5 for HTTP, Part 2 for SSE — and to nothing else. Your internals are private.

---

## 2. Units, constants, and the one conversion boundary

**Nanoseconds internally, milliseconds on the wire and in the database.** Every monotonic value (`*_mono`) is nanoseconds and lives only in memory or in an `INTEGER` column that recovery clears (§8.6). Every duration on the wire is milliseconds.

**The conversion boundary is the repository layer, and it is two functions**, both in `chess_server/store/repositories.py`:

```python
def _clock_from_game(game: GameRow) -> ClockState:
    """The single ms -> ns boundary. Nothing else in chess_server/ calls ms_to_ns."""
    return ClockState(
        white_ns=ms_to_ns(game.white_ms),
        black_ns=ms_to_ns(game.black_ms),
        time_control_ns=ms_to_ns(game.time_control_ms),
        increment_ns=ms_to_ns(game.increment_ms),
        to_move=Color(game.to_move),
        to_move_since_mono=game.to_move_since_mono,
        turn_started_mono=game.turn_started_mono,
        delivered_to_mover=game.delivered_to_mover,
    )


def _clock_to_game_fields(clock: ClockState) -> dict:
    """The single ns -> ms boundary. Nothing else in chess_server/ calls ns_to_ms."""
    return {
        "white_ms": ns_to_ms(clock.white_ns),
        "black_ms": ns_to_ms(clock.black_ns),
        "to_move": clock.to_move.value,
        "to_move_since_mono": clock.to_move_since_mono,
        "turn_started_mono": clock.turn_started_mono,
        "delivered_to_mover": clock.delivered_to_mover,
    }
```

`to_move_since_mono` and `turn_started_mono` pass through unconverted: they are monotonic nanosecond counts, not durations, and rounding them would corrupt the clock rather than truncate it.

**Accepted limit, stated rather than discovered later.** `ns_to_ms` floors, so a round trip through the database loses up to 1 ms of the mover's remaining time per move — about 100 ms over a 100-move game, always against the mover, and identically for both colours. This is accepted. It must not be "fixed" by rounding at one call site, which would make the two sides asymmetric.

### 2.1 Constants are imported, never inlined

Every constant below is imported from `chess_core`. **No numeric literal for any of them appears anywhere in `chess_server/`.** A `1000`, `20.0`, `5000` or `60_000_000_000` in the ticker or the poll handler is a defect.

| Import from | Names |
|---|---|
| `chess_core.clock` | `RATED_TIME_CONTROL_NS`, `RATED_INCREMENT_NS`, `EXHIBITION_TIME_CONTROL_NS`, `EXHIBITION_INCREMENT_NS`, `DELIVERY_GRACE_NS`, `AGENT_DELIVERY_GRACE_NS`, `AGENT_AUTO_RELEASE_NS`, `POLL_RECENCY_NS`, `CHALLENGE_TTL_NS`, `POLL_HOLD_NS`, `TICK_INTERVAL_NS` |
| `chess_core.rules` | `STARTING_FEN`, `PLY_CAP` |
| `chess_core.elo` | `STARTING_RATING`, `K_FACTOR` |
| `chess_core.matchmaker` | `ANCHOR_RATING_WINDOW` |

The ticker sleeps `TICK_INTERVAL_NS / 1e9`. The poll holds `POLL_HOLD_NS / 1e9`. There is no `TIME_CONTROL_MS` and no `RATED_INCREMENT_MS`; the latter name does not exist, and passing a `*_NS` value into a `*_ms` parameter puts 5.7 years on each clock — at which point nothing ever flags, and the failure is invisible because the column is populated, non-null and decreasing.

**Three constants are server-local**, declared once in `chess_server/`, because they drive presentation and nothing in `chess_core` may depend on them:

| Constant | Value | Declared in | Purpose |
|---|---|---|---|
| `MOVE_COALESCE_NS` | 500_000_000 | `api/sse.py` | non-featured `move_played` throttle (design §14) |
| `DISCONNECT_AFTER_NS` | 30_000_000_000 | `engine/ticker.py` | the `bot_disconnected` edge (interfaces Part 2) |
| `REGISTER_PER_IP_PER_MIN` | 10 | `api/rate_limit.py` | `POST /bots` IP limit (design §8.5 gives the rule, not the number) |

If any of these ever affects a game outcome it has been misplaced, and it belongs in design §5.2 instead.

---

## 3. Store — `chess_server/store/`

### 3.1 Schema

Executed on first launch by `schema.py`. The pragmas are applied to every connection, not only the writer.

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE bots (
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
  controller             TEXT    NOT NULL DEFAULT 'client',  -- 'client' | 'agent'
  last_agent_action_mono INTEGER,
  last_poll_at           TEXT,
  last_poll_mono         INTEGER,
  last_color             TEXT,                          -- 'white' | 'black' | NULL   (§9.3)
  white_count            INTEGER NOT NULL DEFAULT 0,    --                            (§9.3)
  last_opponent_id       INTEGER REFERENCES bots(id),   --                            (§9.3)
  created_at             TEXT    NOT NULL
);
CREATE INDEX idx_bots_token_hash ON bots(token_hash);

CREATE TABLE games (
  id                   INTEGER PRIMARY KEY,
  white_bot_id         INTEGER NOT NULL REFERENCES bots(id),
  black_bot_id         INTEGER NOT NULL REFERENCES bots(id),
  status               TEXT    NOT NULL,   -- 'pending'|'active'|'finished'|'aborted'
  result               TEXT,               -- 'white_win'|'black_win'|'draw'
  termination          TEXT,
  fen                  TEXT    NOT NULL,
  ply                  INTEGER NOT NULL,
  to_move              TEXT    NOT NULL,   -- 'white' | 'black'                       (§3.2)
  white_ms             INTEGER NOT NULL,
  black_ms             INTEGER NOT NULL,
  time_control_ms      INTEGER NOT NULL,
  increment_ms         INTEGER NOT NULL,
  to_move_since_mono   INTEGER NOT NULL,
  turn_started_mono    INTEGER,
  delivered_to_mover   INTEGER NOT NULL DEFAULT 0,
  rated                INTEGER NOT NULL,
  source               TEXT    NOT NULL,   -- 'matchmaker' | 'challenge'
  white_strikes        INTEGER NOT NULL DEFAULT 0,
  black_strikes        INTEGER NOT NULL DEFAULT 0,
  created_at           TEXT    NOT NULL,
  started_at           TEXT,
  ended_at             TEXT
);
CREATE INDEX idx_games_status ON games(status);

CREATE TABLE seats (
  bot_id  INTEGER PRIMARY KEY NOT NULL REFERENCES bots(id),
  game_id INTEGER NOT NULL REFERENCES games(id)
) WITHOUT ROWID;

CREATE TABLE moves (
  game_id            INTEGER NOT NULL REFERENCES games(id),
  ply                INTEGER NOT NULL,
  uci                TEXT    NOT NULL,
  san                TEXT    NOT NULL,
  fen_after          TEXT    NOT NULL,
  server_elapsed_ms  INTEGER NOT NULL,
  client_reported_ms INTEGER,
  PRIMARY KEY (game_id, ply)
);

CREATE TABLE rating_history (
  bot_id        INTEGER NOT NULL REFERENCES bots(id),
  game_id       INTEGER NOT NULL REFERENCES games(id),
  rating_before INTEGER NOT NULL,
  rating_after  INTEGER NOT NULL,
  delta         INTEGER NOT NULL,
  ts            TEXT    NOT NULL,
  UNIQUE (game_id, bot_id)
);

CREATE TABLE challenges (
  id                INTEGER PRIMARY KEY,
  challenger_bot_id INTEGER NOT NULL REFERENCES bots(id),
  opponent_bot_id   INTEGER NOT NULL REFERENCES bots(id),
  status            TEXT    NOT NULL,   -- 'open'|'queued'|'consumed'|'declined'|'expired'
  reason            TEXT,               -- 'seat_unavailable'|'timeout'|'server_restart'|NULL
  time_control_ms   INTEGER NOT NULL,
  increment_ms      INTEGER NOT NULL,
  created_at        TEXT    NOT NULL,   -- wall clock, for display
  created_mono      INTEGER NOT NULL,   -- monotonic ns, for CHALLENGE_TTL_NS         (§3.3)
  resolved_at       TEXT,
  game_id           INTEGER REFERENCES games(id)
);
```

There is no `mailbox` table (§5.1) and no `arena_reports` table (§3.5).

**`seats` must be `WITHOUT ROWID`, and `NOT NULL` alone does not work.** In a rowid table, `INTEGER PRIMARY KEY` is a rowid alias and the rowid is substituted *before* constraint checking, so `INSERT (NULL, 1)` is accepted and silently stored as `bot_id = 1` — a phantom row holding bot 1's seat for the rest of the day, with `PRAGMA foreign_key_check` reporting clean. Verified across four DDL variants; `WITHOUT ROWID` still enforces both the uniqueness and the foreign key, so it costs nothing here.

**`challenges.status` has five values, not seven.** `accepted` was never written (accept marks `queued` directly) and `cancelled` had no endpoint. Both are deleted rather than left as states an implementer must reason about and a test must cover.

**`challenges.reason` exists** because the ticker writes `reason='seat_unavailable'` and `reason='timeout'`, and design §12's *"an SSE event explains why; no silent drop"* depends on it reaching the wire.

### 3.2 `games.to_move` is a denormalisation with one write rule

`to_move` is stored because the flag sweep needs the side to move for every active game once a tick, and parsing every FEN to read one field is wasteful under the single writer. **The FEN is authoritative; the column is derived from it and written in the same statement that writes the FEN** — from the second field of `fen_after` at every move, and from `STARTING_FEN` at creation. It is never computed from ply parity: parity is only valid for games that started from the standard position, and the local arena already starts games from an opening book.

### 3.3 `challenges.created_mono`

The TTL sweep compares `now_mono - created_mono > CHALLENGE_TTL_NS`. `created_at` is wall-clock `TEXT` for display; comparing a monotonic count against it is a category error that a builder would otherwise have to invent a fix for. Recovery expires every non-terminal challenge (§8.6), so no `created_mono` from a previous process is ever read.

### 3.4 Storage-level backstops

`moves` PK `(game_id, ply)` prevents a duplicate ply. `rating_history` `UNIQUE (game_id, bot_id)` makes double-rating one game a constraint violation rather than a reconciliation problem. `seats` PK is the one-non-terminal-game-per-bot invariant. Index on `games(status)`, scanned every tick, and on `bots(token_hash)`, hit on every authenticated request.

### 3.5 `arena_reports` is not in this build

Design §21 defers the whole `arena_reports` vertical — table, repository, retention pruning, semantic validation, `POST /arena-reports`, `GET /bots/{bot_id}/arena-reports`, and the `arena_report_posted` SSE event — because its only producer, `arena.py --report`, is deferred with it. Building the consumer without the producer means a table, a repository, two routes and an event with nothing to exercise them, including a retention `ORDER BY` that has now been got wrong twice.

**Do not build any part of it.** It returns as a unit with the dashboard panel that renders it, and when it returns it is display-only: no rating, matchmaking, leaderboard, seat or finalisation code may ever read it.

### 3.6 Repositories

`repositories.py` wraps all SQL in typed methods. **No repository method issues `BEGIN`, `COMMIT`, `ROLLBACK` or `SAVEPOINT`.** Every writing method assumes it is called inside an open transaction under `write_lock`. That is the caller's job, and keeping it there is what makes §4 auditable by reading call sites rather than by tracing helpers.

- `BotRepo` — `insert_bot`, `get_by_token_hash`, `get_by_name`, `get_by_id`, `get_competitor_for_owner`, `update_controller`, `update_rating_and_counters`, `update_pool_history`, `update_last_poll`, `update_last_agent_action`, `list_leaderboard`, `list_pool_candidates`, `list_agent_controlled`, `list_anchors`
- `GameRepo` — `insert_game`, `get_by_id`, `get_for_bot`, `cas_apply_move`, `cas_deliver`, `cas_terminate`, `list_undelivered_non_terminal`, `list_delivered_active`, `list_anchor_to_move`, `list_active_summaries`
- `SeatRepo` — `insert_seat`, `delete_seats_for_game`, `get_seat`, `list_seated_bot_ids`
- `MoveRepo` — `insert_move`, `list_moves_for_game`
- `RatingHistoryRepo` — `insert_rating_change`, `sum_deltas_by_bot`, `list_points_for_bot`
- `ChallengeRepo` — `insert_challenge`, `cas_set_status`, `get_by_id`, `get_open_outgoing`, `list_inbox`, `list_queued`, `list_expired_open`, `expire_all_non_terminal`

`update_pool_history(bot_id, last_color, last_opponent_id, increment_white)` is the §9.3 writer and is called only from the terminal-transition path (§6.5).

### 3.7 The write lock and the transaction

```python
write_lock = asyncio.Lock()


@asynccontextmanager
async def critical_section(conn, executor) -> AsyncIterator[Txn]:
    """Acquire the single writer, open a transaction, yield a Txn, commit or roll back.

    Exactly one COMMIT or ROLLBACK runs before the lock is released, and both run
    to completion even if this task is being cancelled.
    """
    async with write_lock:
        await _finish(conn, "BEGIN IMMEDIATE", executor)
        txn = Txn(conn=conn, executor=executor)
        try:
            yield txn
        except BaseException:
            await _finish(conn, "ROLLBACK", executor)
            txn.discard()
            raise
        else:
            await _finish(conn, "COMMIT", executor)
            txn.flush()


async def _finish(conn, sql: str, executor) -> None:
    """Run BEGIN / COMMIT / ROLLBACK to completion, even under cancellation.

    A bare `await` here is cancellable. A cancelled ROLLBACK releases write_lock with
    the single writer connection still inside a transaction, after which every later
    BEGIN IMMEDIATE raises "cannot start a transaction within a transaction" — for the
    life of the process.
    """
    fut = asyncio.ensure_future(_execute(conn, sql, executor))
    cancelled: BaseException | None = None
    while not fut.done():
        try:
            await asyncio.shield(fut)
        except asyncio.CancelledError as exc:
            cancelled = exc          # ours, not the statement's: hold it and keep waiting
    fut.result()                     # surface a genuine SQL error
    if cancelled is not None:
        raise cancelled
```

**`except BaseException`, not `except Exception`.** `asyncio.CancelledError` is not an `Exception` subclass, so `except Exception` never fires for it — verified. A client disconnecting during a move, an `asyncio.wait_for` firing, or the §7.6 supervisor cancelling a wedged ticker all arrive as a cancellation inside the `yield`, and the naive handler drops the rollback and bricks the writer connection for the rest of the process while `/health` looks fine.

It compounds with the supervisor: design §4.6 requires cancel-and-restart at 15 s, so the prescribed remedy for a stall is a cancellation delivered at exactly the point a naive handler mishandles. Without this block, the fix for the stall is what makes the stall permanent.

### 3.8 `Txn` — the event buffer and savepoints, in one place

Design §4.1 requires that no SSE event be visible before the transaction that produced it commits, and §4.3 requires a savepoint per unit of work. Both are properties of the transaction, so both live on the object the transaction yields, rather than being restated at the twenty sites that would otherwise each have to remember them. A rule enforced by the mechanism cannot be forgotten at one site.

```python
@dataclass
class Txn:
    conn: sqlite3.Connection
    executor: Executor
    events: list[tuple[str, dict]] = field(default_factory=list)
    deferred: list[Callable[[], None]] = field(default_factory=list)

    def emit(self, event_type: str, data: dict) -> None:
        """Buffer an SSE event. Nothing leaves the process until flush()."""

    def defer(self, fn: Callable[[], None]) -> None:
        """Register an in-process mutation to apply only if this transaction commits:
        the mailbox, the history-FEN cache, waking waiters."""

    @asynccontextmanager
    async def savepoint(self, name: str) -> AsyncIterator[None]:
        """SAVEPOINT / RELEASE, or ROLLBACK TO on failure — and on rollback, truncate
        self.events and self.deferred back to their lengths at entry."""

    def flush(self) -> None:
        """Assign the global seq in commit order, fan out to SSE clients, run deferred."""

    def discard(self) -> None:
        """Drop the buffer. A rolled-back pairing must not put a game on the projector
        that GET /games/{id} then 404s, and must not consume a seq for state that does
        not exist — which would also defeat the gap check /state's event_id depends on."""
```

`seq` is assigned at flush time, in commit order, never at `emit()` time.

### 3.9 Every mutating helper has a `*_locked` form

`asyncio.Lock` is not re-entrant and has no timeout. An inner `async with write_lock` inside an outer one never returns: the coroutine wedges on an await, raises nothing, `consecutive_tick_errors` stays at zero, and `last_tick_age_ms` climbs with no exception anywhere. Verified by execution. This is invisible in review because the nested call looks like an ordinary function call.

**Every mutating helper therefore exists in two forms**, and the split is mechanical:

| Inner — assumes the lock and an open transaction | Outer — opens `critical_section`, calls the inner |
|---|---|
| `deliver_position_locked(txn, bot_id, now_mono)` | `deliver_position(bot_id, now_mono)` |
| `apply_move_locked(txn, game, ply, uci, client_ms, now_mono)` | `apply_move(game_id, ply, uci, client_ms, now_mono)` |
| `finalise_game_locked(txn, game, result, termination)` | `finalise_game(game_id, result, termination)` |
| `forfeit_game_locked(txn, game, forfeiter)` | — reached only from inside `apply_move_locked` |
| `abort_game_locked(txn, game, termination)` | `abort_game(game_id, termination)` |
| `create_game_locked(txn, white_id, black_id, tc_ns, inc_ns, source)` | — only the ticker creates games |
| `set_controller_locked(txn, bot, controller)` | `set_controller(bot_id, controller)` |
| `set_challenge_status_locked(txn, ch, status, reason, game_id)` | `set_challenge_status(challenge_id, ...)` |

**Route handlers call the outer form exactly once per request. The ticker calls only inner forms. No inner form calls an outer form.** That last sentence is what makes the invariant checkable by grep rather than by reasoning about call graphs.

### 3.10 CAS on every transition

Every game-state transition — move, flag, finalisation, abort, admin abort, reset — is a single `UPDATE` whose `WHERE` names **the state being transitioned from**, and whose `rowcount` is asserted to be exactly 1:

```sql
UPDATE games
   SET status='finished', result=?, termination=?, ended_at=?,
       delivered_to_mover=0, turn_started_mono=NULL
 WHERE id=? AND status=? AND ply=?
```

`cas.py` provides `assert_cas(cursor, expected=1)`, raising `CASConflict` on 0 and `InvariantViolation` above `expected`. On `CASConflict`, roll back to the nearest savepoint (in the ticker) or roll back the transaction (in a route) and abandon the work silently. A route turns it into `409` carrying `{ply, fen, status}`; the ticker continues with the next unit of work.

Clearing `delivered_to_mover` and `turn_started_mono` at finalisation is not cosmetic: it keeps a finished game out of the delivery sweep even if that sweep's status filter (§7.4) were ever weakened again.

The one exception is the delivery UPDATE, whose zero-row outcome is a legitimate result rather than a conflict — see §5.2.

### 3.11 Connections

The **writer** is one `sqlite3.Connection` on a dedicated single-thread executor with `check_same_thread=False`, touched only under `write_lock`. One connection, one thread, one writer — `SQLITE_BUSY` cannot arise between our own connections.

**Reads use a separate connection**, outside the lock, for display-only queries. There is no permit semaphore: the reader/writer split is what protects the writer's thread, and a five-permit limiter on top of it is a knob nobody will tune correctly at twenty bots.

Reads that inform a write happen inside the lock, on the writer connection.

---

## 4. The concurrency contract, in one place

1. All mutation of `games`, `moves`, `seats`, `bots`, `rating_history`, `challenges` happens under `store.write_lock`.
2. A critical section is a transaction: `BEGIN IMMEDIATE` on acquire, exactly one `COMMIT` or `ROLLBACK` before release, both cancellation-proof (§3.7).
3. `write_lock` is acquired at exactly one place per call stack (§3.9).
4. Every transition is a CAS on the state being left, with `rowcount` asserted (§3.10).
5. Every unit of work inside a tick is its own `SAVEPOINT` (§7.1).
6. SSE events buffer inside the transaction and flush after commit; a rollback discards them (§3.8).
7. Every route handler is `async def`; only `sqlite3` calls enter a thread. A `def` handler runs on the shared thread pool and can deadlock against the writer.
8. Tokens are stored as indexed `sha256(token)`, compared with `secrets.compare_digest`, and never appear in a log line, an error body, an SSE payload, or a rate-limiter key.

---

## 5. Mailbox and long-poll — `chess_server/engine/mailbox.py`

### 5.1 The mailbox is process state, not a table

`mailbox: dict[int, TurnPayload]`, mutated in the same critical section as the delivery UPDATE and applied on commit via `txn.defer`. Design §5 rejects the table explicitly: it bought a write on the hottest path under `write_lock` plus a repository, in exchange for durability that §8.6 deliberately discards on every start.

One mailbox entry per bot, holding at most one payload.

### 5.2 Delivery has exactly two HTTP call sites, and you own both

`GET /bots/me/turn` delivers when `controller='client'`. The route behind the MCP `get_legal_moves()` tool delivers when `controller='agent'`. **`GET /games/{id}` and the MCP `get_game()` never deliver.** Nothing else in the HTTP surface delivers.

There is a third delivery, in the ticker, and it exists only for anchors: an anchor has no HTTP client, so the in-process `choose_move` call *is* the delivery (§7.3).

Delivery is idempotent, and it is what moves a game from `pending` to `active`:

```sql
UPDATE games
   SET turn_started_mono  = :now_mono,
       delivered_to_mover = 1,
       status     = CASE WHEN status='pending' THEN 'active'   ELSE status     END,
       started_at = CASE WHEN status='pending' THEN :now_wall  ELSE started_at END
 WHERE id = :id AND ply = :ply
   AND delivered_to_mover = 0
   AND status IN ('pending','active')
```

The `status` transition is part of this statement, not a separate one. Splitting them fails every first move into a permanent 409 loop that §7.4's grace cannot rescue, because `delivered_to_mover` is already 1 by then.

`rowcount == 0` here is **not** an error: it means the position was already delivered, and re-delivery is free by design. The caller re-reads the row and returns the payload already in the mailbox. `deliver_position_locked` therefore does not call `assert_cas`. **Re-reading a position returns the identical payload and never touches the clock** — without that guard a bot could re-poll while thinking and reset its own clock, the same exploit design §8.3 closes for rejected moves.

When the UPDATE moved `pending → active`, buffer `game_started`.

### 5.3 The mailbox is cleared when the side switches or the game ends

**This is the highest-cost rule in the document.** `mailbox[mover_id]` is cleared inside the same critical section as the side-switch UPDATE (§6.3), and both mailboxes are cleared at every terminal transition.

Without it, a bot re-polls immediately after its move lands, drains its own stale payload for the ply it just played, computes a move for that ply, submits it, gets a `409`, and per design §8.3 discards the move and re-polls — a loop with no timeout, no error log, and a request rate below the limiter's threshold. The bot burns its whole clock and flags. The attendee sees "my bot never moves" and a `flag` termination, and nothing anywhere raised.

**Belt and braces: the turn endpoint discards a payload whose `ply` no longer matches `games.ply`.** Two independent layers, because the clearing site is one line inside a long UPDATE and is easy to lose in a later edit.

### 5.4 One waiter per bot, and supersede is a distinct signal

```python
@dataclass
class Waiter:
    event: asyncio.Event
    superseded: bool = False

waiters: dict[int, Waiter] = {}
```

The poll handler **registers its waiter before it reads the mailbox**, not after. Reading first and registering second loses any wake that fires in the gap — from a game the ticker just created, or from agent auto-release — and the poll then hangs for the full `POLL_HOLD_NS` while a delivered position sits waiting.

A second poll for the same bot sets `old.superseded = True` and then `old.event.set()`. The old waiter wakes, sees the flag, and returns `NoGameResponse(reason='superseded')` **without touching the mailbox**. Without a distinct flag, the woken waiter either drains the payload — handing the same position to two connections, defeating "one waiter per bot" — or returns `None`, which the route reports as `waiting_for_pairing` rather than `superseded`.

Supersede cancels a *waiter*. It can never discard a *delivery*, because the delivery is in the mailbox and the mailbox outlives the request.

The server holds for `POLL_HOLD_NS` (20 s); the SDK's client timeout is 30 s. The skew is deliberate so a healthy hold never times out on the client.

### 5.5 All six `reason` values

Design §8.2 gives six and the handler covers six. A "no game" response is always `200` with an explicit null `game_id`, never `204` — a `204` forces clients to branch on status codes before parsing.

| `reason` | Returned when |
|---|---|
| `waiting_for_pairing` | no seat, `POLL_HOLD_NS` elapsed with no wake |
| `no_seat` | no seat, and matchmaking will not consider this bot (`role='benchmark'`) |
| `not_your_turn` | seat held, game active, the *opponent* is to move |
| `agent_has_control` | `controller='agent'`; also the payload used to wake a held poll at `take_control()` |
| `paused` | matchmaking globally paused (§10) and the bot has no seat |
| `superseded` | a second poll for this bot arrived (§5.4) |

### 5.6 `last_poll_at` / `last_poll_mono`

Updated **only** by `GET /bots/me/turn`. Pool eligibility depends on them meaning "the bot is actually running", so no other endpoint may refresh them — least of all the dashboard.

---

## 6. Move application — `chess_server/engine/runner.py`

### 6.1 `apply_move_locked` — order of operations

The order is normative (design §6.4). Getting it backwards is silently wrong forever.

1. **CAS-read the game** with `WHERE id=? AND ply=? AND status IN ('pending','active')`. No row → `CASConflict`.
2. **Check `controller`** against the caller, *in this transaction*. Authorisation is not a pre-check; a pre-check can be true and then false by the time the UPDATE runs.
3. **Check `delivered_to_mover = 1`.** If 0, return a `not_delivered` result and let the route answer `409` with `{ply, fen, status}` and the prose *"This position has not been delivered to you. Call GET /bots/me/turn first."* Do **not** deliver implicitly: that would let a bot start its own clock by submitting a move. It is also the only thing preventing `account_move_and_switch` from raising `ValueError` on an undelivered position, which inside a critical section is an exception rather than a 409.
4. **Check the flag, before validation.** `clock = _clock_from_game(game)`; if `has_flagged(clock, now_mono)` then `finalise_game_locked(txn, game, opposite_win(clock.to_move), 'flag')` and return a `flagged` result. Design §6.4: *"A bot that submits an illegal move after its flag has fallen has flagged."* If validation ran first, that bot would collect a strike and stay in the game until the next tick, and three of them would end the game `illegal_forfeit` — the wrong termination on the wire and the wrong story for the attendee.
5. **Validate.** `validate_and_apply_move(game.fen, uci)`. On rejection see §6.2.
6. **Detect termination properly.** `detect_termination(fen_after, history + [fen_after])` — see §6.4. Build the `MoveResult` from *that* answer, not from `validate_and_apply_move`'s, which cannot see threefold.
7. **Apply the ply cap.** `transition_after_move(MatchState(ACTIVE, game.ply, None, None), move_result)`. The `MatchState` is constructed immediately before this call and discarded immediately after; it is never stored, and no other `match.py` function is called (§12). This is the only place `PLY_CAP` is applied, and its terminal-before-cap ordering is load-bearing: a checkmate delivered on the capping ply is a checkmate, not an adjudicated draw.
8. **Account the clock.** `account_move_and_switch(clock, receive_mono=now_mono, now_mono=now_mono)`. Its `flagged` result is necessarily `False` here, because step 4 asked the same question with the identical `now_mono`. Passing the same `now_mono` to both is what makes that guarantee hold; taking a fresh reading between them would reintroduce the race the atomic helper exists to remove.
9. **Persist**, all in this transaction: insert the `moves` row with `server_elapsed_ms = result.elapsed_ms` and the caller's `client_reported_ms`; CAS-UPDATE `games` on `WHERE id=? AND ply=? AND status=?`, setting `fen`, `ply = ply + 1`, `to_move` and the `_clock_to_game_fields(result.new_clock)` values — which is where `delivered_to_mover` returns to 0, `turn_started_mono` returns to NULL and `to_move_since_mono` is refreshed; clear `mailbox[mover_id]` via `txn.defer`; append `fen_after` to the history cache via `txn.defer`; `txn.emit('move_played', …)`.
10. **If the transition is terminal**, `finalise_game_locked(txn, game_after, state.result, state.termination)`.

`server_elapsed_ms` is what the clock is charged — delivery to receipt, network included. `client_reported_ms` is optional self-reported compute time, for diagnostics only. Conflating them misattributes network latency to bot slowness.

### 6.2 An illegal move commits

On rejection: increment the mover's per-game strike column and **return** a `rejected` result object carrying `legal_moves`, `fen`, the new strike count and whether the third strike forfeited. The route turns it into `400`.

`apply_move_locked` must not raise `IllegalMove` through `critical_section`. If it did, the rollback would take the strike increment with it: `white_strikes` would never leave 0, `illegal_forfeit` would never fire, and design §8.3's three-strike rule would silently not exist. §11.6 is the test the raising reading fails, and it is required.

On the third strike, `forfeit_game_locked(txn, game, mover)` finalises with `termination='illegal_forfeit'` and the opponent winning, in the same transaction as the strike.

**A rejected move does not stop the clock and does not reset `turn_started_mono`.** Time spent on illegal attempts is charged cumulatively — the same exploit closure as delivery idempotency.

Strikes are per game (`white_strikes` / `black_strikes` on `games`, zero at creation). Mistakes in game *n* do not follow a bot into game *n+1*.

### 6.3 The side switch

`delivered_to_mover = 0`, `turn_started_mono = NULL`, a fresh `to_move_since_mono`, the new `to_move`, and the mover's mailbox cleared: one UPDATE plus one deferred dictionary deletion, never split across statements.

### 6.4 `history_fens`, and where it comes from

**Contract, pinned in interfaces Part 1 and binding here:** `history_fens` is `[starting_fen] + [fen_after for each ply in order]`, **including the position just reached**. `detect_termination` counts `position_key` matches and requires three, so the current position must itself be in the list.

The obvious implementation — `SELECT fen_after FROM moves WHERE game_id=? ORDER BY ply` — is off by one position: it omits ply 0, which is the position repeated in the commonest repetition there is. After `Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8` the start position occurs three times in the full history and twice in the moves-only list, so `detect_termination` returns `(False, None, None)` and the draw is never claimed. This is also the convention `starter-kit/arena.py` already uses, and offline results predicting live behaviour is load-bearing per `AGENTS.md`.

**The server caches history per game, in process** — `history: dict[int, list[str]]` — rather than reading `moves` on every move. It is seeded with `[STARTING_FEN]` in `create_game_locked`, appended in `apply_move_locked`, and dropped at every terminal transition, by recovery and by `/admin/reset`. The read happens on the hottest path inside the critical section, and an O(ply) query there is a cost paid under the single writer on every move of every game.

**The append happens through `txn.defer`, in the post-commit flush**, for exactly the reason SSE events do: a rolled-back move must not leave the cache one position ahead of the database. Step 6 above therefore reads committed history and appends the candidate `fen_after` itself rather than trusting the cache to contain it already.

Nothing needs to survive a restart, because §8.6 aborts every game anyway.

### 6.5 Finalisation

`finalise_game_locked(txn, game, result, termination)`, all in one transaction:

1. **CAS** from the game's current status to `finished` or `aborted`, setting `ended_at` and clearing `delivered_to_mover` / `turn_started_mono` (§3.10). `rowcount != 1` → `CASConflict`; the caller abandons.
2. **Apply rule 1 of design §5.3, and only rule 1.** `rated` was written at creation from rules 2–6 (§7.2). The terminations `no_show`, `server_restart` and `admin_abort` set `rated = 0`. Nothing else touches `rated`, and it can only move from 1 to 0, never back.
3. **Rate the game** if `rated = 1` — see §6.6.
4. **Update `bots` counters** for both participants: `games_played + 1` and one of `wins` / `losses` / `draws`. Anchors' counters update too. This is deliberate: `pair_bots` sorts by `games_played` ascending, so a busy anchor sinks to the end of the sort, which reinforces design §9.3's "only when a competitor would otherwise sit idle" rather than fighting it. Only the anchor's *rating* is fixed.
5. **Update pool history** (§9.3) for both participants: `last_color`, `last_opponent_id`, and `white_count + 1` for whoever had White. This runs on **every** terminal transition including aborts, because both consume a seat and both mean "that bot's last game was against X as colour Y" — which also stops a bot being instantly re-paired with an opponent that just failed to show up.
6. **Delete both `seats` rows.**
7. **Via `txn.defer`:** clear both mailboxes, drop the history-cache entry, drop both bots' `unpaired_ticks` entries, wake both waiters.
8. **Buffer `game_ended`**, plus one `rating_changed` per `rating_history` row actually inserted.

`abort_game_locked` is the same path with `result = NULL`, `rated = 0`, no rating step and no `rating_changed` events.

### 6.6 The rating derivation, settled

Let the participants be White and Black, with `result ∈ {white_win, black_win, draw}`.

- **Both are competitors** (neither `is_anchor`): a decisive result calls `compute_rating_exchange(winner_rating, loser_rating)`; a draw calls **`compute_draw_exchange(white_rating, black_rating)`** — that is its only call site anywhere in the server. Two `rating_history` rows, two `rating_changed` events, zero-sum.
- **Exactly one participant `is_anchor`** — guaranteed by design §5.3 rule 5, which is the only way a game involving an anchor stays rated. **"The competitor" is the participant with `is_anchor = 0`.** `competitor_score` is `1.0` if `result` names the competitor's colour as the winner, `0.0` if it names the opponent's colour, and `0.5` if `result` is `draw`. Call `compute_one_sided_exchange(competitor_rating, anchor_rating, competitor_score)`. One `rating_history` row and one `rating_changed` event, for the competitor only. The anchor's rating is not written and it gets no history row: only the competitor's rating moved, so only the competitor has a delta to record.
- **Both `is_anchor`**: cannot occur. `pair_bots` refuses it and §7.2 never constructs it. If it somehow does, that is an `InvariantViolation`, not a rating case to handle.

Draws against an anchor **are** rated. `compute_one_sided_exchange` raises `ValueError` on any score other than 1.0/0.5/0.0; do not branch around draws to avoid calling it. A draw against a stronger anchor gains points and against a weaker one loses them, which is what stops shuffling for a result from being free — the behaviour the ply cap exists to bound.

Ratings are written and `rating_history` inserted inside this same transaction, guarded by `UNIQUE (game_id, bot_id)`, which is what makes double-rating a constraint violation rather than a number nobody can reconcile afterwards.

---

## 7. The ticker — `chess_server/engine/ticker.py`

The ticker is **the only creator of games**. It runs every `TICK_INTERVAL_NS`. The tick body is wrapped in `try/except Exception`, logs with the tick number, increments `consecutive_tick_errors`, and continues. The loop never exits.

Steps, in order: consume queued challenges; matchmaking; anchor moves; delivery grace; flag; agent auto-release; challenge TTL; presence.

### 7.1 One transaction, one savepoint per unit of work

The whole tick is one `critical_section`. **Every unit of work inside it — each challenge, each pairing, each anchor move, each grace expiry, each flag, each auto-release — is wrapped in its own `txn.savepoint(...)`.** On `CASConflict` or `sqlite3.IntegrityError`, `ROLLBACK TO` that savepoint, discard the events it buffered, and continue the loop.

Design §4.3 made this argument for pairing — *"Aborting the whole tick would discard every other valid pairing"* — and the argument is identical for every other per-game action. A bot's final move landing in the same instant as the ticker's flag pass is the ordinary race, not an exotic one. Without per-unit savepoints, one such conflict at the flag step un-flags a game that was correctly flagged a millisecond earlier, un-consumes the challenges taken at step 1, and discards every pairing made at step 2 — silently, because a rolled-back CAS is not an error.

A savepoint is also what makes seat collisions survivable. `PRAGMA foreign_keys = ON` forces the `games` insert before its two `seats` rows, and a PK violation aborts only the *statement*, not the transaction, so an unhandled collision commits an orphan game plus one stray seat. `ROLLBACK TO` discards both.

**A game is only reachable through `seats`.** Pool eligibility, delivery and the turn endpoint resolve a bot's current game by joining `seats`, never by scanning `games`, so an orphan game row would be inert even if one were ever committed.

### 7.2 Steps 1 and 2 — challenges, then matchmaking

Challenges are consumed **before** pairing, so an accepted challenge always beats matchmaking to the seat.

For each `queued` challenge, in its own savepoint: both bots must have no `seats` row **and** `controller='client'` (unless the challenge is exhibition, per design §13.3). Otherwise set `status='expired'`, `reason='seat_unavailable'`, and buffer `challenge_updated` — never a silent drop. If both are free, `create_game_locked(..., source='challenge')` and set `status='consumed'` with the `game_id`.

`create_game_locked` takes **nanoseconds** and converts at the boundary:

```python
def create_game_locked(txn, white_id, black_id,
                       time_control_ns: int, increment_ns: int, source: str) -> int:
    """Insert a game and its two seats. The caller supplies the savepoint."""
    now_mono = time.monotonic_ns()
    game_id = game_repo.insert_game(txn, {
        "white_bot_id": white_id, "black_bot_id": black_id,
        "status": "pending", "fen": STARTING_FEN, "ply": 0, "to_move": "white",
        "white_ms": ns_to_ms(time_control_ns), "black_ms": ns_to_ms(time_control_ns),
        "time_control_ms": ns_to_ms(time_control_ns),
        "increment_ms": ns_to_ms(increment_ns),
        "to_move_since_mono": now_mono, "turn_started_mono": None,
        "delivered_to_mover": 0,
        "rated": rated_at_creation(white, black, time_control_ns),
        "source": source, "created_at": utc_now_iso(),
    })
    seat_repo.insert_seat(txn, white_id, game_id)
    seat_repo.insert_seat(txn, black_id, game_id)
    txn.defer(lambda: history.setdefault(game_id, [STARTING_FEN]))
    txn.emit("game_created", {...})
    txn.defer(lambda: wake_waiters(white_id))
    txn.defer(lambda: wake_waiters(black_id))
    return game_id
```

The parameters are `*_ns` and the columns are `*_ms`, and the conversion is visible in the same three lines. The previous revision passed `RATED_TIME_CONTROL_NS` into a parameter named `time_control_ms` and wrote it straight through: 5.7 years on each clock, nothing ever flags, and the column reads correctly — populated, non-null, decreasing.

**`rated` is written here, not at finalisation**, from design §5.3 rules 2–6: `0` if either participant has `role='benchmark'`; `0` if both share an `owner`; `0` if `time_control_ns != RATED_TIME_CONTROL_NS`; otherwise `1`, one-sided if exactly one participant is an anchor. All of those facts are known at creation. Writing `rated=1` "to be recomputed later" is wrong on the wire, not merely untidy: `rated` is carried live by `game_created`, `ActiveGameSummary` and `game_ended`, and design §14 colour-codes from exactly that field — an exhibition game would render green and badged **RATED** on the projector for its whole duration, then flip amber in the results ticker, which is precisely the confusion the colour rule exists to prevent.

Waiters are woken in the post-commit flush, not inside the transaction, for the same reason the SSE events are.

**Matchmaking**, if not globally paused (§10):

1. Build the pool snapshot (§9).
2. Split it: `competitors = [e for e in pool if not e.is_anchor]`, `anchors = [e for e in pool if e.is_anchor]`.
3. `pairings = pair_bots(competitors)`.
4. **Offer anchors to whoever `pair_bots` left idle.** For each unpaired competitor, in `(games_played, rating, bot_id)` order — which is what design §9.3's "fewest-games eligible bot" means — try each remaining anchor in order of `|rating − anchor.rating|` and take the first for which `should_offer_anchor(competitor, anchor, has_other_pairing_option=False)` is true. `has_other_pairing_option` is `False` by construction: this bot was not paired by `pair_bots` this tick, which is precisely "would otherwise sit idle". Remove an anchor from the candidate list once used, so one anchor is not offered twice in a tick.
5. Determine colours for an anchor pairing by calling `pair_bots([competitor, anchor])` on that two-element pool and using the `Pairing` it returns. This keeps design §9.2's colour precedence — alternate from `last_color`, tie-break on `white_count`, then `bot_id` — in one place instead of reimplementing it here.
6. For each pairing, in its own savepoint, `create_game_locked(..., RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, 'matchmaker')`.
7. Reset `unpaired_ticks` to 0 for every bot paired; increment it for every bot that was in the pool snapshot and was not (§9.3).

**`should_offer_anchor` must be called.** `pair_bots` only prevents anchor-versus-anchor; the idle-only, fewest-games and ±`ANCHOR_RATING_WINDOW` rules of design §9.3 live entirely in `should_offer_anchor`, and a pairing loop that calls `pair_bots` alone enforces none of them.

### 7.3 Step 3 — anchors move

**Anchors have no HTTP client**, so nothing else in the system will ever deliver to them or move for them. Without this step the three reference bots are dead code, every anchor game dies `no_show` fifteen seconds after it is created, and a lone attendee — or the last unpaired bot in an odd pool — never gets a game.

For every game with `status IN ('pending','active')` whose side to move is a bot with `is_anchor = 1`, in its own savepoint:

1. `deliver_position_locked(txn, anchor_id, now_mono)` — the same idempotent UPDATE every other delivery uses (§5.2). This starts the clock and moves the game `pending → active`. No mailbox entry is written for an anchor and no waiter exists; the mailbox is the transport for HTTP clients only.
2. Call that bot's `choose_move(board, clock_view)` in process.
3. `apply_move_locked(txn, game, game.ply, move.uci(), client_reported_ms=None, now_mono=time.monotonic_ns())` — **the same locked move path a client's move takes.** Not a shortcut, not a second move implementation. The anchor is charged real time for its thinking exactly as a network client is; the reference bots are fast enough that this is negligible, and the alternative is a second clock code path.

**The in-process call is itself the delivery.** There is no deliver-then-wait for a bot that cannot poll.

If `choose_move` raises, that is a bug in our own code rather than an attendee's: log at ERROR with the game id and the FEN, `ROLLBACK TO` the savepoint, and continue. The rollback also undoes the delivery, so the game sits undelivered and step 4 abandons it within `DELIVERY_GRACE_NS` rather than wedging for the afternoon.

This is the one place trusted in-process code plays a game, and it is why `reference_bots.py` is the single exception to "no untrusted code runs on the server". `RefRandomBot`, `RefGreedyBot` and `RefDepth2Bot` are seeded at startup with `role='anchor'`, `is_anchor=1` and fixed ratings. **Those ratings are provisional placeholders, not measurements**; calibration is deferred (design §21) and nothing here depends on them being right.

### 7.4 Step 4 — delivery grace

Query games where `delivered_to_mover = 0` **`AND status IN ('pending','active')`**.

The status filter is not optional. `delivered_to_mover` is cleared to 0 in the side-switch UPDATE, so every finished game ends with `delivered_to_mover = 0` and a `to_move_since_mono` receding into the past. Without the filter, every game that has ever ended re-enters this sweep on every tick forever: `check_delivery_timeout` returns True, the finalisation CAS returns rowcount 0, and — before per-unit savepoints — that rollback discarded everything else the tick did. Pairing stops permanently while `last_tick_age_ms` stays healthy, so the supervisor's red banner never appears and the only symptom is "matchmaking has stopped".

The grace period depends on the **bot to move**, not on the game: join `bots` on the side to move and use `AGENT_DELIVERY_GRACE_NS` when that bot's `controller='agent'`, otherwise `DELIVERY_GRACE_NS`. `controller` is a `bots` column; `games` has none.

`check_delivery_timeout(_clock_from_game(game), now_mono, grace_ns)` decides. On expiry, in that unit's savepoint:

- **at ply 0** — `abort_game_locked(txn, game, 'no_show')`: `aborted`, `rated=0`, seats freed, neither rating moves, and the bot that *was* present returns to the pool.
- **mid-game** — `finalise_game_locked(txn, game, opposite_win(game.to_move), 'abandoned')`: rated normally, loss for the absent side.

**Precedence against the clock.** A delivered position is governed by the clock, so abandonment applies **only** while `delivered_to_mover = 0`. The two can therefore never race, and a bot delivered a position with 500 ms left flags at ~500 ms rather than waiting 15 s to be called abandoned.

**The server never writes `crash`.** Over HTTP a crashed bot and a closed laptop are the same event: `abandoned` mid-game, `no_show` before the first move. `TerminationReason.CRASH` exists because `chess_core` is shared and the local arena, which catches the exception directly, can say so honestly.

### 7.5 Steps 5–8 — flag, auto-release, challenge TTL, presence

**Flag.** Query `status='active' AND delivered_to_mover=1`. For each, `has_flagged(_clock_from_game(game), now_mono)`. On true, `finalise_game_locked(txn, game, opposite_win(game.to_move), 'flag')` in its own savepoint. `chess_server/` computes no remaining time itself: `has_flagged` is the single declaration of design §6.4's `<= 0` predicate, and it exists precisely so the rule is not hand-written here and again at the move endpoint — the two-places-one-rule shape that got the predicate stated inconsistently once already.

**Agent auto-release.** For bots with `controller='agent'` and `now_mono - last_agent_action_mono > AGENT_AUTO_RELEASE_NS`, set `controller='client'` and wake their waiters (deferred). `AGENT_AUTO_RELEASE_NS` (45 s) is deliberately below `AGENT_DELIVERY_GRACE_NS` (60 s); reversed, the delivery grace always fires first and this branch is unreachable code.

**Challenge TTL.** `status='open' AND now_mono - created_mono > CHALLENGE_TTL_NS` → `expired`, `reason='timeout'`, buffer `challenge_updated`.

**Presence.** In-process `connected: set[int]`. A bot whose `last_poll_mono` is within `DISCONNECT_AFTER_NS` and is not in the set is added and gets one `bot_connected`; a bot in the set whose `last_poll_mono` is older is removed and gets one `bot_disconnected`. Edge-triggered, so each event fires once per transition rather than once per tick. This step performs no database writes and buffers only events.

### 7.6 Supervision — the supervisor acts

A separate coroutine, every 2 s:

- `last_tick_age_ms > 5000` → log a **warning** naming the last tick number.
- `last_tick_age_ms > 15000` → log at **error**, naming the tick it died on, then **cancel the ticker task and start a replacement**.

The supervisor watches `last_tick_age_ms`, not `task.done()`: a task wedged on an await is the more likely failure and `done()` never fires for it.

The restart is a sequence, not a bare `task.cancel()`:

1. `task.cancel()`.
2. `await asyncio.wait({task}, timeout=5.0)`.
3. If the task is now done, increment `ticker_restarts`, log the restart, and create the replacement.
4. If it is **not** done, log CRITICAL and **do not create a replacement.** Two tickers is strictly worse than none: the ticker is the only creator of games, and two of them contend for `write_lock` and double every sweep. A wedged ticker with a red banner is a restart the operator performs; a duplicated ticker is a corruption nobody sees.

This sequence is only safe because §3.7 catches `BaseException` and completes the rollback under cancellation. Design the two together or the remediation makes things worse.

`GET /health` returns `{last_tick_age_ms, last_tick_duration_ms, active_games, pending_games, stalled_games, pooled_bots, held_polls, sse_clients, db_writable, consecutive_tick_errors, ticker_restarts}`. The dashboard shows a red banner above 5000 ms — the operator must see the heartbeat from the back of the room. A detector wired to no remediation is what turned a nested-lock bug into a lost afternoon.

**`db_writable` is a real probe, not a constant.** It enters `critical_section` and exits immediately with no statements, under a 1 s `asyncio.wait_for`. `BEGIN IMMEDIATE` acquires SQLite's RESERVED lock, so this tests exactly the property that fails when a cancelled rollback leaves the writer connection mid-transaction (§3.7) — the one failure that bricks every write for the life of the process while every other health field still looks fine. `False` on timeout or on any exception.

---

## 8. Routes — `chess_server/api/`

Every handler is `async def`. Request and response models bind to interfaces Part 5.

### 8.1 Endpoint inventory

| Endpoint | Method | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| `/bots` | POST | join code, IP-limited | `RegisterBotRequest` | `RegisterBotResponse` (201) | 400 name taken / bad role / bad join code, 409 second competitor, 422 name or owner shape, 429 |
| `/bots/me` | GET | Bearer | — | `MyBotResponse` | 401, 429 |
| `/bots/me/turn` | GET | Bearer | — | `TurnResponse \| NoGameResponse` (200) | 401, 429 |
| `/bots/me/control` | POST | Bearer | `SetControlRequest` | `SetControlResponse` | 400 bad action, 401, 409 seat held, 429 |
| `/games/{id}/moves` | POST | Bearer | `SubmitMoveRequest` | `SubmitMoveResponse` | 400 illegal, 401, 403 controller, 409 CAS or undelivered, 429 |
| `/games/{id}/moves` | GET | none | — | `GameMovesResponse` | 404 |
| `/games/{id}/resign` | POST | Bearer | `ResignRequest` | `ResignResponse` | 401, 403 not in game / controller, 409 already ended, 429 |
| `/challenges` | POST | Bearer | `CreateChallengeRequest` | `CreateChallengeResponse` (201) | 400 opponent, 401, 409 open challenge / seat / agent control, 429 |
| `/challenges/{id}/accept` | POST | Bearer | — | `AcceptChallengeResponse` | 401, 403, 404, 409, 429 |
| `/challenges/{id}/decline` | POST | Bearer | — | `DeclineChallengeResponse` | 401, 403, 404, 409, 429 |
| `/challenges` | GET | Bearer | — | `ChallengesInboxResponse` | 401 |
| `/leaderboard` | GET | none | — | `LeaderboardResponse` | — |
| `/games/{id}` | GET | none | — | `GameDetailResponse` | 404 |
| `/bots/{bot_id}/rating_history` | GET | none | — | `RatingHistoryResponse` | 404 |
| `/state` | GET | none | — | `DashboardStateResponse` | — |
| `/events` | GET | none | — | SSE stream | — |
| `/health` | GET | none | — | `HealthResponse` | — |

Admin, all `Depends(get_admin)`:

| Endpoint | Method | Response | Errors |
|---|---|---|---|
| `/admin/games/{id}/abort` | POST | `AbortGameResponse` | 401, 404, 409 already terminal |
| `/admin/matchmaking/pause` | POST | `PauseMatchmakingResponse` | 401 |
| `/admin/matchmaking/resume` | POST | `ResumeMatchmakingResponse` | 401 |
| `/admin/bots/{name}/token` | POST | `ReissueTokenResponse` | 401, 404, 409 seat held |
| `/admin/reset` | POST | `ResetResponse` | 401, 409 matchmaking not paused |
| `/admin/consistency` | GET | `ConsistencyCheckResponse` | 401 |

`POST /arena-reports` and `GET /bots/{bot_id}/arena-reports` are **not in this build** (§3.5).

The four routes that design §8.1 assigns to you and earlier revisions described only in the MCP and interfaces documents — `GET /bots/me`, `POST /bots/me/control`, `GET /games/{id}/moves`, `GET /bots/{bot_id}/rating_history` — are in this inventory and are yours. `mcp-engineer` consumes them and implements none of them.

**Error prose is aimed at an attendee, not at a status-code table.**

- 400 illegal: `"Illegal move '{move}'. Legal moves: {legal_moves}. Current position: {fen}"`
- 401: `"No bot registered for this token. Call register_bot first."`
- 403 controller: `"Controller is 'agent'. Call release_control() before moving from your client."`
- 409 CAS: `"The position has changed since ply {ply}. Discard this move and poll GET /bots/me/turn again."`
- 409 undelivered: `"This position has not been delivered to you. Call GET /bots/me/turn first."`
- 409 seat: `"Either you or your opponent is already in a game."`
- 409 take control: `"Cannot take control while your bot is in a game. Wait for it to finish, or resign."`
- 429: `"Rate limit exceeded."` with `Retry-After: 3`

### 8.2 Registration

`POST /bots` is the one unauthenticated write. It requires the `JOIN_CODE` env value and is IP-limited (§8.7). Token from `secrets.token_urlsafe(32)`, stored as an indexed `sha256`, returned in plaintext exactly once.

**Validate `name` and `owner` against `^[A-Za-z0-9 _-]{1,32}$`**, rejecting with `422` and actionable prose. These strings reach a projector. This layer is independent of the dashboard's `textContent` rendering and both are required: validation could be relaxed later by someone who does not know the dashboard depends on it, and an escape could be missed in one cell. A bot named `<img src=x onerror=...>` must be boring at both ends.

**One competitor per owner** (design §10.4) — this is what closes the rating-farming vector, and the check and the insert are one transaction so two simultaneous registrations from one owner cannot both succeed:

> `"You already have a competitor bot registered ({existing_name}). Register additional bots with role='benchmark' — they can spar with your competitor without affecting ratings."`

The error text matters more than the rule: the attendee hitting it is trying to do something reasonable, and the message has to point at the benchmark role rather than just refusing.

`role` must be `competitor` or `benchmark`. **`anchor` is not registrable over HTTP.** Anchors are seeded at startup (§7.3). The test for "is this an anchor" is `is_anchor = 1`; `role='anchor'` is what keeps them off the leaderboard and out of the one-per-owner rule.

Buffer `bot_registered`.

### 8.3 Control handoff — design §13.3

Four rules, all of which have failed review before by being assigned and not specified:

1. **`POST /bots/me/control {action: "take"}` is refused with `409` whenever the bot holds a `seats` row.** Seat-held, not "a rated game in progress": `rated` can still be revised at termination by §5.3 rule 1, and "in progress" is undefined for `pending`. Seat-held is unambiguous, is the same predicate matchmaking uses, and covers `pending` and `active` alike.
2. **Taking control wakes any held poll**, which returns `NoGameResponse(reason='agent_has_control')`. There is no window in which the SDK still believes it may move.
3. **Taking control does not alter `turn_started_mono`.** A bot cannot pause its own clock by switching controller.
4. **`last_agent_action_mono` is updated by every agent-facing call** — `get_game`, `get_legal_moves`, `make_move`, `take_control`, `release_control` — because §7.5's auto-release is keyed on it and an agent that is working must not be released mid-thought.

**Game creation checks `controller`.** Both pool eligibility (§9.1) and challenge consumption (§7.2) require `controller='client'` for both bots unless the game is exhibition. Without it, a rated 3+2 game is created *for* an agent-controlled bot immediately after the refusal in rule 1 has passed.

`{action: "release"}` sets `controller='client'` and wakes waiters. All transitions happen under `write_lock` in a single critical section, and the move endpoint's `controller` check is inside the same transaction as its CAS (§6.1 step 2).

### 8.4 `/state`, `/events`, and every SSE emission site

`/state` returns `run_id`, `event_id` (the last emitted `seq`), active-game summaries, the leaderboard and `featured_game_id`. Clients connect to `/events` **first**, then fetch `/state`, then apply buffered events with `id > state.event_id`; the reverse order drops events in the gap.

`ActiveGameSummary` carries `fen`, `to_move` and `status` so the dashboard can render a board from `/state` alone without a per-game fetch, plus `white_rating` / `black_rating` for featured-game selection and `turn_elapsed_ms` computed at emit time.

Per-client bounded queue of 256, drop-oldest; a dropped client refetches `/state`. 15 s heartbeat comments. `seq` is compared numerically and a client seeing a different `run` refetches and discards its buffer. Payloads carry no tokens and no owner identifiers. `turn_started_mono` is never sent — a monotonic counter is meaningless outside the emitting process.

**Every event in interfaces Part 2 has exactly one named emission site**, and every one buffers through `txn.emit` and flushes after commit, except the one that reports process state rather than committed state:

| Event | Emitted at |
|---|---|
| `server_run_started` | `recovery.run_recovery`, after commit; and after `/admin/reset` commits (§10.1) |
| `game_created` | `create_game_locked` (§7.2) |
| `game_started` | `deliver_position_locked`, only when the UPDATE moved `pending → active` (§5.2) |
| `move_played` | `apply_move_locked` step 9 (§6.1), after the `games` CAS succeeds |
| `game_ended` | `finalise_game_locked` and `abort_game_locked` (§6.5) |
| `rating_changed` | `finalise_game_locked`, one per `rating_history` row actually inserted — so an anchor never produces one (§6.6) |
| `bot_registered` | the `POST /bots` transaction (§8.2) |
| `bot_connected` / `bot_disconnected` | ticker step 8, edge-triggered (§7.5) |
| `challenge_updated` | `POST /challenges`, `/challenges/{id}/accept`, `/challenges/{id}/decline`; and ticker steps 1 and 7 |
| `health_tick` | the supervisor coroutine (§7.6) — **not** buffered; it reports process state, belongs to no transaction, and would otherwise never be emitted at all |
| `arena_report_posted` | **no producer in this build** (§3.5) |

Non-featured `move_played` events are coalesced: after emitting one for a non-featured game, suppress further `move_played` for that game for `MOVE_COALESCE_NS`. Featured games bypass the throttle. Ten simultaneous blitz games otherwise flood the stream with moves nobody is watching.

### 8.5 `/admin/consistency`

Assert `bots.rating == STARTING_RATING + sum(rating_history.delta)` **for `role='competitor'` bots only**, and log loudly at startup on mismatch.

Anchors have fixed ratings that are not 1200 and, being rated one-sidedly, accrue no `rating_history` rows, so `800 != 1200 + 0` for every anchor on every run. This is the one alarm in the system that catches double-rating (design §10.2); an alarm that is red on a healthy server is an alarm nobody reads. Benchmark bots satisfy the identity trivially — unrated, so no deltas and no movement from 1200 — and are excluded anyway, to match design §10.2 exactly.

### 8.6 Recovery — design §7.1

Runs in the FastAPI lifespan startup hook, **before the listening socket accepts connections**. Otherwise a fast-reconnecting bot is paired into a game that recovery is about to abort. One critical section:

1. Every `pending` / `active` game → `aborted`, `termination='server_restart'`, `rated=0`.
2. Delete all `seats`.
3. Every non-terminal challenge (`open`, `queued`) → `expired`, `reason='server_restart'`. Their `created_mono` came from a process that no longer exists, and a surviving `queued` challenge would create a real game in the new run from an intent formed in the old one.
4. **Clear every persisted monotonic-derived field:** `UPDATE bots SET last_poll_mono=NULL, last_agent_action_mono=NULL, controller='client'`.
5. Clear the in-process mailbox, waiters, history cache, `unpaired_ticks` and presence set.
6. Regenerate the run id and emit `server_run_started`.

Step 4 is the one that is easy to skip and expensive to skip. `time.monotonic_ns()` has no meaning across a restart — its zero point moves. If the new baseline is *lower* (reboot, container restart, database copied to another machine), `now_mono - last_poll_mono` is negative, therefore below `POLL_RECENCY_NS`, therefore **every bot ever registered looks like it is polling right now**: they are paired, never take delivery, and churn through `no_show` aborts every fifteen seconds forever, with `pooled_bots: 20`, `active_games` sawtoothing and no errors. And a surviving `controller='agent'` excludes the bot from matchmaking while auto-release compares against the same broken baseline, so an attendee who called `take_control()` before the restart never plays again, with nothing logged.

The general rule: `*_mono` values may only be compared with other `*_mono` values taken in the same process, and nothing may persist a monotonic deadline across a restart. Wall-clock timestamps are fine to persist; monotonic ones are not.

Bots re-poll, get "no game", and are re-paired within a tick. About twenty seconds of lost play, zero rating damage, and restarting becomes a safe operator action — which matters, because you will restart during the workshop.

### 8.7 Authentication and rate limiting

`Authorization: Bearer <token>` on every authenticated route. Hash with `sha256`, look up by the indexed `token_hash`, compare with `secrets.compare_digest`, `401` with actionable prose on a miss. A fast hash is correct here precisely because the token is high-entropy and random; a KDF would force an O(n) scan across all bots on every request, since there is no username to look up by.

**The rate limiter is keyed on `token_hash`, never on the raw token.** A plaintext token in a long-lived global appears in every traceback frame that touches the limiter, which is exactly what "never logged" exists to prevent.

**The bucket store is bounded**: an `OrderedDict` capped at 256 entries with LRU eviction, not a `defaultdict`. An unauthenticated caller sending garbage tokens must not be able to grow it without limit. Eviction is safe — an evicted bucket restarts full, which at worst grants one extra burst to a bot that has not been seen for a while.

Per-token bucket: 20 req/s sustained, burst 40, `429` with `Retry-After` and actionable prose.

**`POST /bots` is limited by client IP**, in a separate bounded structure, at `REGISTER_PER_IP_PER_MIN`. Registration carries no token, so the per-token limiter cannot cover it, and an open registration endpoint on a conference network fills the bot table.

Behind a proxy: `proxy_buffering off`, `proxy_read_timeout ≥ 60s`. Both long-polling and SSE fail silently without them.

---

## 9. The matchmaking pool

### 9.1 Eligibility

A bot is in the pool when all hold:

- `role IN ('competitor','anchor')` — anchors are admitted so §7.2 can offer them; every other consumer (leaderboard, rating updates, the one-competitor-per-owner check) filters to `competitor`;
- no `seats` row;
- `controller='client'`;
- matchmaking not globally paused;
- **and, for `role='competitor'` only**, a poll currently held or `last_poll_mono` within `POLL_RECENCY_NS`.

The last clause is scoped to competitors because **an anchor never polls**. Applying poll recency to anchors leaves them permanently ineligible, §7.2's anchor offer never finds a candidate, and the result is the same dead end as having no anchor path at all. An anchor runs in process, so its presence is not in question.

### 9.2 `PoolEntry`

`pair_bots` consumes `PoolEntry(bot_id, owner, rating, games_played, is_anchor, last_color, white_count, last_opponent_id, unpaired_ticks)`. Every field must be supplied truthfully. A builder who cannot find one and passes `0` produces a silent deadlock, not an error.

### 9.3 Where the history fields live

`last_color`, `white_count` and `last_opponent_id` are **columns on `bots`** (§3.1), written by `update_pool_history` in the transaction that finalises a game (§6.5 step 5). Deriving them from `games` per tick is an O(games) scan three times over, and three builders would derive them three ways.

`unpaired_ticks` is **in-process ticker state**: `unpaired_ticks: dict[int, int]`, incremented for every bot that was in this tick's pool snapshot and appears in no pairing, reset to 0 on pairing, and the entry deleted when the bot leaves the pool by taking a seat, so its next spell starts from 0. It is per-run by nature and §8.6 discards it anyway.

Passing a constant `0` here is the failure worth naming: `_allowed` never relaxes, and design §9.2's own motivating case — a lone attendee with two bots, where the same-owner rule blocks the only available game — never pairs. Both bots poll happily, `/health` shows `pooled_bots: 2` and `active_games: 0`, and nothing errors.

---

## 10. Admin

**`/admin/games/{id}/abort`** — CAS from the game's current status to `aborted`, `termination='admin_abort'`, `rated=0`, seats freed, mailboxes cleared, waiters woken, `game_ended` buffered. Racing the ticker is fine: exactly one of the two CAS updates gets `rowcount == 1` and the other abandons silently.

**`/admin/matchmaking/pause` and `/resume`** — one process-wide flag. `paused` means global matchmaking pause and nothing else. There is no per-bot pause; a bot that wants to stop playing stops polling.

**`/admin/bots/{name}/token`** — refused with `409` while the bot holds a seat. Re-issue is admin-only so a lost token does not become a re-registration that distorts the ladder.

Structured logging: one line per game start and end with ids, termination and rating deltas. Tokens are never logged, in any path.

### 10.1 `/admin/reset`

Design §15 gives one line: *"wipe games/ratings/seats/mailboxes, reset bot counters to zero, keep bot identities — for a dry run."* It runs on workshop day, between the dry run and the real thing, with twenty bots connected. Specified in full:

**It requires matchmaking to be paused first.** If `matchmaking_paused` is false, refuse with `409`: *"Pause matchmaking before resetting. POST /admin/matchmaking/pause, then retry."* The reset itself is safe under `write_lock` — the ticker cannot interleave — but the very next tick re-pairs the same twenty bots, which are all still polling, so the operator would be reading a `ResetResponse` describing a clean slate that no longer exists. Making the reset a deliberate two-step gives a stable state to inspect, and pause/resume already exists as the operator's control for exactly this.

**Wiped completely:** `games`, `moves`, `rating_history`, `seats`, `challenges`. In process: the mailbox, the history cache, `unpaired_ticks`, the presence set, the SSE coalescing map.

**`rating_history` is deleted, not archived.** `/admin/consistency` asserts `rating == STARTING_RATING + sum(deltas)`; keeping the history while resetting ratings turns that check red for every competitor immediately, which is the same as switching the alarm off. The two must be wiped together or neither, and "neither" contradicts "reset bot counters to zero".

**`challenges` are deleted rather than expired.** A `queued` challenge is an intent to create a game; surviving a reset means the dry run creates a real game in the graded run.

**Survives:** every `bots` row — `id`, `name`, `owner`, `token_hash`, `role`, `is_anchor`, `created_at`. Attendees' tokens keep working and nobody re-registers, which is the whole point of "keep bot identities".

**Reset on `bots`:** `rating` to `STARTING_RATING` **for `is_anchor = 0` only — anchors keep their seeded fixed rating**, because that is a property of the reference bot rather than a score it earned, and resetting it to 1200 would silently recalibrate the entire ladder; `wins`, `losses`, `draws`, `games_played` to 0; `last_color` to NULL, `white_count` to 0, `last_opponent_id` to NULL; `controller` to `'client'`; `last_poll_at`, `last_poll_mono` and `last_agent_action_mono` to NULL — the same monotonic hygiene as §8.6, and for the same reason.

**In-flight games are not individually ended.** The reset regenerates the run id and emits a single `server_run_started`. That is the mechanism design §14 already provides for "the world you were tracking no longer exists": a client seeing a new `run` discards its buffer and refetches `/state`. Emitting a `game_ended` per game would be worse than silence — it would tell every My Bot panel about terminations whose games `GET /games/{id}` then 404s.

**Held polls are woken and return `NoGameResponse(reason='paused')`**, which is exactly true, since the pause is a precondition.

**Rate-limiter buckets are not cleared.** They are keyed on `token_hash`, tokens survive, and the limiter's state is about request rate rather than game state.

The whole operation is one critical section, so a reset is atomic against a move landing at the same instant. `ResetResponse` returns the row counts wiped and the number of bots reset.

---

## 11. Test obligations

Failure paths are written **before** happy paths.

### 11.1 Concurrency — the one that must exist

A move submission and a ticker flag pass fired at the same instant yield exactly one terminal transition and exactly one `rating_history` row per rated participant, with no orphan seats. This is the test that would have caught the revision-1 defect.

### 11.2 Savepoint isolation inside a tick

A tick in which one game's finalisation CAS conflicts must still commit every other pairing, challenge consumption and finalisation in that tick, and must emit no SSE event and consume no `seq` for the rolled-back unit.

### 11.3 The delivery sweep terminates

Finish a game, then run twenty ticks. Assert the finished game is never returned by the undelivered query, `consecutive_tick_errors` stays 0, and pairing still happens on tick 20.

### 11.4 The mailbox is cleared on the side switch

White polls, moves, and re-polls immediately. Assert the second poll does **not** return the payload for the ply just played — neither from the mailbox nor past the ply guard — and that the SDK's normal loop produces no `409`.

### 11.5 Anchors play

Pair one competitor against `ref-random` with no other bot in the pool. Assert `should_offer_anchor` gated the offer, the game reaches `active` with no HTTP poll from the anchor, the anchor's move goes through `apply_move_locked`, and the game completes. Assert the anchor accrues no `rating_history` row and its rating is unchanged while the competitor's moves — including on a draw.

### 11.6 Illegal-move strikes commit

Three illegal moves in one game produce `illegal_forfeit`. The raising reading of §6.2 fails this test, which is why it exists.

### 11.7 Flag precedes validation

A bot whose clock has expired submits an illegal move. Assert `termination='flag'`, not a strike and not `illegal_forfeit`.

### 11.8 Clocks are in milliseconds

Assert a freshly created rated game has `white_ms == ns_to_ms(RATED_TIME_CONTROL_NS) == 180000`, and that a bot which does not move flags within one tick of 180 s of simulated monotonic time. A game with 5.7 years on the clock passes every other assertion in the suite.

### 11.9 Threefold from the starting position

Play `Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8` over the real endpoints and assert `termination='threefold'`. History built from the `moves` table alone returns `(False, None, None)` here.

### 11.10 Cancellation does not brick the writer

Cancel a task inside a `critical_section` body, then run a further mutation. Assert the second `BEGIN IMMEDIATE` succeeds, `conn.in_transaction` is False after the cancel, and `/health` reports `db_writable: true`.

### 11.11 Restart recovery

Restart mid-game: games aborted `server_restart` and unrated, seats freed, non-terminal challenges expired, `last_poll_mono` and `last_agent_action_mono` NULL and `controller='client'` for every bot, a new run id, and a reconnecting bot re-paired within a tick. Include the case where the new monotonic baseline is *lower* than the old one.

### 11.12 Seat collision

A challenge and a pairing racing the same seat yield exactly one game; the loser is `expired` with `reason='seat_unavailable'` and an SSE event that says so.

### 11.13 Fake-bot harness — integration

In-process scripted bots playing complete games over the real endpoints: happy path, illegal move, flag, mid-game abandonment, no-show at ply 0, superseded poll, CAS conflict, supersede-versus-delivery, control handoff (take, refuse-while-seated, release, auto-release), admin abort, and `/admin/reset` with games in flight.

### 11.14 No tokens anywhere

Grep the codebase and the captured test log output for plaintext tokens: none in logs, error bodies, SSE payloads or limiter keys.

---

## 12. Seams

**Produced.** The HTTP API (interfaces Part 5) and the SSE stream (interfaces Part 2), consumed by the SDK, the MCP server and the dashboard.

**Consumed from `chess_core`** — every name here is called, and nothing on the list is decorative:

- `rules`: `validate_and_apply_move`, `detect_termination`, `get_legal_moves`, `position_key`, `uci_to_san`, `fen_to_ascii`, `STARTING_FEN`, `PLY_CAP`
- `clock`: `create_clock`, `deliver_position`, `remaining_ns`, `has_flagged`, `account_move_and_switch`, `check_delivery_timeout`, `compute_turn_elapsed_ms`, `ms_to_ns`, `ns_to_ms`, and every constant in §2.1
- `elo`: `compute_rating_exchange`, `compute_draw_exchange`, `compute_one_sided_exchange`, `STARTING_RATING`, `K_FACTOR`
- `matchmaker`: `pair_bots`, `should_offer_anchor`, `ANCHOR_RATING_WINDOW`
- `match`: **`transition_after_move` only**

`match.py`'s other exports — `create_match`, `transition_to_active`, `transition_to_terminal`, `is_terminal`, `can_transition` — are deliberately **not** consumed. The game state machine is already expressed as CAS predicates in SQL (§3.10), which is where it must be enforced anyway, and two sources of truth for one state machine is one too many. `transition_after_move` survives because it is the only place `PLY_CAP` is applied and because its terminal-before-cap ordering is load-bearing. Note also that `transition_to_terminal` **raises** on an already-terminal state, which inside a critical section is an exception rather than a `409` — a second reason the CAS predicate, not the pure helper, guards finalisation.

`san_list_to_pgn` belongs to the MCP track's `analyze_game`, which consumes `GET /games/{id}/moves` from you and formats the PGN itself.

---

## 13. Acceptance criteria

1. Schema applied with `seats … WITHOUT ROWID`, `games.to_move`, `challenges.reason`, `challenges.created_mono`, the three `bots` pool-history columns, and no `mailbox` or `arena_reports` table.
2. `critical_section` catches `BaseException` and completes its COMMIT/ROLLBACK under cancellation; §11.10 passes.
3. Every mutating helper has a `*_locked` form; grep confirms no inner form calls an outer form and that the ticker calls only inner forms.
4. Every transition asserts `rowcount == 1`, except the delivery UPDATE, whose zero-row case is a documented legitimate result.
5. Every unit of work in the tick is its own savepoint; §11.2 passes.
6. SSE events buffer inside the transaction and flush after commit; a rolled-back pairing emits nothing and consumes no `seq`.
7. `create_game_locked` takes nanoseconds and writes milliseconds; §11.8 passes.
8. The undelivered query filters `status IN ('pending','active')`; §11.3 passes.
9. The mover's mailbox is cleared on the side switch and the turn endpoint guards on ply; §11.4 passes.
10. Anchors are offered via `should_offer_anchor`, delivered in process, and move through `apply_move_locked`; §11.5 passes.
11. `has_flagged` is checked before validation; §11.7 passes, and no monotonic subtraction appears anywhere in `chess_server/`.
12. Illegal-move strikes commit; §11.6 passes.
13. `history_fens` includes the starting position and the current one; §11.9 passes.
14. Every endpoint in §8.1 is implemented, including the four control-handoff routes, and binds to interfaces Part 5.
15. Every event in interfaces Part 2 has the emission site named in §8.4, or is explicitly out of this build.
16. Recovery clears `last_poll_mono`, `last_agent_action_mono` and `controller`, and expires non-terminal challenges; §11.11 passes.
17. The supervisor cancels and restarts the ticker at 15 s, refuses to start a second one, and `/health` reports `ticker_restarts` and a real `db_writable` probe.
18. `/admin/consistency` is scoped to competitors and is green on a healthy server with anchors playing.
19. `/admin/reset` refuses while matchmaking is running and behaves exactly as §10.1 specifies.
20. Rate limiting is keyed on `token_hash`, is bounded, and covers `POST /bots` by IP.
21. No constant from §2.1 appears as a numeric literal in `chess_server/`.
22. The §11.13 fake-bot harness plays complete games over the real endpoints across every failure path listed.
