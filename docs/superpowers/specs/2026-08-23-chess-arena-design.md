# Chess Arena — Design

**Date:** 2026-08-23
**Revision:** 3 (addresses `agent-reports/2026-08-23-spec-review-round2.md`)
**Status:** Phases 1–2 cleared to build; phase 3 cleared subject to review round 3
**Purpose:** A chess bot competition server for an agentic AI workshop (~20 attendees), doubling as a reference example of an agentic repository.

---

## 1. Goals

1. Attendees write chess bots with Claude's help and watch them climb a live ELO leaderboard.
2. The server is finished infrastructure — attendees consume it, they do not build it.
3. The repository demonstrates Claude best practices: `AGENTS.md`, skills, subagents, spec-driven development, in how it was built and in what it hands attendees.

**Non-goals:** running untrusted attendee code server-side; user accounts beyond a bot token; Swiss/knockout tournaments; mobile UI; multi-process or multi-node deployment.

**Operating envelope.** ~20 bots, ~10 concurrent games, one process, one day. Every decision may assume that. Where a choice would be wrong at 10× scale, it is noted and accepted.

---

## 2. Core decisions

| Decision | Choice | Rationale |
|---|---|---|
| Bot execution | **Client-side** | Removes sandboxing and the untrusted-code threat model entirely. |
| What a bot is | Any program implementing `choose_move` | Server speaks a protocol; engines and agents are equally valid clients. |
| Transport | **Long-polled REST + per-bot mailbox** | `curl`-able, language-agnostic; the mailbox makes a dropped response survivable (§8.4). |
| Time control | **3+2 blitz** rated; exhibition control for agent bots (§11) | |
| Persistence | **SQLite only** | |
| Concurrency | **Single process, single writer, one lock, one transaction per critical section** | §4 |
| MCP transport | Streamable HTTP at `/mcp` | One-line attendee setup. |

---

## 3. Architecture

```
chess_core/          # pure: no I/O, no clock reads, no network. Shared by server AND arena.
  rules.py           # python-chess wrapper: validate, apply, detect termination
  clock.py           # blitz arithmetic — time is passed in, never read
  elo.py             # rating math
  matchmaker.py      # pure pairing policy over an explicit pool snapshot
  match.py           # game state machine (pure transitions)

chess_server/
  store/             # SQLite repositories; one writer connection, one lock
  engine/
    runner.py        # applies moves, transitions games, persists
    ticker.py        # THE single supervised loop: pair, consume challenges, expire, flag
    reference_bots.py# anchors (in-process, trusted)
    mailbox.py       # per-bot delivery mailbox + poll waiters
  api/               # FastAPI routes, SSE, admin router
  mcp/               # MCP server — an HTTP client of api/, no privileged access

web/                 # dashboard, single page, SSE, no build step
starter-kit/         # what attendees clone; bot.py is the only file they edit
```

---

## 4. Concurrency contract

**Normative. Nothing here may be relaxed for convenience.**

### 4.1 Single writer, one lock, one transaction

All mutation of `games`, `moves`, `seats`, `bots`, `rating_history`, `challenges` happens while holding one process-wide `asyncio.Lock` (`store.write_lock`).

**A critical section is a transaction.** Acquiring the lock issues `BEGIN IMMEDIATE`; the section ends with exactly one `COMMIT` or `ROLLBACK` before the lock is released. A failed CAS aborts the transaction rather than leaving partial work.

The critical section is wrapped in `asyncio.shield` and no database call is cancellable. A client disconnecting mid-request must never abandon a half-finalised game.

Reads that inform writes happen inside the lock. Display-only reads may use a separate read connection outside it.

### 4.2 Compare-and-swap on every transition

CAS applies to **every** game-state transition — move, flag, finalisation, abort, reset — not only move submission. The predicate names **the state being transitioned from**:

```sql
UPDATE games SET status='finished', result=?, termination=?
 WHERE id=? AND status='active' AND ply=?
```

`rowcount` MUST be asserted to be 1. If it is 0, another path already transitioned the game: roll back and abandon the work silently.

### 4.3 Seats — one non-terminal game per bot

Revision 2 used two partial unique indexes on `games(white_bot_id)` and `games(black_bot_id)`. **That does not enforce the invariant**: the two indexes are independent, so a bot may be White in one non-terminal game and Black in another simultaneously. Verified against SQLite, not assumed.

Replaced by an explicit table:

```sql
CREATE TABLE seats (
  bot_id  INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES games(id)
);
```

Two rows are inserted in the **same transaction** as the game insert; both are deleted on any terminal transition. The primary key makes a bot in two games a constraint violation at the storage layer, which is where an invariant this important belongs.

**Game creation has exactly one creator: the ticker.** Challenges do not create games; they enqueue an intent that the ticker consumes (§12). This removes the second creation path entirely rather than trying to order two of them. A challenge whose seat is unavailable is rejected with `409` and prose explaining that the bot is already playing.

### 4.4 Storage-level backstops

- `moves`: `PRIMARY KEY (game_id, ply)`
- `rating_history`: `UNIQUE (game_id, bot_id)`
- `seats`: `PRIMARY KEY (bot_id)` (§4.3)
- Index on `games(status)` — scanned every tick
- Index on `bots(token_hash)` — see §16.2

### 4.5 Execution model

```
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```

- **Every route handler is `async def`.** Only `sqlite3` calls enter a thread. A `def` handler would run on the shared thread pool and can deadlock against the writer.
- The **writer** connection lives on a dedicated single-thread executor (`check_same_thread=False`), used only under `write_lock`. One connection, one thread, one writer — `SQLITE_BUSY` cannot occur between our own connections.
- **Reads** use a separate connection pool with its own small limiter, so a burst of dashboard queries cannot starve the writer's thread.
- Revision 2's "never block the loop on a thread join" sentence is deleted; it was ambiguous and unactionable.

### 4.6 The ticker is supervised

Its silent death stops pairing and flagging while the server still looks healthy — the highest-blast-radius failure in the system.

- The tick body is wrapped in `try/except Exception`, logs with the tick number, and continues. The loop never exits.
- The supervisor watches **`last_tick_age_ms > 5000`**, not `task.done()`. A task wedged on an await is the more likely failure and `done()` never fires for it.
- `GET /health` returns `{last_tick_age_ms, last_tick_duration_ms, active_games, pending_games, stalled_games, pooled_bots, held_polls, sse_clients, db_writable, consecutive_tick_errors}`.
- The dashboard shows a red banner when `last_tick_age_ms > 5000`. The operator must see the heartbeat from the back of the room.

---

## 5. Data model

All display timestamps are UTC wall clock. **All elapsed arithmetic uses `time.monotonic_ns()`** so an NTP step or a suspended lid cannot flag the board.

**`bots`**
`id, name UNIQUE, owner, token_hash INDEXED, role, rating, is_anchor, wins, losses, draws, games_played, controller, last_agent_action_mono, last_poll_at, last_poll_mono, created_at`

**`games`**
`id, white_bot_id, black_bot_id, status, result, termination, fen, ply,
 white_ms, black_ms, time_control_ms, increment_ms,
 to_move_since_mono, turn_started_mono, delivered_to_mover,
 rated, source, white_strikes, black_strikes, created_at, started_at, ended_at`

- `status` ∈ `pending | active | finished | aborted`
- `termination` ∈ `checkmate | stalemate | insufficient | fifty_move | threefold | resignation | flag | illegal_forfeit | abandoned | adjudicated | no_show | server_restart | admin_abort`
- `source` ∈ `matchmaker | challenge`

**`seats`** — `bot_id PK, game_id` (§4.3)

**`moves`** — `game_id, ply, uci, san, fen_after, server_elapsed_ms, client_reported_ms`, PK `(game_id, ply)`

`server_elapsed_ms` is what the clock is charged (delivery → receipt, includes network). `client_reported_ms` is optional self-reported compute time, for diagnostics only. Conflating them would misattribute network latency to bot slowness.

**`rating_history`** — `bot_id, game_id, rating_before, rating_after, delta, ts`, UNIQUE `(game_id, bot_id)`

**`challenges`** — `id, challenger_bot_id, opponent_bot_id, status, time_control_ms, increment_ms, created_at, resolved_at, game_id`
`status` ∈ `open | accepted | queued | consumed | declined | expired | cancelled`

**`mailbox`** — `bot_id PK, payload_json, delivered_mono` (§8.4)

### 5.1 What sets `rated`

Evaluated **first match wins**, top to bottom:

| # | Condition | `rated` |
|---|---|---|
| 1 | Game ends `no_show`, `server_restart`, `admin_abort` | 0 |
| 2 | Either participant has `role='benchmark'` | 0 |
| 3 | Both bots share an `owner` | 0 |
| 4 | `time_control_ms != TIME_CONTROL_MS` (exhibition) | 0 |
| 5 | Exactly one participant `is_anchor` | 1, **one-sided** (§10.3) |
| 6 | Otherwise | 1 |

---

## 6. Clock and delivery contract

**Normative.** Revision 2 left `delivered_to_mover` without a lifecycle and had no deadline for an undelivered position mid-game. Both are fixed here.

### 6.1 States

- `to_move_since_mono` — when the position **became available** to the side to move (game creation, or the opponent's move landing).
- `turn_started_mono` — when the position was **delivered**. NULL until delivery.
- `delivered_to_mover` — 0 or 1.

**The clock runs only between delivery and receipt.** A bot is never charged for time before the position was sent to it.

### 6.2 Delivery is idempotent

Delivery writes the turn payload to the bot's mailbox (§8.4) under the lock, and:

```sql
UPDATE games SET turn_started_mono=?, delivered_to_mover=1
 WHERE id=? AND ply=? AND delivered_to_mover=0
```

The `delivered_to_mover=0` predicate is what makes re-delivery free. **Re-reading the position returns the identical payload and never touches the clock.** Without this guard a bot could re-poll while thinking and reset its own clock — the same exploit §8.3 closes for rejected moves.

`delivered_to_mover` is cleared to 0 **in the same UPDATE as the side switch** (§6.4 step 5), along with `turn_started_mono = NULL` and a fresh `to_move_since_mono`.

**Delivery goes over the channel named by `controller`:** the long-poll for `client`, `get_game()` / `get_legal_moves()` for `agent` (§13.3). One rule, two transports.

### 6.3 Undelivered positions have a deadline

`DELIVERY_GRACE_MS = 15000`. Each tick, for any non-terminal game where `delivered_to_mover = 0` and `now − to_move_since_mono > DELIVERY_GRACE_MS`:

- **at ply 0** — the game never started: `aborted`, `no_show`, `rated=0`. Seats freed, the present bot returns to the pool, neither rating moves.
- **mid-game** — the side to move has gone away: `finished`, `termination='abandoned'`, rated normally, loss for the absent side.

This is the only thing standing between a closed laptop lid and two bots being dead for the afternoon. It cannot be gamed for extra thinking time, because not taking delivery loses the game outright.

A bot polling normally takes delivery within milliseconds, so 15s never fires on a healthy client, including across the reconnect gap between two 20s holds.

`AGENT_DELIVERY_GRACE_MS = 60000` applies while `controller='agent'`, since a human is in that loop.

### 6.4 Move accounting order

Stated explicitly, because getting it backwards is silently wrong forever:

```
1. elapsed   = receive_mono − turn_started_mono
2. remaining = remaining − elapsed
3. if remaining < 0        -> flag; game over; NO increment
4. apply move (may end the game by mate or draw)
5. if game continues       -> remaining += increment_ms
                              side switches; delivered_to_mover = 0;
                              turn_started_mono = NULL;
                              to_move_since_mono = now
```

Flag takes precedence over an illegal move: step 3 precedes validation. A bot that submits an illegal move after its flag has fallen has flagged.

### 6.5 The unavoidable window, stated honestly

If the HTTP response carrying a delivery is lost (TCP reset, client `Ctrl-C` immediately after commit), the clock is running for a position the bot never saw. The mailbox bounds this: the client re-polls, drains the same payload, and the clock is not restarted — the cost is one round trip, not the game.

The pathological case is a client that dies permanently at that instant, which §6.3 resolves as `abandoned` — the correct outcome for a bot that is gone. **`AGENTS.md` must not claim "a bot is never charged for time before it has seen the position"**; the transport cannot guarantee that. The honest invariant is "a clock starts on delivery, not on pairing."

---

## 7. Game state machine

```mermaid
stateDiagram-v2
    [*] --> pending: ticker creates (pairing or queued challenge)
    pending --> active: position delivered to side to move
    pending --> aborted: delivery grace at ply 0 (no_show)
    active --> finished: mate/draw/resign/flag/illegal_forfeit/abandoned/adjudicated
    active --> aborted: server restart / admin abort
    finished --> [*]
    aborted --> [*]
```

Every terminal transition deletes both `seats` rows in the same transaction.

### 7.1 Restart recovery

**Order matters and is stated relative to the socket, not the ticker:** recovery runs in the FastAPI lifespan startup hook, **before the listening socket accepts connections**. Otherwise a fast-reconnecting bot can be paired into a game that recovery is about to abort.

Recovery marks every `pending`/`active` game `aborted`, `termination='server_restart'`, `rated=0`, deletes all `seats` rows, clears all mailboxes, and assigns a new **run id** (§14). Bots re-poll, get "no game", and are re-paired within a tick.

~20 seconds of lost play, zero rating damage, and restarting becomes a **safe operator action** — which matters, because you will restart during the workshop.

---

## 8. Play protocol

### 8.1 Endpoints

```
POST /bots                     register -> {bot_id, name, token}
GET  /bots/me/turn             long-poll, holds up to 20s
POST /games/{id}/moves         {ply, move, client_reported_ms?}
POST /games/{id}/resign        {ply}
POST /challenges               {opponent, time_control?}
POST /challenges/{id}/accept   {}
POST /challenges/{id}/decline  {}
GET  /challenges               inbox for the authenticated bot
GET  /leaderboard
GET  /games/{id}
GET  /state                    dashboard snapshot; returns current event id
GET  /events                   SSE stream
GET  /health
```

All authenticated endpoints use `Authorization: Bearer <token>` (§16.2).

### 8.2 The turn response

The highest-traffic response in the system, so its shape is fixed rather than left to twenty independently-guessing clients.

**Game available — `200`:**
```json
{"game_id": 42, "ply": 12, "color": "white", "fen": "...",
 "legal_moves": ["e2e4"], "history_san": ["e4", "e5"],
 "white_ms": 152300, "black_ms": 161100,
 "time_control_ms": 180000, "increment_ms": 2000,
 "controller": "client"}
```

Time control is echoed because §11 allows exhibition games; a client must not assume 3+2.

**No game — `200` with an explicit null**, never `204`:
```json
{"game_id": null, "reason": "waiting_for_pairing"}
```

`reason` ∈ `waiting_for_pairing | not_your_turn | agent_has_control | superseded | paused | no_seat`.

A `204` would force clients to branch on status codes before parsing. Revision 2's `poll_token` field is deleted — it had no defined semantics and the mailbox makes it unnecessary.

### 8.3 Move submission

- `200` — accepted; returns resulting state.
- `409` — CAS failure. Body carries `{ply, fen, status}`. **Defined client behaviour: discard the move and re-poll.** Never retry the same move; that is an accidental hot loop.
- `400` — illegal move, with `legal_moves` and the FEN. Increments the mover's strike counter; **three strikes in one game forfeits** (`illegal_forfeit`).
- **Rejected moves do not stop the clock**, and do not reset `turn_started_mono`.
- `403` — `controller` does not match the caller (§13.3).
- `429` — rate limited (§8.6).

### 8.4 Mailbox and long-poll discipline

Revision 2 coupled delivery to a specific held request, so a supersede or a dropped connection could discard a delivery already committed under the lock. Replaced by a **per-bot mailbox**:

- Delivery writes the payload to `mailbox[bot_id]` under `write_lock` and marks the game delivered (§6.2).
- **Any** poll for that bot drains the mailbox. A superseded, reconnecting or brand-new request all get the same payload.
- The mailbox is cleared when the side switches or the game ends.
- Server holds a poll for **20s**; the SDK client timeout is **30s**. The skew is deliberate so a client never times out on a healthy hold.
- **One waiter per bot.** A second concurrent poll supersedes the first, which returns `{"game_id": null, "reason": "superseded"}`. Supersede only cancels a *waiter*; it can no longer discard a *delivery*.
- Waiters are `asyncio.Event`-based; never a thread per waiter.
- `last_poll_at` / `last_poll_mono` are updated **only** by the turn endpoint — pool eligibility depends on it meaning "the bot is actually running".

### 8.5 Registration

`POST /bots` is the one unauthenticated write. It requires a **join code** (`JOIN_CODE` env, printed on the workshop slide) and is rate-limited by IP. Without this, an open endpoint on a conference network can fill the bot table.

### 8.6 Rate limiting and transport

Per-token token bucket: 20 req/s sustained, burst 40, `429` with `Retry-After` and actionable prose.

Behind a proxy: `proxy_buffering off`, `proxy_read_timeout ≥ 60s`. Cloudflare's ~100s cap is compatible with a 20s hold. Both long-polling and SSE fail silently without this. **TLS: plain HTTP on a workshop LAN is accepted** (tokens are low-value and rotate daily); a hosted deployment must use HTTPS, since bearer tokens would otherwise cross the public internet in clear text.

---

## 9. Matchmaking

### 9.1 Pool eligibility

All must hold: `role='competitor'`; no `seats` row; `controller='client'`; matchmaking not globally paused; and a poll currently held **or** `last_poll_mono` within 5s.

### 9.2 Pairing policy — pure function

Input is an explicit snapshot so the function stays pure and seeded-testable:

```python
PoolEntry = (bot_id, owner, rating, games_played, is_anchor,
             last_color, white_count, last_opponent_id)
```

Algorithm:

1. Sort by `games_played` ascending, then `rating` ascending.
2. Walk the sorted list pairing **adjacent** entries. Skip a candidate pair if same `owner`, or if it repeats `last_opponent_id`; try the next adjacent candidate instead.
3. If a bot cannot be paired for **three consecutive ticks**, its same-owner and rematch constraints are dropped in that order. (Revision 2's "30s escape" referenced wall-clock time inside a pure function and was dead; tick count is passed in.)
4. **Colour precedence, explicit:** alternate from `last_color`. On conflict, the bot with the lower `white_count` takes White; if still tied, the lower `bot_id`.

Sorting by `games_played` first means new bots play within seconds. Sorting by rating second gives near-neighbour pairings without the widening-window machinery cut in revision 2.

### 9.3 Anchors in pairing

An anchor is paired only when a competitor would otherwise sit idle, is offered to the **fewest-games** eligible bot, and only when `|rating − anchor_rating| ≤ 400`. Beyond 400 the game is a foregone conclusion and the rating delta is negligible, so it is wasted board time.

---

## 10. Rating

### 10.1 Flat K

**K = 24 for all bots, always.** Two-tier K breaks Elo's zero-sum property, which both injects points into a closed 20-bot pool and contradicts the property test in §18. "Provisional" survives as a **leaderboard annotation** for bots under 10 games — display only, never arithmetic. Starting rating 1200.

### 10.2 Application

Ratings are computed and applied inside the same transaction that finalises the game, guarded by `UNIQUE (game_id, bot_id)`. `bots.rating` must equal 1200 plus the sum of that bot's deltas; `GET /admin/consistency` asserts this and startup logs loudly on mismatch.

### 10.3 Anchors

`ref-random`, `ref-greedy`, `ref-depth2` have **fixed ratings that never change**. Games against them are rated **one-sidedly**: the competitor moves, the anchor does not.

Stated precisely, since revision 2 overclaimed: one-sided updates against a fixed anchor **are** a net injection of points into the pool for any single game. What bounds it is that the injection shrinks toward zero as the competitor's rating approaches the anchor's — a bot at 1400 beating `ref-greedy` (1000) gains under 2 points — so a competitor's rating converges to a ceiling near the anchor rather than climbing without limit. Combined with §9.3's ±400 gate and anchors only being offered when nobody else is free, the total injection over a workshop day is small and self-limiting. It is not zero, and the leaderboard is anchored rather than pure-zero-sum by design.

Anchor ratings are **calibrated before the workshop** from one seeded arena ladder, and the measured numbers are recorded next to the constants. Guessed anchor ratings would bias every rating in the room.

### 10.4 Attendee benchmark bots

`role='benchmark'` bots are unrated, hidden from the leaderboard, excluded from auto-matchmaking, and challengeable. **Games involving a benchmark bot are unrated for both sides**, no exceptions. This is what makes self-play sparring safe and removes farming structurally.

One `competitor` per owner is enforced at registration; further bots must be `benchmark`.

---

## 11. Time control

Rated play is **3+2** (`TIME_CONTROL_MS=180000`, `INCREMENT_MS=2000`).

**3+2 is not viable for an LLM-agent bot** — at ~12s/move the budget is gone around move 18 (`180 + 2n − 12n < 0`). Revision 1's claim that the increment kept agent bots viable was wrong and is withdrawn.

Mechanism, now that it has somewhere to live:

- `games.time_control_ms` / `increment_ms` are per-game columns; `challenges` carries the same pair.
- `POST /challenges` and the MCP `challenge()` tool accept `time_control ∈ {"rated", "exhibition"}`. Exhibition is 300+10.
- Exhibition games are **unrated** by §5.1 rule 4, so a slow agent game cannot distort the ladder.
- The turn payload echoes both values (§8.2), so a client never assumes 3+2.

A second rated division is deferred — two ladders splits an already-thin 20-bot pool.

The dashboard holds a featured game for at least 20s before switching, so fast bots do not make the big screen unwatchable.

---

## 12. Challenges

Challenges exist for self-play against a previous version and for grudge matches. They do **not** create games (§4.3).

```mermaid
sequenceDiagram
    Challenger->>API: POST /challenges {opponent}
    API-->>Challenger: 201 open   (409 if either seat is taken)
    Opponent->>API: GET /challenges (inbox)
    Opponent->>API: POST /challenges/{id}/accept
    API-->>Opponent: 200 queued
    Ticker->>Ticker: consume queued challenge, create game if both seats free
```

- A challenge expires after 60s `open` and is swept by the ticker.
- Accept marks it `queued`; the ticker consumes it **before** running pairing, so an accepted challenge always beats matchmaking to the seat.
- If a seat is taken when the ticker consumes it, the challenge is marked `expired` and an SSE event explains why. No silent drop.
- A bot may have one `open` outgoing challenge at a time.

---

## 13. MCP surface

### 13.1 Identity

- `.mcp.json` carries `"headers": {"Authorization": "Bearer <token>"}`.
- The MCP server forwards it verbatim. It has **no default token and no privileged path**; with no token every tool returns the same actionable error as the API.
- `register_bot` returns a token **into the conversation transcript**. Documented plainly: the token is not a secret from the attendee's own Claude, it is a secret from other attendees. `run.py --register` is the primary path.
- CORS configured for the dashboard origin; `Mcp-Session-Id` in `Access-Control-Expose-Headers`.

### 13.2 Tools

**Observe:** `get_leaderboard()`, `get_my_bot()`, `get_game(game_id?)`, `analyze_game(game_id)`
**Act:** `register_bot(name, owner, role)`, `challenge(opponent, time_control?)`, `make_move(game_id, ply, move)`, `get_legal_moves(game_id)`, `take_control()`, `release_control()`

`get_game()` defaults to the caller's current game and returns an **ASCII board** plus FEN, SAN history, clocks and turn. Claude reasons better over a board it can see, at a fraction of the tokens of JSON.

`analyze_game` returns **PGN, per-move `server_elapsed_ms`, and explicit flag / strike / forfeit markers**. Eval swing was cut in revision 2: it implied an unacknowledged Stockfish dependency, and timing plus strike markers explain the overwhelming majority of real losses.

Errors are actionable prose. Mutating tools carry `destructiveHint`; read-only tools `readOnlyHint`.

### 13.3 Control handoff

`controller` is per-bot and set under `write_lock`.

- **`take_control()` is refused while a `rated=1` game is in progress** (`409`, with prose). A human-paced agent inside a 3+2 rated game would flag it, corrupting a rated result — and §11 already routes agent play to unrated exhibitions. This removes the auto-release-mid-move hazard entirely.
- Taking control **does not alter `turn_started_mono`**. A bot cannot pause its own clock by switching controller.
- `take_control()` wakes any held poll, which returns `{"game_id": null, "reason": "agent_has_control"}`. There is no window where the SDK still believes it may move.
- While `controller='agent'`, delivery happens on `get_game()` / `get_legal_moves()` under the §6.2 guard, and `AGENT_DELIVERY_GRACE_MS` applies.
- `last_agent_action_mono` is updated by every agent tool call. After 120s of inactivity the ticker sets `controller='client'` and wakes waiters. (Revision 2 specified auto-release with no column to measure it.)
- The move endpoint checks `controller` **inside the same transaction as the CAS**, returning `403` on mismatch. Authorisation is not a pre-check.

---

## 14. Dashboard and SSE

Two modes via a toggle (a deliberate product choice):

- **Big Screen** — one featured game large, leaderboard rail, results ticker. Readable from the back of the room.
- **My Bot** — leaderboard, live game grid, personal panel with rating sparkline and recent results.

Rated games render **green**, unrated/local **amber**. Nobody should mistake a practice win for a ranked one.

**SSE:**

- The process has a **run id**, regenerated on every start. Event ids are `"{run_id}:{seq}"`, so a client cannot mistake a fresh run's `seq=1` for a resumed stream.
- `Last-Event-ID` resume is **not** implemented — with no event backlog it would be decorative. Clients connect to `/events` **first**, then fetch `/state`, then apply buffered events with `id > state.event_id`. Connect-then-snapshot; the reverse order drops events in the gap.
- Per-client bounded queue (256), **drop-oldest**; a dropped client refetches `/state`. A stalled browser tab must never apply backpressure to the game loop.
- Non-featured move events are coalesced to ≤2 Hz. Ten simultaneous games at blitz speed otherwise flood the stream with moves nobody is watching.
- 15s heartbeat comments keep proxies from closing idle streams.
- Payloads carry **no tokens and no owner identifiers** — bot id and name only.
- Clock values plus `turn_started_mono` are included so the browser ticks locally; otherwise clocks appear frozen.

---

## 15. Admin and observability

Behind `ADMIN_TOKEN`, never exposed to attendees:

```
POST /admin/games/{id}/abort       CAS from the game's current status; frees seats,
                                   clears mailboxes, wakes waiters, unrated
POST /admin/matchmaking/pause      global only; and /resume
POST /admin/bots/{name}/token      re-issue; refused while the bot holds a seat
POST /admin/reset                  wipe games/ratings/seats/mailboxes, reset bot
                                   counters to zero, keep bot identities — for a dry run
GET  /admin/consistency            assert ratings == 1200 + sum(rating_history)
```

`paused` means **global matchmaking pause only** — revision 2 used the word for three different things. There is no per-bot pause; a bot that wants to stop playing stops polling.

Structured logging: one line per game start and end with ids, termination and deltas. Tokens are never logged, in any path.

---

## 16. Security

### 16.1 Threat model

Attendees are not adversaries, but their Claude sessions are creative and their code is buggy. The realistic threats are accidental: a runaway loop, moving for someone else's bot, a token pasted into a shared channel.

### 16.2 Tokens

- Generated with `secrets.token_urlsafe(32)`.
- Stored as **`sha256(token)`, indexed** — a fast hash is correct here precisely because the token is high-entropy and random; a KDF would force an O(n) scan across all bots on every request, since there is no username to look up by.
- Compared with `secrets.compare_digest`.
- Never logged, never in SSE payloads, never in error bodies.
- Re-issue is admin-only (§15), so a lost token does not become a re-registration that distorts the ladder.

---

## 17. Local arena

```bash
python arena.py --bots bot.py baseline.py ref_greedy.py --games 100 --seed 7
```

Runs bots offline through `chess_core`, printing a local ELO table plus:

- time per move (mean / p95) and **flag count** — the most common way a first bot loses
- illegal-move attempts with the offending position
- head-to-head win rates, PGN export, `--replay <game>` for ASCII stepping

**Opening randomisation is mandatory.** Two deterministic bots otherwise replay one identical game, making "100 games" a statistical illusion that looks like it is working. Openings are drawn from a small book, seeded for reproducibility.

The arena runs the same clock code as the server, so a bot that flags locally flags live.

---

## 18. Testing

- **`chess_core`** — direct unit tests, no fixtures, no mocks. Elo gets a property test asserting the exchange is **zero-sum and symmetric** (true now that K is flat).
- **Clock** — table-driven over §6.4: flag on exact zero, no increment on flag, rejected move does not reset, flag precedes illegal-move validation.
- **Delivery** — re-delivery does not restart the clock; delivery after side switch starts a fresh turn; mailbox drained by a reconnecting poll.
- **Matchmaker** — seeded snapshots; colour precedence, same-owner and rematch relaxation after three ticks.
- **Concurrency** — a move submission and a ticker flag pass fired at the same instant assert exactly one terminal transition and one `rating_history` row. This is the test that would have caught the revision 1 defect.
- **Seats** — attempting a second game for a seated bot raises; challenge and pairing racing the same seat yields exactly one game.
- **Recovery** — restart mid-game aborts unrated, frees seats, and a reconnecting bot is re-paired.
- **API** — in-process scripted fake-bot harness playing complete games over the real endpoints.
- **Failure paths first:** illegal-move strikes, flag-fall, mid-game disconnect (`abandoned`), CAS conflict, control handoff, no-show, superseded poll, admin abort.

---

## 19. Agentic repository layout

**Build-time agents** (`.claude/agents/`)

| Agent | Expertise | Owns |
|---|---|---|
| `chess-domain-engineer` | python-chess, clock semantics, Elo, adjudication | `chess_core/`, strict TDD |
| `server-engineer` | FastAPI, SQLite, async, long-polling, auth, CAS, concurrency | `store/`, `engine/`, `api/` |
| `mcp-engineer` | MCP spec, FastMCP, tool-description ergonomics | `mcp/` |
| `dashboard-engineer` | HTML/CSS/JS, SSE, board rendering, visual design | `web/` |
| `workshop-author` | Pedagogy, Claude customization formats, writing for novices | `AGENTS.md`, skills, starter-kit docs |
| `spec-reviewer` | Diffs vs spec; security, simplicity, YAGNI | Read-only, everything |

`mcp-engineer` and `server-engineer` are not redundant: one designs for HTTP clients, the other for a language model. `chess-domain-engineer` is isolated because it is the only place where being wrong is *silent*.

**Attendee-facing skills** (`starter-kit/.claude/skills/`)

- `chess-engine-techniques` — **the one that must exist.** Material values, piece-square tables, alpha-beta, move ordering, quiescence, 3+2 time management. Concrete and codeable; "consider king safety" is useless to a non-player.
- `writing-a-chess-bot`, `benchmarking-a-bot`, `diagnosing-bot-losses`

**Attendee-facing agents** — only where isolation pays. `eval-tuner` and `/improve-bot` are **stretch**, built only if the server ships early.

**subagents isolate noisy work; skills inject knowledge into work you are already doing.** Corollary: make your tools return summaries and you need fewer subagents.

---

## 20. Phasing

1. `chess_core` — rules, clock (§6), Elo, matchmaker, match state machine
2. `arena.py` + starter-kit `bot.py` + baseline — **a playable competition with no server at all**
3a. `store/` — schema, seats, CAS helpers, transaction discipline (§4)
3b. `api/` + supervised ticker + anchors + fake-bot harness
4. `chess_client` SDK — **the whole loop closes here**
5. MCP server
6. Dashboard + SSE + `/health` banner
7. Admin surface
8. Claude layer — `AGENTS.md`, skills, agents

Phases 2 and 4 are genuine stopping points. Phase 3 is split at the store/API boundary because §4 is the highest-risk code in the project and deserves to be green before anything is layered on it.

---

## 21. Cut and deferred

**Cut:** Postgres capability; `analyze_game` eval swing; widening rating window; two-tier K; 150-move material adjudication; the separate 60s disconnect rule; `poll_token`; `Last-Event-ID` resume; per-bot pause; partial unique indexes for seat enforcement.

**Deferred:** Swiss tournaments; a second rated division; bot code upload with sandboxing; spectator chat; cross-workshop leaderboards; `eval-tuner`; `/improve-bot`.

**Accepted limits:** flag/abandonment detection resolves at one tick (~1s); a crash loses ~20s of play; one-sided anchor rating injects a small, self-limiting number of points; SQLite plus a global lock would be wrong at 10× scale.

---

## 22. Termination rules

Standard results come from `python-chess`. Threefold and fifty-move are **claimed automatically by the server** when available (`can_claim_draw`), not left to the bots — a bot that does not know to claim would otherwise grind to the ply cap.

**Adjudication is a flat cap:** at **200 ply** the game ends `adjudicated`, result **draw**, unconditionally. Revision 1's material-based rule was bespoke, semantically undefined, and nearly unreachable at 3+2.

No draw offers. A dead-drawn ending reaches the cap or flags; at blitz that is acceptable and avoids an entire negotiation protocol.
