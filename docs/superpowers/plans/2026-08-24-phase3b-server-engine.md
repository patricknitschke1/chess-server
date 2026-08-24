# Phase 3b — `chess_server/engine/`

**Date:** 2026-08-24
**Track:** server-engineer
**Scope:** move application and delivery, game creation and finalisation, the rating derivation, the matchmaking pool wiring, anchor execution, the reference bots, the supervised ticker and its eight steps, the supervisor.
**Ends when:** the whole game lifecycle runs from tests with no HTTP server: a scripted in-process competitor plays a complete game against a seeded anchor, driven by `_tick_once`, and every failure path in role spec §11 that does not need a route is covered.

**Not this phase.** FastAPI routes, long-poll and waiter mechanics, the SSE transport and coalescing, auth, rate limiting, admin routes, `/health` and `/state` — all phase 3c. Where the engine needs something 3c owns it takes an **injected callable** (see *Seams* below) and never imports `chess_server.api`.

## How to use this plan

This plan **references** the specs; it does not restate them. Code appears only where the exact text *is* the decision. If a step here disagrees with the role spec, the role spec wins and this plan is a bug — raise it rather than silently reconciling.

Authorities, in precedence order:

1. `docs/superpowers/specs/2026-08-23-chess-arena-design.md` — §4, §6, §7.1 **normative**; §9 (matchmaking), §10 (rating), §22 (termination) bear on the ticker.
2. `docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md` — Part 1 (`chess_core` signatures), Part 2 (SSE payloads).
3. `docs/superpowers/specs/roles/server-engineer-spec.md` — the build document. §§5, 6, 7, 9 cover all of 3b.
4. `AGENTS.md`.

## Rules for every task

- **Failing test first.** Write it, run it, watch it fail for the stated reason, then implement. **Failure paths before happy paths** (role spec §11).
- **Name the mutation.** Apply it, run, confirm red, revert, confirm green. Where a step's expected outcome is *no change*, this plan says so — a green run there proves nothing by itself.
- **If a plan-specified mutation cannot fail its test, stop and report it.** Phase 3a tasks 1–7 hit this three times. An unfalsifiable mutation means the test or the plan is wrong; both are findings. Do not quietly substitute another and move on — substitute one *and* report both.
- **Clear caches between mutation steps:** `find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +`. OneDrive/APFS mtime granularity has faked a mutation result here before.
- Every task ends with the whole suite green (`.venv/bin/pytest -q`) and the tree committable. Never pipe a test run through `tail` — it buffers and looks hung.
- **Always `.venv/bin/pytest` and `.venv/bin/python`.** Python 3.14.3, `asyncio_mode="auto"`. Baseline: **267 passing, clean at `f6da2bd`**.
- Import every constant from `chess_core`; never write a literal that has a name. `tests/chess_server/test_units.py` enforces this across `chess_server/` and must stay green.
- **The ticker calls only inner `*_locked` forms.** `tests/chess_server/test_locking_discipline.py` already greps for this and must stay green.
- **No `asyncio.sleep` in a ticker test.** Time is injected (task 1); ticks are driven by calling `_tick_once`.

## Layout this phase creates

```
chess_server/engine/__init__.py
chess_server/engine/deps.py            EngineDeps — the injected seams, incl. the clock
chess_server/engine/state.py           mailbox, history cache, unpaired_ticks, connected, clear_all
chess_server/engine/games.py           create/finalise/forfeit/abort, inner + outer forms
chess_server/engine/rating.py          the §6.6 derivation
chess_server/engine/runner.py          deliver_position_locked, apply_move_locked, inner + outer
chess_server/engine/pool.py            PoolEntry snapshot, anchor offer selection
chess_server/engine/reference_bots.py  RefRandom / RefGreedy / RefDepth2 + seeding
chess_server/engine/ticker.py          _tick_once, the eight steps, run_ticker, metrics
chess_server/engine/supervisor.py      the 5 s warning, the 15 s cancel-and-restart, db_writable
tests/chess_server/...                 one module per task
```

## Seams owned by phase 3c

The engine never imports `chess_server.api`. Each of these is a field on `EngineDeps` (task 1) with an inert default so every engine test runs headless:

| Seam | Signature | Default in 3b | 3c supplies |
|---|---|---|---|
| SSE fan-out | `sink(seq, event_type, data)` | `txn._drop` | the real SSE hub, which also stamps `is_featured` and applies `MOVE_COALESCE_NS` |
| wake a waiter | `wake(bot_id) -> None` | no-op | the poll waiter registry (role spec §5.4) |
| matchmaking paused | `is_paused() -> bool` | `lambda: False` | the `/admin/matchmaking` flag |
| the clock | `now_mono() -> int` | `time.monotonic_ns` | unchanged |

**The engine clears the mailbox; 3c populates it.** `state.mailbox` and its clearing points (side switch, every terminal transition, recovery) are 3b, because they are properties of the transaction. Writing a turn payload into it is 3c, because the payload is `TurnResponse` and needs `history_san`, which nothing in the engine's hot path has. An anchor never gets a mailbox entry at all (role spec §7.3).

**`move_played` is emitted without `is_featured`.** The engine emits committed facts; featured-game policy and the coalescing window live in `api/sse.py`.

---

## Task 1 — `EngineDeps`: the injected seams and the injectable clock

**Files:** `chess_server/engine/__init__.py`, `chess_server/engine/deps.py`, `tests/chess_server/conftest.py`, `tests/chess_server/test_deps.py`

A frozen-ish dataclass carrying `conn`, `executor`, `sink`, `wake`, `is_paused`, `now_mono`, with the defaults in the *Seams* table. Nothing else. Every engine function that needs the time takes it from `deps.now_mono()`; **no engine module calls `time.monotonic_ns` directly** except this default.

Add to `tests/chess_server/conftest.py`: a `FakeClock` (a callable with `.advance(ns)` and `.set(ns)`), a `deps` fixture built on the `store` fixture with a `FakeClock` and a recording `sink` (a list of `(seq, type, data)`), and a `seed_bots` helper that inserts N competitor rows through `BotRepo` inside a `critical_section`.

**Tests first:**
1. `deps.now_mono()` returns the `FakeClock`'s value, and returns the new value after `.advance()`.
2. A grep test: no file under `chess_server/engine/` other than `deps.py` contains the text `monotonic_ns`. (This test is worth almost nothing today and everything by task 10 — write it now so it is never retrofitted.)
3. The default `sink`, `wake` and `is_paused` are callable and inert.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_deps.py`

**Mutation:** make `EngineDeps.now_mono` ignore the constructor argument and always return `time.monotonic_ns()`. Test 1 must go red. (Mutating the *default* instead proves nothing — `time.time_ns` is also increasing.)

**Commit:** `phase3b: EngineDeps — injected sink, wake, pause flag and clock`

---

## Task 2 — Process state, and wiring it into recovery

**Files:** `chess_server/engine/state.py`, `tests/chess_server/test_engine_state.py`, `tests/chess_server/test_recovery.py` (extend)

Per role spec §5.1, §6.4, §9.3 and §8.6 step 5: four module-level containers — `mailbox: dict[int, object]`, `history: dict[int, list[str]]`, `unpaired_ticks: dict[int, int]`, `connected: set[int]` — plus `clear_all()` emptying all four in place (rebinding the names would orphan any reference recovery already holds).

`recovery.recover` already takes `clear_process_state` as a parameter and defers it past the commit. This task supplies `state.clear_all` as that argument at the one call site and tests the wiring.

**Tests first:**
1. `clear_all()` empties all four containers, and the container objects are the same objects afterwards (`id()` unchanged).
2. Recovery run with all four populated leaves all four empty.
3. Recovery whose transaction **rolls back** (force it by raising inside `recover_locked`) leaves all four populated — the clear is deferred, so a failed recovery must not have wiped in-process state the database still matches.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_engine_state.py tests/chess_server/test_recovery.py`

**Mutation:** drop `unpaired_ticks.clear()` from `clear_all`. Tests 1 and 2 must go red. Then separately rebind (`global mailbox; mailbox = {}`) instead of clearing — test 1's `id()` assertion must go red.

**Commit:** `phase3b: engine process state and its recovery wiring`

---

## Task 3 — `create_game_locked`

**Files:** `chess_server/engine/games.py`, `tests/chess_server/test_create_game.py`

Per role spec §7.2. `GameRepo.insert_game` already takes **nanoseconds** and owns the ns→ms conversion and `rated_at_creation`, so this helper is: insert the game, insert two seats, `txn.defer` the history cache seed to `[STARTING_FEN]`, `txn.emit('game_created', …)` per interfaces Part 2, and `txn.defer` a `wake` for each participant. It takes `white: BotRow, black: BotRow` (not ids) because `rated_at_creation` and the event payload both need the rows.

**Tests first, failure paths first:**
1. Creating a game for a bot that already holds a seat raises `sqlite3.IntegrityError`, and inside a `txn.savepoint` that rollback leaves the *other* pairing in the same transaction intact and leaves no orphan `games` row. This is the §7.1 seat-collision property and it is the reason this task exists before the ticker.
2. A rolled-back creation emits nothing and consumes no `seq` (assert the recording sink saw nothing and that the next successful emit's `seq` is the value the rolled-back one would have taken).
3. Happy path: `status='pending'`, `ply=0`, `to_move='white'`, `delivered_to_mover=0`, `turn_started_mono IS NULL`, two seats, `history[game_id] == [STARTING_FEN]`, one `game_created` event whose `data` carries both names, `rated`, `source`, `time_control_ms` and `increment_ms`.
4. `white_ms == black_ms == ns_to_ms(RATED_TIME_CONTROL_NS) == 180_000`, asserted **both** ways (role spec §11.8). A game with 5.7 years on the clock satisfies every other assertion here.
5. `rated` is 0 when either side is `role='benchmark'`, 0 when the owners match, 0 at exhibition time control, and 1 for two distinct-owner competitors at rated time control.
6. The history seed and the wakes happen only after commit: assert `history` is still empty *inside* the critical section and populated after it.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_create_game.py`

**Mutation:** pass `ns_to_ms(time_control_ns)` into `insert_game`'s `time_control_ns` parameter (the double-conversion bug that reads as "populated, non-null, decreasing"). Test 4 must go red. Then separately move the history seed out of `txn.defer` — test 6 must go red.

**Commit:** `phase3b: create_game_locked with seats, history seed and deferred wakes`

---

## Task 4 — `finalise_game_locked` and `abort_game_locked`, without rating

**Files:** `chess_server/engine/games.py`, `tests/chess_server/test_finalise.py`

Role spec §6.5, steps 1, 2 and 4–8. Step 3 (rating) is task 5 — build it here as a call to a `rate_game_locked` stub that does nothing, and use unrated games in this task's tests so the stub is never exercised.

Points a builder gets wrong silently:

- **Step 2 applies design §5.3 rule 1 and nothing else.** `no_show`, `server_restart` and `admin_abort` set `rated=0`; every other termination leaves it alone. `cas_terminate` already takes `rated: Optional[int]` and `COALESCE`s it — pass `0` for those three, `None` otherwise.
- **Step 4 uses `BotRepo.update_rating_and_counters(bot_id, rating, outcome)` for both participants even when nothing is rated** — pass the bot's *current* rating. There is no counters-only method and you must not add one.
- **Step 5 runs on aborts too.** Both participants get `update_pool_history`, White with `increment_white=True`.
- `abort_game_locked` is the same path with `status='aborted'`, `result=NULL`, `rated=0`, no rating step, no `rating_changed`.

**Tests first, failure paths first:**
1. Finalising a game whose `ply` has moved raises `CASConflict` and mutates nothing (assert seats still present, counters unchanged, sink silent).
2. Finalising twice: the second call raises `CASConflict`. Exactly one `game_ended` event exists.
3. `no_show`, `server_restart`, `admin_abort` each drive `rated` from 1 to 0; `checkmate` and `flag` leave `rated=1`.
4. `rated` never returns to 1: finalise a game created with `rated=0` under `termination='checkmate'` and assert it is still 0.
5. Happy path: `status`, `result`, `termination`, `ended_at` set, `delivered_to_mover=0`, `turn_started_mono IS NULL`, both `seats` rows gone, both bots' `games_played` up by one and the right one of `wins`/`losses`/`draws`, both bots' `last_color` / `last_opponent_id` / `white_count` updated.
6. Deferred work runs only on commit: both mailboxes cleared, `history[game_id]` dropped, both `unpaired_ticks` entries dropped, `wake` called for both — and none of that has happened yet while still inside the critical section.
7. `game_ended` payload matches interfaces Part 2 field for field, and carries the **post-rule-1** `rated`.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_finalise.py`

**Mutation:** move step 1's CAS to a plain `UPDATE … WHERE id=?`. Tests 1 and 2 must go red. Then separately drop `update_pool_history` for the losing side — test 5 must go red. Then separately add `no_show` to the terminations that leave `rated` alone — test 3 must go red.

**Commit:** `phase3b: finalise_game_locked and abort_game_locked`

---

## Task 5 — The rating derivation

**Files:** `chess_server/engine/rating.py`, `chess_server/engine/games.py`, `tests/chess_server/test_rating.py`

Role spec §6.6, design §10.2–§10.3. `rating.py` holds one function that takes the two `BotRow`s and the `GameResult` and returns the list of `(bot_id, RatingUpdate)` to persist — zero, one or two entries. `games.py` persists them, updates `bots.rating`, inserts `rating_history` and emits one `rating_changed` per row **actually inserted**.

The three cases, and the two that are silently wrong if guessed:

- **Both competitors:** decisive → `compute_rating_exchange(winner_rating, loser_rating)`; draw → `compute_draw_exchange(white_rating, black_rating)`. Two rows, zero-sum.
- **Exactly one anchor:** the competitor is the participant with `is_anchor == 0`. `competitor_score` is `1.0` when `result` names the competitor's **colour**, `0.0` when it names the opponent's, `0.5` on a draw. `compute_one_sided_exchange(competitor_rating, anchor_rating, competitor_score)`. **One row, for the competitor only.** The anchor's rating is not written and it gets no history row.
- **Both anchors:** `InvariantViolation`, not a rating case.

Do **not** branch around draws to avoid calling `compute_one_sided_exchange` — it raises `ValueError` on anything but 1.0/0.5/0.0, and that is the guard.

**Tests first:**
1. Both anchors raises `InvariantViolation`.
2. An unrated (`rated=0`) game produces zero `rating_history` rows and zero `rating_changed` events, for every result value.
3. Anchor as White winning: the competitor (Black) gets exactly one row with a negative delta; the anchor's `bots.rating` is bit-identical to before and it has no row. Repeat with the anchor as Black. Both orientations, because a colour-keyed derivation passes one and fails the other.
4. A **draw** against an anchor produces one row for the competitor, with a non-zero delta whose sign follows the rating gap (draw against a stronger anchor gains, weaker loses). Assert the sign both ways.
5. Competitor vs competitor, decisive and drawn: two rows, deltas summing to zero, `rating_before + delta == rating_after` for each.
6. Double-rating one game raises `sqlite3.IntegrityError` from `UNIQUE (game_id, bot_id)`.
7. `bots.rating == STARTING_RATING + sum(rating_history.delta)` for every `role='competitor'` bot after a mixed sequence of ten finalisations — the §8.5 identity, asserted here rather than waiting for the route.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_rating.py`

**Mutation:** define the competitor as "White unless White is the anchor" (i.e. keep the anchor's row too). Test 3 must go red on both orientations. Then separately map a draw to `competitor_score=0.0` — test 4 must go red. Then separately skip the rating call entirely for draws against an anchor — test 4 must go red again, for a different reason; if it does not, test 4 is asserting existence rather than value.

**Commit:** `phase3b: one-sided, two-sided and draw rating on finalisation`

---

## Task 6 — `deliver_position_locked`

**Files:** `chess_server/engine/runner.py`, `tests/chess_server/test_deliver.py`

Role spec §5.2. `GameRepo.cas_deliver` already returns `(delivered, started)` and deliberately never calls `assert_cas` — `rowcount == 0` means "already delivered", which is free by design. This helper wraps it: on `started`, emit `game_started` (interfaces Part 2); on `delivered == False`, do nothing at all and let the caller re-read the row.

Also add the outer form `deliver_position(deps, bot_id)` — one `critical_section`, resolving the game through `seats` (`GameRepo.get_for_bot`), never by scanning `games`.

**Tests first:**
1. **Re-delivery never touches the clock.** Deliver, advance the fake clock 5 s, deliver again: `turn_started_mono` is bit-identical and no second `game_started` is emitted. This is the exploit closure — a bot must not be able to re-poll while thinking and reset its own clock.
2. Delivery on a `finished` game is a no-op: `rowcount == 0`, no event, no exception.
3. First delivery moves `pending → active` in the **same statement**, sets `started_at`, and emits exactly one `game_started`. Assert `status` and `delivered_to_mover` together — a split implementation leaves `delivered_to_mover=1` with `status='pending'`.
4. Delivery to a bot with no seat returns a "no game" outcome rather than raising.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_deliver.py`

**Mutation:** emit `game_started` on every delivery rather than only when `started` is true. Test 1 must go red. Then separately make the helper call `assert_cas` on the delivery UPDATE — test 2 must go red with `CASConflict`.

**Commit:** `phase3b: idempotent delivery and the pending -> active transition`

---

## Task 7 — `apply_move_locked`, steps 1–4: the pre-validation gates

**Files:** `chess_server/engine/runner.py`, `tests/chess_server/test_apply_move_gates.py`

Role spec §6.1 steps 1–4, **and the order is normative**. Implement the function's skeleton returning a small result union (`Applied` / `Rejected` / `NotDelivered` / `Flagged` / `WrongController`); steps 5–10 land in task 8 and may raise `NotImplementedError` until then.

1. CAS-read on `id` + `ply` + `status IN ('pending','active')`; no row → `CASConflict`.
2. `controller` checked **in this transaction**, not as a pre-check.
3. `delivered_to_mover == 1` required. Never deliver implicitly — that would let a bot start its own clock by submitting a move, and it is the only thing keeping `account_move_and_switch` from raising `ValueError` inside a critical section.
4. `has_flagged(_clock_from_game(game), now_mono)` **before validation** → `finalise_game_locked(..., opposite_win(clock.to_move), 'flag')`.

`chess_server` computes no remaining time itself: `has_flagged` is the only expression of design §6.4's `<= 0`.

**Tests first, all failure paths:**
1. Submitting with a stale `ply` raises `CASConflict`; the game is untouched.
2. Submitting while `controller='agent'` returns `WrongController` and mutates nothing — including after a `controller` change committed between the read and the call.
3. Submitting an **undelivered** position returns `NotDelivered` and does not deliver, does not set `turn_started_mono`, and does not raise `ValueError`.
4. **Flag precedes validation** (role spec §11.7): a bot whose clock has expired submits an *illegal* move. Assert `termination='flag'`, `result` is the opponent's win, `white_strikes`/`black_strikes` are both still 0, and the termination is not `illegal_forfeit`.
5. Same as 4 with a *legal* move — still `flag`, and no `moves` row is written.
6. No engine module subtracts monotonic timestamps: extend the task-1 grep to assert `turn_started_mono` never appears adjacent to a `-` in `chess_server/engine/`, or simpler and stronger, assert `has_flagged` is the only flag predicate by grepping for `remaining` and `<= 0` outside `chess_core`.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_apply_move_gates.py`

**Mutation:** swap steps 4 and 5 so validation runs before the flag check. Test 4 must go red with `illegal_forfeit` or a strike. Then separately make step 3 deliver instead of refusing — test 3 must go red.

**Commit:** `phase3b: apply_move gates — CAS, controller, delivery, flag before validation`

---

## Task 8 — `apply_move_locked`, steps 5–10: rejection, accounting, persistence

**Files:** `chess_server/engine/runner.py`, `chess_server/engine/games.py` (`forfeit_game_locked`), `tests/chess_server/test_apply_move.py`

Role spec §6.1 steps 5–10 and §6.2. Termination detection and the history cache are task 9; here, call `detect_termination` with the history the cache already holds plus the candidate `fen_after`, and leave the cache-correctness tests to task 9.

The two rules that are silent when wrong:

- **An illegal move commits.** Increment the mover's strike column and **return** a `Rejected` result carrying `legal_moves`, `fen`, the new strike count and whether the third strike forfeited. Raising through `critical_section` would take the strike increment with it and design §8.3's three-strike rule would silently not exist.
- **A rejected move does not stop the clock** and does not reset `turn_started_mono`. Time on illegal attempts is charged cumulatively.

Step 8 passes **the same `now_mono`** to `has_flagged` (task 7) and to `account_move_and_switch`; a fresh reading between them reintroduces the race the atomic helper removes.

**Tests first, failure paths first:**
1. One illegal move: `Rejected`, strike count 1 **persisted after the transaction commits**, `ply` unchanged, `turn_started_mono` unchanged, no `moves` row, no `move_played`.
2. Three illegal moves in one game → `termination='illegal_forfeit'`, opponent wins, game finalised in the same transaction as the third strike (role spec §11.6).
3. Strikes are per game: a bot with two strikes in game *n* starts game *n+1* at 0.
4. Illegal attempts are charged: after two rejections separated by 3 s of fake clock, the mover's `white_ms` after the eventual legal move is lower by the whole elapsed span, not just the last leg.
5. Happy path: `moves` row with `server_elapsed_ms == result.elapsed_ms` and the caller's `client_reported_ms` stored separately; `games` CAS applied; `ply + 1`; `to_move` taken from the **FEN**, not parity; `delivered_to_mover` back to 0; `turn_started_mono` NULL; `to_move_since_mono` refreshed; one `move_played` matching interfaces Part 2 minus `is_featured`.
6. **The mover's mailbox is cleared on the side switch** (role spec §5.3): put a sentinel in `state.mailbox[mover_id]`, play a legal move, assert it is gone after commit and still present if the transaction rolls back.
7. A terminal move (back-rank mate from a crafted FEN) finalises in the same transaction: one `move_played` then one `game_ended`, seats gone, `ply` reflecting the mating move.
8. Duplicate ply is impossible: force two inserts for the same `(game_id, ply)` and assert `IntegrityError`.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_apply_move.py`

**Mutation:** raise `IllegalMove` instead of returning `Rejected`. Tests 1 and 2 must go red — and note *how*: the strike count reads 0, not 1. Then separately reset `turn_started_mono` on rejection — test 4 must go red. Then separately drop the mailbox clear — test 6 must go red.

**Commit:** `phase3b: apply_move — strikes commit, clock accounting, persistence`

---

## Task 9 — `history_fens`, threefold and the ply cap

**Files:** `chess_server/engine/runner.py`, `tests/chess_server/test_history_and_cap.py`

Role spec §6.4 and §6.1 steps 6–7, interfaces Part 1. **The contract:** `history_fens` is `[STARTING_FEN] + [fen_after for each ply in order]`, **including the position just reached** — `SELECT fen_after FROM moves ORDER BY ply` omits ply 0 and never claims the commonest repetition there is.

The cache is seeded in `create_game_locked` (task 3), appended **through `txn.defer`**, and dropped at every terminal transition. Step 6 therefore reads committed history and appends the candidate `fen_after` itself rather than trusting the cache to already contain it.

Build the `MoveResult` from `detect_termination`'s answer, **not** from `validate_and_apply_move`'s `is_terminal`, which cannot see threefold. Then `transition_after_move(MatchState(ACTIVE, game.ply, None, None), move_result)` — constructed immediately before and discarded immediately after, never stored, and the only place `PLY_CAP` is applied.

**Tests first:**
1. **Threefold from the starting position** (role spec §11.9): play `Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8` through the outer `apply_move`, assert `termination='threefold'`, `result='draw'`. Assert separately that a history built from the `moves` table alone returns `(False, None, None)` for the same position — that second assertion is what makes the first one about the *contract* rather than about luck.
2. A rolled-back move leaves the cache unchanged: force a failure after the append is deferred, assert `len(history[game_id])` is what it was.
3. Fifty-move: from a crafted FEN with `halfmove_clock` at 99, one quiet move gives `termination='fifty_move'`.
4. **The cap does not beat a mate.** From a crafted position, arrange `game.ply == PLY_CAP - 1` and deliver mate: `termination='checkmate'`, not `adjudicated`.
5. The cap fires otherwise: at `ply == PLY_CAP - 1` a quiet move gives `termination='adjudicated'`, `result='draw'`.
6. The cache is dropped at every terminal transition including `abort_game_locked`.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_history_and_cap.py`

**Mutation:** seed the cache with `[]` instead of `[STARTING_FEN]`. Test 1 must go red. Then separately build `MoveResult` from `validate_and_apply_move`'s `is_terminal` — test 1 must go red again (the game grinds on). Then separately swap `transition_after_move`'s terminal/cap order by checking the cap first in the caller — test 4 must go red.

**Commit:** `phase3b: history_fens contract, threefold and the ply cap`

---

## Task 10 — Reference bots and anchor seeding

**Files:** `chess_server/engine/reference_bots.py`, `tests/chess_server/test_reference_bots.py`

The single exception to "no untrusted code runs on the server" (design §9.3) — because we wrote it. Three bots with the signature `choose_move(board: chess.Board, clock: ClockView) -> chess.Move`, using `chess_core.types.ClockView`. **Do not import from `starter-kit/`**: it is not an installed package and the server must not depend on attendee-facing code. Port the logic — `ref_depth2`'s fixed-perspective negamax is the corrected version and the sign convention is load-bearing (a perspective keyed on `board.turn` is wrong at terminal nodes, which return before the flip).

| name | class | rating | source |
|---|---|---|---|
| `ref-random` | `RefRandomBot` | 800 | `starter-kit/ref_bots/ref_random.py` |
| `ref-greedy` | `RefGreedyBot` | 1000 | `starter-kit/ref_bots/ref_greedy.py` |
| `ref-depth2` | `RefDepth2Bot` | 1200 | `starter-kit/ref_bots/ref_depth2.py` |

Every rating is a **placeholder, not a measurement**; calibration is deferred (design §21) and the docstrings must say so.

`seed_anchors_locked(txn)`: for each entry, `BotRepo.get_by_name`; insert only if absent, with `role='anchor'`, `is_anchor=1`, `controller='client'`, `owner='server'`, and a `token_hash` of a freshly generated secret **immediately discarded** — no token can ever authenticate as an anchor, and `token_hash` is `NOT NULL`. Plus the outer form, called from the lifespan after recovery and before the ticker starts.

**Tests first:**
1. `ref-random` seeded with a fixed `random.Random` is reproducible, and every bot returns a **legal** move on: the start position, a position with exactly one legal move, and a position where the only legal moves are captures.
2. `ref-greedy` takes a free queen; `ref-depth2` finds a mate in one from a crafted FEN and does **not** hang material to a one-move recapture that `ref-greedy` falls for. (Do not test perspective by mirroring the board — tie-breaks are not mirror-invariant and that produced 23/66 false "asymmetries" on correct code.)
3. Seeding is idempotent: run it twice, assert three anchors, and assert a rating manually changed between runs is **not** overwritten.
4. Seeded anchors have `role='anchor'`, `is_anchor=1`, `last_poll_mono IS NULL`, and are absent from `BotRepo.list_leaderboard()`.
5. No anchor's `token_hash` matches the sha256 of any string the test can construct — assert only that the three hashes are distinct and non-empty, and that no plaintext token is returned by the seeding API (it returns nothing).

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_reference_bots.py`

**Mutation:** make `seed_anchors_locked` insert unconditionally. Test 3 must go red with `IntegrityError` on `bots.name`. Then separately set `role='competitor'` on the anchors — test 4 must go red, and note that it goes red on the leaderboard assertion specifically.

**Commit:** `phase3b: reference bots and idempotent anchor seeding`

---

## Task 11 — The ticker skeleton: one transaction, a savepoint per unit, and it never dies

**Files:** `chess_server/engine/ticker.py`, `tests/chess_server/test_ticker_core.py`

Role spec §7 and §7.1. Build the frame before any step:

- `TickerMetrics` — `tick_number`, `last_tick_mono`, `last_tick_duration_ms`, `consecutive_tick_errors`, `ticker_restarts`. Written by the ticker, read by the supervisor and (in 3c) by `/health`.
- `async def _tick_once(deps, metrics) -> None` — **the single-step entry point every test drives.** One `critical_section`. Runs the eight steps in order. Records the metrics at the end.
- `async def run_ticker(deps, metrics)` — `while True:` calling `_tick_once` inside `try/except Exception`, logging with the tick number, incrementing `consecutive_tick_errors`, then `await asyncio.sleep(TICK_INTERVAL_NS / 1e9)`. **The loop never exits.** `consecutive_tick_errors` resets to 0 on a clean tick.
- `async def _unit(txn, name)` — the per-unit wrapper: `txn.savepoint(name)`, swallow `CASConflict` and `sqlite3.IntegrityError`, re-raise everything else. Every step body uses it, and this is the only place the swallow is written.

Design §4.3's argument for pairing holds for every other per-game action: without per-unit savepoints one conflict at the flag step discards every pairing and challenge that tick, silently, because a rolled-back CAS is not an error.

Register steps as an ordered list of coroutines so tasks 12–17 append to it and tests can run a single step in isolation.

**Tests first:**
1. **Savepoint isolation** (role spec §11.2): a tick containing three units where the middle one raises `CASConflict` commits the outer two, emits events for the outer two only, and consumes **no `seq`** for the middle one — assert the surviving events' `seq` values are contiguous.
2. `sqlite3.IntegrityError` in one unit behaves identically (this is the seat-collision path).
3. A step raising something that is *not* a CAS conflict propagates out of `_tick_once`, `run_ticker` catches it, `consecutive_tick_errors` becomes 1, and the next tick still runs.
4. `consecutive_tick_errors` returns to 0 after a clean tick.
5. `_tick_once` opens exactly one `critical_section` (assert via the AST helper `test_locking_discipline.py` already has, or by counting `write_lock` acquisitions with a wrapper).
6. Metrics update every tick: `tick_number` increments, `last_tick_mono` equals the injected clock.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_ticker_core.py tests/chess_server/test_locking_discipline.py`

**Mutation:** remove the `savepoint` from `_unit` (keep the swallow). Test 1 must go red — and it must go red on the *surviving* units' data, not on the exception. Then separately let `_unit` swallow bare `Exception` — test 3 must go red.

**Commit:** `phase3b: supervised ticker frame with a savepoint per unit of work`

---

## Task 12 — Step 4: the delivery-grace sweep

**Files:** `chess_server/engine/ticker.py`, `tests/chess_server/test_tick_delivery_grace.py`

Role spec §7.4. Built before the earlier-numbered steps because it is the step whose omission wedges the ticker while `last_tick_age_ms` stays green.

`GameRepo.list_undelivered_non_terminal` **already filters `status IN ('pending','active')`** — do not replace it with a broader query. Without the filter every finished game re-enters the sweep forever (the side switch leaves `delivered_to_mover = 0`), its CAS returns rowcount 0, and pairing stops permanently with nothing logged.

The grace depends on the **bot to move**, not the game: read the mover with `BotRepo.get_by_id` inside the unit and use `AGENT_DELIVERY_GRACE_NS` when `controller='agent'`, else `DELIVERY_GRACE_NS`. Decide with `check_delivery_timeout(_clock_from_game(game), now_mono, grace_ns)`. **ply 0** → `abort_game_locked(txn, game, 'no_show')`; **mid-game** → `finalise_game_locked(txn, game, opposite_win(game.to_move), 'abandoned')`. **The server never writes `crash`** (design §22).

**Tests first:**
1. **The sweep terminates** (role spec §11.3): finish a game, then run twenty ticks. Assert the finished game is never returned by `list_undelivered_non_terminal`, `consecutive_tick_errors` stays 0, and a pairing still happens on tick 20. Drive all twenty with the fake clock; no sleeping.
2. Undelivered at ply 0, clock advanced past `DELIVERY_GRACE_NS` → `aborted`, `no_show`, `rated=0`, both seats gone, neither rating moved.
3. Undelivered mid-game → `finished`, `abandoned`, opponent wins, rated normally.
4. An **agent-controlled** mover gets 60 s, not 15 s: at 30 s nothing happens; at 61 s it aborts. Both halves matter — the first is the one a hard-coded grace fails.
5. A **delivered** position is never swept, however long it sits — abandonment applies only while `delivered_to_mover = 0`, so the clock and the sweep can never race.
6. One game's `CASConflict` in this sweep does not stop the other games in the same tick from being swept.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_tick_delivery_grace.py`

**Mutation:** query `WHERE delivered_to_mover = 0` with no status filter. Test 1 must go red — and note *how*: `consecutive_tick_errors` climbs, or the sweep keeps returning the finished game. If test 1 stays green, it is not testing the wedge. Then separately use `DELIVERY_GRACE_NS` for every mover — test 4's 30 s half must go red.

**Commit:** `phase3b: delivery-grace sweep with no_show and abandonment`

---

## Task 13 — Step 5: the flag sweep

**Files:** `chess_server/engine/ticker.py`, `tests/chess_server/test_tick_flag.py`

Role spec §7.5. `GameRepo.list_delivered_active` gives the candidates. For each, in its own unit: `has_flagged(_clock_from_game(game), now_mono)` → `finalise_game_locked(txn, game, opposite_win(game.to_move), 'flag')`.

`chess_server` computes no remaining time. `has_flagged` is the single declaration of design §6.4's `<= 0` predicate and it exists precisely so the rule is not hand-written here and again in `apply_move_locked`.

**Tests first:**
1. A game delivered and then left for `RATED_TIME_CONTROL_NS + 1` of fake monotonic time flags on the next tick: `finished`, `flag`, opponent wins, rated, seats freed, one `rating_history` row per rated participant (role spec §11.8's second half).
2. A game delivered 1 ms ago does not flag. Expected outcome: **no change** — pair this with test 1 rather than reading it as proof on its own.
3. Exactly at zero remaining: flags (`<= 0`, not `< 0`).
4. An undelivered active game is not a flag candidate even if `to_move_since_mono` is ancient — that is task 12's sweep, and the two must not both fire.
5. Both colours flag correctly: build the same scenario with Black to move.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_tick_flag.py`

**Mutation:** change the predicate to `remaining < 0` by inlining it in the ticker instead of calling `has_flagged`. Test 3 must go red. Then separately drop the `delivered_to_mover = 1` filter — test 4 must go red.

**Commit:** `phase3b: flag sweep via has_flagged`

---

## Task 14 — Step 2: the pool snapshot, pairing, and the anchor offer

**Files:** `chess_server/engine/pool.py`, `chess_server/engine/ticker.py`, `tests/chess_server/test_matchmaking.py`

Role spec §7.2 (matchmaking half) and §9, design §9.1–§9.4. The largest task in the phase and the one with the most ways to be silently inert.

**The snapshot.** `BotRepo.list_pool_candidates(cutoff_mono)` already encodes §9.1 — roles, no seat, `controller='client'`, and poll recency scoped to competitors only. Compute the cutoff with `window_start_mono(now_mono, POLL_RECENCY_NS)`; do not subtract. Build one `PoolEntry` per row with **every field supplied truthfully**: `last_color` from `bots.last_color` (as a `Color`, or `None`), `white_count`, `last_opponent_id`, `is_anchor`, and `unpaired_ticks` from `state.unpaired_ticks`. A builder who cannot find a field and passes `0` produces a silent deadlock, not an error.

**Pairing.** Skip the whole step when `deps.is_paused()`. Split the snapshot into competitors and anchors. `pair_bots(competitors)` — anchors are **not** passed in, so `pair_bots` never sees an anchor-vs-anchor option.

**The anchor offer** — every §9.3 rule except anchor-vs-anchor lives in `should_offer_anchor`, and a loop that calls `pair_bots` alone enforces none of them. For each competitor `pair_bots` left unpaired, in `(games_played, rating, bot_id)` order (that is what "fewest-games eligible bot" means), try each remaining anchor in order of `|rating − anchor.rating|` and take the first for which `should_offer_anchor(competitor, anchor, has_other_pairing_option=False)` is true. `False` is correct by construction: this bot was not paired this tick, which is precisely "would otherwise sit idle". Remove an anchor from the candidate list once used.

**Colours for an anchor pairing** come from `pair_bots([competitor, anchor])` on that two-element pool — §9.2's colour precedence stays in one place.

**`unpaired_ticks`** is in-process ticker state: reset to 0 for every bot paired this tick, incremented for every bot in this tick's snapshot that appears in no pairing, and deleted when a bot takes a seat (task 4 step 7 already deletes it). Left at zero, `_allowed` never relaxes and design §9.2's lone-attendee-with-two-bots case never pairs, with `pooled_bots: 2`, `active_games: 0` and nothing logged.

**Tests first:**
1. **`should_offer_anchor` is actually called**: one competitor and one anchor in an otherwise empty pool. Gap 200 (competitor 1200, anchor `ref-greedy` at 1000) → a game is created. Gap 500 (competitor 1300, anchor `ref-random` at 800) → **no game is created**. The refusal case is the whole point: `pair_bots` on a two-element pool would happily pair them.
2. Two competitors and one anchor, all pairable: the two competitors pair with **each other** and the anchor is not offered — the offer is idle-only.
3. **Two bots, one owner** (design §9.2's motivating case): no pairing on ticks 1–3, and a pairing on the tick where `unpaired_ticks` reaches 3. Assert `state.unpaired_ticks` actually incremented — a test that only asserts "eventually paired" passes on a build that pairs immediately.
4. `unpaired_ticks` resets to 0 on pairing and the entry is gone once the bot holds a seat.
5. Anchors are never paired with each other, even as the only two pool members.
6. A competitor whose `last_poll_mono` is older than `POLL_RECENCY_NS` is not in the pool; an **anchor** with `last_poll_mono IS NULL` **is**. The second half is what makes the anchor path reachable at all.
7. `deps.is_paused() == True` creates no games and does not touch `unpaired_ticks`.
8. Colours follow §9.2: a bot whose `last_color='white'` gets Black next; ties break on `white_count` then `bot_id`. Assert on an anchor pairing too, to prove the two-element `pair_bots` call is the colour source.
9. Every `PoolEntry` field matches its `bots` row — a direct field-by-field assertion, because this is where zeros get passed.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_matchmaking.py`

**Mutation:** hard-code `unpaired_ticks=0` in the snapshot builder. Test 3 must go red. Then separately drop the `should_offer_anchor` call and offer the nearest anchor unconditionally — test 1's refusal case must go red. Then separately pass the full pool (competitors + anchors) to `pair_bots` — test 2 must go red.

**Commit:** `phase3b: pool snapshot, pairing and the gated anchor offer`

---

## Task 15 — Step 1: queued challenges, and the seat race

**Files:** `chess_server/engine/ticker.py`, `tests/chess_server/test_tick_challenges.py`

Role spec §7.2 (challenge half), design §12. Challenges are consumed **before** pairing, so an accepted challenge always beats matchmaking to the seat.

For each `ChallengeRepo.list_queued()` row, in its own unit: both bots must have no `seats` row **and** `controller='client'`, unless the challenge is exhibition (design §13.3 — compare `challenge.time_control_ms` against `ns_to_ms(EXHIBITION_TIME_CONTROL_NS)`). Otherwise `cas_set_status(..., 'expired', reason='seat_unavailable')` and emit `challenge_updated` — **never a silent drop**. If both are free, `create_game_locked(..., source='challenge')` then `cas_set_status(..., 'consumed', game_id=...)`. Time control comes from the challenge row (`ms_to_ns` at the boundary), not from the rated constants.

**Tests first:**
1. Opponent already seated → `expired`, `reason='seat_unavailable'`, one `challenge_updated` carrying that reason, no game.
2. Challenger `controller='agent'` on a **rated** challenge → same. On an **exhibition** challenge → consumed.
3. **Seat collision** (role spec §11.12): a queued challenge and a matchmaking pairing that both want the same bot in one tick yield exactly one game; the loser is `expired` with `reason='seat_unavailable'` and says so on the wire. Assert no orphan `games` row and no stray `seats` row — `PRAGMA foreign_keys=ON` forces the game insert before its seats, so an unhandled collision commits both.
4. Consumption precedes pairing: with a queued challenge and a pool that would otherwise pair those two bots differently, the challenge wins.
5. A consumed challenge's game carries the challenge's own `time_control_ms` / `increment_ms`, and an exhibition game is `rated=0`.
6. A `CASConflict` on `cas_set_status` rolls back only that challenge's unit.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_tick_challenges.py`

**Mutation:** move challenge consumption after matchmaking. Test 4 must go red. Then separately drop the `challenge_updated` emit on the `seat_unavailable` path — test 1 must go red on the event, not on the status.

**Commit:** `phase3b: queued-challenge consumption and seat-collision isolation`

---

## Task 16 — Step 3: anchors move

**Files:** `chess_server/engine/ticker.py`, `tests/chess_server/test_tick_anchor_moves.py`

Role spec §7.3, design §9.3. Without this step the three reference bots are dead code, every anchor game `no_show`s fifteen seconds after creation, and a lone attendee never gets a game.

For every row from `GameRepo.list_anchor_to_move()` (already filtered to `status IN ('pending','active')` with the mover an anchor), in its own unit:

1. `deliver_position_locked(txn, game, now_mono)` — the same idempotent UPDATE every other delivery uses. This starts the clock and moves `pending → active`. **No mailbox entry and no waiter**: the mailbox is the transport for HTTP clients only.
2. Call the bot's `choose_move(board, clock_view)` in process, building the `board` from `game.fen` and the `ClockView` from the game row (`my_ms` is always the mover's).
3. `apply_move_locked(txn, game, game.ply, move.uci(), client_reported_ms=None, now_mono=deps.now_mono())` — **the same locked move path a client's move takes.** Not a shortcut and not a second move implementation; the anchor is charged real time exactly as a network client is.

**The in-process call is itself the delivery** — there is no deliver-then-wait for a bot that cannot poll. If `choose_move` raises, log at ERROR with the game id and FEN, roll back the unit, continue; the rollback undoes the delivery too, so task 12's sweep abandons the game rather than the ticker wedging.

**Tests first:**
1. `choose_move` raising rolls back the unit: the game is still `pending`, `delivered_to_mover=0`, no `moves` row, no `game_started` event, `consecutive_tick_errors` still 0, and the next tick's other units still commit.
2. Following that, the abandonment sweep aborts the game `no_show` once the grace elapses — the failure is self-limiting.
3. **Anchors play** (role spec §11.5): one competitor against `ref-random` with nobody else in the pool. Over successive `_tick_once` calls plus scripted competitor moves through the outer `apply_move`, the game reaches `active` **with no poll from the anchor**, the anchor's move appears in `moves`, and the game completes.
4. The anchor's move went through `apply_move_locked`: assert `server_elapsed_ms` on the anchor's `moves` row is present and consistent with the fake clock's advance, and that `white_ms`/`black_ms` moved. A second move path would leave the clock untouched.
5. Two anchor games in one tick both move; one raising does not stop the other.
6. An anchor to move in a `finished` game is never a candidate.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_tick_anchor_moves.py`

**Mutation:** delete step 1 (the delivery) and go straight to `choose_move` + `apply_move_locked`. Test 3 must go red — and note *how*: `apply_move_locked` returns `NotDelivered`, and the game later `no_show`s. Then separately bypass `apply_move_locked` and write the `games` row directly — test 4 must go red.

**Commit:** `phase3b: anchors deliver to themselves and move through the locked path`

---

## Task 17 — Steps 6–8: agent auto-release, challenge TTL, presence

**Files:** `chess_server/engine/ticker.py`, `tests/chess_server/test_tick_housekeeping.py`

Role spec §7.5. Three small steps, one task, and none of them subtracts monotonic timestamps.

**Auto-release.** For each `BotRepo.list_agent_controlled()` row, release when `not is_within(bot.last_agent_action_mono, now_mono, AGENT_AUTO_RELEASE_NS)`: set `controller='client'` and `txn.defer(lambda: deps.wake(bot_id))`. `AGENT_AUTO_RELEASE_NS` (45 s) sits deliberately below `AGENT_DELIVERY_GRACE_NS` (60 s); reversed, task 12's grace always fires first and this branch is unreachable. **`last_agent_action_mono IS NULL` with `controller='agent'` releases immediately** — the safe direction, and it should not occur because 3c's `take` writes the field in the same transaction.

**Challenge TTL.** `ChallengeRepo.list_expired_open(window_start_mono(now_mono, CHALLENGE_TTL_NS))` → `expired`, `reason='timeout'`, emit `challenge_updated`.

**Presence.** Edge-triggered against `state.connected`. A bot whose `last_poll_mono` `is_within(..., DISCONNECT_AFTER_NS)` and is not in the set is added and gets one `bot_connected`; a bot in the set whose `last_poll_mono` is older (or NULL) is removed and gets one `bot_disconnected`. **This step performs no database writes** and buffers only events. Declare `DISCONNECT_AFTER_NS = 30_000_000_000` here, per role spec §2.1's server-local table.

**Tests first:**
1. An agent-controlled bot at 44 s is not released; at 46 s it is, and `wake` fires **after** commit.
2. A released bot re-enters the pool on the same tick's snapshot only if the snapshot is taken after the release — assert the ordering you implement, and if the release runs after matchmaking (it does; step 6 follows step 2) assert the bot pairs on the *next* tick, not this one.
3. `last_agent_action_mono IS NULL` with `controller='agent'` releases.
4. An `open` challenge older than `CHALLENGE_TTL_NS` becomes `expired` with `reason='timeout'` and one `challenge_updated`; a `queued` one is untouched by this step.
5. Presence is edge-triggered: ten ticks with a freshly polling bot produce exactly **one** `bot_connected`, not ten. Then age its `last_poll_mono` past `DISCONNECT_AFTER_NS` and assert exactly one `bot_disconnected` over the next ten ticks.
6. The presence step writes nothing: snapshot every table before and after a tick containing only presence work and assert equality.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_tick_housekeeping.py`

**Mutation:** emit `bot_connected` whenever the bot is recent rather than on the transition. Test 5 must go red with ten events. Then separately swap `AGENT_AUTO_RELEASE_NS` for `AGENT_DELIVERY_GRACE_NS` in the release predicate — test 1's 46 s half must go red.

**Commit:** `phase3b: agent auto-release, challenge TTL and edge-triggered presence`

---

## Task 18 — The supervisor

**Files:** `chess_server/engine/supervisor.py`, `tests/chess_server/test_supervisor.py`

Role spec §7.6, design §4.6. A separate coroutine polling `TickerMetrics` every 2 s:

- stale beyond 5 s → log a **warning** naming the last tick number;
- stale beyond 15 s → log at **error**, then **cancel the ticker task and start a replacement**.

It watches `last_tick_mono`, **not `task.done()`**: a task wedged on an await is the more likely failure and `done()` never fires for it.

The restart is a sequence, not a bare cancel: `task.cancel()`; `await asyncio.wait({task}, timeout=5.0)`; if done, increment `ticker_restarts`, log, create the replacement; **if not done, log CRITICAL and create nothing.** Two tickers is strictly worse than none — the ticker is the only creator of games, and two contend for `write_lock` and double every sweep. This is only safe because `critical_section` catches `BaseException` and completes its rollback under cancellation.

Staleness is decided with `is_within(metrics.last_tick_mono, now_mono, threshold_ns)`, never a subtraction. `TICK_WARN_NS = 5_000_000_000` and `TICK_RESTART_NS = 15_000_000_000` are declared here as server-local ops constants — see *Gaps*, §2.1's table omits them.

Also here: `async def probe_db_writable(deps) -> bool` — enters `critical_section` and exits immediately with no statements, under a 1 s `asyncio.wait_for`, `False` on timeout or any exception. `BEGIN IMMEDIATE` takes SQLite's RESERVED lock, so this tests exactly the property that fails when a cancelled rollback leaves the writer mid-transaction. 3c's `/health` calls it; it lives here because it is a store property, not a route.

**Tests first — drive the supervisor by calling its single-step body with an injected clock, never by sleeping:**
1. Fresh metrics → no warning, no restart. Expected outcome: **no change**; keep it paired with 2 and 3.
2. 6 s stale → warning logged (assert via `caplog`), **no** restart, `ticker_restarts` still 0.
3. 16 s stale with a task that *does* respond to cancellation → `ticker_restarts == 1` and a new task object exists and is running.
4. 16 s stale with a task that **swallows** `CancelledError` and keeps running → CRITICAL logged, `ticker_restarts` still 0, and **no second task created**. Assert the count of live ticker tasks is exactly 1. This is the test that stops the remedy being worse than the disease.
5. `probe_db_writable` is `True` on a healthy store, and `False` when the writer is left inside a transaction (open one in another task and hold it past the timeout).
6. A ticker wedged on an await keeps `task.done() is False` while the supervisor still fires — assert the supervisor's decision does not consult `done()`.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_supervisor.py`

**Mutation:** create the replacement unconditionally after `asyncio.wait`. Test 4 must go red on the task count. Then separately gate the restart on `task.done()` — test 6 must go red. Then separately make `probe_db_writable` return a constant `True` — test 5 must go red.

**Commit:** `phase3b: ticker supervisor with cancel-and-restart and a real db_writable probe`

---

## Task 19 — The concurrency test: a move and a flag fired at the same instant

**Files:** `tests/chess_server/test_concurrency.py` (new code only if a defect is found)

Role spec §11.1 — the definition-of-done test, and the one that would have caught the revision-1 defect. **No production code should need changing.** If it does, that is a finding: report it before fixing.

Set up a rated competitor-vs-competitor game, delivered, with the mover's clock at exactly the boundary. Launch `apply_move` (the outer form) and `_tick_once` as two tasks against the **same store**, started together with `asyncio.gather`. The single writer serialises them; which one wins is not deterministic and the test must not assume.

Assert, whichever order they land in:

1. Exactly **one** terminal transition — one `game_ended` event, one `ended_at`, one `termination`.
2. Exactly **one** `rating_history` row per rated participant.
3. `bots.rating == STARTING_RATING + sum(deltas)` for both.
4. No orphan `seats` row and no orphan `moves` row for a ply the game never reached.
5. The loser of the race raised `CASConflict` and mutated nothing.
6. Run the whole scenario 25 times in a loop. A race test that runs once tests one interleaving.

Add the mirror case: two `apply_move` calls for the same ply, gathered. Exactly one succeeds; the other gets `CASConflict`; `moves` has one row.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_concurrency.py`

**Mutation:** replace `cas_terminate`'s `WHERE id=? AND status=? AND ply=?` with `WHERE id=?`. Assertions 1 and 2 must go red. If they stay green across 25 iterations, the test is not creating a genuine race — fix the test before believing the code.

**Commit:** `phase3b: concurrency test — one terminal transition under a move/flag race`

---

## Task 20 — Integration: a full game, ticker-driven

**Files:** `tests/chess_server/test_engine_integration.py`

The end-to-end check for this phase, standing in for role spec §11.13's harness until 3c gives it real endpoints. A scripted in-process competitor plays through the **outer** forms (`deliver_position`, `apply_move`) — not through HTTP, which does not exist yet — while `_tick_once` runs between its moves under an injected clock.

Cover, each as its own test:

1. **Happy path against an anchor.** Seed the three anchors, register one competitor, poll it into the pool (set `last_poll_mono` via `BotRepo.update_last_poll`), run ticks until paired, then alternate scripted moves and ticks until terminal. Assert: the game reached `finished`, the competitor has exactly one `rating_history` row, the anchor has **none** and its rating is unchanged, both seats are freed, both bots' counters moved, and `state.history` / `state.mailbox` hold nothing for that game.
2. **Competitor vs competitor**, two scripted clients, decisive result: two `rating_history` rows, zero-sum.
3. **Flag mid-game**: the scripted client stops moving; the ticker flags it.
4. **Abandonment**: the scripted client stops polling before delivery mid-game → `abandoned`.
5. **No-show at ply 0** → `aborted`, unrated, the present bot back in the pool and paired again within a tick.
6. **Illegal-move forfeit** over the full path: three rejections then `illegal_forfeit`.
7. **The event stream is coherent**: for every game, the recorded sink shows `game_created` → `game_started` → `move_played`×n → `game_ended` (→ `rating_changed`×k), with `seq` strictly increasing and no gaps across the whole run.
8. **Twenty ticks after everything has ended** change nothing and raise nothing: no new games beyond what the pool justifies, `consecutive_tick_errors == 0`.

**Verify:** `.venv/bin/pytest -q tests/chess_server/test_engine_integration.py` then the whole suite, `.venv/bin/pytest -q`.

**Mutation:** delete the anchor-move step (task 16) from the ticker's step list. Test 1 must go red — and note *how*: the game aborts `no_show`. Then separately drop `rating_changed` from finalisation — test 7 must go red on the sequence, not on a count.

**Commit:** `phase3b: engine integration — full games driven by the ticker`

---

## Definition of done for phase 3b

- The suite is green, the tree is clean, and **no engine test calls `asyncio.sleep` to wait for behaviour** — `_tick_once` is the only entry point any test needs.
- `test_locking_discipline.py` and `test_units.py` are green over the new `engine/` package.
- Role spec §11.1, §11.2, §11.3, §11.5, §11.6, §11.7, §11.8 and §11.9 each have a passing test. §11.4 and §11.10–§11.14 are 3c's or already covered by 3a.
- Every mutation in this plan has been applied, seen red, and reverted — and any that could not fail is reported.

## Gaps — decisions this plan could not take from the specs

Each of these is an invented decision if left unrecorded. The builder should implement the stated interim and **flag it in the completion report**, not quietly settle it.

1. **`last_tick_age_ms` has no `chess_core` helper.** `AGENTS.md` and role spec §1.2 forbid `chess_server` subtracting two monotonic timestamps, and `chess_core.clock` exposes `is_within` (boolean) and `window_start_mono` (a bound) but nothing returning an elapsed count for an arbitrary pair. The supervisor's *decisions* therefore use `is_within` and need no subtraction (task 18). The *number* `last_tick_age_ms` on `/health` and in `health_tick` still needs one. **Interim:** 3b does not compute it; `TickerMetrics` exposes `last_tick_mono` and 3c derives the display value. **Ask the chess-domain track for `elapsed_ms(earlier_mono, now_mono)` in `chess_core.clock`** before 3c needs it.
2. **The supervisor's thresholds have no declared home.** Role spec §2.1 names exactly three server-local constants and `TICK_WARN_NS`, `TICK_RESTART_NS`, the 2 s supervisor period and the 5 s cancel-wait are none of them. **Interim:** declared in `engine/supervisor.py` as ops constants that affect no game outcome. Role spec §2.1's table should gain them.
3. **Anchors' `owner` is unspecified.** **Interim:** `owner='server'`. It is safe — `role='anchor'` exempts them from the one-competitor-per-owner rule, and `pair_bots` refuses anchor-vs-anchor anyway, so the same-owner constraint between anchors is never load-bearing. The residual edge: an attendee registering with `owner='server'` would be blocked from anchor games by `_allowed` and their games would be `rated=0` by `rated_at_creation`. 3c's registration validation should reserve the name.
4. **`TurnResponse.history_san` has no specified source.** The engine caches FENs, not SAN. Reading `moves` per delivery is an O(ply) query on the hot path inside the writer's lock. **Interim:** out of scope for 3b; 3c decides, and if it chooses a cache, the seeding and clearing points are the same ones `state.history` already uses.
5. **`move_played.is_featured` has a pinned field but no pinned producer.** Interfaces Part 2 shows it in the payload; role spec §8.4 puts coalescing and featured selection in `api/sse.py`. **Interim:** the engine omits it and 3c stamps it at fan-out. If a reviewer prefers the engine to carry it, the engine needs a `featured_game_id` seam.
6. **`health_tick` is emitted by the supervisor and is deliberately unbuffered** (role spec §8.4) — it reports process state, belongs to no transaction. It needs the SSE hub directly, not `txn.emit`. **Interim:** 3b's supervisor does not emit it; 3c wires it, since it owns the hub. Recorded so it is not lost between the two plans.
7. **`BotRepo` has no counters-only update.** `update_rating_and_counters` requires a rating even for unrated games. **Interim:** pass the bot's current rating (task 4). Do not add a method to `store/` — 3a is built and green, and a second write path to `bots.rating` is exactly the kind of seam that produces two sources of truth.

