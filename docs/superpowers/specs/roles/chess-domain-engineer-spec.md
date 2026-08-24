# Chess Domain Engineer — Role Specification

> **Revision 5 errata — binding, and they override anything below that disagrees.**
> Applied from [the round-4 review](../../../agent-reports/2026-08-24-spec-review-round4.md). Where this spec and design spec revision 5 conflict, **the design spec wins**.
>
> 1. **The flag predicate is `remaining_ns <= 0`, not `< 0`.** Reaching exactly zero is a flag. §18's "flag on exact zero" test asserts this, and every document body has been corrected to match — if you find a `< 0` anywhere, it is a leftover and `<= 0` wins.
> 2. **Matchmaker pairing is now explicit pseudocode in design §9.2 — implement that, not the prose below.** It fixes two things revision 4 left unimplementable: which bot advances on a skipped candidate (`b` advances, `a` holds), and that **one** waiting side (`unpaired_ticks >= 3`) is enough to relax, with same-owner and rematch constraints dropped together rather than in sequence.
> 3. **`unpaired_ticks` is carried in `PoolEntry`** so the function reads no clock and stays pure and seeded-testable.
> 4. Use the canonical constant names in design §5.2 (`RATED_TIME_CONTROL_NS`, `K_FACTOR`, `PLY_CAP`, …). `clock.py` and `elo.py` are the only declaration sites.
> 5. Elo at K=24 is zero-sum under integer rounding — verified across 1,201 rating samples — so §18's property test is achievable as specified.

**Owner:** chess-domain-engineer agent  
**Date:** 2026-08-24  
**Purpose:** Distilled specification for the pure-logic layer shared by the live server and the offline arena

---

## 1. Scope and Boundaries

### What you own

```
chess_core/
  rules.py       — move validation, termination detection, FEN/SAN/PGN, ASCII board
  clock.py       — 3+2 blitz arithmetic, delivery lifecycle, unit conversion helpers
  elo.py         — flat K=24 exchange, one-sided anchor case
  matchmaker.py  — pure pairing over PoolEntry snapshots
  match.py       — game state machine, legal transitions
tests/chess_core/
  test_rules.py
  test_clock.py
  test_elo.py
  test_matchmaker.py
  test_match.py
```

### What you do NOT own

- Pool eligibility (§9.1) — that is `chess_server/engine/`
- Persistence, SQLite, transactions — `chess_server/store/`
- HTTP, FastAPI, SSE, auth — `chess_server/api/`
- MCP server — `chess_server/mcp/`
- Dashboard, web UI — `web/`
- Starter kit, SDK, arena.py — `starter-kit/` and `client-engineer`

### Who consumes you

- **server-engineer** — calls your functions from `chess_server/engine/runner.py` (move application, clock updates, Elo exchange, match transitions) and `chess_server/engine/ticker.py` (matchmaker, delivery grace checks)
- **client-engineer** — uses your constants (`RATED_TIME_CONTROL_NS`, `RATED_INCREMENT_NS`, `STARTING_RATING`) and imports `ClockView` and enums from `chess_core` for SDK types
- **arena.py** — runs complete games through your rules, clock, Elo, and matchmaker to produce a local leaderboard

### Boundaries

If your work requires opening a socket, reading the system clock (`time.monotonic_ns()`), or touching a database, the design is wrong. Say so rather than reaching across. Time is always passed to you as a parameter.

---

## 2. What You Build

### 2.1 `chess_core/rules.py`

**Responsibilities:**

1. **Move validation and application** (§22, Interfaces Part 1)
   - `validate_and_apply_move(fen: str, move_uci: str) -> MoveOutcome`
   - Returns `MoveOutcome(accepted=True, move_result=...)` or `MoveOutcome(accepted=False, rejection_reason=...)`
   - Exceptions reserved for malformed FEN or syntactically invalid UCI, not illegal moves
   - Uses `python-chess` for move generation and validation; never hand-roll

2. **Termination detection** (§22)
   - `detect_termination(fen: str, history_fens: List[str]) -> (bool, Optional[TerminationReason], Optional[GameResult])`
   - Covers: checkmate, stalemate, insufficient material, fifty-move (server-claimed via `can_claim_draw`), threefold (server-claimed via `can_claim_draw`)
   - **Threefold via position key** (§22), not full FEN — compares `position_key(fen)` values, which is the first four FEN fields only (placement, side to move, castling, en passant)
   - `position_key(fen: str) -> str` — extracts position key for threefold detection

3. **Legal moves generation**
   - `get_legal_moves(fen: str) -> List[str]` — returns UCI strings, sorted lexicographically

4. **Notation conversion**
   - `uci_to_san(fen: str, move_uci: str) -> str` — for move history
   - `san_list_to_pgn(san_moves: List[str], white_name: str, black_name: str, result: GameResult, white_rating: Optional[int], black_rating: Optional[int], starting_fen: Optional[str]) -> str` — for arena.py export. `starting_fen` writes `[SetUp "1"]`/`[FEN ...]` and numbers movetext from that position; every arena opening is a non-start position, so omitting it makes the export unreadable.

5. **ASCII board rendering** (§13.2)
   - `fen_to_ascii(fen: str) -> str` — for MCP `get_game()`, readable on a projector

6. **Constants**
   - `STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"`
   - `PLY_CAP = 200` — adjudication cap (§22)

**Normative:**
- §22: Threefold and fifty-move are **server-claimed** when `can_claim_draw` is true, not left to bots
- §22: Adjudication is a **flat 200-ply cap**, result draw, unconditionally
- §22: Threefold uses `position_key()`, not full FEN comparison

---

### 2.2 `chess_core/clock.py`

**Responsibilities:**

1. **Clock creation**
   - `create_clock(time_control_ns: int, increment_ns: int, to_move: Color, now_mono: int) -> ClockState`

2. **Delivery lifecycle** (§6.2)
   - `deliver_position(clock: ClockState, now_mono: int, ply: int) -> ClockState`
   - Sets `turn_started_mono`, marks `delivered_to_mover=1`
   - **Idempotent:** re-delivery returns identical clock without restarting timer

2b. **Reading the clock without mutating it** (§6.4)
   - `remaining_ns(clock: ClockState, color: Color, now_mono: int) -> int`
   - `has_flagged(clock: ClockState, now_mono: int) -> bool` — the `<= 0` predicate
   - `is_within(earlier_mono, now_mono, window_ns) -> bool` and `window_start_mono(now_mono, window_ns) -> int` — poll recency (§9.1) and challenge TTL (§12). The second exists because a SQL filter needs a bound, not a predicate.
   - These let §6.4's "flag precedes illegal-move validation" be asked *before* a move is validated. `account_move_and_switch` is atomic by design and cannot answer it without also mutating. `chess_server` must never subtract monotonic timestamps itself.
   - Must agree with `account_move_and_switch` exactly — pin that with a swept test, not a spot check.

3. **Move accounting** (§6.4)
   - `account_move_and_switch(clock: ClockState, receive_mono: int, now_mono: int) -> ClockUpdateResult`
   - Performs the complete §6.4 sequence atomically:
     1. `elapsed = receive_mono − turn_started_mono`
     2. `remaining = remaining − elapsed`
     3. `if remaining_ns <= 0 → flag (NO increment on flag)`
     4. `if not flagged → remaining += increment_ns`
     5. Switch side; `delivered_to_mover=0`; `turn_started_mono=NULL`; `to_move_since_mono=now_mono`
   - Returns `ClockUpdateResult` with `new_clock`, `flagged` bool, `flagged_color`, `elapsed_ms`

4. **Delivery grace checks** (§6.3)
   - `check_delivery_timeout(clock: ClockState, now_mono: int, grace_ns: int) -> bool`
   - Returns `True` if `delivered_to_mover=0` and `(now_mono − to_move_since_mono) > grace_ns`

5. **Turn elapsed computation** (§14)
   - `compute_turn_elapsed_ms(clock: ClockState, now_mono: int) -> Optional[int]`
   - For SSE payloads: returns `None` if undelivered

6. **Unit conversion helpers**
   - `ms_to_ns(ms: int) -> int`
   - `ns_to_ms(ns: int) -> int`

7. **Constants** (§6, §11, §13.3)
   - `RATED_TIME_CONTROL_NS = 180_000_000_000` (3 minutes)
   - `RATED_INCREMENT_NS = 2_000_000_000` (2 seconds)
   - `EXHIBITION_TIME_CONTROL_NS = 300_000_000_000` (5 minutes)
   - `EXHIBITION_INCREMENT_NS = 10_000_000_000` (10 seconds)
   - `DELIVERY_GRACE_NS = 15_000_000_000` (15 seconds)
   - `AGENT_DELIVERY_GRACE_NS = 60_000_000_000` (60 seconds)
   - `AGENT_AUTO_RELEASE_NS = 45_000_000_000` (45 seconds)

**Normative:**
- §6: **Clocks run only between delivery and receipt.** A bot is never charged for time before the position was sent.
- §6.3: Undelivered positions have a **15s deadline** (DELIVERY_GRACE_NS). At ply 0 → aborted/no_show; mid-game → finished/abandoned.
- §6.3: **AGENT_DELIVERY_GRACE_NS = 60s** applies while `controller='agent'`
- §6.4: **Ordering is unbreakable** — deduct → flag-check → no-increment-on-flag → apply → increment → switch. The API is shaped so the wrong order is unrepresentable.
- §6.4: **Flag takes precedence over illegal move:** step 3 precedes validation. A bot that submits an illegal move after its flag has fallen has flagged.
- §6.2: **Re-delivery is idempotent.** Re-reading a position returns identical payload and never restarts the clock.

**Unit discipline:**
- **Nanoseconds internally**, milliseconds at the boundary
- Every field and parameter carries `_ns` or `_ms`
- A field without a suffix is a bug

---

### 2.3 `chess_core/elo.py`

**Responsibilities:**

1. **Two-sided exchange for decisive games** (§10.1)
   - `compute_rating_exchange(winner_rating: int, loser_rating: int) -> (RatingUpdate, RatingUpdate)`
   - K=24 flat for all bots, always
   - Exchange is **zero-sum**. It is **not swap-symmetric** — the underdog gains more than the favourite

2. **Two-sided exchange for draws** (§10.1)
   - `compute_draw_exchange(white_rating: int, black_rating: int) -> (RatingUpdate, RatingUpdate)`
   - K=24 flat
   - Exchange is **zero-sum**. Equal ratings move nothing

3. **One-sided exchange against anchors** (§10.3)
   - `compute_one_sided_exchange(competitor_rating: int, anchor_rating: int, competitor_score: float) -> RatingUpdate`
   - Anchor rating **never changes**
   - Net injection per game, but shrinks toward zero as competitor approaches anchor

4. **Constants**
   - `STARTING_RATING = 1200`
   - `K_FACTOR = 24`

**Normative:**
- §10.1: **Flat K=24 for all bots, always.** No two-tier K. "Provisional" is a leaderboard annotation for bots under 10 games — display only, never arithmetic.
- §10.1: **Elo is zero-sum** for competitor-vs-competitor. There is a property test asserting this across the rating range. It is **not swap-symmetric** and no test may assert that it is — swapping the two ratings changes the magnitude, which is the entire point of Elo.
- §10.3: **One-sided updates for anchors.** Competitor moves, anchor does not. This injects points into the pool per game but the injection shrinks as the competitor's rating approaches the anchor's. Combined with §9.3's ±400 gate, total injection over a workshop day is small and self-limiting.

---

### 2.4 `chess_core/matchmaker.py`

**Responsibilities:**

1. **Pure pairing function** (§9.2)
   - `pair_bots(pool: List[PoolEntry], seed: Optional[int]) -> List[Pairing]`
   - Algorithm:
     1. Sort by `games_played` ascending, then `rating` ascending
     2. Walk sorted list pairing **adjacent** entries
     3. Skip if same `owner` or if it repeats `last_opponent_id`
     4. Bot with `unpaired_ticks >= 3` has constraints dropped: same-owner first, then rematch
     5. **Colour precedence:** alternate from `last_color`; tie-break by lower `white_count`, then lower `bot_id`
   - Deterministic. The algorithm has **no random component**, so the result does not depend on `seed`. `seed` is retained only because the signature is pinned in the interfaces document; `pair_bots` must never call `random.seed`, which would mutate process-global state and break purity.

2. **Anchor gating** (§9.3)
   - `should_offer_anchor(bot: PoolEntry, anchor: PoolEntry, has_other_pairing_option: bool) -> bool`
   - Anchor offered **only** when competitor would otherwise sit idle
   - Only when `|rating − anchor_rating| ≤ 400`

3. **Constants**
   - `ANCHOR_RATING_WINDOW = 400`

**Normative:**
- §9.2: Input is an **explicit snapshot** (`List[PoolEntry]`) so the function stays pure and repeatable. No I/O, no clock reads, no global state.
- §9.2: Sorting by `games_played` first means new bots play within seconds. Sorting by rating second gives near-neighbour pairings.
- §9.2: **Colour precedence is explicit:** alternate from `last_color`. On conflict, lower `white_count` takes White; if tied, lower `bot_id`.
- §9.3: **Beyond ±400 the game is foregone** and the rating delta is negligible, so it is wasted board time.

---

### 2.5 `chess_core/match.py`

**Responsibilities:**

1. **State machine encoding** (§7)
   - `MatchState` dataclass: `status`, `ply`, `result`, `termination`
   - `create_match() -> MatchState` — initial: `pending` at ply 0

2. **Legal transitions** (§7)
   - `transition_to_active(state: MatchState) -> MatchState` — `pending → active` (first delivery)
   - `transition_after_move(state: MatchState, move_result: MoveResult) -> MatchState` — increments ply or transitions to `finished` if terminal
   - `transition_to_terminal(state: MatchState, termination: TerminationReason, result: Optional[GameResult]) -> MatchState` — covers flag, forfeit, resignation, abandonment, no-show, adjudication, admin abort, restart abort

3. **Validation helpers**
   - `is_terminal(state: MatchState) -> bool`
   - `can_transition(state: MatchState, to_status: GameStatus) -> bool` — validates against §7 diagram

**Normative:**
- §7: State transitions are: `[*] → pending → active → finished → [*]` and `pending → aborted → [*]`, `active → aborted → [*]`
- §7: Every terminal transition (in `chess_server/`) must delete both `seats` rows in the same transaction. You provide the pure state logic; the server enforces the seat invariant.

---

## 3. Normative Behaviour

### §6.4 ordering (clock.py)

**Unbreakable sequence:**

```
1. elapsed   = receive_mono − turn_started_mono
2. remaining = remaining − elapsed
3. if remaining_ns <= 0 → flag; game over; NO increment
4. apply move (may end the game by mate or draw)
5. if game continues → remaining += increment_ms
                        side switches
                        delivered_to_mover = 0
                        turn_started_mono = NULL
                        to_move_since_mono = now
```

**Implications:**
- Flag on **exact zero** — not "≤ 0", strictly `< 0` after deduction
- **No increment on flag** — step 3 ends the game before step 5
- **Flag precedes illegal-move validation** — step 3 precedes move application in step 4

### Delivery lifecycle (clock.py)

- `to_move_since_mono` — when the position **became available**
- `turn_started_mono` — when the position was **delivered** (NULL until delivery)
- `delivered_to_mover` — 0 or 1
- **Re-delivery is free:** the `delivered_to_mover=0` guard makes re-polling return the identical payload without restarting the clock

### Threefold detection (rules.py)

**Via position key, not full FEN.**

`position_key(fen)` returns the first four FEN fields: placement, side to move, castling rights, en passant target. Omits halfmove clock and fullmove number. Two positions with identical keys are the same position for threefold purposes.

Contract: threefold detection compares `position_key(fen)` strings, never full FEN strings.

### Flat K=24 and zero-sum property (elo.py)

**K=24 for all bots, always.** Two-tier K breaks Elo's zero-sum property, which both injects points into a closed 20-bot pool and contradicts the property test.

For competitor-vs-competitor games:
- `winner_delta + loser_delta = 0` (exactly)
- `white_delta + black_delta = 0` (exactly)
- The underdog gains more than the favourite

**The exchange is not swap-symmetric.** `compute_rating_exchange(1000, 1400)` gives the winner +22; `compute_rating_exchange(1400, 1000)` gives the winner +2. A test asserting `compute_rating_exchange(A, B) == negate(compute_rating_exchange(B, A))` cannot be made to pass and must not be written. Revisions 1–5 of this document claimed otherwise.

### One-sided anchor exception (elo.py)

Games against anchors (`ref-random`, `ref-greedy`, `ref-depth2`) are rated **one-sidedly**: competitor moves, anchor does not.

**This is a net injection of points per game**, but the injection shrinks toward zero as the competitor's rating approaches the anchor's. Combined with §9.3's ±400 gate and anchors only being offered when nobody else is free, total injection over a workshop day is small and self-limiting. It is not zero; the leaderboard is anchored rather than pure-zero-sum by design.

### 200-ply adjudication cap (rules.py)

At **200 ply** the game ends `adjudicated`, result **draw**, unconditionally. No material-based rule. This is what makes the cap evaluable by a student reading the code on a projector.

---

## 4. Seams You Produce

All signatures are pinned in **Interfaces Part 1**. Bind to them; do not invent or rename.

### Consumed by server-engineer (`chess_server/engine/`)

**From rules.py:**
- `validate_and_apply_move(fen, move_uci) -> MoveOutcome`
- `detect_termination(fen, history_fens) -> (is_terminal, reason, result)`
- `get_legal_moves(fen) -> List[str]`
- `uci_to_san(fen, move_uci) -> str`
- `position_key(fen) -> str`
- Constants: `STARTING_FEN`, `PLY_CAP`

**From clock.py:**
- `create_clock(time_control_ns, increment_ns, to_move, now_mono) -> ClockState`
- `deliver_position(clock, now_mono, ply) -> ClockState`
- `remaining_ns(clock, color, now_mono) -> int`
- `has_flagged(clock, now_mono) -> bool`
- `account_move_and_switch(clock, receive_mono, now_mono) -> ClockUpdateResult`
- `check_delivery_timeout(clock, now_mono, grace_ns) -> bool`
- `compute_turn_elapsed_ms(clock, now_mono) -> Optional[int]`
- `ms_to_ns(ms) -> int`, `ns_to_ms(ns) -> int`
- Constants: all `*_NS` constants

**From elo.py:**
- `compute_rating_exchange(winner_rating, loser_rating) -> (RatingUpdate, RatingUpdate)`
- `compute_draw_exchange(white_rating, black_rating) -> (RatingUpdate, RatingUpdate)`
- `compute_one_sided_exchange(competitor_rating, anchor_rating, competitor_score) -> RatingUpdate`
- Constants: `STARTING_RATING`, `K_FACTOR`

**From matchmaker.py:**
- `pair_bots(pool, seed) -> List[Pairing]`
- `should_offer_anchor(bot, anchor, has_other_pairing_option) -> bool`
- Constant: `ANCHOR_RATING_WINDOW`

**From match.py:**
- `create_match() -> MatchState`
- `transition_to_active(state) -> MatchState`
- `transition_after_move(state, move_result) -> MatchState`
- `transition_to_terminal(state, termination, result) -> MatchState`
- `is_terminal(state) -> bool`
- `can_transition(state, to_status) -> bool`

### Consumed by client-engineer (`starter-kit/`)

**Types and enums:**
- `Color`, `GameStatus`, `TerminationReason`, `GameResult` (shared types)
- `ClockView` dataclass (for `choose_move` signature)

**Constants:**
- `RATED_TIME_CONTROL_NS`, `RATED_INCREMENT_NS`
- `EXHIBITION_TIME_CONTROL_NS`, `EXHIBITION_INCREMENT_NS`
- `STARTING_RATING`

**From rules.py:**
- `fen_to_ascii(fen) -> str` — used by `arena.py` for game replay

**From clock.py:**
- Constants for SDK timeouts and defaults

### Consumed by arena.py

**All of the above**, plus:
- `san_list_to_pgn(...)` for PGN export

---

## 5. Seams You Consume

You should consume **almost nothing** but `python-chess`. State this explicitly:

- **python-chess library** — for `chess.Board`, `chess.Move`, move generation, legal-move validation, termination detection
- **Standard library only** — `dataclasses`, `enum`, `typing`. Not `random`: nothing in `chess_core` has a random component, and seeding the global RNG is the one purity violation this module has already shipped once.

**You do NOT consume:**
- `time.monotonic_ns()` — time is passed to you as a parameter
- `asyncio`, `sqlite3`, `fastapi`, `httpx` — you are pure
- Any file I/O or network calls
- Any other `chess_server/` or `starter-kit/` modules

---

## 6. Failure Modes and Edge Cases

Enumerate the specific cases that must be tested:

### Clock edge cases (clock.py)

1. **Flag on exact zero** — `remaining = 0` after deduction → flagged
2. **No increment on flag** — a flagged move does not add increment
3. **Rejected move does not reset the clock** — `turn_started_mono` is untouched by validation failure
4. **Undelivered position** — `check_delivery_timeout` returns `True` at exactly DELIVERY_GRACE_NS + 1ns
5. **Delivery grace at ply 0** — triggers `no_show` (tested by server, you provide the timeout check)
6. **Delivery grace mid-game** — triggers `abandoned` (tested by server, you provide the timeout check)
7. **Re-delivery idempotency** — calling `deliver_position` twice returns identical clock, `turn_started_mono` unchanged
8. **Side switch clears delivery** — `account_move_and_switch` sets `delivered_to_mover=0`, `turn_started_mono=NULL`

### Termination edge cases (rules.py)

1. **Threefold with differing halfmove clocks** — `position_key()` omits halfmove clock, so two FENs differing only in halfmove/fullmove are equal for threefold
2. **Insufficient material variants** — K+K, K+B vs K, K+N vs K (all cases `python-chess` handles)
3. **Fifty-move at exactly 50** — server claims via `can_claim_draw`, not by scanning history
4. **200-ply cap** — tested explicitly: game at ply 200 is adjudicated draw

### Elo edge cases (elo.py)

1. **Zero-sum property** — `winner_delta + loser_delta = 0` for all rating pairs
2. **Symmetry** — swapping ratings produces negated deltas
3. **Extreme rating gaps** — 1000 vs 2000, 800 vs 1600 (verify exchange is well-behaved)
4. **Anchor gating at exactly ±400** — `should_offer_anchor` returns `False` at 401, `True` at 400

### Matchmaker edge cases (matchmaker.py)

1. **Pairing with odd pool** — one bot left unpaired
2. **Pairing with one bot** — returns empty list
3. **Same owner blocks pairing** — skipped until `unpaired_ticks >= 3`
4. **Rematch blocks pairing** — skipped until `unpaired_ticks >= 3`
5. **Colour precedence determinism** — tie-break by `white_count`, then `bot_id`
6. **Pairing determinism** — same pool snapshot → identical pairings, whatever `seed` is passed

### Match state machine edge cases (match.py)

1. **Invalid transition** — `pending → finished` without going through `active` (should be rejected by `can_transition`)
2. **Terminal state is idempotent** — `transition_to_terminal` on an already-terminal state (caller responsibility, but validation helper must exist)

---

## 7. Test Obligations

Per Interfaces Part 4 and §18, these are the tests that **must exist**:

### `test_rules.py`

- `test_validate_legal_move` — e2e4 from starting position
- `test_validate_illegal_move` — e2e5 from starting position (not legal for White)
- `test_position_key_omits_halfmove_clock` — two FENs differing only in halfmove/fullmove produce identical keys
- `test_threefold_detection` — replay a position three times, assert `detect_termination` claims threefold
- `test_fifty_move_claim` — use `python-chess` `can_claim_draw` at exactly 50 halfmoves
- `test_insufficient_material` — K+K, K+B vs K, K+N vs K
- `test_200_ply_adjudication` — game at ply 200 is adjudicated draw
- `test_uci_to_san` — e2e4 → "e4", g1f3 → "Nf3"
- `test_fen_to_ascii` — starting position renders recognizable board

### `test_clock.py`

- **Table-driven tests for §6.4:**
  - `test_account_move_flags_on_timeout` — elapsed exactly equals remaining → flagged
  - `test_account_move_no_increment_on_flag` — flagged move does not add increment
  - `test_account_move_adds_increment_when_not_flagged`
  - `test_account_move_switches_side`
  - `test_account_move_clears_delivery` — `delivered_to_mover=0`, `turn_started_mono=NULL`
- `test_deliver_position_idempotent` — second delivery returns identical clock
- `test_check_delivery_timeout_at_grace` — exactly DELIVERY_GRACE_NS + 1ns
- `test_check_delivery_timeout_mid_game` — same but for mid-game scenario
- `test_compute_turn_elapsed_returns_none_when_undelivered`

### `test_elo.py`

- **Property test:** `test_elo_zero_sum` — sweep the rating range, assert `winner_delta + loser_delta = 0`
- `test_underdog_gains_more_than_the_favourite` — 1000 beats 1400 moves 22; 1400 beats 1000 moves 2
- `test_draw_exchange_zero_sum` — white_delta + black_delta = 0
- `test_draw_between_equal_ratings_moves_nothing` — swept, both deltas exactly 0
- `test_one_sided_exchange_competitor_only` — anchor rating unchanged
- `test_extreme_rating_gaps` — 1000 vs 2000, verify exchange is sane

### `test_matchmaker.py`

- `test_pair_adjacent_by_games_played` — new bot paired immediately
- `test_pair_skips_same_owner` — until `unpaired_ticks >= 3`
- `test_pair_skips_rematch` — until `unpaired_ticks >= 3`
- `test_colour_precedence` — alternate from `last_color`, tie-break by `white_count`, then `bot_id`
- `test_pairing_is_deterministic_and_ignores_seed` — same pool → one pinned pairing list, for every seed
- `test_pair_bots_does_not_mutate_the_global_rng` — purity
- `test_should_offer_anchor_within_400` — returns `True` at 400, `False` at 401
- `test_should_offer_anchor_only_when_idle` — `has_other_pairing_option=False`

### `test_match.py`

- `test_create_match_is_pending`
- `test_transition_pending_to_active`
- `test_transition_after_move_increments_ply`
- `test_transition_after_terminal_move_ends_game`
- `test_transition_to_terminal_finished`
- `test_transition_to_terminal_aborted`
- `test_can_transition_validates_state_machine`
- `test_is_terminal`

**No mocks. No fixtures.** Explicit test data a reader can follow on a projector.

---

## 8. Acceptance Criteria

How someone else verifies your track is done:

1. **All public functions have tests** — coverage ≥90% for `chess_core/`
2. **Property test passes** — Elo zero-sum and symmetry for 1000 random rating pairs
3. **Matchmaker determinism test passes** — identical output for identical input, independent of `seed`
4. **Table-driven clock tests pass** — all §6.4 ordering cases
5. **No I/O, no clock reads** — `grep -r "time.monotonic" chess_core/` returns nothing
6. **Unit suffix discipline** — every time field/parameter has `_ns` or `_ms`
7. **Threefold uses position key** — `grep "position_key" chess_core/rules.py` shows threefold detection
8. **200-ply cap test exists** — `grep -r "200" tests/chess_core/test_rules.py`
9. **All signatures match Interfaces Part 1** — no invented or renamed functions
10. **python-chess is the only move-generation dependency** — no hand-rolled validation

---

## 9. Requires Decision

### None identified

All signatures, constants, and behaviour are pinned in the design spec (§6, §7, §9.2, §9.3, §10.1, §10.3, §22) and Interfaces Part 1. If a signature you need is missing from the interfaces document, that is a spec bug — raise it rather than inventing it.

**Recommendation:** Proceed with implementation. All requirements are evaluable and complete.

---

## Summary

**Sections claimed:** §6 (clock and delivery), §7 (state machine), §9.2 (pairing algorithm), §9.3 (anchor gating), §10.1 (flat K=24), §10.3 (one-sided anchor case), §22 (termination rules, 200-ply cap), Interfaces Part 1 (all `chess_core` signatures), Interfaces Part 4 (`chess_core` test conventions).

**Seams produced:** 30+ public functions consumed by server-engineer, client-engineer, and arena.py. All pinned in Interfaces Part 1.

**Seams consumed:** `python-chess` only. No I/O, no network, no system clock reads.

**Requires decision:** None. All behaviour is specified precisely enough to build.

**Test count:** ~40 unit tests across five modules, including one property test and multiple table-driven tests. All pure, no mocks, no fixtures.
