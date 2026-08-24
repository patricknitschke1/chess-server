# Phase 3a — `chess_server/store/`

**Date:** 2026-08-24
**Track:** server-engineer
**Scope:** schema and DDL, connection setup, the write lock and `critical_section`, `Txn`, CAS helpers, repositories, restart recovery.
**Ends when:** the store is fully driveable from tests. No FastAPI, no ticker, no routes, no SSE transport. If a step can only be verified by starting a server, it belongs in 3b.

## How to use this plan

This plan **references** the specifications; it does not restate them. Code appears here only where the exact text *is* the decision (a pragma list, a CAS predicate) and cannot be pointed at.

Authorities, in precedence order:

1. `docs/superpowers/specs/2026-08-23-chess-arena-design.md` — §4 (concurrency), §5 (data model), §7.1 (recovery) are **normative**.
2. `docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md` — Parts 1 and 5.
3. `docs/superpowers/specs/roles/server-engineer-spec.md` — the build document for this track. §§2, 3, 4 cover all of 3a.
4. `AGENTS.md` — the invariants.

If a step here disagrees with the role spec, the role spec wins and this plan is a bug — raise it rather than silently reconciling.

## Rules for every task

- **Failing test first.** Write the test, run it, watch it fail for the stated reason, then implement.
- **Name the mutation.** Every task lists a source mutation that must turn the new test red. Apply it, run, confirm red, revert, confirm green. Where a step's expected outcome is *no change*, the plan says so explicitly — a green run there proves nothing on its own and must be paired with a mutation that does go red.
- **Clear caches between mutation steps:** `find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +`. OneDrive/APFS mtime granularity has already faked one mutation result on this repo.
- **Failure paths before happy paths**, per role spec §11.
- Every task ends with the whole suite green (`.venv/bin/pytest`) and the tree committable.
- **Always `.venv/bin/pytest` and `.venv/bin/python`.** Python is 3.14.3. Baseline at plan time: 160 tests passing, tree clean.
- Import every constant from `chess_core`; never write a numeric literal that has a name (role spec §2.1).
- No repository method issues `BEGIN`, `COMMIT`, `ROLLBACK` or `SAVEPOINT` (role spec §3.6).

## Layout this phase creates

```
chess_server/__init__.py
chess_server/store/__init__.py
chess_server/store/schema.py        DDL + apply_schema
chess_server/store/db.py            connections, pragmas, writer executor
chess_server/store/txn.py           write_lock, critical_section, Txn, seq counter
chess_server/store/cas.py           assert_cas, CASConflict, InvariantViolation
chess_server/store/rows.py          row dataclasses
chess_server/store/repositories.py  the six repos + the ms<->ns boundary
chess_server/store/recovery.py      §7.1 recovery, inner and outer forms
tests/chess_server/__init__.py
tests/chess_server/...                    one test module per task
```

`tests/chess_server/__init__.py` **is** required (it makes `tests.server` a subpackage of the existing `tests` package, matching `tests/chess_core/`). Do **not** add an `__init__.py` anywhere that would make another directory importable as a top-level `tests` package — that shadowed the repo's own `tests` package and broke collection repo-wide once already.

---

## Task 1 — Package skeleton and async test harness

**Files:** `chess_server/__init__.py`, `chess_server/store/__init__.py`, `tests/chess_server/__init__.py`, `tests/chess_server/test_harness.py`, `pyproject.toml`

The store is async (`asyncio.Lock`, `critical_section`), so the suite must be able to run `async def` tests before anything else is written.

1. Create the empty package files above.
2. `pyproject.toml`: add `"chess_server*"` to `[tool.setuptools.packages.find] include`, and add `pytest-asyncio` to the `dev` extra.
3. Install it: `.venv/bin/pip install pytest-asyncio`.
4. `pyproject.toml` `[tool.pytest.ini_options]`: add `asyncio_mode = "auto"`. Leave `testpaths` and `addopts` alone.

**Test first** — `tests/chess_server/test_harness.py`: one `async def` test that awaits `asyncio.sleep(0)` and asserts a value set by the coroutine, plus a sync test asserting `import chess_server.store` succeeds. Run before step 3 and confirm the async test is *skipped or errored*, not silently passing.

**Verify:** `.venv/bin/pytest tests/chess_server/ -q` then `.venv/bin/pytest -q` (must still be green, 162 tests).

**Mutation:** set `asyncio_mode = "strict"`. The async test must error with "async def functions are not natively supported". Revert.

**If `pytest-asyncio` will not install on 3.14:** fall back to sync test functions wrapping `asyncio.run(_scenario())`, and record the fallback in the plan's gaps section rather than inventing a third mechanism. Every later task's tests work either way.

**Commit:** `phase3a: chess_server package skeleton and async test harness`

---

## Task 2 — Schema and DDL

**Files:** `chess_server/store/schema.py`, `tests/chess_server/test_schema.py`

Implement `SCHEMA_SQL` and `apply_schema(conn)` **exactly** as role spec §3.1. That section's DDL is normative and is one of the few places the exact text is the decision — copy it, do not paraphrase it. Cross-check against design §5.1 for column coverage.

Three deletions are part of the task, not omissions: **no `mailbox` table** (design §5.1), **no `arena_reports` table** (role spec §3.5, design §21), and `challenges.status` has five values, not seven.

**Tests first**, failure paths in this order:

1. `INSERT INTO seats (bot_id, game_id) VALUES (NULL, 1)` raises `IntegrityError`. This is the `WITHOUT ROWID` test and it is the most important test in the task.
2. A second `seats` row for the same `bot_id` raises `IntegrityError`.
3. `seats.bot_id` referencing an absent bot raises `IntegrityError` (set `PRAGMA foreign_keys=ON` in the test's own connection; task 3 owns making that automatic).
4. Duplicate `(game_id, ply)` in `moves` raises `IntegrityError`.
5. Duplicate `(game_id, bot_id)` in `rating_history` raises `IntegrityError` — the double-rating backstop.
6. Duplicate `bots.name` raises `IntegrityError`.
7. `sqlite_master` contains no table named `mailbox` and none named `arena_reports`.
8. Column presence via `PRAGMA table_info`: `games.to_move`; `challenges.reason` and `challenges.created_mono`; `bots.last_color`, `bots.white_count`, `bots.last_opponent_id`.
9. Indexes `idx_games_status` and `idx_bots_token_hash` exist in `sqlite_master`.
10. `apply_schema` is idempotent on a fresh connection to the same file (second call does not raise).

**Verify:** `.venv/bin/pytest tests/chess_server/test_schema.py -q`

**Mutations** (one at a time, caches cleared):

- Drop `WITHOUT ROWID` from `seats`. Test 1 must fail — and note *how* it fails: the insert **succeeds** and stores a phantom row. If test 1 still passes, the test is wrong, not the schema.
- Remove `UNIQUE (game_id, bot_id)` from `rating_history`. Test 5 must fail.
- Rename `games.to_move` to `side_to_move`. Test 8 must fail.

**Commit:** `phase3a: schema DDL with WITHOUT ROWID seats and storage backstops`

---

## Task 3 — Connections, pragmas, and the reader/writer split

**Files:** `chess_server/store/db.py`, `tests/chess_server/test_db.py`

Per role spec §3.11. One writer `sqlite3.Connection` with `check_same_thread=False`, pinned to a **single-thread** executor; a separate reader connection for display-only queries; the pragma block applied to **every** connection, not only the writer.

The pragma text is the decision, so it appears here once:

```
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```

`5000` here is a SQLite pragma value, not a `chess_core` constant, and has no name to import.

**Tests first:**

1. On a **file** database in `tmp_path`, `PRAGMA journal_mode` reads back `wal` on the writer connection *and* on the reader connection. Failure path first: assert the reader too, because applying pragmas only to the writer is the likely defect.
2. `PRAGMA foreign_keys` reads back `1` on both. A seats FK violation raised through the reader-configured connection proves it is live, not merely reported.
3. The writer executor runs every submitted callable on **one** thread: submit ten calls returning `threading.get_ident()`, assert one distinct value, and assert it differs from the test's own thread.
4. Reader and writer are distinct connection objects and the reader sees a row the writer committed.
5. `PRAGMA busy_timeout` reads back `5000`.

**`:memory:` will not work for this task and must not be used.** `journal_mode` on an in-memory database reports `memory`, and two connections to `:memory:` are two different databases, so the reader/writer split cannot be observed. Use `tmp_path`. Provide a `tests/chess_server/conftest.py` fixture yielding a file-backed store handle; every later task uses it.

**Verify:** `.venv/bin/pytest tests/chess_server/test_db.py -q`

**Mutations:**

- Apply the pragma block to the writer only. Tests 1 and 2 must fail on the reader.
- Give the writer a `ThreadPoolExecutor(max_workers=2)`. Test 3 must fail.

**Commit:** `phase3a: WAL connections, pragmas on every connection, single-thread writer`

---

## Task 4 — `write_lock` and `critical_section`

**Files:** `chess_server/store/txn.py`, `tests/chess_server/test_critical_section.py`

Implement `write_lock`, `critical_section` and `_finish` **per role spec §3.7**. That section's control flow is normative and its exact text is the decision — the `except BaseException`, the `asyncio.ensure_future` + `shield` loop in `_finish`, and the single `COMMIT`-or-`ROLLBACK` before release. Do not simplify the loop; do not replace `BaseException` with `Exception`.

`Txn` is introduced in task 5. For this task `critical_section` may yield a minimal object carrying `conn` and `executor`; task 5 fills it in.

**Tests first**, failure paths in this order:

1. **Cancellation does not brick the writer** (role spec §11.10). Start a task that enters `critical_section`, performs an INSERT, and awaits an event that never fires. Cancel it. Then assert, in order: the cancellation propagated; `conn.in_transaction` is `False`; the inserted row is **absent**; and a **subsequent** `critical_section` completes and commits. That last assertion is the point of the test — the first three pass on several broken implementations.
2. **Cancellation delivered during the rollback still rolls back.** Cancel the task, then cancel it again while `_finish` is running its `ROLLBACK`. Same assertions as test 1. If this is hard to time deterministically, drive it by wrapping the executor so the `ROLLBACK` submission yields control once, and cancel at that point.
3. An ordinary `Exception` raised inside the block rolls back, re-raises, and leaves no row.
4. A `sqlite3` error raised by `BEGIN IMMEDIATE` itself surfaces to the caller rather than being swallowed (`fut.result()` in `_finish`).
5. Two concurrent `critical_section` users **serialise**: record entry/exit ordering in a list and assert no interleaving. Wrap in `asyncio.wait_for(..., timeout=5)` so a lock defect fails the test rather than hanging the suite.
6. Happy path last: commit persists.

**Verify:** `.venv/bin/pytest tests/chess_server/test_critical_section.py -q`

**Mutations:**

- `except BaseException` → `except Exception`. Tests 1 and 2 must fail. This is the single most valuable mutation in the phase: `asyncio.CancelledError` is not an `Exception` subclass, so the naive handler skips the rollback, strands the writer connection mid-transaction, and every later `BEGIN IMMEDIATE` raises for the life of the process.
- Replace `_finish`'s shield loop with a bare `await self._execute(conn, sql)`. Test 2 must fail.
- Remove `async with write_lock`. Test 5 must fail (and must fail, not hang — that is what the `wait_for` is for).

**Commit:** `phase3a: cancellation-proof critical_section under the single write lock`

---

## Task 5 — `Txn`: buffered events, deferred work, savepoints

**Files:** `chess_server/store/txn.py`, `tests/chess_server/test_txn.py`

Implement `Txn` per role spec §3.8: `emit`, `defer`, `savepoint`, `flush`, `discard`. Design §4.1 (no event visible before its transaction commits) and §4.3 (a savepoint per unit of work) are the normative sources.

Two seams to pin, because 3b consumes them:

- `flush()` assigns `seq` **in commit order, at flush time, never at `emit()` time**, from a process-wide counter owned by this module, and hands `(seq, event_type, data)` to a **sink** callable injected at construction. In 3a the sink is a list-appending test double; 3b replaces it with the SSE broadcaster. The store does no network I/O.
- Interfaces Part 2 shows `server_run_started` at `seq: 0`, so the counter's first assigned value is `0`.

**Tests first**, failure paths in this order:

1. **Rollback discards buffered events and consumes no `seq`.** Emit inside a section that then raises; assert the sink received nothing, and that the *next* successful transaction's first event carries the `seq` the rolled-back one would have taken. The second half is the real assertion — a `discard()` that clears the list but has already burned a sequence number defeats the gap check `/state` depends on.
2. **`defer` callbacks do not run on rollback**, and **do** run after commit — never before. Assert a flag is still unset while inside the block.
3. **Savepoint rollback truncates `events` and `deferred` back to their lengths at entry**, and rolls back only that unit's rows. Outer transaction commits; the surviving unit's rows and events are present, the rolled-back unit's are absent from both the database and the sink.
4. Nested savepoints with distinct names release correctly.
5. Nothing reaches the sink before `COMMIT` returns: have the sink record `conn.in_transaction` at call time and assert `False`.
6. Happy path last: emit, commit, sink sees the events in emit order with contiguous `seq`.

**Verify:** `.venv/bin/pytest tests/chess_server/test_txn.py -q`

**Mutations:**

- Assign `seq` in `emit()` instead of `flush()`. Test 1's second half and test 6's ordering must fail.
- Delete the truncation in `savepoint`'s rollback path. Test 3 must fail with the rolled-back unit's event in the sink.
- Run `deferred` callbacks inside the `try` rather than from `flush()`. Test 2 must fail.

**Commit:** `phase3a: Txn event buffer, deferred work and per-unit savepoints`

---

## Task 6 — CAS helpers

**Files:** `chess_server/store/cas.py`, `tests/chess_server/test_cas.py`

Per role spec §3.10: `assert_cas(cursor, expected=1)`, raising `CASConflict` on `rowcount` below `expected` and `InvariantViolation` above it. Both exception types live here and are exported; 3b's routes map `CASConflict` to `409`.

**Tests first:**

1. An UPDATE matching zero rows raises `CASConflict`.
2. An UPDATE matching two rows raises `InvariantViolation` — a *different* exception, because "too many" is a corrupted invariant, not a lost race, and 3b must not turn it into a `409`.
3. An UPDATE matching exactly one row returns normally.
4. `CASConflict` is not a subclass of `InvariantViolation` and vice versa.

**Verify:** `.venv/bin/pytest tests/chess_server/test_cas.py -q`

**Mutation:** make both branches raise `CASConflict`. Test 2 must fail.

**Commit:** `phase3a: CAS assertion helpers`

---

## Task 7 — Row types, the ms↔ns boundary, and the no-literals guard

**Files:** `chess_server/store/rows.py`, `chess_server/store/repositories.py`, `tests/chess_server/test_units.py`

Two things, together because they are the same defect class.

**(a) The conversion boundary.** Implement `_clock_from_game` and `_clock_to_game_fields` per role spec §2. That section's two function bodies are the decision and are reproduced there in full — copy them. They are the **only** callers of `ms_to_ns` / `ns_to_ms` in `chess_server/`. `to_move_since_mono` and `turn_started_mono` pass through **unconverted**: they are monotonic counts, not durations.

`rows.py` holds plain dataclasses mirroring the schema (`BotRow`, `GameRow`, `MoveRow`, `SeatRow`, `RatingHistoryRow`, `ChallengeRow`) plus a `sqlite3.Row` → dataclass helper. Bind `GameRow`'s field names to the §3.1 column names exactly; `_clock_from_game` reads them by name.

**(b) The no-literals guard.** A test that walks every `.py` file under `chess_server/` and fails if any of the §2.1 constant *values* appears as a numeric literal (`180_000_000_000`, `180000`, `2000`, `1200`, `24`, `400`, `200`, `15_000_000_000`, `60_000_000_000`, `45_000_000_000`, `5_000_000_000`, `20_000_000_000`, `1_000_000_000`). Allow-list `schema.py`'s `DEFAULT 1200` (SQL text, not Python) and `db.py`'s `busy_timeout` 5000 explicitly, by module name, so the allow-list is auditable.

**Tests first:**

1. `_clock_to_game_fields` output for a fresh rated clock has `white_ms == 180000` — assert on **the integer**, computed as `ns_to_ms(RATED_TIME_CONTROL_NS)` and also asserted `== 180_000` so a broken `ns_to_ms` cannot satisfy it tautologically. This is role spec §11.8's store half.
2. `to_move_since_mono` and `turn_started_mono` survive a `ClockState` → fields → `ClockState` round trip **bit-identical**, including a `turn_started_mono` of `None`.
3. A round trip loses at most 1 ms, and loses it from the mover, symmetrically for both colours (role spec §2's accepted limit, stated as a test so nobody "fixes" it asymmetrically later).
4. The no-literals guard, run against the current tree.

**Verify:** `.venv/bin/pytest tests/chess_server/test_units.py -q`

**Mutations:**

- Write `clock.white_ns` into the `white_ms` field (drop the `ns_to_ms`). Test 1 must fail. Note what the broken value looks like: `180_000_000_000` ms is 5.7 years, populated, non-null and decreasing — invisible to every other assertion in the suite.
- Apply `ns_to_ms` to `to_move_since_mono`. Test 2 must fail.
- Add `POLL_HOLD_NS`'s value as a literal somewhere in `chess_server/`. Test 4 must fail.

**Commit:** `phase3a: row types, the single ms/ns boundary, and the no-literals guard`

---

## Task 8 — `BotRepo`

**Files:** `chess_server/store/repositories.py`, `tests/chess_server/test_bot_repo.py`

Methods per role spec §3.6. `update_pool_history(bot_id, last_color, last_opponent_id, increment_white)` is the §9.3 writer.

`list_pool_candidates` takes a **precomputed `cutoff_mono`** parameter and compares (`last_poll_mono >= :cutoff_mono`); it does not compute the cutoff. Role spec §1.2 forbids `chess_server/` from subtracting two monotonic timestamps, and no `chess_core` predicate exists for `POLL_RECENCY_NS` — see Gaps, G1. Its filter is role spec §9.1: `role IN ('competitor','anchor')`, no `seats` row, `controller='client'`, and the recency clause **for `role='competitor'` only**.

**Tests first:**

1. Inserting a duplicate `name` raises `IntegrityError`.
2. `get_by_token_hash` on an unknown hash returns `None`, not a raise.
3. `list_pool_candidates` **excludes a seated bot**, excludes `controller='agent'`, excludes `role='benchmark'`, and excludes a competitor whose `last_poll_mono` is below the cutoff.
4. `list_pool_candidates` **includes an anchor whose `last_poll_mono` is NULL.** An anchor never polls; applying recency to anchors leaves them permanently ineligible and the anchor path silently dead.
5. `update_pool_history` writes `last_color` and `last_opponent_id` and increments `white_count` **only** when `increment_white` is true; assert the not-incremented case explicitly as an expected *no change*, paired with the incremented case in the same test so a no-op implementation cannot pass both.
6. `update_rating_and_counters` moves exactly one of `wins`/`losses`/`draws` and always `games_played + 1`.
7. `update_last_poll` sets both `last_poll_at` (wall TEXT) and `last_poll_mono` (INTEGER ns).
8. **Structural:** no method body in `repositories.py` contains `BEGIN`, `COMMIT`, `ROLLBACK` or `SAVEPOINT` (source-text assertion over the module).

**Verify:** `.venv/bin/pytest tests/chess_server/test_bot_repo.py -q`

**Mutations:**

- Apply the recency clause to all roles. Test 4 must fail.
- Make `update_pool_history` always increment `white_count`. Test 5 must fail.
- Add a `conn.execute("COMMIT")` to any repo method. Test 8 must fail.

**Commit:** `phase3a: BotRepo including pool-history and pool-candidate filters`

---

## Task 9 — `GameRepo`: creation and reads

**Files:** `chess_server/store/repositories.py`, `tests/chess_server/test_game_repo.py`

`insert_game`, `get_by_id`, `get_for_bot`, `list_undelivered_non_terminal`, `list_delivered_active`, `list_anchor_to_move`, `list_active_summaries`.

`insert_game` **takes nanoseconds and writes milliseconds** (role spec acceptance 7), seeds `fen = STARTING_FEN`, `ply = 0`, `to_move` from the FEN's second field (role spec §3.2 — **never from ply parity**), `status='pending'`, `delivered_to_mover=0`, `turn_started_mono=NULL`, both strike columns 0, and `rated` computed from design §5.3 **rules 2–6 at creation**. Rule 1 belongs to finalisation and is 3b.

`get_for_bot` resolves a bot's current game **by joining `seats`**, never by scanning `games` (role spec §7.1: a game is reachable only through `seats`).

**Tests first:**

1. `list_undelivered_non_terminal` **does not return a finished undelivered game**, nor an aborted one. This is role spec §11.3's store half: without the `status IN ('pending','active')` filter the sweep never terminates and the ticker stops doing anything from the first finished game onward.
2. `get_for_bot` returns `None` for a bot with no `seats` row **even when a non-terminal `games` row names that bot**. An orphan game must be inert.
3. `insert_game` with `RATED_TIME_CONTROL_NS` stores `time_control_ms == 180000` and `white_ms == black_ms == 180000`.
4. `rated` at creation, one case per design §5.3 rule: benchmark participant → 0; shared owner → 0; `EXHIBITION_TIME_CONTROL_NS` → 0; exactly one anchor → 1; two plain competitors → 1. Table-driven.
5. `to_move` is `'white'` at ply 0 and comes from the FEN — insert with a black-to-move starting FEN at an even ply and assert `'black'`, so parity cannot satisfy the test.
6. `list_active_summaries` returns `fen`, `to_move` and `status` (interfaces Part 5's `ActiveGameSummary`).
7. `list_anchor_to_move` returns only games whose side to move is an `is_anchor` bot and whose status is non-terminal.

**Verify:** `.venv/bin/pytest tests/chess_server/test_game_repo.py -q`

**Mutations:**

- Drop the status filter from `list_undelivered_non_terminal`. Test 1 must fail.
- Derive `to_move` from `ply % 2`. Test 5 must fail.
- Set `rated=1` unconditionally at creation. Test 4 must fail.

**Commit:** `phase3a: GameRepo creation and reads, rated at creation, to_move from FEN`

---

## Task 10 — `GameRepo`: the three CAS transitions

**Files:** `chess_server/store/repositories.py`, `tests/chess_server/test_game_cas.py`

`cas_apply_move`, `cas_deliver`, `cas_terminate`, per role spec §3.10 and §5.2. Every one is a single `UPDATE` whose `WHERE` names **the state being transitioned from**.

- `cas_terminate` — the UPDATE in role spec §3.10 verbatim, including `delivered_to_mover=0` and `turn_started_mono=NULL`. Calls `assert_cas`.
- `cas_apply_move` — CAS on `WHERE id=? AND ply=? AND status=?`, setting `fen`, `ply+1`, `to_move` **from `fen_after`'s second field in the same statement**, and the `_clock_to_game_fields` values. Calls `assert_cas`.
- `cas_deliver` — the UPDATE in role spec §5.2 verbatim. **`rowcount == 0` is a legitimate result here, not a conflict**: it means already delivered. It does **not** call `assert_cas`; it returns whether it delivered and whether it moved `pending → active`. This is the one documented exception to acceptance 4.

**Tests first, failure paths first:**

1. `cas_terminate` against a **stale status** raises `CASConflict` and **nothing moved** — capture the whole row before and after and assert equality. "Nothing moved" is the assertion that catches a `WHERE id=?`-only predicate.
2. `cas_apply_move` against a stale `ply` raises `CASConflict`, nothing moved.
3. Two `cas_terminate` calls for the same game: the first succeeds, the second raises `CASConflict`. Exactly one terminal transition. This is the store-level core of role spec §11.1.
4. `cas_deliver` on an already-delivered position returns "not delivered by me" and **leaves `turn_started_mono` unchanged** — re-reading a position must never restart the clock. Expected outcome is *no change*; pair it with mutation (c) below so the green run means something.
5. `cas_deliver` on a `pending` game sets `status='active'`, `started_at`, `turn_started_mono` and `delivered_to_mover=1` **in one statement**; assert `status` and `delivered_to_mover` together after a single call.
6. `cas_terminate` leaves `delivered_to_mover=0` and `turn_started_mono` NULL on the finished row, so a finished game cannot re-enter the delivery sweep.
7. Happy path last: deliver, apply a move, and assert `ply` advanced by one, `fen` updated, `to_move` flipped, `delivered_to_mover` back to 0 and `turn_started_mono` back to NULL.

**Verify:** `.venv/bin/pytest tests/chess_server/test_game_cas.py -q`

**Mutations:**

- (a) Reduce `cas_terminate`'s predicate to `WHERE id=?`. Tests 1 and 3 must fail.
- (b) Have `cas_deliver` call `assert_cas`. Test 4 must fail with `CASConflict` — the failure mode this exception exists to prevent, since it would turn every re-poll into a `409`.
- (c) Have `cas_deliver` drop its `delivered_to_mover = 0` predicate so it always rewrites `turn_started_mono`. Test 4 must fail.
- (d) Split `cas_deliver`'s status change into a second statement issued only when the first matched. Test 5 must fail.

**Commit:** `phase3a: CAS move, deliver and terminate transitions`

---

## Task 11 — `SeatRepo` and seat-collision isolation

**Files:** `chess_server/store/repositories.py`, `tests/chess_server/test_seats.py`

`insert_seat`, `delete_seats_for_game`, `get_seat`, `list_seated_bot_ids`.

**Tests first:**

1. A second `insert_seat` for a bot that already holds a seat raises `IntegrityError`. The invariant is the `seats` table, not application logic.
2. `insert_seat` for an unknown `bot_id` raises `IntegrityError` (FK, requires task 3's `foreign_keys=ON`).
3. **Savepoint isolation.** In one `critical_section`, do two "pairings": pairing A inserts a game and two seats and succeeds; pairing B, in its own `txn.savepoint`, inserts a game and hits a seat collision. Assert after commit that pairing A's game **and** both its seats exist, pairing B's game row does **not** exist, no stray seat exists, and the sink received A's buffered event and not B's. This is the store-level form of role spec §11.2 and §11.12.
4. Test 3's negative control: without the savepoint, the collision leaves an orphan game plus one stray seat. Write this as a comment on the test, not a second test — the point is that a statement-level `IntegrityError` does not abort the transaction, only the statement.
5. `delete_seats_for_game` removes both rows and is a no-op on an already-freed game (expected outcome *no change*; assert row counts before and after).

**Verify:** `.venv/bin/pytest tests/chess_server/test_seats.py -q`

**Mutation:** remove the `txn.savepoint` wrapper from pairing B in test 3, catching the `IntegrityError` at the top level instead. Test 3 must fail with an orphan game row present.

**Commit:** `phase3a: SeatRepo and per-pairing savepoint isolation`

---

## Task 12 — `MoveRepo` and `RatingHistoryRepo`

**Files:** `chess_server/store/repositories.py`, `tests/chess_server/test_move_rating_repos.py`

`MoveRepo.insert_move`, `list_moves_for_game`; `RatingHistoryRepo.insert_rating_change`, `sum_deltas_by_bot`, `list_points_for_bot`.

`server_elapsed_ms` and `client_reported_ms` are **separate columns with separate meanings** (design §5.1): the first is what the clock was charged, delivery to receipt, network included; the second is optional self-reported compute time, nullable, diagnostics only.

**Tests first:**

1. A duplicate `(game_id, ply)` raises `IntegrityError`.
2. A second `insert_rating_change` for the same `(game_id, bot_id)` raises `IntegrityError`. Double-rating one game is a constraint violation, not a reconciliation problem — this is the storage half of role spec §11.1.
3. `insert_move` accepts `client_reported_ms=None` and stores NULL; `server_elapsed_ms` is NOT NULL and its absence raises.
4. `list_moves_for_game` orders by `ply` ascending, verified by inserting out of order.
5. `sum_deltas_by_bot` over an empty history returns `0`, not `None` — the `/admin/consistency` identity in 3b divides on this.
6. `sum_deltas_by_bot` equals `rating_after - STARTING_RATING` across a chain of three inserts, using imported `STARTING_RATING`.

**Verify:** `.venv/bin/pytest tests/chess_server/test_move_rating_repos.py -q`

**Mutations:**

- Drop the `UNIQUE (game_id, bot_id)` predicate by inserting through a raw `INSERT OR REPLACE`. Test 2 must fail.
- Remove the `ORDER BY ply`. Test 4 must fail.
- Return `None` for an empty sum. Test 5 must fail.

**Commit:** `phase3a: MoveRepo and RatingHistoryRepo with double-rating backstop`

---

## Task 13 — `ChallengeRepo`

**Files:** `chess_server/store/repositories.py`, `tests/chess_server/test_challenge_repo.py`

`insert_challenge`, `cas_set_status`, `get_by_id`, `get_open_outgoing`, `list_inbox`, `list_queued`, `list_expired_open`, `expire_all_non_terminal`.

`insert_challenge` writes **both** `created_at` (wall TEXT, display) and `created_mono` (monotonic ns, TTL) — role spec §3.3. `list_expired_open(cutoff_mono)` takes a precomputed cutoff for the same reason as task 8 (Gaps, G1). `cas_set_status` CASes on the status being left and calls `assert_cas`. `expire_all_non_terminal(reason)` is recovery's helper and is exercised again in task 15.

**Tests first:**

1. `cas_set_status` from a stale status raises `CASConflict` and nothing moved (whole-row before/after).
2. Two concurrent transitions of one challenge: exactly one succeeds. This is role spec §11.12's challenge half.
3. `cas_set_status` writes `reason` — specifically `'seat_unavailable'` — and it is readable afterwards. Design §12's "an SSE event explains why; no silent drop" depends on the column reaching the wire.
4. `list_expired_open` returns only `open` challenges whose `created_mono` is at or before the cutoff, and **never** a `queued`, `consumed`, `declined` or `expired` one.
5. `insert_challenge` populates `created_mono` as an INTEGER and `created_at` as TEXT; assert the types, so a builder cannot satisfy the TTL by comparing against the wall-clock string.
6. `expire_all_non_terminal('server_restart')` moves `open` and `queued` to `expired` with that reason and leaves `consumed`, `declined` and `expired` rows untouched — assert the untouched rows explicitly as an expected *no change*.

**Verify:** `.venv/bin/pytest tests/chess_server/test_challenge_repo.py -q`

**Mutations:**

- Have `list_expired_open` compare against `created_at`. Test 5 stays green (it only checks types), but test 4 must fail once a row's `created_at` and `created_mono` disagree in ordering — construct exactly that case in test 4.
- Make `expire_all_non_terminal` update every row regardless of status. Test 6 must fail.
- Drop the from-status predicate in `cas_set_status`. Tests 1 and 2 must fail.

**Commit:** `phase3a: ChallengeRepo with monotonic TTL and CAS status transitions`

---

## Task 14 — The `*_locked` structural guard

**Files:** `tests/chess_server/test_locking_discipline.py`

Role spec §3.9 and design §4: `write_lock` is acquired at **exactly one place per call stack**. `asyncio.Lock` is not re-entrant and has no timeout — a nested acquire wedges the coroutine on an await, raises nothing, and looks like an ordinary function call in review. This guard is written now, in 3a, so that 3b inherits it rather than discovering it.

No new production code; only recovery (task 15) currently has an inner/outer pair, and the guard must be in place before it lands.

**Tests:**

1. **Behavioural.** Define a two-line outer-form helper **inside the test module** (it opens `critical_section` and writes one row) and call it from inside an already-open `critical_section`. Assert `asyncio.wait_for(..., timeout=1)` raises `TimeoutError`. The helper is local because no production outer form exists until task 15; the point is to demonstrate that the deadlock is real, so the structural tests below are understood to be preventing something rather than enforcing a style.
2. **Structural.** Walk the AST of every module under `chess_server/`. For every `async def` whose name ends `_locked`, assert its body contains no `async with write_lock` and no call to any name in the outer-form set. Fail with the offending function's file and line.
3. **Structural.** Assert every outer-form helper acquires `write_lock` exactly once — one `critical_section` context in its body, no direct `write_lock` acquire.

Both structural tests must be written so that they fail when the tree is empty of matches for the wrong reason: assert first that at least one `_locked` function was found, otherwise the test passes vacuously forever and nobody notices when the naming convention drifts.

**Verify:** `.venv/bin/pytest tests/chess_server/test_locking_discipline.py -q`

**Mutation:** add a throwaway `async def _probe_locked(txn)` containing `async with write_lock:`. Test 2 must fail and name it. Delete it.

**Commit:** `phase3a: structural guard for the single-acquire lock discipline`

---

## Task 15 — Restart recovery

**Files:** `chess_server/store/recovery.py`, `chess_server/store/run.py`, `tests/chess_server/test_recovery.py`

Design **§7.1 is normative**; role spec §8.6 restates it as six steps. Implement both forms: `recover_locked(txn, ...)` and `recover(...)`, per the §3.9 split. 3b calls the outer form from the FastAPI lifespan startup hook, before the listening socket accepts connections; 3a verifies only the store half.

`run.py` holds `new_run_id() -> str` (a uuid4 hex, matching interfaces Part 2's `run: str`) and the module-level current run id. `recover` regenerates it and returns a `RecoveryReport` carrying the counts and the new run id. **The `server_run_started` event is buffered via `txn.emit` and flushed after commit** — the sink is the test double in 3a and the SSE broadcaster in 3b.

In-process state (mailbox, waiters, history cache, `unpaired_ticks`, presence) is owned by 3b. `recover_locked` takes a single `clear_process_state: Callable[[], None]` and registers it with `txn.defer`, so the clearing happens only if the transaction commits. In 3a it is a test double.

**Tests first.** Seed a deliberately dirty database — a `pending` game, an `active` game, a `finished` game, seats for all four seated bots, an `open` challenge, a `queued` challenge, a `consumed` challenge, and bots carrying non-NULL `last_poll_mono` / `last_agent_action_mono` and `controller='agent'`. Then run recovery once and assert **every** one of the following, each as a separate assertion so a partial implementation cannot pass:

1. The `pending` and `active` games are `aborted`, `termination='server_restart'`, `rated=0`, `ended_at` set.
2. The `finished` game is **untouched** — status, result, termination and `rated` byte-identical to before. Expected outcome *no change*; paired with mutation (a).
3. `seats` is empty.
4. The `open` and `queued` challenges are `expired` with `reason='server_restart'`; the `consumed` one is untouched.
5. **`last_poll_mono` IS NULL for every bot.**
6. **`last_agent_action_mono` IS NULL for every bot.**
7. `controller = 'client'` for every bot, including the one that was `'agent'`.
8. The run id differs from the pre-recovery one, and a `server_run_started` event reached the sink **after** commit.
9. `clear_process_state` was called exactly once, and only after commit.
10. Idempotence: a second `recover` on the now-clean database completes without raising and aborts zero games.
11. **The lower-monotonic-baseline case.** Seed `last_poll_mono` at a value far *above* any plausible new baseline (simulating a reboot that reset the monotonic origin downward), run recovery, then assert `list_pool_candidates` with a small `cutoff_mono` returns **no competitor**. Assert the mechanism, not the symptom: because step 5 nulled the column, no comparison against a stale baseline can happen at all. Without step 5, every bot ever registered looks like it is polling right now — they are paired, never take delivery, and churn through `no_show` aborts forever with nothing logged.
12. Recovery runs in exactly one `critical_section` — assert via the task 14 structural guard plus a single-`BEGIN` count on an instrumented executor.

**Verify:** `.venv/bin/pytest tests/chess_server/test_recovery.py -q`

**Mutations:**

- (a) Widen the game-abort UPDATE to every status. Test 2 must fail.
- (b) Delete step 4's `UPDATE bots SET last_poll_mono=NULL, last_agent_action_mono=NULL, controller='client'`. Tests 5, 6, 7 and 11 must fail. This is the easy step to skip and the expensive one to skip.
- (c) Leave `queued` challenges alone. Test 4 must fail. A surviving `queued` challenge creates a real game in the new run from an intent formed in the old one.
- (d) Call `clear_process_state` directly instead of through `txn.defer`. Test 9's "only after commit" must fail.

**Commit:** `phase3a: restart recovery clearing games, seats, challenges and monotonic state`

---

## Definition of done for phase 3a

Run `.venv/bin/pytest` — green, with 160 pre-existing tests still passing. Then confirm each of the following by executing it, not by reading:

1. `seats` is `WITHOUT ROWID` and a NULL `bot_id` insert raises (task 2).
2. Every connection reports `foreign_keys=1` and `journal_mode=wal` (task 3).
3. `critical_section` catches `BaseException`, and a cancelled section leaves the writer usable (task 4).
4. Events buffer inside the transaction, flush after commit, and a rolled-back unit consumes no `seq` (task 5).
5. Every transition asserts `rowcount == 1`, with `cas_deliver` the one documented exception (tasks 6, 10, 13).
6. `insert_game` takes ns and stores `white_ms == 180000` (tasks 7, 9).
7. `ms_to_ns` / `ns_to_ms` appear in exactly two functions in `chess_server/`, and no §2.1 constant appears as a literal (task 7).
8. No `_locked` function acquires `write_lock` (task 14).
9. Recovery clears `last_poll_mono`, `last_agent_action_mono` and `controller`, and expires non-terminal challenges (task 15).
10. `grep -rn "mailbox\|arena_reports" chess_server/` returns nothing.
11. `grep -rn "is_checkmate\|monotonic_ns() -" chess_server/` returns nothing — no chess rules and no monotonic subtraction in the server (role spec §1.2).
12. No plaintext token appears anywhere: the store handles `token_hash` only (role spec §11.14, store half).

Deliberately **not** done in 3a, and owned by 3b: the ticker, savepoint-per-tick-step across all six steps, delivery's HTTP call sites, the mailbox and waiters, move application, finalisation and rating, routes, SSE transport, the supervisor, rate limiting, `/admin/*`.

---

## Gaps — what the specs do not answer

Each of these is a decision the plan makes explicitly rather than leaving to be invented at the keyboard. Raise any of them that looks wrong before starting the affected task.

**G1 (RESOLVED — `chess_core.clock.is_within(earlier_mono, now_mono, window_ns)` now exists, is tested and is re-exported; repositories take a predicate rather than a precomputed cutoff, and `chess_server` still never subtracts monotonic timestamps.)** ~~G1. No `chess_core` predicate exists for `POLL_RECENCY_NS` or `CHALLENGE_TTL_NS`.** Role spec §1.2 forbids `chess_server/` from subtracting two monotonic timestamps, and `chess_core.clock` supplies `has_flagged` and `check_delivery_timeout` but nothing for poll recency or challenge TTL. **Plan's decision:** repositories take a precomputed `cutoff_mono` and compare rather than subtract; the subtraction lands in the ticker in 3b. **Better fix, and the one to prefer if the chess-domain track has capacity:** add `is_within(now_mono, then_mono, window_ns) -> bool` to `chess_core.clock`, exactly as `remaining_ns` was added for the flag predicate in round 6. Until then, 3b will contain two monotonic subtractions that §1.2 forbids.

**G2. `challenges.created_mono` is in the role spec and not in design §5.1.** The role spec declares the design spec authoritative on disagreement, which read literally deletes the fix for review finding M12. The column is necessary — a TTL cannot be measured against a wall-clock `TEXT`. **Plan's decision:** build the column; design §5.1's `challenges` line needs the same edit. Same for the three `bots` pool-history columns, which design §9.3 mandates in prose while design §5.1's column list omits them.

**G3. `challenges.status` and `games.status` have no `CHECK` constraints.** The five- and four-value sets are enforced only by code, so task 13 cannot test the set. **Plan's decision:** no `CHECK` constraints — role spec §3.1's DDL is normative and has none. Consequence stated so it is not mistaken for coverage: a typo'd status value stores fine.

**G4. Who owns the `seq` counter, and when does it reset?** Interfaces Part 2 pins `{run, seq}` and shows `server_run_started` at `seq: 0`. Nothing says whether `seq` restarts at 0 on a new run. **Plan's decision:** a module-level counter in `txn.py`, reset to 0 by `recover`, so `seq` is per-run and `server_run_started` is always 0 — which is what makes a client's gap check meaningful after it discards its buffer on a new `run`.

**G5. `TerminationReason.CRASH` still has no producer** (review M3). Not a 3a problem — no store code writes it — but the enum value exists and nothing sets it. Decide in 3b or drop it.

**G6. `arena_reports` is cut, but design §5.1 still specifies the table and its retention rule.** Role spec §3.5 and design §21 defer the whole vertical. **Plan's decision:** build none of it. Design §5.1's `arena_reports` block should be marked deferred rather than left reading as a build instruction.

**G7. Test directory. RESOLVED** — `tests/chess_server/`, matching interfaces Part 4 and the role spec. The `tests/server/` wording in the commissioning prompt was casual and the spec wins.

**G8. Interfaces Part 4 says `chess_server` tests use in-memory SQLite.** They cannot: WAL is unobservable on `:memory:` and two connections to `:memory:` are two databases, so the reader/writer split cannot be exercised. **RESOLVED** — file-backed `tmp_path` databases throughout, via a shared `tests/chess_server/conftest.py` fixture. Interfaces Part 4 has been corrected to say so, with the reason.

**G9. `pytest-asyncio` is a new dev dependency** and is not currently installed. It is test-only, so it does not breach "3a needs no new runtime dependency", but it is a dependency decision and is called out rather than slipped in. Fallback in task 1 if it will not install on 3.14.

