# Client Engineer — Role Specification

**Date:** 2026-08-24
**Parent spec:** [2026-08-23-chess-arena-design.md](../2026-08-23-chess-arena-design.md)
**Interfaces:** [2026-08-23-chess-arena-interfaces.md](../2026-08-23-chess-arena-interfaces.md) Parts 1, 3, 5
**Purpose:** Define everything an attendee runs on their own machine

---

## 1. Scope and boundaries

### You own

```
starter-kit/
  bot.py                    # ONLY file attendees edit
  chess_client/             # SDK: registration, long-poll, submit, retries, handoff
    __init__.py
    client.py
    errors.py
    types.py
  run.py                    # CLI entrypoint
  arena.py                  # Offline local arena
  requirements.txt
  .env.example
  .claude/                  # Attendee-facing agent customizations (code only)
    agents/                 # Workshop-author owns the prose
tests/
  arena/
    test_arena.py
  starter-kit/
    test_client.py
```

### Boundaries

- **You do NOT modify:** `chess_core/`, `chess_server/`, `web/`, `docs/` (except this role spec)
- **`workshop-author` owns:** All prose documentation, README content, setup instructions, skill descriptions, workshop slides. You own the code; they translate it into words for attendees.
- **Seam with `chess-domain-engineer`:** You consume `chess_core` as a library. If the clock or opening randomisation needs to work differently, propose a spec change — do not fork the rules into a simplified local version.
- **Seam with `server-engineer`:** You consume HTTP endpoints per Interfaces Part 5. If the protocol makes a good SDK impossible, say so and propose a spec change — do not work around it with client-side cleverness.

---

## 2. The attendee's mental model

This frames everything else. The whole track is judged by: **what does this look like to someone who has been here forty minutes?**

### First five minutes (goal: playing rated games)

1. Clone `starter-kit/`
2. `python run.py --register` — prints token, saves to `.env`
3. Edit `bot.py` — change one function
4. `python run.py` — bot connects, plays, rating moves

The only file they touch is `bot.py`. The only function they implement is `choose_move(board, clock)`. They never see FEN strings, ply numbers, HTTP status codes, or protocol details unless they go looking.

### What they see when things go wrong

Every error an attendee can trigger must tell them **what to do next**, not dump a traceback or a bare 400.

- `"Your bot took 4.2s to move and flagged. Try reducing search depth in bot.py."`
- `"Illegal move e2e5 at position <FEN>. Legal moves: e2e4, d2d4, ... Check board.legal_moves before returning."`
- `"Server is unreachable at http://localhost:8000. Is it running?"`
- `"No bot registered for token abc123. Run: python run.py --register"`

### What they see when agent control happens

When Claude takes control via `take_control()`, the SDK logs **one clear line** and idles:

```
Agent has control. Waiting for release...
```

Not a stream of "waiting" messages, not an error, not a traceback. One line. Silence is the signal.

---

## 3. What you build

### 3.1 `bot.py` — The attendee's only file

**Signature per Interfaces Part 3:**

```python
import chess
from chess_client.types import ClockView

def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    """Choose a move for your bot.
    
    This is the only function you need to implement. It is called whenever
    it's your turn to move.
    
    The chess.Board object gives you the current position and all legal moves.
    The ClockView gives you time information without needing to know which
    color you are.
    
    Args:
        board: chess.Board with the current position (use board.turn for your
               color, board.legal_moves for available moves)
        clock: ClockView with my_ms (your remaining time), opponent_ms,
               increment_ms, and ply
    
    Returns:
        Your chosen move as a chess.Move object (must be in board.legal_moves)
    
    Raises:
        You may raise any exception; the SDK will catch it, log it, and
        resign the game on your behalf.
    """
    # Shipped baseline implementation goes here
```

**The shipped baseline implementation:**

- Must not flag at 3+2 (180s budget, 2s increment). A naive deep search will.
- Should demonstrate one useful technique (material count + legal move filtering, or shallow alpha-beta, or piece-square tables).
- Must be under 50 lines including comments, readable on a projector.
- Should beat `ref-random` reliably and lose to `ref-greedy` reliably, so attendees see rating movement in both directions immediately.

**Recommendation:** A simple material-counting minimax to depth 2 with a time-per-move budget of `clock.my_ms / 40` (assumes ~40 moves remaining). This is safe at 3+2, demonstrates one technique, and gives newcomers a 60-second improvement path (add piece-square tables).

### 3.2 `chess_client/` — The SDK

**`types.py`:**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ClockView:
    """Clock information for choose_move.
    
    my_ms is always YOUR remaining time, regardless of color.
    This removes color-indexing as a category of bug.
    """
    my_ms: int
    opponent_ms: int
    increment_ms: int
    ply: int
```

**`errors.py`:**

All exceptions are subclasses of `ClientError` and carry actionable prose in their message.

```python
class ClientError(Exception):
    """Base exception with actionable message for attendees."""
    pass

class MoveRejected(ClientError):
    """Move was rejected (illegal, wrong ply, CAS conflict). Re-poll."""
    pass

class NotYourTurn(ClientError):
    """Submitted move but it's not your turn. Re-poll."""
    pass

class GameEnded(ClientError):
    """Game has ended. Stop polling for this game."""
    pass

class TokenInvalid(ClientError):
    """Bot token is invalid. Re-register."""
    pass

class ServerUnreachable(ClientError):
    """Server is down or unreachable. Retry with backoff."""
    pass

class RateLimited(ClientError):
    """Rate limited. Includes Retry-After duration."""
    pass
```

**`client.py`:**

```python
class ChessClient:
    def __init__(self, server_url: str, token: Optional[str] = None):
        """Initialize client.
        
        Args:
            server_url: Base URL like "http://localhost:8000"
            token: Bot token if already registered
        """
        self.server_url = server_url.rstrip('/')
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'chess-client/1.0'})
        if token:
            self.session.headers.update({'Authorization': f'Bearer {token}'})
    
    def register(
        self,
        name: str,
        owner: str,
        join_code: str,
        role: str = "competitor"
    ) -> str:
        """Register a new bot per Interfaces Part 5.
        
        Raises ClientError with actionable message on failure.
        Returns token (caller must save it).
        """
        ...
    
    def run(
        self,
        choose_move_fn: Callable[[chess.Board, ClockView], chess.Move],
        idle_on_control_handoff: bool = True
    ) -> None:
        """Main bot loop per §8 protocol.
        
        - Long-polls GET /bots/me/turn with 30s client timeout (server holds 20s per §8.4)
        - On 200 with game: convert wire format to chess.Board and ClockView
        - Call choose_move_fn, measure elapsed time
        - POST /games/{id}/moves with ply, UCI move, client_reported_ms
        - On 200: continue
        - On 409: discard move, re-poll (NEVER retry same move — that's a hot loop per §8.3)
        - On 400 (illegal): log full error with legal moves and position, re-poll
        - On "agent_has_control": log one line, poll until released
        - On "superseded": silent re-poll
        - On network error: exponential backoff, max 5s
        
        Runs until Ctrl-C or fatal error (invalid token, server permanently down).
        """
        ...
    
    def challenge(self, opponent_name: str, time_control: str = "rated") -> int:
        """POST /challenges per Interfaces Part 5."""
        ...
    
    def resign(self, game_id: int, ply: int) -> None:
        """POST /games/{id}/resign per Interfaces Part 5."""
        ...
```

**Wire-to-attendee conversions:**

- **FEN → `chess.Board`**: `chess.Board(fen)`
- **UCI string → `chess.Move`**: `chess.Move.from_uci(uci)`
- **`chess.Move` → UCI string**: `move.uci()`
- **`TurnResponse` → `ClockView`**: Extract `white_ms`/`black_ms`, map to `my_ms`/`opponent_ms` based on `color` field. Attendee NEVER indexes by color.
- **Error responses → exceptions**: Parse `error` field, raise appropriate `ClientError` subclass with the server's prose prepended by context (e.g., `"Move rejected: " + server_error`).

**409 handling per §8.3:**

```python
if response.status_code == 409:
    # CAS conflict or superseded position
    # DISCARD the move we just tried
    # Re-poll for the current position
    # NEVER retry the same move — that creates a hot loop
    continue
```

**Rate limiting per §8.6:**

```python
if response.status_code == 429:
    retry_after = int(response.headers.get('Retry-After', 1))
    raise RateLimited(f"Rate limited. Retry after {retry_after}s")
```

**Agent control per §13.3:**

```python
if turn_data['controller'] == 'agent':
    if idle_on_control_handoff:
        print("Agent has control. Waiting for release...")
        # Poll every 2s until controller returns to 'client'
        # Do NOT log repeatedly; one line is enough
    else:
        raise ClientError("Agent has control but idle_on_control_handoff=False")
```

### 3.3 `run.py` — CLI entrypoint

```bash
# Registration
python run.py --register --name MyBot --owner alice --server http://localhost:8000

# Normal run (reads token from .env)
python run.py

# Challenge another bot
python run.py --challenge BetaBot --time-control exhibition

# Resign current game
python run.py --resign
```

**Responsibilities:**

- Parse args with `argparse`
- Load token from `.env` or `CHESS_BOT_TOKEN` env var
- On `--register`: call `client.register()`, save token to `.env`, print success message
- On normal run: import `bot.choose_move`, call `client.run(bot.choose_move)`
- Catch `KeyboardInterrupt` and exit cleanly with "Bot stopped by user"
- Catch `ClientError` and print actionable message without traceback (traceback only with `--debug`)

### 3.4 `arena.py` — Local offline arena per §17

```bash
python arena.py --bots bot.py baseline.py ref_greedy.py --games 100 --seed 7
python arena.py --replay 5 --pgn results.pgn
```

**What it does:**

1. Loads bot modules (each must have `choose_move` function)
2. Runs round-robin pairings for `--games` total games
3. Uses `chess_core` for rules, clock, and ELO — **never a simplified reimplementation**
4. **Randomises openings** using a small opening book (8-12 positions), seeded by `--seed`
5. Simulates the same clock discipline as the server (§6.4): delivery timestamp, elapsed deduction, flag-fall, increment
6. Prints results table:
   - Local ELO (starts all bots at 1200)
   - W/L/D, games played
   - **Mean and p95 move time** in milliseconds
   - **Flag count** — most first bots lose by flagging
   - **Illegal move attempts** with count
7. Exports PGN to `--pgn <file>`
8. `--replay <game_number>` steps through a game in ASCII with clock display

**Why opening randomisation is mandatory per §17:**

Two deterministic bots otherwise replay one identical game, and "100 games" becomes a statistical illusion that *looks* like it is working but measures nothing.

**Opening book:**

- 8-12 positions from mainline openings (e4 e5, d4 d5, Sicilian, etc.)
- Stored as FEN strings in `arena.py`
- Selected by `random.Random(seed).choice()`
- Applied at game start before first move

**Clock simulation:**

```python
from chess_core.clock import (
    create_clock, deliver_position, account_move_and_switch, 
    ms_to_ns, ns_to_ms, RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS
)

# At game start
clock = create_clock(RATED_TIME_CONTROL_NS, RATED_INCREMENT_NS, Color.WHITE, now_mono)

# Before choose_move call
clock = deliver_position(clock, now_mono, ply)
start_ns = time.monotonic_ns()

# After choose_move returns
end_ns = time.monotonic_ns()
result = account_move_and_switch(clock, end_ns, end_ns)

if result.flagged:
    # Game ends, flagged_color loses
    ...
```

**Output format:**

```
Local Arena Results (100 games, seed=7)
========================================

Bot               Rating   W   L   D  Games  Avg(ms)  P95(ms)  Flags  Illegal
----------------  ------  --  --  --  -----  -------  -------  -----  -------
MyBot               1287  42  35  23    100      156      420      3        0
baseline            1213  38  40  22    100       89      180      0        1
ref_greedy          1200  20  25   5     50       12       18      0        0

Head-to-Head:
  MyBot vs baseline: 18-15-17
  MyBot vs ref_greedy: 14-6-5
  baseline vs ref_greedy: 11-9-5

PGNs exported to results.pgn
```

**`--replay` display:**

```
Game 5: MyBot (White) vs baseline (Black)
Result: White wins by checkmate
Time control: 3+2

Move 1. e4 (White: 179.8s, Black: 180.0s)
r n b q k b n r
p p p p p p p p
. . . . . . . .
. . . . . . . .
. . . . P . . .
. . . . . . . .
P P P P . P P P
R N B Q K B N R

[Press Enter for next move, 'q' to quit]
```

**`--report` flag — opt-in local arena reporting per design spec §14:**

```bash
python arena.py --bots bot.py baseline.py --games 100 --seed 7 --report
```

When `--report` is present:

1. After completing the arena run, build a report payload matching `SubmitArenaReportRequest` from Interfaces Part 5:
   ```python
   {
       "candidate_name": candidate_bot_name,  # first bot in --bots
       "opponent_name": opponent_bot_name,     # second bot in --bots, or "mixed" if >2 bots
       "games": total_games,
       "wins": candidate_wins,
       "draws": candidate_draws,
       "losses": candidate_losses,
       "mean_move_ms": int(mean_move_time_ms),
       "p95_move_ms": int(p95_move_time_ms),
       "flags": candidate_flags,
       "illegal_attempts": candidate_illegal_attempts,
       "seed": seed,
       "time_control_ms": time_control_ms,
       "increment_ms": increment_ms
   }
   ```

2. Read token from `.env` or `CHESS_BOT_TOKEN` env var

3. POST to `/arena-reports` with bearer token authentication

4. **Offline-first failure behaviour:**
   - On success (201): Log `"Arena report posted (report_id={id})"`
   - On network error or server unavailable: Log warning `"Could not post arena report (server unavailable). Results saved locally."` and **continue normally**
   - On 401 (invalid token): Log warning `"Could not post arena report (no valid bot token). Run 'python run.py --register' first."` and **continue normally**
   - On 429 (rate limited): Log warning `"Could not post arena report (rate limited). Try again later."` and **continue normally**
   
   **NEVER fail the arena run** because of a failed POST. The arena must remain fully functional offline.

5. The token is read from the same `.env` file that `run.py` uses, so `--report` requires that a bot has been registered, but does not require the server to be running during the arena execution itself — only during the final POST.

**Token source:** Same as `run.py` — `.env` file with `CHESS_BOT_TOKEN=<token>` or environment variable. If no token is found, log the `401` warning and continue.

**`--serve` flag (Stretch Goal, added Harmonization Revision 4):**

```bash
python arena.py --bots bot.py baseline.py --games 100 --serve
```

**Purpose:** Resolves the "local stats gap" identified in harmonization—arena runs offline so the main dashboard never sees local games. With `--serve`, the arena launches a simple HTTP server at `localhost:8001` showing a minimal read-only web view of the just-completed arena run's results: ELO table, W/L/D records, and optionally a game viewer.

**Implementation notes:**
- Uses Python's `http.server.HTTPServer` with a simple HTML template, no JavaScript build step
- Serves static results snapshot—arena must finish all games before starting the server
- Separate from main dashboard at `localhost:8000` (which shows only live server games)
- Not required for core workshop functionality—attendees can read terminal output instead
- Does NOT POST results to the server (that would create an unverifiable attack vector against the rated leaderboard)

**Why this matters:** Attendees running offline arena games want to see formatted results without cluttering the main projector dashboard with unverifiable local data.

---

## 4. Normative behaviour

These are invariants you uphold. Violating any of them fails the track.

1. **`choose_move(board: chess.Board, clock: ClockView) -> chess.Move`** — Exactly two arguments. Never `choose_move(board, white_ms, black_ms, color)` or any color-indexed signature, because color-indexing is a category of bug attendees should not be able to write.

2. **`clock.my_ms` — never `white_ms`/`black_ms` in attendee-facing code.** The SDK does the color mapping internally. If attendees see color-indexed time anywhere, they will index it wrong.

3. **The SDK hides the wire completely.** Attendees work with `chess.Board`, `chess.Move`, `ClockView`. They never see FEN strings, UCI strings, ply numbers, HTTP status codes, JSON payloads, or the word "endpoint" unless they open `client.py` to read it.

4. **409 means discard and re-poll per §8.3.** Never retry the same move; that is an accidental hot loop. The correct response to a CAS conflict is to throw away the move you just tried and ask for the current position.

5. **Agent control means idle, not error per §13.3.** On `controller: "agent"`, log one clear line (`"Agent has control. Waiting for release..."`) and poll silently every 2s. Do not spew repeated "waiting" messages. Silence is the signal that the SDK is behaving correctly.

6. **The arena randomises openings, seeded per §17.** Two deterministic bots otherwise replay one identical game, making "100 games" a statistical illusion. Opening randomisation is mandatory, not optional.

7. **The arena uses `chess_core`, never a simplified local reimplementation.** Local results must predict live behaviour. If a bot flags locally, it flags live. If the arena uses different clock logic, attendees will tune against the wrong target.

8. **Errors are actionable prose per §2.** Every error an attendee can trigger must say what to do next. No bare 400s, no tracebacks without context, no `"Request failed"`.

9. **The shipped baseline in `bot.py` must not flag at 3+2.** A naive deep search will burn the clock and flag before move 20. The baseline must demonstrate safe time management (e.g., `clock.my_ms / 40` per move).

10. **Rejected moves do not stop the clock per §8.3.** An illegal move or a 409 does not reset `turn_started_mono` on the server, so the SDK must not behave as if it does. The clock keeps running until a legal move lands.

---

## 5. Seams you consume

### From `chess_core` (Interfaces Part 1)

Functions:
- `validate_and_apply_move(fen, move_uci) -> MoveOutcome`
- `detect_termination(fen, history_fens) -> (is_terminal, reason, result)`
- `get_legal_moves(fen) -> List[str]`
- `fen_to_ascii(fen) -> str` (for arena `--replay`)
- `uci_to_san(fen, move_uci) -> str`
- `san_list_to_pgn(...) -> str`
- `position_key(fen) -> str`

Clock functions:
- `create_clock(time_control_ns, increment_ns, to_move, now_mono) -> ClockState`
- `deliver_position(clock, now_mono, ply) -> ClockState`
- `account_move_and_switch(clock, receive_mono, now_mono) -> ClockUpdateResult`
- `ms_to_ns(ms: int) -> int`
- `ns_to_ms(ns: int) -> int`

Constants:
- `STARTING_FEN`
- `PLY_CAP`
- `RATED_TIME_CONTROL_NS`, `RATED_INCREMENT_NS`
- `EXHIBITION_TIME_CONTROL_NS`, `EXHIBITION_INCREMENT_NS`

Types:
- `Color`, `GameStatus`, `TerminationReason`, `GameResult`
- `MoveResult`, `MoveOutcome`, `ClockState`, `ClockUpdateResult`

ELO functions (arena only):
- `compute_rating_exchange(winner_rating, loser_rating) -> (RatingUpdate, RatingUpdate)`
- `compute_draw_exchange(white_rating, black_rating) -> (RatingUpdate, RatingUpdate)`
- `STARTING_RATING`, `K_FACTOR`

### From `chess_server` HTTP API (Interfaces Part 5)

Endpoints the SDK calls:
- `POST /bots` — registration (unauthenticated, needs join code)
- `GET /bots/me/turn` — long-poll, returns `TurnResponse | NoGameResponse`
- `POST /games/{id}/moves` — submit move with `{ply, move, client_reported_ms?}`
- `POST /games/{id}/resign` — resign with `{ply}`
- `POST /challenges` — create challenge
- `POST /challenges/{id}/accept`, `/decline` — respond to challenge
- `GET /challenges` — inbox

Error shapes per Interfaces Part 5:
- `400` — ErrorResponse with `error` field and optional `details` dict
- `401` — token invalid or missing
- `403` — controller mismatch or not your turn
- `409` — CAS conflict, includes `ply`, `fen`, `status` in details
- `429` — rate limited, includes `Retry-After` header

Transport details per §8.4, §8.6:
- Server holds poll for 20s, SDK client timeout is 30s
- One waiter per bot; second concurrent poll supersedes first
- Rate limiting: 20 req/s sustained, burst 40

---

## 6. Seams you produce

### The `choose_move` contract

**Attendees depend on this:**

```python
def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
    ...
```

**`workshop-author` depends on this:** When they write `chess-engine-techniques.md` (the skill describing material values, piece-square tables, alpha-beta, time management), they assume this signature. If you add a third parameter, their skill breaks.

**`arena.py` depends on this:** The arena imports `bot.choose_move` and calls it with exactly these two arguments.

**Contract guarantees:**

- `board` is a valid `chess.Board` object with the current position
- `board.turn` is the color to move
- `board.legal_moves` contains all legal moves in the position
- `clock.my_ms` is the bot's remaining time, regardless of color
- `clock.opponent_ms` is the opponent's remaining time
- `clock.increment_ms` is the increment per move
- `clock.ply` is the current ply number (0-indexed)

**Contract requirements:**

- Return must be a `chess.Move` object
- Return must be in `board.legal_moves`
- If return is illegal, SDK logs full error and resigns the game on bot's behalf
- If function raises exception, SDK logs it and resigns the game on bot's behalf

### Arena result types

**`ArenaStats` (per bot):**

```python
@dataclass
class ArenaStats:
    bot_name: str
    rating: int
    wins: int
    losses: int
    draws: int
    games_played: int
    mean_move_time_ms: float
    p95_move_time_ms: float
    flags: int              # critical diagnostic
    illegal_attempts: int   # critical diagnostic
```

**`HeadToHead` (pairwise):**

```python
@dataclass
class HeadToHead:
    bot1_name: str
    bot2_name: str
    bot1_wins: int
    bot2_wins: int
    draws: int
```

**`ArenaResult` (full run):**

```python
@dataclass
class ArenaResult:
    stats: List[ArenaStats]
    head_to_head: List[HeadToHead]
    total_games: int
    seed: int
    pgns: List[str]
```

These types are what `diagnosing-bot-losses.md` skill (workshop-author) assumes exist when it tells attendees to run `python arena.py --bots bot.py baseline.py --games 50` and look at flag counts.

---

## 7. Error handling

Enumerate every failure an attendee can hit and give exact message text. Each must say what to do next.

### Server unreachable

**Trigger:** `requests.exceptions.ConnectionError` on any request

**Message:**
```
Server is unreachable at http://localhost:8000.
Check that the server is running and the URL is correct.
```

**SDK behaviour:** Exponential backoff (1s, 2s, 4s, max 5s), retry indefinitely until Ctrl-C.

### Invalid token

**Trigger:** `401` response on authenticated endpoint

**Message:**
```
No bot registered for token abc123.
Run: python run.py --register --name YourBotName --owner yourname
```

**SDK behaviour:** Raise `TokenInvalid`, exit.

### Bot name already taken

**Trigger:** `400` on `POST /bots` with `"Name already taken"` in error field

**Message:**
```
Bot name "MyBot" is already taken.
Choose a different name: python run.py --register --name MyBot2 --owner yourname
```

**SDK behaviour:** Raise `ClientError`, exit.

### Invalid join code

**Trigger:** `400` on `POST /bots` with `"Invalid join code"` in error field

**Message:**
```
Join code "wrong123" is invalid.
Get the correct join code from the workshop slide.
```

**SDK behaviour:** Raise `ClientError`, exit.

### Illegal move

**Trigger:** `400` on `POST /games/{id}/moves` with `"Illegal move"` in error field, `details` contains `legal_moves` and `fen`

**Message:**
```
Illegal move: e2e5
Position: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1
Legal moves: e2e4, e2e3, d2d4, d2d3, g1f3, g1h3, b1c3, b1a3, ...

Your bot returned a move not in board.legal_moves.
Check your move generation logic in bot.py.

Strike 1 of 3. Three illegal moves in one game forfeits.
```

**SDK behaviour:** Log full error, continue polling. Server increments strike counter.

### Three strikes (illegal forfeit)

**Trigger:** `400` on third illegal move, game ends with `termination='illegal_forfeit'`

**Message:**
```
Game forfeited: three illegal moves.
Your bot submitted illegal moves on plies 5, 12, 18.

Review your move generation in bot.py. Ensure all returned moves
are in board.legal_moves before returning.
```

**SDK behaviour:** Log error, game over, continue polling for next game.

### Flagged

**Trigger:** Game ends with `termination='flag'`, SDK sees this in next turn poll or `SubmitMoveResponse`

**Message:**
```
Your bot flagged (ran out of time).
Move time: 4.2s | Remaining: 1.8s | Increment: 2.0s

At 3+2 time control, you have ~180s total and gain 2s per move.
Try reducing search depth or using a time-per-move budget in bot.py.
Example: time_per_move = clock.my_ms / 40
```

**SDK behaviour:** Log error, game over, continue polling for next game.

### Agent has control

**Trigger:** `TurnResponse` with `controller: "agent"` or `NoGameResponse` with `reason: "agent_has_control"`

**Message:**
```
Agent has control. Waiting for release...
```

**SDK behaviour:** Log **once**, then poll silently every 2s until `controller` returns to `"client"`. Do not log repeatedly.

### CAS conflict (409)

**Trigger:** `409` on `POST /games/{id}/moves`, details include `ply`, `fen`, `status`

**Message:**
```
Position changed while your bot was thinking (CAS conflict).
Expected ply 12, server is at ply 13.
Re-polling for current position...
```

**SDK behaviour:** Discard the move just attempted (do NOT retry it), continue to next poll iteration.

### Not your turn

**Trigger:** `NoGameResponse` with `reason: "not_your_turn"`

**SDK behaviour:** Silent continue, keep polling.

### Superseded poll

**Trigger:** `NoGameResponse` with `reason: "superseded"`

**SDK behaviour:** Silent continue, immediate re-poll (another SDK instance took over).

### Rate limited

**Trigger:** `429` with `Retry-After` header

**Message:**
```
Rate limited. Waiting 3s before retry.
Your bot is making requests too quickly. This should not happen
in normal operation. Check for accidental tight loops in bot.py.
```

**SDK behaviour:** Sleep for `Retry-After` seconds, retry.

### Server down mid-game

**Trigger:** Connection error during polling or move submission

**Message:**
```
Lost connection to server during game.
Retrying in 2s... (attempt 3)
```

**SDK behaviour:** Exponential backoff, retry indefinitely. If server comes back and game is aborted due to restart (§7.1), attendee sees `reason: "waiting_for_pairing"` on next successful poll.

### Opponent disconnected (game abandoned)

**Trigger:** Game ends with `termination='abandoned'`

**Message:**
```
Opponent disconnected. You win by abandonment.
```

**SDK behaviour:** Log, game over, continue polling for next game.

### No opponent available

**Trigger:** `NoGameResponse` with `reason: "waiting_for_pairing"`

**SDK behaviour:** Silent continue, keep polling. Log once on first poll: `"Connected. Waiting for opponent..."`

### Challenge declined

**Trigger:** SSE event (not SDK's problem) or GET `/challenges` shows `status: "declined"`

**Message:** (Only if attendee manually polls challenges)
```
Challenge to BetaBot was declined.
```

### Challenge expired (seat unavailable)

**Trigger:** `status: "expired"` in challenge response, `reason` may be `"seat_unavailable"`

**Message:**
```
Challenge to BetaBot expired: opponent is already in a game.
Wait for their game to finish or challenge someone else.
```

### Opponent not found

**Trigger:** `400` on `POST /challenges` with `"Opponent bot not found"`

**Message:**
```
Bot "XYZ" not found.
Check the leaderboard for available bot names.
```

### Already in a game (challenge rejected)

**Trigger:** `409` on `POST /challenges` with `"already in a game"`

**Message:**
```
Cannot create challenge: you are already playing a game.
Wait for your current game to finish.
```

---

## 8. Test obligations

### Pure SDK tests (`tests/starter-kit/test_client.py`)

- **Wire to attendee type conversions:**
  - `TurnResponse` → `chess.Board` (FEN parsing)
  - `TurnResponse` → `ClockView` (color mapping to `my_ms`/`opponent_ms`)
  - `chess.Move` → UCI string
  - `ErrorResponse` → `ClientError` subclass with prose
  
- **409 handling:** Mock a 409 response, assert SDK discards move and re-polls, never retries same move

- **Agent control:** Mock `controller: "agent"`, assert SDK logs once and idles, does not log repeatedly

- **Rate limiting:** Mock 429 with `Retry-After`, assert SDK waits correct duration

- **Error message construction:** For each error trigger in §7, assert exact message text matches spec

### Arena tests (`tests/arena/test_arena.py`)

- **Opening randomisation:** Given seed S, assert first 10 games start from different positions; given same seed S twice, assert identical opening sequence

- **Clock simulation matches server:** Create a game with known time control, simulate 5 moves with known elapsed times, assert clock state matches what `chess_core.clock.account_move_and_switch` produces

- **Flag detection:** Simulate a move that exhausts remaining time, assert game ends with `termination='flag'` and correct loser

- **Illegal move handling:** Bot that returns `chess.Move.from_uci("e2e5")` at starting position, assert arena logs illegal attempt and increments counter, does not crash

- **ELO convergence:** Two bots with known win rate (e.g., ref-greedy vs ref-random over 50 games), assert final ratings differ by expected delta ±20

- **PGN export:** Run 3-game arena, assert PGN file contains 3 complete games with correct headers (White, Black, Result)

- **`--replay` output:** Mock game data, assert ASCII board + clock display renders correctly

### Integration test (optional, in `tests/arena/`)

Run `arena.py` against the shipped baseline bot and `ref-random`, assert:
- Baseline wins ≥70% of games
- Baseline flags ≤5% of games at 3+2
- Mean move time <200ms

This is the acceptance bar: if the shipped baseline fails this test, it is not safe to hand to attendees.

---

## 9. Acceptance criteria

**Definition of done per `.claude/agents/client-engineer.md`:**

> A newcomer clones the starter kit, edits one function, and is playing rated games in under five minutes. `arena.py` reports time-per-move, p95 and flag counts, because flagging is how most first bots lose and the tool should say so plainly. Every error an attendee can trigger has been read aloud and passes the test: *does this tell them what to do next?*

**Specific gates:**

1. **Five-minute-to-first-game:** From `git clone` to first rated game on server, a newcomer with Python experience takes ≤5 minutes (measure this with a real newcomer, not yourself).

2. **Baseline bot is safe:** Shipped `bot.py` baseline plays 20 consecutive games at 3+2 without flagging.

3. **All errors are actionable:** Every error message in §7 printed to stdout and read aloud to a non-chess-player. They must understand what to do next without asking a follow-up question.

4. **Arena diagnostics work:** `arena.py` run with a bot that flags shows flag count >0 and p95 move time near the time control limit. A bot with illegal moves shows illegal attempt count >0.

5. **Agent control is silent:** Mock `controller: "agent"`, run SDK, assert log output is **one line total**. No stream of "waiting..." messages.

6. **Opening randomisation is real:** `arena.py --games 10 --seed 1` and `--seed 2` produce different first-move distributions (measure with chi-squared test or visual inspection).

7. **Local matches server:** A bot that flags in `arena.py` at 3+2 also flags on the live server. If arena uses different clock logic, this fails.

8. **workshop-author can write against your signatures:** The `choose_move(board, clock)` signature and `ArenaStats` dataclass are stable enough that documentation written against them does not need to be revised when implementation details change.

---

## 10. Implementation Decisions (Non-Blocking)

**Note (Harmonization Revision 4):** The following decisions affect internal implementation details but do not change interfaces or seams. All have recommendations; implementer may choose differently if reasoning is sound.

### 10.1 Opening book composition

**Question:** Which 8-12 opening positions should the arena use for randomisation?

**Options:**

1. **Mainline only:** `e4 e5`, `d4 d5`, `e4 c5` (Sicilian), `d4 Nf6` (Indian systems), `c4` (English). Safe, balanced, no traps.
2. **Include gambits:** Add King's Gambit, Scandinavian, Alekhine. More variety, but some positions are objectively better for one side.
3. **Random legal positions:** Generate random positions with 4-6 pieces developed. Maximum variety, but quality unknown.

**Recommendation:** **Option 1 (mainline only).** Two deterministic bots replaying the Sicilian 10 times is still 10 data points, not 1. Option 2 introduces eval bias. Option 3 risks unbalanced positions.

**Affects:** `arena.py` opening book constant

### 10.2 Shipped baseline bot depth

**Question:** The shipped baseline in `bot.py` uses minimax to what depth?

**Options:**

1. **Depth 1 (immediate captures):** Safest time-wise, very weak.
2. **Depth 2 with material-only eval:** Safe at 3+2 (~100ms/move), beats ref-random, loses to ref-greedy.
3. **Depth 3 with alpha-beta and piece-square tables:** Stronger, but risks flagging if implemented naively.

**Recommendation:** **Option 2.** Depth 2 is the sweet spot: attendees see rating movement (they beat random, lose to greedy), time management is obvious (budget = `clock.my_ms / 40`), and the implementation fits in 50 lines. Depth 3 requires alpha-beta to be safe, which is too much code for a baseline.

**Affects:** `starter-kit/bot.py`

### 10.3 Re-poll interval when no game available

**Question:** When `NoGameResponse` with `reason: "waiting_for_pairing"`, how long should SDK wait before next poll?

**Options:**

1. **Immediate re-poll (long-poll only):** Server holds for 20s, so client immediately re-polls and waits again. Zero client-side delay.
2. **1s delay between polls:** Reduces request rate, but adds latency to pairing.
3. **Exponential backoff:** Start at 1s, double up to 5s.

**Recommendation:** **Option 1 (immediate re-poll).** The long-poll hold (§8.4) already provides backpressure. Adding client-side delay means attendees wait longer to be paired. The server's 20-req/s rate limit is plenty of headroom.

**Affects:** `chess_client/client.py` run loop

### 10.4 Handling choose_move exceptions

**Question:** When `choose_move()` raises an exception, should the SDK resign immediately or retry?

**Options:**

1. **Resign immediately:** Log exception, `POST /games/{id}/resign`, continue polling for next game.
2. **Retry once:** Call `choose_move()` again, resign on second failure.
3. **Continue without moving:** Poll again, hope the position changed (wrong per §8.3).

**Recommendation:** **Option 1 (resign immediately).** An exception in `choose_move` is a bug, not a transient error. Retrying the same position will hit the same bug. Resigning gives the attendee a clear signal ("your bot crashed on this position") and keeps the server moving. Log the full traceback so they can debug.

**Affects:** `chess_client/client.py` run loop

### 10.5 client_reported_ms measurement

**Question:** When measuring elapsed time to include in `client_reported_ms`, should we measure wall time or exclude GC pauses?

**Options:**

1. **Wall time (`time.monotonic()` before and after `choose_move()` call):** Simple, matches what the server charges.
2. **Process time (`time.process_time()`):** Excludes sleeps and I/O, closer to "CPU time spent thinking."

**Recommendation:** **Option 1 (wall time).** The server charges wall time (§6.4), so the diagnostic should match. If a bot sleeps during `choose_move`, that should appear in `client_reported_ms` as a red flag. `process_time` would hide that bug.

**Affects:** `chess_client/client.py` move submission

### 10.6 Arena time control default

**Question:** `arena.py` defaults to 3+2 rated time control, or should it allow faster local testing?

**Options:**

1. **Default 3+2:** Matches server, local results predict live results.
2. **Default 1+0 or 0.5+0 for speed:** 100 games finish faster, but time management no longer matches server.
3. **Require explicit `--time-control` flag:** No default, force attendees to choose.

**Recommendation:** **Option 1 (default 3+2).** The whole point of the arena is "local results predict live results" (§17, AGENTS.md). A bot that flags at 3+2 locally will flag live. A faster default breaks that. If attendees want faster iteration, they can pass `--time-control 1000 --increment 0` explicitly.

**Affects:** `arena.py` CLI argparse defaults

---

## Summary for report

**File:** `docs/superpowers/specs/roles/client-engineer-spec.md`

**Sections claimed:**
- §8 (protocol consumer: long-poll loop, 409 handling, mailbox semantics, timeout skew)
- §11 (time control as SDK concern: echoing time_control_ms, ClockView conversion)
- §13.3 from SDK side (idling under agent control, logging once)
- §17 (local arena in full: opening randomisation, clock simulation, diagnostics, replay)
- Interfaces Part 3 (produce: `choose_move` signature, `ClockView`, arena result types)

**Seams consumed:**
- `chess_core` (Part 1): rules, clock, ELO, all functions and constants
- `chess_server` HTTP API (Part 5): `/bots`, `/bots/me/turn`, `/games/{id}/moves`, `/games/{id}/resign`, `/challenges/*`

**Seams produced:**
- `choose_move(board: chess.Board, clock: ClockView) -> chess.Move` — attendees and workshop-author both depend on this
- Implementation decisions (6 items, all non-blocking with recommendations):**
1. Opening book composition (recommend: mainline only, §10.1)
2. Shipped baseline bot depth (recommend: depth 2 material-only, §10.2)
3. Re-poll interval when no game (recommend: immediate, long-poll provides backpressure, §10.3)
4. Handling `choose_move` exceptions (recommend: resign immediately, log traceback, §10.4)
5. `client_reported_ms` measurement (recommend: wall time, matches server charging, §10.5)
6. Arena time control default (recommend: 3+2, local must predict live, §10.6)

**New requirement (Harmonization Revision 4):**
- `arena.py --serve` (stretch goal): Launch local web view at `localhost:8001` to show offline arena results, resolving the "local stats gap" without posting unverifiable data to the server (§3.4)

**Document completeness:** This spec is self-contained and buildable. Implementation decisions have recommendations but do not block progress.g traceback)
5. `client_reported_ms` measurement (recommend: wall time, matches server charging)
6. Arena time control default (recommend: 3+2, local must predict live)
