# MCP Engineer — Role Specification

> **Revision 5 errata — binding, and they override anything below that disagrees.**
> Applied from [the round-4 review](../../../../agent-reports/2026-08-24-spec-review-round4.md). Where this spec and design spec revision 5 conflict, **the design spec wins**.
>
> 1. **You own tools, not routes.** `POST /bots/me/control`, `GET /bots/me`, `GET /games/{id}/moves` and `GET /bots/{bot_id}/rating_history` are `server-engineer`'s to implement (design §8.1). You consume them. Revision 4 left them described only here — the one track forbidden from writing routes owned the surface §13.3 depends on.
> 2. **`get_legal_moves()` is the sole delivery trigger for `controller='agent'`.** `get_game()` never delivers and never starts a clock, which is what makes its `readOnlyHint` honest. Say so in both tool descriptions.
> 3. **`take_control()` is refused whenever the bot holds a `seats` row** — not "while a rated game is in progress", which is not evaluable at call time.
> 4. Canonicalise the controller-mismatch error to one string that names `take_control()` explicitly.

**Date:** 2026-08-24  
**Owner:** `mcp-engineer`  
**Track:** `chess_server/mcp/`  
**Status:** Ready to build from

**Purpose:** This specification defines the complete MCP server surface for Chess Arena attendees' Claude sessions. It distils §13 of the design spec and Part 6 of the interfaces document into a buildable contract.

---

## 1. Scope and Boundaries

### What You Own

```
chess_server/mcp/
  __init__.py
  server.py          # FastMCP server, tool definitions, HTTP client
  tools.py           # Tool implementations
  formatting.py      # ASCII boards, Markdown rendering, prose helpers
tests/chess_server/
  test_mcp.py        # Unit tests for tool logic
  integration/
    test_mcp_client.py  # Real MCP client exercising all tools
```

### What You Do Not Own

- `chess_server/store/` — database layer (owned by `server-engineer`)
- `chess_server/engine/` — game loop, ticker, matchmaker (owned by `server-engineer`)
- `chess_server/api/` — HTTP routes (owned by `server-engineer`)
- `chess_core/` — pure game logic (owned by `chess-domain-engineer`)
- `.mcp.json` client configuration docs (owned by `workshop-author`)

### Why You Are a Separate Track from `server-engineer`

They look redundant. They are not.

**`server-engineer` designs for HTTP clients:**
- Status codes (200/400/409/429) as the primary signal
- JSON response bodies optimised for parsing
- Wire efficiency — minimal payloads, numeric codes
- Idempotency via HTTP verbs and CAS predicates
- CORS, rate limiting, bearer token validation

**You design for a language model:**
- **Prose over codes.** "No bot registered for this token. Call register_bot first." not `{"error": "AUTH_REQUIRED"}`.
- **Boards over blobs.** An ASCII-rendered position that the model can reason over directly, not a FEN string that costs tokens to visualise.
- **Tool descriptions as UX.** The description is what the attendee reads when Claude suggests the tool — it is teaching material, not metadata.
- **Reasoning economy.** A smaller tool count with richer returns beats thirty single-purpose tools that require composition.
- **Honest annotations.** `readOnlyHint` vs `destructiveHint` determines whether Claude seeks permission before invoking — false annotations train attendees to click through prompts without reading.

**Practical consequence:** The same HTTP endpoint (`POST /games/{id}/moves`) becomes two different surfaces:
- SDK clients get `{ply, move, client_reported_ms}` and a 409 with `{ply, fen, status}` to handle stale state.
- Your `make_move(game_id, ply, move)` tool returns "CAS conflict. The position has changed since ply 12. Call get_game() to see the current position." — actionable, explicit, no JSON parsing required.

This separation is **why MCP exists.** Collapsing the two would produce an MCP server that is a thin wrapper over HTTP status codes, which attendees' Claude would reason over poorly while burning tokens.

---

## 2. Design Principles for an LLM-Facing Surface

These principles are workshop teaching material. Attendees will be shown this codebase as an example of good MCP design. Every tool you build demonstrates one or more of these.

### 2.1 Prose and Boards over JSON Blobs

**Problem:** A JSON dump of a chess position is high-token and low-information for a language model. `{"fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1", "legal_moves": ["a7a6", ...]}` forces the model to parse FEN, mentally reconstruct the board, and reason over 20+ UCI strings it cannot visualise.

**Solution:** `get_game()` returns Markdown with an ASCII board, labelled ranks/files, the move history in SAN (human-readable), and prose-wrapped clock state. The model sees the position directly and reasons over it in a fraction of the tokens.

**Example return:**
```
Game #42 — AlphaBot (White, 1245) vs BetaBot (Black, 1198)
Status: active, Ply: 12, Rated: yes

  a b c d e f g h
8 ♜ ♞ ♝ ♛ ♚ ♝ ♞ ♜
7 ♟ ♟ ♟ ♟   ♟ ♟ ♟
6               
5         ♟     
4         ♙     
3             ♘ 
2 ♙ ♙ ♙ ♙   ♙ ♙ ♙
1 ♖ ♘ ♗ ♕ ♔ ♗   ♖

FEN: rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 1 3

Moves: 1. e4 e5 2. Nf3

Clock: White 152.3s (to move), Black 161.1s
Time control: 180s + 2s/move
```

This is 4-5× cheaper to reason over than the equivalent structured JSON, and the model can answer "What is White's best move?" without first parsing FEN.

### 2.2 Tool Descriptions are the UX

**The description is what the attendee reads when Claude suggests calling the tool.** It must:
- State what the tool does in one sentence.
- List parameters with types and defaults.
- Give one concrete example invocation.
- Enumerate failure cases with the exact error text the tool will return.

**Example (from `get_game`):**
```python
"""Get the current state of a chess game with an ASCII board.

Retrieves your current game by default, or a specific game by ID.
Returns a rendered board, move history in algebraic notation, and clock state.

Parameters:
- game_id (optional int): Specific game to view. Omit to see your current game.

Example:
  get_game()           # Your current game
  get_game(game_id=42) # Specific game

Errors:
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} 
   to .mcp.json or call register_bot first."
- "No current game. Specify a game_id or wait for pairing."
- "Game 42 not found."
"""
```

Attendees will read these descriptions on a projector. If a description is vague or omits error cases, that becomes the attendee's debugging experience.

### 2.3 Actionable Error Prose

**Never:** `{"error": "UNAUTHORIZED", "code": 401}`  
**Always:** `"No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."`

Every error tells the attendee **what to do next.** No error codes that require looking up documentation. No bare HTTP semantics.

This is enforced by testing: every failure path in `test_mcp.py` asserts the exact error string, so the prose cannot drift into vagueness.

### 2.4 Honest Annotations

- **`readOnlyHint`** on observers: `get_leaderboard`, `get_my_bot`, `get_game`, `analyze_game`.
- **`destructiveHint`** on mutators: `register_bot`, `challenge`, `make_move`, `get_legal_moves` (triggers delivery), `take_control`, `release_control`.

This determines whether Claude seeks explicit user permission before invoking the tool. False annotations — marking a read-write tool as `readOnly` — train attendees to click through permission prompts without reading them, which is the opposite of what we want to teach.

`get_legal_moves` is marked `destructiveHint` because it triggers delivery per §6.2 when `controller='agent'` and `delivered_to_mover=0`. This starts the clock, which makes it a mutating operation even though the return value is read-only.

### 2.5 Small Tool Count

**Eleven tools total.** Five observers, six mutators. No single-purpose tools that require composition.

`get_game()` returns both the board and the legal moves together, so the model does not need to call two tools to decide on a move. `analyze_game()` returns PGN, timing, and event markers in one response, so the model does not need to fetch moves, then clocks, then strikes separately.

Every additional tool is discoverability cost and namespace collision risk. A well-designed tool does one **job**, not one **field**.

---

## 3. Identity and Auth, End to End

### 3.1 Token in `.mcp.json`

Attendees configure the MCP server by adding this to their `.mcp.json`:

```json
{
  "mcpServers": {
    "chess-arena": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

The token is returned by `POST /bots` (registration) or by `register_bot()`. It is a 43-character `secrets.token_urlsafe(32)` string.

### 3.2 Forwarding, Not Validating

Your MCP server **does not validate tokens.** It extracts the `Authorization` header from `.mcp.json` and forwards it verbatim to every HTTP API call.

```python
# Pseudocode
def call_api(endpoint: str, method: str, headers: dict, **kwargs):
    auth_header = headers.get("Authorization")  # from .mcp.json
    response = httpx.request(
        method,
        f"{API_BASE_URL}{endpoint}",
        headers={"Authorization": auth_header} if auth_header else {},
        **kwargs
    )
    return response
```

**Why:** This is what enforces the no-privileged-path rule (§13.1). If the MCP server had a back door to the database or a default token, it could do things on behalf of a bot that the bot itself could not do through the HTTP API. That would make the MCP surface a separate, more powerful interface, which breaks the "Claude and a bot are equivalent clients" invariant.

### 3.3 No Token, No Default

If no `Authorization` header is present in `.mcp.json`, every tool returns:

```
"No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
```

This error is **identical** to the HTTP API's 401 response (per Part 5 of the interfaces doc). The only difference is that your tool wraps it in plain English rather than returning `{"error": "..."}`.

### 3.4 How `register_bot` Works

`register_bot(name, owner, role)` calls `POST /bots` **without** a token (it is the one unauthenticated write per §8.5). The HTTP API returns `{bot_id, name, token}`.

Your tool returns this **into the conversation transcript** as Markdown:

```
Bot registered successfully!

Name: MyBot
ID: 42
Token: <43-character token>

IMPORTANT: Add this token to your .mcp.json file:

{
  "mcpServers": {
    "chess-arena": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}

The token is not a secret from your own Claude session, but keep it out of shared channels.
```

This is the **only** time a token appears in a transcript. All subsequent tools use the token from `.mcp.json` headers.

### 3.5 CORS and `Mcp-Session-Id`

Per §13.1, your MCP server configures CORS for the dashboard origin and includes `Mcp-Session-Id` in `Access-Control-Expose-Headers`. This allows a browser-based MCP client (if one is built) to read the session ID header.

Implementation detail, not a behaviour change: FastMCP may handle this automatically. If not, configure it explicitly.

---

## 4. The Tool Inventory

Eleven tools. Exact parameter types, return formats, annotations, and error messages per Interfaces Part 6.

### 4.1 Observe Tools (readOnlyHint)

#### `get_leaderboard()`

**Parameters:** none

**Returns:** Markdown-formatted leaderboard table:

```
Chess Arena Leaderboard (20 bots)

Rank | Bot Name         | Rating | W-L-D    | Games | Status
-----|------------------|--------|----------|-------|------------
1    | AlphaBot         | 1312   | 8-2-1    | 11    | Competitor
2    | BetaBot          | 1289   | 7-3-2    | 12    | Competitor
3    | GammaBot         | 1245   | 5-4-1    | 10    | Provisional
...
18   | ref-greedy       | 1000   | -        | -     | Anchor (fixed)
19   | ref-random       | 800    | -        | -     | Anchor (fixed)

Provisional = fewer than 10 games played
Anchors are reference bots with fixed ratings
```

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."

**Annotation:** `readOnlyHint`

---

#### `get_my_bot()`

**Parameters:** none

**Returns:** Markdown-formatted status:

```
Your Bot: AlphaBot (ID 42)

Owner: alice@example.com
Rating: 1312 (12 games played)
Record: 8 wins, 2 losses, 2 draws
Role: Competitor
Controller: client

Current game: #156 (active, ply 8)
- Opponent: BetaBot (Black, 1289)
- Your color: White
- Clock: 145.2s (yours), 158.3s (opponent)
```

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."

**Annotation:** `readOnlyHint`

---

#### `get_game(game_id: Optional[int] = None)`

**Parameters:**
- `game_id` (optional int): Specific game to view. Omit to see your current game.

**Returns:** Markdown with ASCII board, FEN, move history, clock state (see §2.1 example).

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- "No current game. Specify a game_id or wait for pairing."
- "Game {id} not found."

**Annotation:** `readOnlyHint`

**Implementation note:** Uses `chess_core.rules.fen_to_ascii()` for board rendering. Clock times are formatted as seconds with one decimal place (e.g., "152.3s"). Move history uses SAN notation.

---

#### `analyze_game(game_id: int)`

**Parameters:**
- `game_id` (required int): Game ID to analyze.

**Returns:** Markdown with three sections per §5 below.

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- "Game {id} not found."
- "Game {id} is still in progress. Analysis is only available for finished games."

**Annotation:** `readOnlyHint`

---

### 4.2 Act Tools (destructiveHint)

#### `register_bot(name: str, owner: str, role: str = "competitor")`

**Parameters:**
- `name` (str, required): Bot name (must be unique, no spaces)
- `owner` (str, required): Owner identifier (displayed on leaderboard)
- `role` (str, optional): "competitor" or "benchmark", default "competitor"

**Returns:** Markdown per §3.4 with token and `.mcp.json` instructions.

**Errors:**
- "Name '{name}' is already taken. Choose a different name."
- "Invalid role '{role}'. Must be 'competitor' or 'benchmark'."
- "Invalid join code. Ask the workshop organizer for the correct code."
- "Rate limit exceeded. Wait 60 seconds and try again."

**Annotation:** `destructiveHint`

**Implementation note:** The join code is passed automatically from an environment variable (`JOIN_CODE`), not as a parameter. Attendees do not see or provide it — the tool includes it in the HTTP request body.

---

#### `challenge(opponent: str, time_control: str = "rated")`

**Parameters:**
- `opponent` (str, required): Name of bot to challenge
- `time_control` (str, optional): "rated" (3+2) or "exhibition" (5+10), default "rated"

**Returns:**

```
Challenge created!

Challenge ID: 17
You (AlphaBot) challenged BetaBot
Time control: 3 minutes + 2 seconds/move (rated)
Status: open (waiting for opponent to accept)

The opponent will be notified. You can check challenge status with get_my_bot().
```

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- "Opponent bot '{opponent}' not found."
- "You already have an open outgoing challenge. Wait for it to be resolved or decline it."
- "Either you or {opponent} is already in a game. Wait for the current game to finish."
- "Invalid time_control '{time_control}'. Must be 'rated' or 'exhibition'."

**Annotation:** `destructiveHint`

---

#### `make_move(game_id: int, ply: int, move: str)`

**Parameters:**
- `game_id` (int, required): Game ID
- `ply` (int, required): Current ply (for CAS)
- `move` (str, required): Move in UCI notation (e.g., "e2e4", "e7e8q")

**Returns:**

```
Move accepted: e2e4

Game: #42, Ply now: 13
Status: active
FEN after move: rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2

(If game ended:)
Game ended: White wins by checkmate
```

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- "Illegal move '{move}'. Legal moves: {legal_moves}. Current position: {fen}"
- "CAS conflict. The position has changed since ply {ply}. Call get_game() to see the current position."
- "Controller is 'client'. Call release_control() before using agent tools, or use take_control() if you meant to switch."
- "Game {game_id} not found or already ended."

**Annotation:** `destructiveHint`

---

#### `get_legal_moves(game_id: int)`

**Parameters:**
- `game_id` (int, required): Game ID

**Returns:**

```
Legal moves in game #42 (ply 12):

e2e4, e2e3, d2d4, d2d3, g1f3, g1h3, b1c3, b1a3, f1e2, f1d3, f1c4, f1b5, f1a6

(21 legal moves)

Current position (White to move):
  [ASCII board]
```

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- "Game {game_id} not found or already ended."
- "Controller is 'client'. Call take_control() before using agent tools."

**Annotation:** `destructiveHint`

**Critical note:** This tool triggers delivery per §6.2 if `controller='agent'` and `delivered_to_mover=0`. This starts the clock, which is why it is marked `destructiveHint` despite returning read-only data.

---

#### `take_control()`

**Parameters:** none

**Returns:**

```
Control transferred to agent mode.

Your client bot (if running) will now idle when polling. You can use agent tools like 
get_legal_moves() and make_move() to play manually through this Claude session.

To return control to your bot, call release_control().
```

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
- "Cannot take control while bot holds a seat (is in an active or pending game). Wait for the current game to finish or resign first."

**Annotation:** `destructiveHint`

---

#### `release_control()`

**Parameters:** none

**Returns:**

```
Control released back to client mode.

Your bot will resume polling and playing automatically. Agent tools are now disabled 
until you call take_control() again.
```

**Errors:**
- "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."

**Annotation:** `destructiveHint`

---

## 5. `analyze_game` in Detail

Per §13.2, this is "the workshop's central moment." What a model can conclude from this tool's return determines whether an attendee can turn a loss into working code.

### 5.1 What It Returns

Three sections, Markdown-formatted:

#### Section 1: PGN with Headers

```
[Event "Chess Arena Game 42"]
[Site "localhost"]
[Date "2026.08.23"]
[Round "1"]
[White "AlphaBot"]
[Black "BetaBot"]
[WhiteElo "1245"]
[BlackElo "1198"]
[Result "0-1"]
[Termination "flag"]
[TimeControl "180+2"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 O-O 
8. c3 d5 9. exd5 Nxd5 10. Nxe5 Nxe5 11. Rxe5 c6 12. d4 Bd6 13. Re1 Qh4 
14. g3 Qh3 15. Re4 g5 16. Qf1 Qxf1+ 17. Kxf1 Bf5 18. Re1 Rfe8 19. Nd2 Rad8 
20. Nf3 Bg4 21. Be3 Bxf3 22. Bxd5 cxd5 {White flagged} 0-1
```

Standard PGN format. The final move includes a comment marker (e.g., `{White flagged}`) if the game ended abnormally.

#### Section 2: Timing Table

```
Timing Analysis

Ply | Move  | Server ms | Client ms | White remaining | Black remaining | Flags
----|-------|-----------|-----------|-----------------|-----------------|------
1   | e4    | 1200      | 1150      | 178800          | 180000          |
2   | e5    | 1350      | 1300      | 178800          | 178650          |
3   | Nf3   | 1180      | 1120      | 177620          | 178650          |
...
22  | cxd5  | 950       | 920       | 8500            | 177300          |
23  | (flag)| -         | -         | 0               | 177300          | White flagged

Average move time: 1150ms (server), 1100ms (client)
Time budget consumed: White 100% (flagged), Black 2%
```

**Columns:**
- **Ply**: Move number (half-move, 1-indexed)
- **Move**: SAN notation
- **Server ms**: Delivery-to-receipt time (what the clock was charged), from `moves.server_elapsed_ms`
- **Client ms**: Self-reported think time, from `moves.client_reported_ms` (may be NULL)
- **White remaining / Black remaining**: Clock state after the move and increment
- **Flags**: Special markers (flag, strike, forfeit) aligned to the ply

**What a model can conclude:**
- "White flagged at ply 23" → time management issue, not position evaluation.
- "Client ms consistently 50-100ms less than server ms" → network latency, consider faster polling.
- "Three strikes at ply 15, 18, 20" → illegal move bug, not a strategic error.
- "White remaining dropped from 120s to 8s between ply 18-22" → panic mode, bot needs time-based pruning.

#### Section 3: Event Log

```
Key Events

Ply 15: White illegal move strike (1/3) — attempted Ng5 (not legal)
Ply 18: White illegal move strike (2/3) — attempted Bxf7+ (king not in check)
Ply 20: White illegal move strike (3/3) — attempted Qg8+ (queen not on board)
Ply 23: White flagged (0ms remaining)

Result: Black wins by flag fall
```

Prose annotations for events that are not in the move table: strikes (including the illegal move that was attempted), flags, forfeits, resignations, abandonment, no-shows.

**What a model can conclude:**
- The specific illegal moves attempted → likely a move generation bug, not an eval bug.
- Three strikes in quick succession → the bot does not validate moves before submitting.
- Flag at ply 23 after steady time consumption → not a single slow move, but cumulative budget exhaustion.

### 5.2 Why Eval Swing Was Cut

Revision 2 of the spec included per-move eval swing (centipawn loss) in this tool. Cut in revision 2 because:
- It implied an unacknowledged Stockfish dependency, which is new server infrastructure.
- Timing + strike markers explain >90% of real losses in a blitz competition, so eval adds marginal value.
- Token cost: evaluating 40 moves at depth 20 is ~10s of compute per game, which does not scale to 100+ analyzed games in a workshop afternoon.

If eval is re-added post-workshop, it goes in a separate `deep_analyze_game(game_id)` tool, not in the default analysis.

---

## 6. Control Handoff from Your Side

Per §13.3, control handoff allows an attendee to **pause their bot and play moves manually through Claude** (agent mode), then resume autonomous bot play (client mode).

### 6.1 The Two Modes

- **`controller='client'`**: The bot polls `GET /bots/me/turn` and submits moves through the SDK. Agent tools (`get_legal_moves`, `make_move`, `take_control`) are **refused** with a 403.
- **`controller='agent'`**: The bot idles when polling (receives `"reason": "agent_has_control"`). Delivery happens on `get_legal_moves()` or `get_game()` under the §6.2 idempotency guard. `take_control` is refused.

### 6.2 Refusing `take_control` While Seated

Per §13.3 revision 3 fix: **`take_control()` is refused whenever the bot holds a `seats` row**, not "during a rated game in progress."

**Why:** `rated` is not finalized until the game ends (per §5.1 rule 1, a game that ends `no_show` or `server_restart` is unrated regardless of how it started). "In progress" is ambiguous for `pending` games. The `seats` predicate is unambiguous: if the bot is in a game (pending or active), control cannot be taken.

**Error returned:**

```
"Cannot take control while bot holds a seat (is in an active or pending game). 
Wait for the current game to finish or resign first."
```

**Why this rule exists:** A 3+2 rated game at human pace flags around move 18. Control handoff is only safe **between** games, not during them. Exhibition games (5+10) are more forgiving but still benefit from this guard.

### 6.3 Delivery While `controller='agent'`

When `controller='agent'` and the bot calls `get_legal_moves(game_id)` or `get_game(game_id)`, and the game is at an undelivered position (`delivered_to_mover=0`), your MCP server triggers delivery by calling the HTTP API's move endpoint **with the delivery flag**.

**Idempotency (§6.2):** Delivery is guarded by `delivered_to_mover=0` in the database UPDATE. Re-calling `get_legal_moves()` while thinking returns the same payload and does **not** restart the clock.

**Grace period:** `AGENT_DELIVERY_GRACE_NS = 60_000_000_000` (60 seconds) applies instead of the client 15s grace, since a human is in the loop.

### 6.4 Auto-Release After Inactivity

Per §13.3 revision 3 fix: **after `AGENT_AUTO_RELEASE_NS = 45_000_000_000` (45 seconds) of no agent tool calls**, the ticker sets `controller='client'` and wakes any held polls.

**Why 45s:** Must be **less than** `AGENT_DELIVERY_GRACE_NS` (60s), otherwise the grace timer always fires first and auto-release is unreachable. A seated agent bot that goes silent for 45s is treated as abandoned, and control reverts to the client so the game can continue.

**What counts as activity:** Every agent tool call updates `last_agent_action_mono`. This includes `get_game()`, `get_legal_moves()`, `make_move()`, `get_my_bot()` when `controller='agent'`.

**Attendee experience:**
1. Bot is playing autonomously (client mode).
2. Attendee calls `take_control()` between games.
3. Bot is paired into a new game (exhibition, since rated games require `controller='client'` at creation per §13.3).
4. Attendee calls `get_legal_moves()` → delivery happens, clock starts.
5. Attendee takes 2 minutes to think → auto-release fires at 45s, control reverts to client, bot resumes polling and plays the move autonomously.

This makes "forgot to release control" recoverable rather than a guaranteed flag.

---

## 7. Seams You Consume

You are an **HTTP client** of the Chess Arena API. Every HTTP endpoint you call is documented in Interfaces Part 5. You have **no privileged access** to the database, engine, or store.

### 7.1 Endpoints You Call

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/bots` | POST | Registration (called by `register_bot`) | No |
| `/bots/me/turn` | GET | Check bot status (not directly called; used for control state queries) | Yes |
| `/games/{id}` | GET | Fetch game details (called by `get_game`, `analyze_game`) | No |
| `/games/{id}/moves` | GET | Fetch move history with timing (called by `analyze_game`) | No |
| `/games/{id}/moves` | POST | Submit move (called by `make_move`) | Yes |
| `/games/{id}/resign` | POST | Resign (if you add a resign tool later) | Yes |
| `/challenges` | POST | Create challenge (called by `challenge`) | Yes |
| `/challenges` | GET | Fetch inbox (if you add a challenges tool later) | Yes |
| `/leaderboard` | GET | Fetch leaderboard (called by `get_leaderboard`) | No |
| `/bots/me` | GET | Fetch authenticated bot details (called by `get_my_bot`) | Yes |
| `/bots/me/control` | POST | Take/release control (called by `take_control`, `release_control`) | Yes |

### 7.2 Response Handling

All HTTP responses use the models in Interfaces Part 5. Your job is to **translate** them into Markdown prose and ASCII boards.

**Error handling pattern:**

```python
response = httpx.post(f"{API_BASE_URL}/games/{game_id}/moves", json=payload, headers=headers)

if response.status_code == 200:
    return format_move_success(response.json())
elif response.status_code == 400:
    error = response.json()
    return f"Illegal move '{move}'. Legal moves: {error['details']['legal_moves']}. Current position: {error['details']['fen']}"
elif response.status_code == 401:
    return "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
elif response.status_code == 403:
    return "Controller is 'client'. Call release_control() before using agent tools."
elif response.status_code == 409:
    error = response.json()
    return f"CAS conflict. The position has changed since ply {error['details']['ply']}. Call get_game() to see the current position."
else:
    return f"Server error: {response.status_code}. Contact the workshop organizer if this persists."
```

### 7.3 What You Do Not Call

You **do not** call:
- Any admin endpoints (`/admin/*`) — those are operator-only.
- Database queries directly — you have no database connection.
- Store or engine functions — those are internal to `server-engineer`'s track.

If a tool needs data the HTTP API does not expose, you **request the endpoint from `server-engineer`** rather than inventing a back door. This is what keeps the no-privileged-path invariant honest.

---

## 8. Seams You Produce

The MCP tool surface, consumed by attendees' Claude sessions and documented by `workshop-author`.

### 8.1 Tool Surface Contract

- Eleven tools, per §4 above.
- All tool names are lowercase with underscores (`get_game`, `make_move`, `take_control`).
- All parameters are explicitly typed (Python type hints, enforced by FastMCP).
- All return values are Markdown strings, never raw JSON dumps.
- All errors are actionable prose strings, never structured error objects.
- All mutating tools are marked `destructiveHint`, all observers `readOnlyHint`.

### 8.2 FastMCP Server Configuration

Your server is configured at `/mcp` endpoint (streamable HTTP per §13.1).

```python
from fastmcp import FastMCP

mcp = FastMCP("Chess Arena")

@mcp.tool(readOnlyHint=True)
async def get_leaderboard() -> str:
    """Get the current leaderboard with ratings, records, and rankings.
    
    Returns a formatted table of all bots sorted by rating.
    Provisional status (< 10 games) and anchor bots are annotated.
    
    Example:
      get_leaderboard()
    
    Errors:
      - "No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} 
         to .mcp.json or call register_bot first."
    """
    ...
```

### 8.3 Documentation Surface

`workshop-author` writes the attendee-facing docs that reference your tools. They will document:
- How to add the token to `.mcp.json`.
- The difference between client and agent mode.
- When to use `analyze_game` (after every loss).
- How to interpret the timing table.

You provide them with:
- The exact tool names and signatures.
- Example returns for each tool.
- The failure cases and error texts.

This happens **after** your track is built, so the docs match the implementation rather than diverging from a spec.

---

## 9. Failure Modes

Every failure mode returns exact prose. These are specified so `test_mcp.py` can assert them.

### 9.1 No Token or Invalid Token

**Trigger:** No `Authorization` header in `.mcp.json`, or token not in database.

**All tools return:**
```
"No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."
```

### 9.2 No Bot Registered

**Trigger:** Token is valid but bot was deleted or never existed.

**Same error as 9.1.** The attendee cannot distinguish "token is wrong" from "bot was deleted" — both require re-registration.

### 9.3 Bot Already in a Game

**Trigger:** Calling `take_control()` while `seats` row exists for the bot.

**Error:**
```
"Cannot take control while bot holds a seat (is in an active or pending game). Wait for the current game to finish or resign first."
```

**Trigger:** Calling `challenge(opponent)` when either bot holds a seat.

**Error:**
```
"Either you or {opponent} is already in a game. Wait for the current game to finish."
```

### 9.4 Not Your Turn

**Trigger:** Calling `make_move()` when it's the opponent's turn, or game is pending, or game has ended.

**Error:**
```
"Game {game_id} not found or already ended."
```

(The API does not distinguish "not your turn yet" from "game ended" in the 404 response, because both mean "you cannot move now.")

### 9.5 Stale Ply (CAS Conflict)

**Trigger:** `make_move(game_id, ply=12, move)` but the game is now at ply 13.

**Error:**
```
"CAS conflict. The position has changed since ply {ply}. Call get_game() to see the current position."
```

### 9.6 Illegal Move

**Trigger:** `make_move(game_id, ply, "e2e5")` but e2e5 is not in legal_moves.

**Error:**
```
"Illegal move '{move}'. Legal moves: {legal_moves}. Current position: {fen}"
```

(Includes the actual legal moves and the FEN, so the model can understand why the move was rejected without making another tool call.)

### 9.7 Control Refused

**Trigger:** Calling agent tools (`make_move`, `get_legal_moves`) when `controller='client'`.

**Error:**
```
"Controller is 'client'. Call take_control() before using agent tools, or use release_control() if you are the bot's SDK."
```

**Trigger:** Calling `take_control()` when `controller='agent'` (already taken).

**Error:**
```
"Control is already in agent mode. You can use agent tools directly."
```

### 9.8 Server Unreachable

**Trigger:** HTTP connection fails (ECONNREFUSED, timeout, DNS failure).

**Error:**
```
"Cannot reach Chess Arena server at {url}. Check that the server is running and the URL in .mcp.json is correct."
```

### 9.9 Rate Limited

**Trigger:** HTTP 429 response.

**Error:**
```
"Rate limit exceeded. Wait {retry_after} seconds and try again. If you are polling in a loop, this is a bug — the SDK handles polling for you."
```

### 9.10 Opponent Not Found

**Trigger:** `challenge(opponent="NonexistentBot")`.

**Error:**
```
"Opponent bot '{opponent}' not found. Check the spelling or call get_leaderboard() to see available bots."
```

---

## 10. Test Obligations

### 10.1 Unit Tests (`test_mcp.py`)

Test every tool in isolation by mocking the HTTP client. Assert:
- Successful returns match the expected Markdown format.
- Every failure case returns the exact error prose from §9.
- Annotations are correct (`readOnlyHint` vs `destructiveHint`).
- Parameter validation (type errors, missing required params).

**Example test:**

```python
def test_make_move_illegal_move(mock_http_client):
    mock_http_client.post.return_value = MockResponse(
        status_code=400,
        json={"error": "Illegal move", "details": {"legal_moves": ["e2e4", "d2d4"], "fen": "..."}},
    )
    
    result = make_move(game_id=42, ply=1, move="e2e5")
    
    assert "Illegal move 'e2e5'" in result
    assert "e2e4" in result
    assert "d2d4" in result
```

### 10.2 Integration Tests (`test_mcp_client.py`)

Exercise every tool through a **real MCP client** against a running server (in-memory test mode). Assert:
- `register_bot` returns a token that subsequent tools accept.
- `get_game` renders an ASCII board.
- `analyze_game` returns PGN, timing table, and event log for a completed game.
- `take_control` → `get_legal_moves` → `make_move` → `release_control` completes a full handoff cycle.
- Concurrent tool calls (two Claude sessions with different tokens) do not interfere.

### 10.3 ASCII Board Rendering Test

Create a `test_formatting.py` that asserts `fen_to_ascii()` produces correct output for:
- Starting position.
- Mid-game position.
- Endgame position (few pieces).
- Promotion position (queens on unexpected squares).

**Why separate:** This is the highest-visibility output (attendees will see dozens of boards in transcripts). A rendering bug is immediately obvious and embarrassing.

### 10.4 Error Message Consistency Test

One test that asserts every error string in §9 **appears exactly once in the codebase**, so they cannot drift into variants like "No bot found for this token" vs "Bot not registered for token."

```python
def test_error_messages_are_canonical():
    codebase = read_all_python_files("chess_server/mcp/")
    
    for error_text in CANONICAL_ERRORS:
        assert codebase.count(error_text) == 1, f"Error text '{error_text}' must appear exactly once"
```

---

## 11. Acceptance Criteria

Your track is **done** when:

1. All eleven tools are implemented and pass unit tests.
2. Every tool has been exercised through a real MCP client (Claude Desktop or equivalent) in integration tests.
3. `analyze_game` returns PGN + timing table + event log for a flagged game, an illegal-forfeit game, and a clean checkmate, and a human reviewer confirms the output is actionable.
4. Every error path in §9 returns the exact prose specified, verified by assertion in tests.
5. `get_game` renders a readable ASCII board for the starting position, a mid-game position, and an endgame position.
6. Control handoff works end-to-end: `take_control` → manual move via `make_move` → `release_control` → bot resumes autonomous play.
7. No tokens appear in logs, in error responses, or in SSE payloads (verified by grep).
8. Tool descriptions include parameters, example calls, and all error cases (verified by manual review).
9. Annotations are correct and tested (`readOnlyHint` on observers, `destructiveHint` on mutators).
10. The MCP server forwards the `Authorization` header from `.mcp.json` verbatim and has no default token or database back door (verified by code review).

---

## 12. Requires Decision

None. All ambiguities from the interfaces document (§§1-8 of "Decisions Required") have been resolved or delegated to other tracks.

- Opening book format (decision #1): Owned by `client-engineer` (arena.py).
- `client_reported_ms` semantics (decision #2): Resolved in interfaces revision — SDK auto-includes it.
- `controller` initial value (decision #3): Owned by `server-engineer` (bots table schema).
- SSE coalescing window (decision #4): Owned by `server-engineer` (SSE emitter).
- `analyze_game` format (decision #5): **Resolved in §5 of this spec** — Markdown with three sections.
- Strike counter reset (decision #6): Owned by `server-engineer` (games table, per-game columns).
- Provisional threshold (decision #7): Owned by `server-engineer` (leaderboard logic, 10 games).
- Featured game selection (decision #8): Owned by `dashboard-engineer` (dashboard controller).

You have zero open decisions. Everything you need to build is specified.

---

## Summary for Report-Back

**File:** `docs/superpowers/specs/roles/mcp-engineer-spec.md`

**Sections claimed:**
- §13 (MCP surface, identity, control handoff) — in full
- Interfaces Part 6 (MCP tool signatures) — all eleven tools

**Endpoints consumed (Part 5):**
- `POST /bots` (registration)
- `GET /games/{id}` (game details)
- `GET /games/{id}/moves` (move history with timing)
- `POST /games/{id}/moves` (submit move)
- `POST /challenges` (create challenge)
- `GET /leaderboard` (leaderboard)
- `GET /bots/me` (authenticated bot status)
- `POST /bots/me/control` (take/release control)

**"Requires decision" items:** Zero. All ambiguities resolved or delegated.

**Critical behaviours specified:**
- Prose and ASCII boards over JSON (§2), with example returns
- Identity as forwarded bearer token, no privileged path (§3)
- All eleven tools with exact parameters, returns, annotations, and error texts (§4)
- `analyze_game` three-section format: PGN, timing table, event log (§5)
- Control handoff refused while seated, delivery on `get_legal_moves`, 45s auto-release (§6)
- Failure modes with exact prose for nine categories (§9)
- Test obligations: unit, integration, real MCP client, ASCII rendering, error consistency (§10)
