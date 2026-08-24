# Phase 3c — `chess_server/api/`

**Date:** 2026-08-24 · **Track:** server-engineer · **Prerequisite:** 3a and 3b committed and green.

## What this builds

The HTTP surface every other track binds to: app and lifespan, auth, the play protocol,
the mailbox and long-poll, SSE, control handoff, admin. Until this lands `run_supervisor`
is never started, `probe_db_writable` has no caller, and `state.mailbox` is written by
nobody.

## Authorities — read them, do not restate them

- `specs/roles/server-engineer-spec.md` — **primary**: §5 (mailbox/long-poll), §8
  (routes), §10 (admin), §11 (tests), §13 (acceptance).
- `specs/2026-08-23-chess-arena-design.md` — §8, §11, §12, §13.3, §14, §15, §16.
  **§4, §6 and §7.1 remain normative.**
- `specs/2026-08-23-chess-arena-interfaces.md` — **Part 2** (SSE) and **Part 5** (HTTP
  models) are the wire contract; bind field-for-field. Part 6 pins the MCP consumers.
- `AGENTS.md`.

*"Implement X per role spec §N"* means read §N. Copying normative text into a plan is
what made the phase 1–2 plans 90 KB, and duplicated normative text is what the pre-build
review punished throughout the server spec.

## Baseline and environment (verified)

- Python 3.14.3 in `.venv`. **Always `.venv/bin/pytest` and `.venv/bin/python`.**
- **498 tests passing at `703f0e1`, tree clean.** Every task ends green and committable.
- FastAPI, uvicorn and httpx are not installed; task 1 installs them.
- Tests in `tests/chess_server/`, file-backed SQLite under `tmp_path`, never `:memory:`.
  `asyncio_mode="auto"`. Reuse the existing fixtures (`store`, `deps`, `clock`, `sink`,
  `wake`, `seed_bots`, `make_game`, `poll`, and the three autouse resets); do not fork them.
- **Never pipe a verification through `tail`** — it buffers and the run looks hung.

## Mutation protocol

Eight shipped tests could not fail, and nine plan-specified mutations turned out
unfalsifiable against the test they were attached to. Therefore:

1. Every task names the mutation and **which test goes red**. Where the honest answer is
   *no change*, the task says so and names the check that does discriminate.
2. Apply, run the named test, confirm red, restore, confirm green.
3. **Clear caches between steps** or a stale `.pyc` reports the previous code's result:
   `find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +`
4. Mutation scripts go in `/tmp/mut_*.py`; heredocs inside a terminal chain wedge the shell.
5. A surviving mutation means the test is the defect. Fix the test; never weaken the mutation.

## Test-shape rules

- **No `asyncio.sleep` to wait for a poll.** A hold that must expire uses
  `hold_seconds = 0`, which fires deterministically. A hold that must be woken awaits the
  waiter registry's `on_register` hook, then wakes, then awaits the task.
- **`FakeClock` everywhere**; no test reads `time.monotonic_ns()`.
- **Route tests bypass the lifespan.** `create_app(state)` takes a pre-built `AppState`;
  the lifespan is exercised once, in task 1, via `app.router.lifespan_context`.
- Failure paths before happy paths.

## Layout

```
chess_server/api/  settings.py state.py app.py auth.py rate_limit.py models.py errors.py
                   validation.py routes_bots.py routes_play.py routes_challenges.py
                   routes_public.py sse.py featured.py health.py admin.py
chess_server/engine/mailbox.py   TurnPayload, waiters, the two HTTP delivery sites
```

## Decisions on what 3b handed over

| Handover | Decision | Task |
|---|---|---|
| `health_tick` has no emitter | `Supervisor` gains an inert `on_health` async hook awaited at the end of each `step()`; the app supplies one that publishes to the hub. Emission site stays the supervisor, as §8.4 pins it. | 17 |
| `probe_db_writable` has no caller | `GET /health` awaits it per request. `health_tick` does not probe — Part 2 has no `db_writable` field. | 17 |
| `run_supervisor` never started | Lifespan: `seed_anchors` → `recover` → ticker task → supervisor task, all before the socket accepts. | 1 |
| `TurnResponse.history_san` | A second process cache `state.history_san`, written by the same `txn.defer` that appends the FEN. No O(ply) read inside the writer's lock. `GET /games/{id}` reads `moves` on the reader instead — display-only, off the hot path. | 5 |
| `move_played.is_featured` | Stamped by the hub at publish time from `api/featured.py`, never by the transaction. Highest rating sum, held ≥ 20 s, ties to lowest `game_id` (interfaces "Decisions" §8). | 15 |
| `state.mailbox` unwritten | `engine/mailbox.py` fills it inside the same critical section as `deliver_position_locked`, via `txn.defer`. | 5, 8 |
| Anchor impersonation | `name` in `ANCHORS` and `owner == 'server'` both rejected `422`, case-insensitively. | 4 |
| `last_tick_mono` defaults to 0 | `run_ticker` seeds it before the loop; the lifespan seeds it before creating the task. | 17 |
| Presence reads `list_leaderboard()` | Yes, this is a `store/` change and it is made explicitly: add `BotRepo.list_presence_candidates()` (`role IN ('competitor','benchmark')`) and switch `step_presence`. `list_leaderboard()` then widens to include anchors, which is what `LeaderboardEntry.is_anchor` is for. | 2, 14 |

---

## Task 1 — dependencies, settings, app skeleton, lifespan

**Files:** `pyproject.toml`, `api/{settings,state,app}.py`, `tests/chess_server/test_app_lifespan.py`
**Spec:** role spec §8.6, §7.6; design §7.1.

Install, then record in `pyproject.toml` (`fastapi`, `uvicorn` in `dependencies`; `httpx` in `dev`):
`.venv/bin/pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27"`

**Do:**
- `settings.py` — frozen `Settings` from env: `db_path`, `join_code`, `admin_token`,
  `poll_hold_seconds` (default `POLL_HOLD_NS / 1e9`, injectable). Refuse to construct with
  an empty `JOIN_CODE` or `ADMIN_TOKEN`; an empty admin token equals a missing header.
- `state.py` — `AppState`: store, `EngineDeps`, settings, `TickerMetrics`, hub, waiters,
  limiter, featured selector, `matchmaking_paused`, the two tasks. Routes read it through
  one dependency and never import module globals.
- `app.py` — `create_app(state)` (no I/O) and `build_state(settings)`. Lifespan order:
  `open_store` → `seed_anchors` → `recover` → seed `metrics.last_tick_mono` → ticker task
  → supervisor task → yield → cancel both, await, `store.close()`.
- Seeding before recovery so `clear_monotonic_state` covers the anchors and recovery is
  the last write before the socket opens — `server_run_started` is then genuinely seq 0.
  Fix `seed_anchors`' docstring, which claims the reverse.

**Tests:** (1) startup with a pre-seeded `pending` game and live seat leaves it
`aborted`/`server_restart` with `seats` empty; (2) recorders on `seed_anchors`, `recover`
and the ticker factory produce exactly `["seed","recover","ticker","supervisor"]`;
(3) shutdown cancels and awaits both tasks and shuts the executor down; (4) empty
`ADMIN_TOKEN` raises at construction.

**Mutation:** create the ticker task above the `recover` call → test 2 red on the
sequence; **test 1 stays green**, because recovery still runs eventually. The ordering
assertion is the load-bearing one.

**Verify:** `.venv/bin/pytest tests/chess_server/test_app_lifespan.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: app skeleton and lifespan — recovery completes before the socket opens`

---

## Task 2 — store seams the API needs

**Files:** `store/{db,txn,repositories}.py`, `engine/ticker.py`, `tests/chess_server/{test_db,test_txn,test_bot_repo,test_tick_housekeeping}.py`
**Spec:** role spec §3.11, §3.6, §7.5.

**Do:**
- `Store` gains `reader_executor` (`max_workers=1`, `thread_name_prefix="sqlite-reader"`),
  shut down in `close()`. Display reads must not queue behind the writer, and one reader
  thread sidesteps every question about concurrent use of one `sqlite3.Connection`.
- `txn.current_seq()` — the last assigned seq, `-1` before anything is emitted.
  `/state.event_id` is defined as this.
- `BotRepo.list_presence_candidates()` (`role IN ('competitor','benchmark')`), and
  `step_presence` switches to it. `list_leaderboard()` is untouched here; task 14 widens
  it, and doing both at once would move presence silently.

**Tests:** (1) a read on `reader_executor` returns while a `critical_section` is held
open — wrap it in `asyncio.wait_for(..., 0.5)` and assert no `TimeoutError`;
(2) `current_seq()` is `-1` after `reset_seq()` and matches the last flushed event;
(3) `step_presence` emits `bot_connected` for a **benchmark** bot that polled —
impossible before this change.

**Mutation:** point `reader_executor` at the writer executor → test 1 red with
`TimeoutError`. Second: revert `step_presence` to `list_leaderboard()` → test 3 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_db.py tests/chess_server/test_txn.py tests/chess_server/test_bot_repo.py tests/chess_server/test_tick_housekeeping.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: reader executor, current_seq, and a real presence query`

---

## Task 3 — auth and rate limiting

**Files:** `api/{models,errors,auth,rate_limit}.py`, `tests/chess_server/{test_auth,test_rate_limit}.py`
**Spec:** role spec §8.7, §4 rule 8; design §8.6, §16.2.

**Do:**
- `models.py` — the Part 5 models needed so far, field names verbatim. Extend it per
  route; one model file, no duplicates.
- `errors.py` — every attendee-facing string from role spec §8.1, in one place. Routes
  never inline prose.
- `auth.py` — `require_bot`: parse bearer, `sha256`, `get_by_token_hash` on the **reader**,
  `compare_digest`, `401` with the pinned prose on any miss. `require_admin` compares
  against `settings.admin_token`.
- `rate_limit.py` — 20 req/s sustained, burst 40, keyed on **`token_hash`**, `OrderedDict`
  capped at 256 with LRU eviction; a separate bounded per-IP structure for `POST /bots` at
  `REGISTER_PER_IP_PER_MIN = 10`; `429` with `Retry-After: 3`. Time from `deps.now_mono`.

**Tests:** (1) missing, malformed, unknown and hash-mismatched tokens → four `401`s with
the exact prose; (2) a valid token authenticates and the raw token is absent from the
body; (3) 40 pass, the 41st is `429`, and exactly 20 more pass after advancing the clock
one second; (4) 300 garbage tokens leave 256 buckets; (5) per-IP limit trips and a second
IP is unaffected; (6) no bucket key equals a raw token.

**Mutation:** key the limiter on the raw token → test 6 red. Second: plain `dict` with no
eviction → test 4 red. Third: `==` instead of `compare_digest` → **no test change
expected**; timing is not observable here, so it is enforced by task 21's grep.

**Verify:** `.venv/bin/pytest tests/chess_server/test_auth.py tests/chess_server/test_rate_limit.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: bearer auth and bounded, hash-keyed rate limiting`

---

## Task 4 — `POST /bots` and `GET /bots/me`

**Files:** `api/{routes_bots,validation}.py`, `tests/chess_server/{test_register,test_bots_me}.py`
**Spec:** role spec §8.2; design §8.5, §10.4, §14, §16.2.

**Do:**
- `validation.py` — `^[A-Za-z0-9 _-]{1,32}$` on `name` and `owner`, plus the two
  reservations 3b handed over: no name matching `reference_bots.ANCHORS` and no
  `owner == "server"`, both case-folded. Without them an attendee registers as an anchor
  and the leaderboard, the anchor gate and `/admin/consistency` all read a bot that is
  not what they think it is.
- `POST /bots` — join code, name uniqueness, one-competitor-per-owner and the insert in
  **one** critical section, so two simultaneous registrations from one owner cannot both
  succeed. `secrets.token_urlsafe(32)`, stored as indexed `sha256`, returned once.
  `role ∈ {competitor, benchmark}`; `anchor` is not registrable. `txn.emit("bot_registered")`.
- `GET /bots/me` — Part 5 `MyBotResponse`; `is_provisional = games_played < 10`;
  `current_game_id` resolved through `seats`, never by scanning `games`.

**Tests:** (1) wrong join code → `400` and no row; (2) `role="anchor"` and `role="wizard"`
→ `400`; (3) `<img src=x onerror=1>`, a 33-char name and an empty owner → `422` each;
(4) `ref-greedy`, `REF-Greedy`, `owner="server"`, `owner="SERVER"` → `422` each;
(5) duplicate name → `400`; second competitor per owner → `409` naming the existing bot
and `role='benchmark'`; (6) two gathered registrations for one owner → exactly one `201`,
one `409`, one row; (7) happy path `201`, token authenticates `/bots/me`, `bot_registered`
emitted with no token in it; (8) `current_game_id` set when seated, `None` otherwise, and
`is_provisional` flips at exactly 10.

**Mutation:** delete the anchor-name reservation → test 4 red. Second: move the
uniqueness check out of the critical section into its own transaction → test 6 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_register.py tests/chess_server/test_bots_me.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: registration with reserved anchor identities, and GET /bots/me`

---

## Task 5 — `engine/mailbox.py`: payload, fill, and the SAN cache

**Files:** `engine/{mailbox,state,runner,games}.py`, `store/recovery.py`, `tests/chess_server/test_mailbox.py`
**Spec:** role spec §5.1–§5.3, §6.4; Part 5 `TurnResponse`.

**Do:**
- `state.history_san: dict[int, list[str]]` beside `state.history`: seeded `[]` in
  `create_game_locked`, appended in the **same** `txn.defer` that appends `fen_after`,
  popped in `_end_game_locked`, covered by `clear_all()` and the conftest reset. This is
  the `history_san` decision: the SAN is already computed there, so the alternative is an
  O(ply) `moves` read on the hottest path inside the writer's lock.
- `mailbox.py` — `TurnPayload` (frozen; exactly the `TurnResponse` fields plus `bot_id`),
  `fill_mailbox_locked(txn, bot, game)` deferring the dict write, and
  `deliver_for_poll(deps, bot_id)`: the **outer** form opening one `critical_section`,
  resolving the game through `seats`, calling `deliver_position_locked` then
  `fill_mailbox_locked`. Both HTTP delivery sites (tasks 7 and 11) call this and nothing
  else — §3.9 is why the route never opens its own section around it.
- Anchors get no mailbox entry; the mailbox is the transport for HTTP clients only.

**Tests:** (1) re-delivery is idempotent and free — two calls at different `FakeClock`
values leave `turn_started_mono` unchanged and the payload field-identical (a bot must
not restart its own clock by re-polling); (2) no seat → `None` and no entry; (3)
`history_san` after three plies is those three SANs in order and `legal_moves` is sorted
UCI for the payload's own FEN; (4) `history_san` is popped at finalisation and empty
after `clear_all()`; (5) `step_anchor_moves` leaves `state.mailbox` empty.

**Mutation:** write the mailbox directly instead of through `txn.defer`, then force the
enclosing transaction to roll back — a rollback test asserting an empty mailbox goes red.
Second: append the SAN outside the `defer` → the same rollback test for `history_san` red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_mailbox.py tests/chess_server/test_recovery.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: the mailbox and the SAN cache — nothing wrote either before now`

---

## Task 6 — one waiter per bot, supersede distinct from wake

**Files:** `engine/mailbox.py`, `tests/chess_server/test_waiters.py`
**Spec:** role spec §5.4; design §8.4.

**Do:**
- `Waiter(event, superseded=False)` and `WaiterRegistry` with `register`, `wake`,
  `discard(bot_id, waiter)` and `held_count()` (read by `/health.held_polls`).
- `register` supersedes any existing waiter: set `superseded = True` **then** `event.set()`.
  The loser returns `NoGameResponse(reason="superseded")` **without touching the mailbox**.
  Without the flag it either drains the payload — one position to two connections — or
  reports `waiting_for_pairing`, which is a different fact.
- `registry.wake` is what `EngineDeps.wake` binds to. Supersede cancels a *waiter*, never
  a *delivery*: the delivery is in the mailbox and the mailbox outlives the request.
- `on_register: Callable[[int], None] | None = None`, called once the waiter is in the
  dict. It exists so a test can observe registration without sleeping.

**Tests:** (1) two registrations — the first is flagged and set, the second is not, one
entry remains; (2) `wake` sets the event and does not set `superseded`; (3) `wake` for an
unregistered bot is a no-op (the ticker wakes non-polling bots on every pairing);
(4) `discard` removes only the waiter handed to it — a superseded waiter cleaning up must
not evict its successor; (5) `held_count()` tracks register/discard.

**Mutation:** set the event without the flag → test 1 red. Second: make `discard` do
`waiters.pop(bot_id)` unconditionally → test 4 red, which is the bug that silently
un-registers the live poll and leaves the bot never woken again.

**Verify:** `.venv/bin/pytest tests/chess_server/test_waiters.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: one waiter per bot, supersede distinct from wake`

---

## Task 7 — `GET /bots/me/turn`: the long-poll

**Files:** `api/routes_bots.py`, `engine/mailbox.py`, `tests/chess_server/test_turn.py`
**Spec:** role spec §5.2, §5.4–§5.6; design §8.2, §8.4; Part 5.

**Do:**
- Handler order **is** the specification: update `last_poll_at`/`last_poll_mono` (this
  endpoint and no other), **register the waiter**, then read the mailbox. Reading first
  loses any wake firing in the gap — a pairing, or auto-release — and the poll then hangs
  for the whole hold with a delivered position sitting in the mailbox.
- Resolve the seat; if the bot is to move and `controller == 'client'`, call
  `deliver_for_poll` and answer through `take_payload`.
- All six `reason` values per §5.5, always `200` with an explicit null `game_id`.
- Hold with `asyncio.timeout(settings.poll_hold_seconds)` around `waiter.event.wait()`;
  on wake, re-resolve once; `discard` in a `finally`. Default is `POLL_HOLD_NS / 1e9`
  (20 s) against the SDK's 30 s — the skew is deliberate.

**Tests** (`hold_seconds = 0` unless stated): (1) unauthenticated `401`, limited `429`;
(2) no seat → `waiting_for_pairing`; paused → `paused`; `role="benchmark"` → `no_seat`;
(3) opponent to move → `not_your_turn` **and no delivery** (`turn_started_mono`
unchanged); (4) `controller='agent'` → `agent_has_control` and no delivery — the agent's
delivery site is task 11's route; (5) supersede with a real hold, driven by the
`on_register` hook, and the second poll still receives the payload — no sleeps;
(6) wake path: poll, await `on_register`, `create_game_locked`, assert a `TurnResponse`;
(7) every Part 5 field present, with `time_control_ms`/`increment_ms` echoing an
exhibition game's own values; (8) only this endpoint moves `last_poll_mono` — `/bots/me`,
`/leaderboard` and `/state` leave it alone; (9) the default hold equals `POLL_HOLD_NS / 1e9`.

**Mutation:** read the mailbox before registering → test 6 red (the wake lands in the gap
and the poll runs to its hold). Second: return `waiting_for_pairing` from the superseded
branch → test 5 red. Third: drop the `last_poll` update → test 8 red and the pool starves.

**Verify:** `.venv/bin/pytest tests/chess_server/test_turn.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: GET /bots/me/turn — register before read, six reasons, one waiter`

---

## Task 8 — the mailbox is cleared on the side switch, plus the ply guard

**Files:** `engine/{runner,mailbox}.py`, `tests/chess_server/test_mailbox_staleness.py`
**Spec:** role spec §5.3 — *"the highest-cost rule in the document"*.

C1 from the review, and it gets its own task. Without it a bot re-polls after its own
move lands, drains the payload for the ply it just played, submits for that ply, takes a
`409`, discards and re-polls — a loop with no error, no log, and a request rate under the
limiter's threshold. It burns its clock and flags; the attendee sees *"my bot never moves"*.

**Do:**
- Pin the existing clear: `apply_move_locked` already defers `state.mailbox.pop(mover_id)`
  in the same critical section as the side-switch CAS. This task adds the test that makes
  removing it fail.
- Add the independent second layer in `mailbox.py`: `take_payload(bot_id, game)` discards
  and returns `None` when `payload.ply != game.ply` or `payload.game_id != game.id`. Two
  layers, because the clearing site is one line inside a long function.

**Tests:** (1) **two consecutive moves** — White polls and moves, then polls again with
`hold_seconds = 0`: the response is `NoGameResponse(reason="not_your_turn")`, not a
`TurnResponse`, and `state.mailbox` holds no entry for White; Black then moves and
White's third poll receives the **new** ply, with no `409` anywhere in the loop;
(2) **ply guard alone** — hand-write a stale `TurnPayload` for the previous ply into
`state.mailbox[white]` and assert the poll discards it, still answers `not_your_turn`,
and the entry is gone; (3) both mailboxes empty after a terminal transition.

**Mutation:** run two, separately. (a) Delete the `txn.defer(... mailbox.pop ...)` in
`apply_move_locked` → **test 1 red**, test 2 green. (b) Delete the ply comparison in
`take_payload` → **test 2 red**, test 1 green. Each layer is proved to carry its own weight.

**Verify:** `.venv/bin/pytest tests/chess_server/test_mailbox_staleness.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: clear the mover's mailbox on the side switch, and guard on ply`

---

## Task 9 — `POST /games/{id}/moves`

**Files:** `api/routes_play.py`, `tests/chess_server/test_route_moves.py`
**Spec:** role spec §6.1, §6.2, §8.1; design §8.3, §13.3; Part 5.

**Do:**
- One outer `apply_move(...)` per request; the route opens no critical section. Map the
  `MoveOutcome` union the runner already returns: `Applied` → `200`; `Rejected` → `400`
  with `legal_moves` and `fen` in `details`; `NotDelivered` → `409` with the "call GET
  /bots/me/turn first" prose; `WrongController` → `403`; `Flagged` → `409` carrying the
  terminal state; `CASConflict` → `409` with `{ply, fen, status}` and the discard-and-
  re-poll prose.
- The `controller` check stays **inside the transaction with the CAS** (runner step 2).
  The route passes `controller="client"`; task 11's agent route passes `"agent"`. No
  route may pre-check it and drop the parameter.
- `client_reported_ms` passes through untouched; it is diagnostics only.

**Tests:** (1) illegal move → `400` with sorted `legal_moves` and `fen`, game still
active, strike **committed**; three → `illegal_forfeit` (§11.6); (2) not in the game →
`403`; stale ply → `409` with `{ply, fen, status}`; undelivered → `409`; (3) agent
controller on the client route → `403` with no strike and no clock change; (4) flag
precedes validation — clock past expiry plus an *illegal* move gives `flag`, not a strike
(§11.7); (5) a rejected move leaves `turn_started_mono` unchanged; (6) happy path fields
match Part 5, including `result`/`termination` on a mating move; (7) no token in any body.

**Mutation:** hoist the controller check into a pre-check before `apply_move`. Test 3
alone **stays green** — so the task requires a gathered test gathering `release_control`
with a move, and **that** test goes red. Second: return `400` instead of `409` for
`NotDelivered` → test 2 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_route_moves.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: POST /games/{id}/moves — outcomes mapped, authorisation inside the CAS`

---

## Task 10 — `POST /games/{id}/resign`

**Files:** `api/routes_play.py`, `engine/games.py`, `tests/chess_server/test_route_resign.py`
**Spec:** role spec §6.5, §8.1; Part 5; design §22.

**Do:** `resign_game(deps, game_id, bot_id, from_ply)` — one outer critical section
calling `finalise_game_locked` with `opposite_win` of the **resigner's** colour, not of
the side to move, and `RESIGNATION`. Rated normally: resignation is not in §6.5's
rule-1 unrating set. CAS on `(status, ply)` so a resignation racing the final move loses
cleanly. `403` with no seat in that game, or `controller='agent'`.

**Tests:** (1) not in the game → `403`; wrong ply → `409`; finished game → `409`;
(2) agent controller → `403`; (3) **Black resigns while White is to move → `white_win`**;
(4) two `rating_history` rows and empty `seats` afterwards.

**Mutation:** use `opposite_win(game.to_move)`. Test 3 red; 1, 2 and 4 stay green — a
one-assertion defect, stated as such.

**Verify:** `.venv/bin/pytest tests/chess_server/test_route_resign.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: POST /games/{id}/resign — the resigner loses, whoever is to move`

---

## Task 11 — control handoff and the agent delivery site

**Files:** `api/{routes_bots,routes_play}.py`, both spec documents, `tests/chess_server/test_control.py`
**Spec:** role spec §8.3, §5.2; design §13.3; Part 5 `SetControl*`, Part 6
`get_legal_moves` / `take_control`.

**Do:**
- `POST /bots/me/control` — field is `action` (`take` | `release`); anything else `400`
  naming both. `take` is refused `409` **whenever the bot holds a `seats` row**, not
  "while a rated game is in progress", which is not evaluable. `take` wakes any held poll
  → `agent_has_control`. Neither action touches `turn_started_mono`: a bot must not pause
  its own clock by switching controller. One critical section for the read, the
  `controller` CAS and the `last_agent_action_mono` write.
- **`GET /games/{id}/legal_moves`** — authenticated, Part 6's `LegalMovesResult` shape.
  It is the agent's delivery site (§5.2): calls `deliver_for_poll` when
  `controller='agent'`, `403`s with Part 6's prose when `'client'`. **This route is in
  neither endpoint inventory**, while both documents require the behaviour behind it, so
  this task adds it to design §8.1 and interfaces Part 5 in the same change per
  `AGENTS.md`. See gap 1; it is the only route this plan adds.
- `last_agent_action_mono` is written by both control actions, by `legal_moves`, and by
  `POST /games/{id}/moves` when the bot's controller is `agent`.

**Tests:** (1) `action="pause"` → `400` naming both; unauthenticated `401`; (2) `take`
while seated — **both `pending` and `active`** — `409`; while free `200`; (3) `take`
alters neither `turn_started_mono` nor either clock column; (4) `take` wakes a held poll
→ `agent_has_control`, driven by the register hook; (5) `legal_moves` with `'client'` →
`403`; with `'agent'` on an undelivered position the game goes `pending → active` and a
second call does not restart the clock; (6) an agent-controlled bot is absent from
`list_pool_candidates`, so no rated game is created for it right after the `409`;
(7) auto-release — advance past `AGENT_AUTO_RELEASE_NS`, drive `step_agent_release`,
assert `client` and a woken waiter.

**Mutation:** predicate the refusal on `status == 'active'` → test 2's `pending` case red.
Second: drop the `last_agent_action_mono` write from `legal_moves` → test 7 red, the agent
released mid-thought.

**Verify:** `.venv/bin/pytest tests/chess_server/test_control.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: control handoff, and the agent delivery route both specs implied`

---

## Task 12 — challenges

**Files:** `api/routes_challenges.py`, `tests/chess_server/test_route_challenges.py`
**Spec:** role spec §7.2, §8.1; design §12; Part 5.

**Do:**
- `POST /challenges` — opponent by name, `time_control ∈ {rated, exhibition}` mapped to
  the `chess_core` constants, `201`, status `open`. `409` on an existing open outgoing
  challenge, on either bot holding a seat, or on either being agent-controlled for a rated
  challenge. `400` on an unknown opponent, a bad `time_control`, or a self-challenge —
  otherwise the `seats` PK kills it later as `seat_unavailable`, which reads as a server
  fault rather than an input error.
- `accept` → `queued` (never `accepted`; that status does not exist). `decline` →
  `declined`. Both: `403` if not the opponent, `404` unknown, `409` if no longer `open`.
- `GET /challenges` — `incoming`/`outgoing` per Part 5.
- Every transition buffers `challenge_updated` with the full Part 2 payload including
  `game_id` and `reason`. No silent drops.

**Tests:** (1) unknown opponent, self-challenge, bad `time_control` → `400` each;
(2) second open outgoing, challenger seated, opponent seated → `409` each; (3) accept by
a non-opponent `403`, twice `409`, expired `409`; (4) round trip create → inbox → accept
→ one tick → `consumed` with a real `game_id`, and the `challenge_updated` sequence is
exactly `["created","queued","consumed"]`; (5) exhibition → `rated == 0`, its own time
control echoed in the turn payload; (6) **seat collision** (§11.12): a challenge racing a
pairing yields exactly one game and the loser is `expired` with
`reason == "seat_unavailable"` plus the event that says so.

**Mutation:** drop the emit from the accept path → test 4 red on the sequence. Second:
allow a second open outgoing challenge → test 2 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_route_challenges.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: challenge routes — no silent drops, one open outgoing per bot`

---

## Task 13 — the SSE hub and `GET /events`

**Files:** `api/sse.py`, `store/recovery.py`, `tests/chess_server/test_sse.py`
**Spec:** role spec §8.4; design §14; Part 2.

**Do:**
- `Hub.publish(seq, event_type, data)` is the `EventSink` bound into `EngineDeps`, so
  every event arrives through `Txn.flush` — **after commit, never before**. No route
  writes to the hub directly, and nothing inside a transaction may.
- Envelope `{"run": current_run_id(), "seq": seq, "event_type": ..., "data": ...}`,
  serialised as `event:` / `data:` / `id:`.
- **`server_run_started`'s payload is wrong today**: `recover_locked` emits `{"run": run}`
  while Part 2 pins `data: {run_id, started_at}` with `run` in the envelope. Fix
  `recovery.py` and `test_recovery.py` here.
- Per-client bounded queue of 256, **drop-oldest**; a dropped client refetches `/state`.
  A stalled tab must never apply backpressure to the game loop.
- 15 s heartbeat comments as a float-seconds constant — `15_000` would collide with
  `ns_to_ms(DELIVERY_GRACE_NS)` in the no-literals guard. Expose `sse_clients`.

**Tests:** (1) 300 `publish` calls with no consumer never block, and the surviving window
is the **last** 256; (2) a rolled-back unit publishes nothing and consumes no `seq` —
`current_seq()` unchanged (§11.2's event half); (3) one of each event type checked
field-by-field against Part 2, including `server_run_started.data == {run_id, started_at}`;
(4) `seq` strictly increases and restarts at 0 with a new `run` after `reset_seq()`;
(5) no payload contains a token substring or an `owner` key; (6) a disconnected client is
removed and `sse_clients` drops.

**Mutation:** drop the **newest** event when full → test 1 red. Second: write to the hub
directly inside a route's critical section, then force a rollback → test 2 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_sse.py tests/chess_server/test_recovery.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: SSE hub — bounded, drop-oldest, and nothing visible before commit`

---

## Task 14 — the public read routes

**Files:** `api/routes_public.py`, `store/repositories.py`, `tests/chess_server/test_routes_public.py`
**Spec:** role spec §8.1; design §10.4, §14; Part 5.

**Do:** all four read on the **reader** connection and executor, outside `write_lock`.
- `GET /leaderboard` — widen `list_leaderboard()` to `role IN ('competitor','anchor')`:
  benchmarks are hidden by design §10.4, and `LeaderboardEntry.is_anchor` exists so
  anchors are shown and marked. Task 2 already moved presence off this query.
  `is_provisional = games_played < 10`.
- `GET /games/{id}` — `GameDetailResponse`; `history_san` read from `moves` on the reader,
  **not** the process cache, because this route must answer for finished games too. It
  **never delivers**.
- `GET /games/{id}/moves` — `GameMovesResponse` with `starting_fen`, `final_ply`, both
  strike counts and per-entry `server_elapsed_ms`, `client_reported_ms`, `white_ms_after`,
  `black_ms_after`. `analyze_game` builds its PGN from exactly these; the server returns
  data, not Markdown.
- `GET /bots/{bot_id}/rating_history` — `RatingHistoryResponse`, `404` unknown bot.

**Tests:** (1) `404` with the pinned prose for an unknown game and bot, `422` for a
non-numeric id; (2) `/leaderboard` excludes benchmarks, includes anchors with
`is_anchor == true`, orders by rating then name; (3) `/games/{id}` on an active game does
**not** deliver — `turn_started_mono`, `delivered_to_mover` and `status` unchanged;
(4) three plies return three entries in order with per-colour clocks non-increasing and
null `client_reported_ms` where none was sent; (5) `history_san` from a **finished** game
matches what the cache held while it was live; (6) a name with `_`, `-` and spaces
round-trips unescaped — validation, not escaping, is this layer's job.

**Mutation:** make `/games/{id}` call `deliver_for_poll` → test 3 red. Second: build its
`history_san` from `state.history_san` → test 5 red, the cache entry having been dropped
at finalisation.

**Verify:** `.venv/bin/pytest tests/chess_server/test_routes_public.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: public read routes on the reader connection; anchors on the leaderboard`

---

## Task 15 — featured game selection and move coalescing

**Files:** `api/{featured,sse}.py`, `tests/chess_server/{test_featured,test_units}.py`
**Spec:** design §11, §14; Part 2 `move_played.is_featured`; Part 5
`ActiveGameSummary.is_featured`; interfaces "Decisions" §4 and §8.

**Do:**
- `FeaturedSelector.current(summaries, now_mono)` — highest `white_rating +
  black_rating`, ties to lowest `game_id`, **held ≥ `FEATURED_HOLD_NS` (20 s)** before
  switching, re-selected immediately when the held game leaves the active set. Pure apart
  from the injected clock.
- This is the `is_featured` decision: the hub stamps it at **publish** time. Featured is a
  presentation choice keyed on process state; inside the transaction it would make a
  committed row depend on who is watching.
- Coalescing: after publishing a `move_played` for a non-featured game, suppress that
  game's `move_played` for `MOVE_COALESCE_NS = 500_000_000`. Featured bypasses it. The map
  clears on `game_ended` and on `/admin/reset`.
- `FEATURED_HOLD_NS` equals `POLL_HOLD_NS` numerically and is unrelated semantically,
  exactly like the supervisor's thresholds — add a `featured.py` entry to `ALLOWED` in
  `test_units.py` rather than importing a poll constant into a display module.

**Tests:** (1) a higher-rated game arriving at 19 s does not switch, at 21 s it does;
(2) the held game ending switches immediately; (3) ties go to the lowest `game_id` and
selection is stable across repeated identical calls; (4) five non-featured moves inside
500 ms publish one, the sixth at 501 ms publishes, and five featured moves publish five;
(5) `is_featured` is true on exactly the featured game and agrees with `/state`;
(6) an empty active set gives `None` and no crash.

**Mutation:** drop the hold and re-select every call → test 1 red. Second: throttle
featured games too → test 4's second half red. Third: stamp `is_featured` inside
`apply_move_locked` → **no functional test changes**; caught by task 21's grep asserting
`is_featured` appears in no `engine/` module.

**Verify:** `.venv/bin/pytest tests/chess_server/test_featured.py tests/chess_server/test_units.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: featured-game selection and 2 Hz coalescing, stamped at publish`

---

## Task 16 — `GET /state`

**Files:** `api/routes_public.py`, `tests/chess_server/test_state.py`
**Spec:** role spec §8.4; design §14; Part 5 `DashboardStateResponse`.

**Do:** `run_id`, `event_id = current_seq()`, `active_games`, `leaderboard`,
`featured_game_id`. `ActiveGameSummary` carries `fen`, `to_move`, `status`, both ratings,
`rated`, `is_featured` and `turn_elapsed_ms` via `compute_turn_elapsed_ms`, **`None`
while undelivered**. Use `list_active_summaries()`; do not add a second query. Read
`event_id` **after** the summaries, so a client applying `id > event_id` cannot skip an
event that landed between the two reads — say so in one comment line.

**Tests:** (1) `turn_elapsed_ms` is `None` before delivery and a positive int after, and
is never derived by a subtraction written in `api/`; (2) `event_id == current_seq()` and
grows after a game is created; (3) `pending` games appear — `status` is what
distinguishes paired-but-undelivered; (4) `featured_game_id` agrees with exactly one
summary's flag, or is `None` with no active games; (5) `/state` neither delivers nor
touches `last_poll_mono`.

**Mutation:** read `event_id` before the summaries and interleave a `create_game_locked`.
Test 2's stronger form — the created game appears in the summaries only if its `seq` is
`<= event_id` — goes red. Second: return `0` rather than `None` for an undelivered
`turn_elapsed_ms` → test 1 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_state.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: GET /state — one snapshot the dashboard can render from alone`

---

## Task 17 — `/health`, the `health_tick`, and the metrics seed

**Files:** `api/{health,app}.py`, `engine/{supervisor,ticker}.py`,
`tests/chess_server/{test_health,test_supervisor}.py`
**Spec:** role spec §7.6, §8.1; design §4.6; Part 5 `HealthResponse`, Part 2 `health_tick`.

Three engine handovers land here.

**Do:**
- `build_health(state, db_writable)` — one snapshot builder for every `HealthResponse`
  field, on the reader. `stalled_games` := non-terminal games with
  `delivered_to_mover = 0`; `pooled_bots` from `list_pool_candidates`; `held_polls` from
  `WaiterRegistry.held_count()`; `sse_clients` from the hub.
- `GET /health` awaits `probe_db_writable(deps)` — the missing caller. It is a **real
  write probe**: `BEGIN IMMEDIATE` takes the RESERVED lock, which is exactly what fails
  when a cancelled rollback leaves the writer mid-transaction. A `SELECT 1` reports
  healthy straight through that failure.
- `health_tick`: `Supervisor` gains `on_health: Callable[[], Awaitable[None]] | None`,
  awaited at the end of every `step()`; the app supplies one that publishes the Part 2
  subset **unbuffered** — it reports process state, belongs to no transaction, and would
  otherwise never be emitted. Period is `SUPERVISOR_PERIOD_SECONDS` (2 s).
- Seed `metrics.last_tick_mono = deps.now_mono()` as the first statement of `run_ticker`
  **and** in the lifespan before the task is created. `0` reads as infinitely stale, so a
  supervisor step landing before the first completed tick restarts a healthy ticker.

**Tests:** (1) **the seed** — fresh `TickerMetrics`, start `run_ticker`, run one
`Supervisor.step()` before any tick completes, assert `ticker_restarts == 0`;
(2) `db_writable` is false with the writer left mid-transaction by hand and true
afterwards — a read probe passes both, which is why the write probe is required;
(3) every `HealthResponse` field present and typed, `stalled_games` counting an
undelivered non-terminal game and stopping once delivered; (4) `held_polls` follows
register/discard; (5) `health_tick` is published once per step, carries exactly the Part 2
fields, and is published even while a critical section is open elsewhere; (6)
`last_tick_age_ms` grows with `FakeClock` and is derived through `chess_core`.

**Mutation:** delete the `run_ticker` seed → test 1 red. Second: make
`probe_db_writable` run `SELECT 1` → test 2 red. Third: buffer `health_tick` through a
`Txn` → test 5 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_health.py tests/chess_server/test_supervisor.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: /health with a real write probe, health_tick, and a seeded tick clock`

---

## Task 18 — the admin router

**Files:** `api/admin.py`, `tests/chess_server/test_admin.py`
**Spec:** role spec §10, §8.5; design §15; Part 5.

**Do:** one `APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])`; every
route `401`s before doing anything else.
- `abort` — outer critical section calling `abort_game_locked` with `ADMIN_ABORT`:
  `rated=0`, seats freed, mailboxes cleared, waiters woken, `game_ended` buffered. Racing
  the ticker is fine — exactly one CAS gets `rowcount == 1`.
- `pause` / `resume` — the single `AppState.matchmaking_paused` that `EngineDeps.is_paused`
  already reads. Global only; there is no per-bot pause.
- `POST /admin/bots/{name}/token` — `409` while the bot holds a seat; new token and hash,
  plaintext returned once, the old token stops authenticating.
- `GET /admin/consistency` — `rating == STARTING_RATING + sum(deltas)` for
  **`role='competitor'` only**; anchors have fixed non-1200 ratings and no history rows,
  so including them leaves the one alarm that catches double-rating permanently red.
  Call it once in the lifespan and log loudly on a mismatch.

**Tests:** (1) each admin route with no token, a wrong token and a **bot** token → `401`;
(2) abort finished → `409`, unknown → `404`; (3) abort live → `aborted`/`admin_abort`,
`rated == 0`, no `rating_history` row, seats and mailboxes empty, both waiters woken, one
`game_ended`; (4) reissue seated → `409`; reissue free → old token `401`s, new one works,
and `caplog` contains **neither token**; (5) `pause` stops `_tick_once` creating games and
`resume` restores it; (6) consistency on a healthy server **with anchors that have played**
is `true` with no violations, and a hand-corrupted competitor rating is reported with
`expected_rating`, `actual_rating`, `delta_sum`.

**Mutation:** widen the consistency scope to all bots → test 6's first half red, which is
why that test must seed an anchor game rather than an empty database. Second: drop the
seat check from reissue → test 4 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_admin.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: admin router — abort, pause, reissue, and a competitor-scoped alarm`

---

## Task 19 — `POST /admin/reset`

**Files:** `api/admin.py`, `store/repositories.py`, `tests/chess_server/test_admin_reset.py`
**Spec:** role spec §10.1 (specified in full there); design §15.

**Do:**
- **Refuse `409` unless matchmaking is already paused**, with prose naming the pause
  endpoint. The reset is safe under `write_lock`, but the next tick re-pairs the same
  twenty still-polling bots, so the operator would be reading a `ResetResponse` describing
  a clean slate that no longer exists.
- One critical section. Wipe `games`, `moves`, `rating_history`, `seats`, `challenges`
  (deleted, not expired — a `queued` challenge is an intent to create a real game in the
  next run). Reset `bots`: `rating` to `STARTING_RATING` **for `is_anchor = 0` only**,
  counters to 0, pool history cleared, `controller='client'`, all three monotonic fields
  NULL. Identities, tokens and anchor ratings survive.
- Process state: mailbox, history, `history_san`, `unpaired_ticks`, presence, the
  coalescing map. Rate-limiter buckets are **not** cleared — keyed on `token_hash`, which
  survives, and they are about request rate.
- Regenerate the run id and emit one `server_run_started`. **No per-game `game_ended`**:
  that would announce terminations whose games `GET /games/{id}` then 404s. Held polls
  wake and return `paused`, which is exactly true because the pause is a precondition.

**Tests:** (1) reset while running → `409` and **nothing wiped**; (2) with a game in
flight — tables empty, counts correct, new `run_id`, exactly one `server_run_started` and
**zero** `game_ended`; (3) anchors keep 800/1000/1200 while a 1350 competitor returns to
`STARTING_RATING`; (4) `/admin/consistency` is green immediately afterwards, because
ratings and history were wiped together; (5) tokens still authenticate and bot ids are
unchanged; (6) a held poll returns `paused`, not `superseded`; (7) `state.history_san` and
the coalescing map are empty.

**Mutation:** reset anchor ratings too → test 3 red. Second: wipe `rating_history` but
leave `bots.rating` → test 4 red. Third: drop the pause precondition → test 1 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_admin_reset.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: POST /admin/reset — paused-only, identities survive, one run change`

---

## Task 20 — the fake-bot harness

**Files:** `tests/chess_server/{harness,test_integration_api}.py`
**Spec:** role spec §11.13, exercising §11.3, §11.5, §11.9 and §11.11 end to end.

**Do:** extend the existing `test_harness.py` scripted bots into an HTTP harness — an
in-process client that registers, polls `GET /bots/me/turn`, submits over the real routes
and follows the documented `409` behaviour (discard and re-poll, never retry the same
move). Ticks are driven by explicit `_tick_once` calls, never the 1 s loop. Every scenario
asserts a terminal state **and** exactly one set of side effects: one `game_ended`, the
right number of `rating_history` rows, `seats` empty.

**Scenarios, one test each:** (1) checkmate over the real endpoints; (2) threefold from
the start — `Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8` → `threefold` (§11.9; a `moves`-only history
returns `(False, None, None)`); (3) three illegal moves → `illegal_forfeit` with the
strikes visible in `GET /games/{id}/moves`; (4) flag; (5) mid-game abandonment → rated
loss for the absent side; (6) no-show at ply 0 → unrated abort, the present bot re-paired;
(7) superseded poll **and** supersede-versus-delivery — the winner receives the position
the loser was superseded out of; (8) control handoff: take, refuse-while-seated, release,
auto-release; (9) admin abort with a move in flight, and `/admin/reset` with games in
flight; (10) the delivery sweep terminates (§11.3) — finish a game, twenty ticks,
`consecutive_tick_errors == 0`, pairing still happening on tick 20; (11) restart recovery
(§11.11) through the lifespan **with the monotonic baseline moving backwards**: rebuild
with a lower `FakeClock` and assert no bot is treated as currently polling.

**Mutation:** two suffice, the unit tests carrying the rest. Remove the
`status IN ('pending','active')` filter from `list_undelivered_non_terminal` → scenario 10
red. Make supersede drain the mailbox → scenario 7 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_integration_api.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: fake-bot harness — complete games over the real endpoints`

---

## Task 21 — the discipline sweep

**Files:** `tests/chess_server/{test_api_discipline,test_units}.py`
**Spec:** role spec §4 rules 7–8, §3.9, §13 items 3, 20, 21; design §16.2.

Static checks, because these are the properties no functional test observes.

1. **Every route handler is `async def`** — walk `app.routes` and assert
   `asyncio.iscoroutinefunction(route.endpoint)`. A `def` handler runs on the shared
   thread pool and can deadlock against the writer.
2. **One `write_lock` acquire per call stack** — AST-check `api/`: no function body calls
   `critical_section` twice, and no `api/` function calls a `*_locked` helper from outside
   one. Extend `test_locking_discipline.py`'s conventions; do not invent a second mechanism.
3. **No token leaks** — register three bots, play a game to completion with `caplog` at
   `DEBUG`, and assert no plaintext token appears in any log record, error body, SSE
   payload or limiter key; plus a source grep for `token` inside a logged f-string.
4. **No monotonic arithmetic in `api/`** — grep for `monotonic_ns()` and `- *_mono`;
   readings come from `deps.now_mono` and comparisons go through `chess_core`.
5. **No HTML from the server** — every route's media type is `application/json` or
   `text/event-stream`.
6. **No named constant as a literal** — add task 15's `featured.py` entry to `ALLOWED` and
   confirm nothing else in `api/` trips the guard.
7. **`is_featured` appears in no `engine/` module** — it is a publish-time stamp.

**Mutation:** turn one handler into `def` → check 1 red. Second: add
`logger.info("token=%s", token)` to the register route → check 3 red. Third: add a second
`critical_section` to a route body → check 2 red.

**Verify:** `.venv/bin/pytest tests/chess_server/test_api_discipline.py tests/chess_server/test_units.py -q` then `.venv/bin/pytest -q`
**Commit:** `phase3c: discipline sweep — async handlers, one lock acquire, no token leaks`

---

## Gaps — what the specs do not answer

Decisions this plan makes explicitly; each should be reflected back into the specs.

1. **`GET /games/{id}/legal_moves` is in no endpoint inventory.** Role spec §5.2 requires
   *"the route behind the MCP `get_legal_moves()` tool delivers when `controller='agent'`"*,
   design §13.3 requires agent-path delivery, and Part 6 pins `LegalMovesResult` — but
   design §8.1 and Part 5 list no route. It cannot fold into `GET /bots/me/turn`, because
   the agent and the SDK present the same bearer token while §13.3 requires no window in
   which the SDK still believes it may move. Task 11 adds the route and both spec entries.
2. **`get_game()` cannot refresh `last_agent_action_mono`** — it maps to the
   unauthenticated `GET /games/{id}`. The refresh happens on the three authenticated agent
   paths only. The exposure is a read-only agent released after 45 s; it must call
   `get_legal_moves` before moving anyway, which refreshes.
3. **Anchors on the leaderboard is inferred.** Design §10.4 hides only benchmarks, and
   `LeaderboardEntry.is_anchor` is otherwise a field with no true value. Task 14 includes
   them; reversing it is a one-line query change affecting no other consumer.
4. **`stalled_games` is defined nowhere.** Task 17 defines it as non-terminal games with
   `delivered_to_mover = 0`.
5. **`health_tick` cadence.** Part 2 says ~3–5 s; the supervisor's period is 2 s. Task 17
   emits per step rather than adding a second timer — more often than specified, never less.
6. **`challenge_updated.status` vocabulary.** Part 2's prose lists `accepted` and
   `cancelled`; role spec §3.1 deletes both from the schema. The five schema values win and
   Part 2's prose should be corrected when this lands.
7. **`SubmitMoveResponse` carries no strike count**, so a bot sees its position only in the
   `400` prose or via `GET /games/{id}/moves`. Left as-is; adding a field diverges from Part 5.
8. **`Retry-After: 3` is chosen, not specified.** Design §8.6 requires the header, not a
   value. Three seconds is well above the ~50 ms a full bucket needs for one token and low
   enough not to stall a bot for a whole move.
9. **`arena_reports` is not built** — no table, route, model or event (role spec §3.5,
   design §21). A task that appears to need it is wrong.
