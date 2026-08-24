# Spec Review — Chess Arena Full Spec Set, Round 4 (Adversarial)

| | |
|---|---|
| **Reviewed** | The complete spec set: `2026-08-23-chess-arena-design.md` (rev 4), `2026-08-23-chess-arena-interfaces.md`, all six role specs in `docs/superpowers/specs/roles/` plus `roles/README.md`, `agent-reports/2026-08-24-spec-harmonisation.md` (including §15 override), rounds 1–3, and `AGENTS.md`. ~7,000 lines across nine documents. |
| **Commit** | `af4601e` |
| **Date** | 2026-08-24 |
| **Reviewer role** | `design-adversary` — pre-build attack on documents |
| **Verdict** | **Not cleared beyond phase 2.** Phases 1 and 2 clear with two fixes. **Phase 3b is blocked by two defects that stop the server working at all** — nothing in the spec set calls delivery, and the ticker as specified deadlocks itself on the first flag. Phases 5 and 6 are blocked on seams that have a consumer and no producer. The round-3 criticals stayed fixed; the damage is in the role-spec split and the two late passes, exactly where the brief predicted. |

**New issues:** 8 critical · 20 significant · 14 minor/gaps · 9 over-engineering.
**Prior-round regression check:** round 3's four criticals — **all four still fixed, verified by execution.** But **eleven** round-3 recommendations were never applied, and **three round-3 fixes were lost when the role specs were written** (R4-C1, R4-C3, R4-C5).

Everything asserted below about SQLite, asyncio and Elo was **executed** against `sqlite3` 3.51.3 / CPython, not reasoned about. Method and output in §6.

### Per-phase build readiness (§20)

| Phase | Ready? | Blocker |
|---|---|---|
| 1 `chess_core` rules / clock / elo / match | **Yes, after R4-C8** | The flag predicate is stated two ways in three documents and the named test contradicts the normative rule. One line, but it is the one place where being wrong is silent. |
| 1 `chess_core/matchmaker.py` | **No** | **R4-S19.** Round 3's N3-S3 was half-fixed: `unpaired_ticks` was added to `PoolEntry`, but rule 2 is still not an algorithm and "one relaxed side suffices" is still unstated. Also `should_offer_anchor` has no caller anywhere (R4-S11). |
| 2 `arena.py` + starter kit | **Yes, after R4-S9** | Depends only on rules/clock/elo. But `--serve` and `--report` are both normative in the same document and contradict each other in prose. Delete one paragraph. |
| 3a `store/` | **Yes, after R4-S20** | Schema, CAS and transaction discipline are correct and were verified. `seats.bot_id` still lacks `NOT NULL` — round 3 asked twice; a `NULL` insert is accepted and silently steals bot 1's seat. |
| 3b `api/` + ticker | **No** | **R4-C1** (nothing triggers delivery — every game dies at ply 0), **R4-C2** (the ticker deadlocks itself), R4-C3, R4-C4, R4-C5, R4-C6, R4-S15, R4-S18. |
| 4 `chess_client` SDK | **No** | R4-S4 (supersede hot loop, and `reason:"superseded"` is unproducible as specified), R4-S8 (the SDK is told both to resign and to continue on an illegal move). |
| 5 MCP | **No** | **R4-C3.** Three of eleven tools call endpoints that exist in no producing spec; `take_control`/`release_control` have no HTTP surface anywhere. R4-S5 (join code bypassed), R4-S6, R4-S7. |
| 6 Dashboard + SSE | **No** | **R4-C7** (XSS), R4-S1 (`/state` carries no position, so no board can be drawn and no game is ever featured), R4-S2, R4-S3. |
| 7 Admin | **No** | R4-S16, R4-S17 — both are round-3 findings that were never applied. |
| 8 Claude layer | **Yes** | `AGENTS.md` is consistent with rev 4, including the `arena_reports` display-only invariant and the reviewer split. |

The three that matter most:

1. **No document in the set says who calls delivery.** `deliver_position()` is defined in `server-engineer-spec.md` §2.2 and is called from nothing. The ticker's six-step inventory (§5) does not include delivery; the poll handler reads the mailbox and, finding it empty, waits. So a paired game is never delivered, `DELIVERY_GRACE_MS` fires 15 seconds later, and **every game in the workshop ends `no_show` at ply 0**. This is round 3's N3-C4 — "the delivery trigger is never named" — which was recommended, never applied to the design spec, and then faithfully reproduced as a hole in the role spec.
2. **The ticker deadlocks against itself on the first flag or abandonment.** `_tick()` opens `critical_section(...)` at the top, and steps 3 and 4 call `abort_game()` / `finalise_game()`, whose own definitions say "all under `critical_section`". `asyncio.Lock` is not reentrant — verified: the inner acquire never returns. The ticker then never ticks again, and §4.6's supervisor only *logs*, because round 1's C1 residue ("state the supervisor's action, not just its detection") was never closed. Pairing and flagging stop for the rest of the day behind a red banner.
3. **`take_control` / `release_control` have no HTTP endpoint in any producing document.** `mcp-engineer-spec.md` §7.1 calls `POST /bots/me/control`, `GET /bots/me` and `GET /games/{id}/moves`; none appear in design §8.1, interfaces Part 5, or `server-engineer-spec.md`'s route list or §4.1 inventory. `server-engineer-spec.md` §12 does **not** claim §13.3, and `mcp-engineer-spec.md` claims "§13 in full" — so the one role that explicitly may not write server routes owns the surface. This is round 3's N3-S9 verbatim, and it is the same class of failure the orchestrator's coverage check caught once (§10.4) and reports as closed.

---

## 1. Verification of prior rounds

### 1.1 Round 3's four criticals — all still fixed

| # | Round-3 critical | Status | Justification |
|---|---|---|---|
| N3-C1 | Delivery UPDATE never reaches `active` | **Fixed** | Design §6.2 and `server-engineer` §3.7 both carry the `CASE WHEN status='pending'` clause and the `status IN ('pending','active')` predicate. Executed: three successive deliveries return rowcount 1/0/0, `status` goes `pending → active` on the first, `turn_started_mono` pinned at the first value. Correct. |
| N3-C2 | Seat-insert failure path | **Fixed** | Design §4.3 and `server-engineer` §3.3 specify per-pairing `SAVEPOINT` with `ROLLBACK TO`, and "a game is only reachable through `seats`". Executed: a colliding second seat aborts the statement only, `in_transaction` stays `True`, and `ROLLBACK TO SAVEPOINT` discards the orphan game and the stray seat while earlier pairings commit intact. |
| N3-C3 | `take_control` predicate | **Fixed in the design spec, regressed in the role spec** | §13.3's seat-held predicate is correct and is restated correctly in `mcp-engineer` §6.2. But part (c) — the `controller` check on challenge consumption — is **absent from `server-engineer`'s ticker**, which checks only seats. See **R4-C5**. |
| N3-C4 | Delivery trigger never named | **Not fixed** | The recommended paragraph was never added to design §6.2, and the role spec inherited the hole. See **R4-C1**. This was tracked as critical in round 3 and is the single largest defect still open. |

### 1.2 Round 3's significant findings

| # | Finding | Status | One line |
|---|---|---|---|
| N3-S1 | Held vs immediate `reason` responses | **Partially fixed** | `server-engineer`'s poll pseudocode implies a 20s hold for `waiting_for_pairing`, but `client-engineer` §7 polls every 2s under `agent_has_control` and re-polls **immediately** on `superseded`. The mapping §8.4 was asked for still does not exist. See R4-S4. |
| N3-S2 | Agent timer inversion | **Fixed** | 45s auto-release < 60s agent grace, with the reason stated in design §13.3 and `mcp-engineer` §6.4. |
| N3-S3 | `PoolEntry` / rule 2 not an algorithm | **Partially fixed** | `unpaired_ticks` added ✓. Rule 2's skip-walk ambiguity — after skipping (1,2), is the next candidate (1,3) or (2,3)? — is unresolved in all three documents, and "one relaxed side suffices" is stated nowhere. Still blocks phase 1. |
| N3-S4 | Lexicographic seq / `/state` sampling order | **Partially fixed** | Integer `seq` ✓, numeric comparison spelled out in `dashboard-engineer` §4 ✓. **"`/state` samples `seq` before its read" is stated in no document** — grep for `samples` returns nothing. |
| N3-S5 | `turn_started_mono` on the wire | **Fixed, and improved** | Replaced by `turn_elapsed_ms` computed at emit, with `compute_turn_elapsed_ms` pinned in interfaces Part 1. Better than the recommendation. |
| N3-S6 | `/admin/reset` | **Not fixed** | `server-engineer` §4.2 still says "reset bot counters to zero, keep bot identities" — silent on `rating`, silent on delete order, and now silent on `arena_reports`. See R4-S16. |
| N3-S7 | `/admin/consistency` fails on anchors | **Not fixed** | `server-engineer` still specifies `bots.rating == 1200 + sum(deltas)` for **all** bots. Anchors have fixed non-1200 ratings and no `rating_history` rows, so the check fails on all three at every start. |
| N3-S8 | Persisted monotonic values survive restart | **Not fixed** | `recovery.py` aborts games, deletes seats and mailboxes; it does not null `last_poll_mono` / `last_agent_action_mono`, and §9.1 does not treat `NULL` as ineligible. |
| N3-S9 | Five MCP tools with no REST route | **Not fixed** | See R4-C3. The MCP spec resolved it by *inventing* three endpoints in its own document rather than raising it — which its own §7.3 forbids. |
| N3-S10 | Challenges 409 against a seated bot | **Not fixed, not labelled a decision** | Design §12 still returns 409 at creation. Acceptable as a product call, but it is undocumented as one. |
| N3-S11 | Consumption order, pool snapshot ordering | **Partially fixed** | The ticker consumes challenges before pairing ✓ (the ordering round 3 required). Still missing: `ORDER BY id`, a cap on accepted-unconsumed challenges, and "no `queued` challenge" in §9.1 eligibility. |

### 1.3 Round 3's gaps

Fixed: gap 1 (`to_move_since_mono NOT NULL` — present in `server-engineer`'s schema), gap 5 (401 auth shape and prose). Not fixed: gap 2 (`seats.bot_id NOT NULL` — see R4-S20), gap 3 (`role`/`is_anchor`), gap 4 (duplicate name is `400` in interfaces, round 3 asked for `409`), gap 6 (`no_seat` still in the enum, defined nowhere), gap 7 (no reason meaning "your game was aborted"), gap 8 (`strikes_remaining` — and `client-engineer` §7 prints "Strike 1 of 3" from data the wire does not carry), gap 9 (`challenges.status` still has seven values, two unreachable), gap 10 (§10.3 still says "under 2 points"; executed value is **2.18**), gap 12 (three missing tests).

### 1.4 The harmonisation report's own claims

- **"Constant Drift — 0 Found" is wrong.** Three names exist for one constant: design §5.1/§11 use `TIME_CONTROL_MS`, interfaces `chess_core/clock.py` defines `RATED_TIME_CONTROL_NS`, and `server-engineer`'s `_create_game` calls `RATED_TIME_CONTROL_MS`. §5.1 rule 4's predicate `time_control_ms != TIME_CONTROL_MS` names a constant that exists in no interface. See R4-S12.
- **"No contradictions found at any seam"** — §2 of this report lists thirteen.
- **The §15 override was only partially applied.** `client-engineer` §3.4 and `dashboard-engineer` §13 still carry the superseded Option-2 text alongside the new Option-1 text. See R4-S9.

---

## 2. Critical issues

### R4-C1. Nothing calls delivery. Every game ends `no_show` at ply 0.

*(design §6.2 / §6.3; `server-engineer` §2.2 `mailbox.py`, §5)*

`deliver_position(bot_id, payload, now_mono)` is defined and is invoked from nowhere in the spec set.

- The ticker's inventory (`server-engineer` §5) has six numbered steps: consume challenges, matchmake, delivery-grace expiry, flag detection, agent auto-release, expire challenges. **Delivery is not among them**, and design §6.3's precedence paragraph implies the ticker must *not* deliver.
- `_create_game` calls `wake_waiters(...)` but never writes a mailbox.
- The poll handler "check mailbox first; if present, return TurnResponse. Otherwise `wait_for_turn(...)`", and `wait_for_turn` reads the mailbox and waits on an event. It never delivers either.

Exact sequence:

```
t=0.000  ticker (write_lock): _create_game -> games(status='pending', delivered_to_mover=0,
                              to_move_since_mono=t0), seats(white), seats(black).
                              wake_waiters(white); wake_waiters(black). COMMIT.
t=0.001  white's held poll wakes. read_mailbox(white) -> empty -> returns None.
         Handler returns {"game_id": null, "reason": "waiting_for_pairing"}.
t=0.002  SDK re-polls immediately (client-engineer §10.3). Mailbox still empty. Waits 20s.
t=15.00  ticker step 3: delivered_to_mover=0 and now - to_move_since_mono > 15s
         -> ply 0 -> abort, termination='no_show', rated=0, seats freed.
t=16.00  both bots re-enter the pool. Ticker pairs them again. Repeat forever.
```

**Impact:** the server never plays a single move. `/health` shows a healthy ticker, climbing `pending_games`, and zero `active_games`. The results ticker fills with `no_show`. This is not a race — it is the only path.

**Fix (one paragraph in design §6.2, verbatim from round 3's N3-C4, plus the two call sites in `server-engineer` §2.2 and §5):**

> Delivery is attempted at exactly two moments, both already inside `write_lock`: (a) when the position becomes available — game creation, or the opponent's move committing; and (b) when a poll, or an agent read under `controller='agent'`, arrives for a bot whose current position is undelivered. The ticker never delivers; it only enforces §6.3's deadline. Delivery is idempotent, so (a) and (b) racing is free.

Then state that `_create_game` delivers to the side to move before it returns, and that the poll handler delivers before it reads the mailbox.

### R4-C2. The ticker deadlocks against itself on the first flag, abandonment or admin-visible termination.

*(`server-engineer` §2.2 `ticker.py` vs §2.2 `runner.py`; design §4.1)*

`_tick()` is written as:

```python
async def _tick(tick_number: int):
    async with critical_section(writer_conn, writer_executor):
        ...
        abort_game(game.id, termination='no_show')          # step 3
        finalise_game(game.id, opposite_win(...), 'abandoned')
        finalise_game(game.id, opposite_win(mover_color), 'flag')   # step 4
```

and `runner.py` specifies of both: *"All under `critical_section`"* / *"All in one transaction"*. `critical_section` acquires `store.write_lock`, an `asyncio.Lock`. **Executed:** an inner `async with lock` inside an outer one never returns — the coroutine blocks forever, and `asyncio.Lock` has no reentrancy and no timeout.

The first flag-fall or delivery-grace expiry — i.e. within about 15 seconds of the first pairing — wedges the ticker task on an await. It never raises, so §4.6's `try/except` never fires and `consecutive_tick_errors` stays 0; `last_tick_age_ms` climbs forever. §4.6 chose to watch `last_tick_age_ms` rather than `task.done()` precisely for this failure, and it detects it correctly — but the specified supervisor only calls `logger.critical`. Round 1's C1 residue ("say what the supervisor *does*, not just what it detects") is still open, so the correct detector is wired to no remediation.

`deliver_position` has the same shape (it opens its own `critical_section`), which makes the R4-C1 fix a deadlock too if applied naively.

**Fix.** Split every mutation helper into an inner form that assumes the lock is held and takes the open connection, and a thin outer form for route handlers:

```
_finalise_game(conn, ...)   # assumes write_lock held, inside the open transaction
finalise_game(...)          # async with critical_section(...): _finalise_game(conn, ...)
```

The ticker calls only the `_`-prefixed forms. State in §4.1 that **`write_lock` is acquired at exactly one place per call stack and helpers never acquire it**, because this is the mistake the current pseudocode makes and it is invisible in review. Add to §4.6 that a stalled ticker is cancelled and restarted by the supervisor after two consecutive stale checks, with the tick number logged.

### R4-C3. The control-handoff HTTP surface has a consumer, an owner who cannot build it, and no producer.

*(design §13.3; interfaces Part 5 / Part 6; `mcp-engineer` §7.1; `server-engineer` §4.1 and §12)*

| Endpoint | Consumer | In design §8.1? | In interfaces Part 5? | In `server-engineer`? |
|---|---|---|---|---|
| `POST /bots/me/control` | `mcp-engineer` §7.1 (`take_control`, `release_control`) | No | No | No |
| `GET /bots/me` | `mcp-engineer` §7.1 (`get_my_bot`) | No | No | No |
| `GET /games/{id}/moves` | `mcp-engineer` §7.1 (`analyze_game` timing table) | No | No | No |
| `GET /bots/{bot_id}/rating_history` | `dashboard-engineer` §8.3 | No | **Yes** | **No** — absent from the route list and from the §4.1 inventory |

`server-engineer-spec.md` §12 lists the design sections it claims: §4, §5, §6, §7, §7.1, §8, §9.1, §12, §15, §16, §4.6, §10.2, and reference bots from §10.3. **§13.3 is not among them.** `mcp-engineer-spec.md` claims "§13 in full". So the entire control-handoff mechanism — a `write_lock` mutation that must be designed alongside the move endpoint's in-transaction controller check — is owned by the one track whose spec forbids it from touching `chess_server/api/`. `roles/README.md`'s coverage table has no row for §13.3 at all.

`GET /games/{id}` returns `history_san` but not `server_elapsed_ms` / `client_reported_ms` per move, so `analyze_game`'s entire timing table (the "workshop's central moment", §13.2) has no data source even if the endpoint existed.

**Fix.** Add to design §8.1 and interfaces Part 5, and assign §13.3's server half to `server-engineer` in `roles/README.md`:

```
GET  /bots/me                       -> MyBotResult (authenticated)
POST /bots/me/control  {mode:"agent"|"client"}  -> {controller}
GET  /games/{id}/moves              -> [{ply, uci, san, server_elapsed_ms, client_reported_ms,
                                         white_ms_after, black_ms_after, event?}]
GET  /bots/{bot_id}/rating_history  -> RatingHistoryResponse   (already modelled; needs a producer)
```

`GET /games/{id}/moves` must also carry strike and flag markers, or `analyze_game` §5.3's event log has nowhere to come from.

### R4-C4. SSE events are emitted inside the transaction, including inside a savepoint that may be rolled back.

*(`server-engineer` §2.2 `_create_game` and `_tick`; design §4.1, §14)*

`_create_game` ends with `emit_sse(game_created_event(...))` — and its caller wraps it in `SAVEPOINT pairing` / `ROLLBACK TO SAVEPOINT pairing` on `IntegrityError`. `emit_sse` increments the global `event_seq` and pushes to every client queue immediately.

```
t=0.000  SAVEPOINT pairing
t=0.001  INSERT games(id=101)             -- succeeds
t=0.002  INSERT seats(alice, 101)         -- succeeds
t=0.003  emit_sse(game_created, id=101)   -- seq=873, already in every browser's queue
t=0.004  INSERT seats(bob, 101)           -- UNIQUE constraint failed
t=0.005  ROLLBACK TO SAVEPOINT pairing    -- game 101 and alice's seat are gone
```

Every dashboard now shows game 101 in the grid and (if it wins the featured sort) on the projector. `GET /games/101` returns 404. The game never ends, so no `game_ended` ever removes it. `seq` 873 was consumed by an event describing state that does not exist, so no client can detect the problem via the gap check.

The same shape applies to `challenge_updated` events emitted in `_tick` before its single `COMMIT`, and to any exception path where `critical_section` rolls back after events were already emitted.

**Fix.** Buffer events in the critical section and flush them **after** `COMMIT`, discarding the buffer on `ROLLBACK` or `ROLLBACK TO`. State it in design §4.1 alongside "a critical section is a transaction": *no SSE event is visible to any client before the transaction that produced it has committed.* Ten lines in `sse.py`, and it also fixes the ordering guarantee `/state`'s `event_id` depends on.

### R4-C5. §13.3's controller check on challenge consumption was dropped in the role-spec split.

*(design §13.3; `server-engineer` §2.2 `_tick` step 1 and §5 step 1)*

Design §13.3 is explicit: *"Both §9.1 pool eligibility and §12 challenge consumption require `controller='client'` for both bots, unless the game is an exhibition."* This was round 3's N3-C3(c) fix.

`server-engineer`'s ticker step 1 checks **only seats**:

```python
white_seat = seat_repo.get_bot_seat(ch.challenger_bot_id)
black_seat = seat_repo.get_bot_seat(ch.opponent_bot_id)
if white_seat or black_seat: ... expired
game_id = _create_game(...)
```

`_build_pool_snapshot` does filter on `controller='client'`, so §9.1 survived the split; §12 did not. The bypass round 3 closed is reopened:

```
t=0    alice: take_control()  -> no seat -> ALLOWED. controller='agent'.
t=5    bob:   POST /challenges {opponent:'alice', time_control:'rated'} -> 201 open
              (POST /challenges checks seats and open-challenge count; it does not check controller)
t=8    alice's Claude: accept -> 'queued'
t=9    ticker step 1: both seats free -> creates a RATED 3+2 game for an agent-controlled bot
t=69   AGENT_DELIVERY_GRACE_MS fires. Rated loss, termination='abandoned'.
```

The rated ladder is now distorted by a game the design spec says cannot be created.

**Fix.** Add the `controller='client'` predicate to `server-engineer` §2.2/§5 step 1 (skipped for exhibition time controls), mark the challenge `expired` with `reason='controller_agent'`, and add the same check to `POST /challenges` with prose pointing at `time_control='exhibition'`.

### R4-C6. Every game is broadcast as `rated: true` while it is played, including exhibition, benchmark and same-owner games.

*(design §5.1, §14; `server-engineer` `_create_game`; interfaces Part 2 `game_created`, Part 5 `ActiveGameSummary`; `dashboard-engineer` §7)*

`_create_game` writes `'rated': 1,  # will be recomputed at finalisation`, and `finalise_game` applies §5.1. But `rated` is on the wire in three places consumed live: `game_created.data.rated`, `ActiveGameSummary.rated`, and `game_ended.data.rated`. `dashboard-engineer` §7 colour-codes from exactly this field — green **RATED** border and badge on the featured board and every grid thumbnail.

Consequence: an exhibition 5+10 game between an attendee and an anchor renders **green, badged RATED, on the projector, for its entire duration**, then flips amber in the results ticker when it ends. So does a benchmark spar and a same-owner game. `dashboard-engineer` §7 opens with the invariant this violates: *"Nobody should ever mistake a practice win for a ranked one."*

§5.1 rules 2, 3 and 4 are all evaluable at creation — role, owner and time control are all known then. Only rule 1 (`no_show`, `server_restart`, `admin_abort`) is a termination-time fact.

**Fix.** State in §5.1 that `rated` is **written at creation from rules 2–6** and that rule 1's terminations override it to `0` in the finalising transaction. This is the same two-sentence fix round 3 asked for under N3-C3(a) for a different reason, and it was applied to neither. Add a test: an exhibition game emits `game_created` with `rated: false`.

### R4-C7. Stored and reflected XSS in the dashboard. Attendee-controlled strings are interpolated into HTML with no escaping specified anywhere.

*(`dashboard-engineer` §7 `renderLeaderboardRow`, §7.3, §5 `arena_report_posted`; `server-engineer` `POST /bots`, `POST /arena-reports`; OWASP A03)*

The only rendering code in the spec set is:

```javascript
return `<tr>
  <td>${bot.rank}</td>
  <td>${bot.bot_name}</td>
  ...
```

Three attacker-controlled sources reach the DOM, and **no document in the set specifies output escaping or an input character set**:

1. **`bots.name` and `bots.owner`** — `POST /bots` is specified as "check name uniqueness" and nothing else. `owner` is explicitly a public display handle (interfaces Part 1). A bot named `<img src=x onerror=…>` appears in every attendee's leaderboard and on the projector.
2. **`arena_reports.candidate_name` / `opponent_name`** — free `TEXT NOT NULL`, `422 "Invalid payload"` with no stated validation, rendered into the My Bot panel and broadcast to every SSE client in `arena_report_posted`. This is a **new** surface added by the override with no review.
3. **`?bot=` (`dashboard-engineer` §7.3)** — read from `URLSearchParams`, written to `localStorage`, and used for the "YOU" badge. §7.3 explicitly instructs attendees to **share the URL** (`http://localhost:8000?bot=MyBot`). A shared link is the classic reflected-XSS delivery vector, and the spec is teaching attendees to click them.

The dashboard is unauthenticated and holds no token, so the direct prize is small — but it runs on the projector all day and on twenty laptops, and one attendee's Claude writing a "creative" bot name defaces the workshop.

**Fix.** Three lines, all cheap:
- `POST /bots`: constrain `name` and `owner` to `^[A-Za-z0-9 _.-]{1,32}$` with prose (`"Bot names may contain letters, digits, spaces, dots, dashes and underscores."`).
- `POST /arena-reports`: same charset and a 64-char cap on both name fields.
- `dashboard-engineer`: mandate `textContent` / `createElement`, never template-literal `innerHTML`, for every value that came from the server or the URL. Add one test that a bot named `<b>x</b>` renders as literal text.

### R4-C8. The flag predicate is stated two ways, and the named test contradicts the normative rule.

*(design §6.4 step 3 vs design §18; `chess-domain-engineer` §3 and §7)*

- Design §6.4 step 3, normative: `if remaining < 0 -> flag`.
- Design §18: *"Clock — table-driven over §6.4: **flag on exact zero**, no increment on flag…"*
- `chess-domain-engineer` §3 restates **both halves in one bullet**: *"Flag on **exact zero** — not '≤ 0', strictly `< 0` after deduction"*, which is self-contradictory.
- `chess-domain-engineer` §7 names the test: `test_account_move_flags_on_timeout — elapsed exactly equals remaining → flagged`.

Under `remaining < 0`, elapsed exactly equal to remaining leaves `remaining == 0`, which does **not** flag. The named test fails against the normative rule; whichever the implementer picks, one of the two documents is wrong, and this is the one module the design spec isolates *because being wrong here is silent*.

The substantive question is real, not cosmetic: at 3+2, a bot that consumes exactly its remaining time should flag (it produced no move within its budget). `remaining <= 0` is the correct predicate.

**Fix.** Change §6.4 step 3 to `if remaining <= 0 -> flag`, and delete the "not '≤ 0'" clause from `chess-domain-engineer` §3. One character in the normative document; it must be the same character in all three.

---

## 3. Significant concerns

**R4-S1. `/state` carries no position, so the dashboard can draw no board and never features a game.**
`ActiveGameSummary` (interfaces Part 5) has `game_id`, both bots' ids/names/ratings, `ply`, clocks, `turn_elapsed_ms`, `is_featured`, `rated`. It has **no `fen`, no `to_move`, no `status`, no `history_san`.** `dashboard-engineer` §4 does `this.board.render(featuredGame.fen)` and §7.1 does `activeGames.filter(g => g.status === 'active')` — the filter matches nothing, so `pickFeaturedGame()` always returns `null` and Big Screen shows an empty board until a `move_played` arrives for whatever the server thinks is featured. Add `fen`, `to_move` and `status` to `ActiveGameSummary`. (`dashboard-engineer` §8.1's own `/state` example omits `white_rating`/`black_rating` too and still says "**Request to `server-engineer`**", contradicting §7.1 and §13, which mark it resolved.)

**R4-S2. Client-side game selection is broken by the server's featured flag, and there are three variables for one concept.**
`onMovePlayed` re-renders the main board only `if (data.is_featured)`, which is the **server's** notion. Clicking a grid cell sets `this.locallyFeaturedGameId`, but no `move_played` for that game ever carries `is_featured: true`, so the board a viewer deliberately selected **never updates**. Worse, §14's coalescing throttles non-featured games to 500ms on the server, so even after fixing the render gate the selected game is a laggy board while the projector's is smooth. Meanwhile `featuredGameId`, `locallyFeaturedGameId` and `state.featured_game_id` all exist, and `updateFeaturedClocks()` reads the first while `getFeaturedGameId()` returns the second. Decision 8 delegates selection to the dashboard entirely, yet interfaces still ships `featured_game_id` and `is_featured` — pick one authority. Recommended: the dashboard owns selection, `move_played` drops `is_featured`, and the server coalesces on its own internal choice while the client renders from whichever game it selected.

**R4-S3. `reconnectSSE()` leaks a 100 ms interval per reconnect and re-enters `init()`.**
`reconnectSSE` calls `this.init()`, which calls `connectSSE()` and `startClockTick()` without clearing `this.clockTickInterval`. Every SSE drop, seq gap or run change adds another `setInterval`. Over a workshop day with a flaky LAN this is dozens of concurrent tickers on the projector, and it directly fails the spec's own acceptance criterion ("tab left open for 60 minutes: no memory leaks"). Clear the interval and close the EventSource before re-init. Separately: `onmessage` buffers only while `this.state === null`, and `fetchState()` sets `this.state` before `applyBufferedEvents()` runs — an event arriving in that window is handled ahead of the buffered ones, trips the strict `seq === lastSeq + 1` gap check, and triggers a refetch that can loop.

**R4-S4. Two SDK instances supersede each other in a hot loop, and `reason: "superseded"` cannot be produced by the specified code.**
`client-engineer` §7: on `superseded`, *"Silent continue, immediate re-poll."* An attendee who leaves `run.py` running in one terminal and starts it in another — the single most likely accident of the day — gets two clients each immediately re-polling and superseding the other, at whatever rate the network allows, until §8.6's bucket 429s them both. Neither is ever delivered, so the game dies at `DELIVERY_GRACE_MS`. Separately, `wait_for_turn`'s superseded waiter wakes, reads an empty mailbox and returns `None`, which the handler maps to `waiting_for_pairing` — there is no path that returns `superseded` at all. Fix: give the waiter a supersede flag, and have the SDK back off 1–2 s on `superseded` with the prose *"Another copy of your bot is polling. Stop one of them."*

**R4-S5. MCP `register_bot` bypasses the join code entirely.**
`mcp-engineer` §4.2: *"The join code is passed automatically from an environment variable (`JOIN_CODE`)… Attendees do not see or provide it."* The MCP server is mounted at `/mcp` in the same process, so it holds `JOIN_CODE`. Anyone who can reach the server can therefore register without the code, which is the exact scenario §8.5 exists to prevent ("an open endpoint on a conference network can fill the bot table"). The tool's own error list still contains `"Invalid join code."`, which can never fire. Also `POST /bots` is rate-limited **by IP** (§8.5) but `rate_limit.py` implements only a per-token bucket, and every MCP registration arrives from `127.0.0.1`. Fix: make `join_code` a required `register_bot` parameter, keep the error prose, and specify the IP limiter explicitly.

**R4-S6. Three mutually contradictory strings for the controller-mismatch error, two of which instruct the opposite of what works.**
| Document | String |
|---|---|
| interfaces Part 6 (`make_move`) | "Controller is 'client'. Call **release_control()** before using agent tools." |
| `mcp-engineer` §4.2 (`make_move`) | "Controller is 'client'. Call **release_control()** … or use take_control() if you meant to switch." |
| `mcp-engineer` §9.7 | "Controller is 'client'. Call **take_control()** before using agent tools, or use release_control() if you are the bot's SDK." |
| `mcp-engineer` §4.2 (`get_legal_moves`) | "Controller is 'client'. Call **take_control()** before using agent tools." |
The correct action when `controller='client'` and you want agent tools is `take_control()`. Two of the four say `release_control()`. `mcp-engineer` §10.4 mandates a test that every canonical error appears **exactly once** in the codebase — that test cannot pass, and its whole point was to stop this drift.

**R4-S7. `get_game` is annotated `readOnlyHint` while it starts a clock.**
Design §6.2 and §13.3: *"delivery happens on `get_game()` / `get_legal_moves()`"*. `mcp-engineer` §2.4 and §4.1 mark `get_game` `readOnlyHint` and `get_legal_moves` `destructiveHint` "because it triggers delivery". Both trigger delivery. Under `readOnlyHint`, Claude starts a rated clock without seeking permission — which §2.4 itself calls out as the thing that trains attendees to click through prompts. Mark `get_game` `destructiveHint` when `controller='agent'`, or (simpler and better) make `get_legal_moves` the sole delivery trigger and leave `get_game` genuinely read-only.

**R4-S8. The SDK is told both to resign and to continue on an illegal move, which breaks the three-strike design.**
`client-engineer` §6: *"If return is illegal, SDK logs full error and resigns the game on bot's behalf."* `client-engineer` §7 "Illegal move": *"SDK behaviour: Log full error, continue polling. Server increments strike counter."* Design §8.3's three-strike forfeit only means anything under the second. Resigning on the first illegal move also removes the diagnostic that `analyze_game`'s event log is built to surface. Delete the resign clause from §6 for illegal moves; keep it for `choose_move` raising (§10.4).

**R4-S9. The override was applied on top of the decision it replaced; both are still normative.**
`client-engineer` §3.4 contains `--report` (POSTs to the server) **and** `--serve`, whose text reads *"Does NOT POST results to the server (that would create an unverifiable attack vector against the rated leaderboard)"* — an argument against the feature two paragraphs above it. `dashboard-engineer` §13 lists as a current requirement *"Remove all 'local amber' color-coding—dashboard shows server games only"* and *"`arena.py --serve` (stretch) provides separate local view"*, contradicting its own §2 and §7 and design §14. Six agents building from these will not agree on whether local data reaches the server. Delete the `--serve` paragraph and `dashboard-engineer` §13's stale bullets.

**R4-S10. The interfaces document is textually corrupted in three places.**
(a) The `arena_report_posted` payload section and the `EVENT_ARENA_REPORT_POSTED` constant are each **duplicated verbatim**. (b) The "### Arena Reports" block was spliced **into the middle of the `ResetResponse` class body**, splitting an unterminated code fence; `wiped_rating_history`, `wiped_seats`, `wiped_mailboxes`, `reset_bots` now appear after the arena-reports section as orphaned text. (c) The "Decisions" section at the end has resolutions 5–8 interleaved: Decision 6's heading is followed by Decision 7's issue text, then Decision 6's resolution, then Decision 8's heading, then Decision 7's resolution, then Decision 8's resolution — with `Resolution:**` fragments missing their opening `**`. This is the *pinned seams* document; an implementer reading Decision 7 gets Decision 6's answer.

**R4-S11. Anchors can never be paired, so §9.3 and §10.3's calibration are unreachable.**
§9.1 requires, for every pool member, *"a poll currently held **or** `last_poll_mono` within 5s"*. Anchors are in-process (`reference_bots.py`); they never call `GET /bots/me/turn`, and §8.4 says `last_poll_mono` is updated **only** by that endpoint. So no anchor is ever pool-eligible. Independently, `should_offer_anchor` is defined in interfaces Part 1 and `chess-domain-engineer` §2.4 and is **called from nothing** — `_tick` calls only `pair_bots(pool)`. §10.3 says anchor ratings are calibrated before the workshop because guessed ones "would bias every rating in the room"; as specified, they are never used at all, and the ±400 gate never evaluates. Fix: state that anchors are exempt from the poll-recency predicate (or that the ticker refreshes their `last_poll_mono` each tick), and make `pair_bots` take the anchor pool and apply `should_offer_anchor`, or delete `should_offer_anchor` and fold the gate into `pair_bots`.

**R4-S12. Three names for one constant, and §5.1 rule 4 names one that does not exist.**
`TIME_CONTROL_MS` / `INCREMENT_MS` (design §5.1, §11) vs `RATED_TIME_CONTROL_NS` / `RATED_INCREMENT_NS` (interfaces Part 1, the only place they are actually defined) vs `RATED_TIME_CONTROL_MS` / `RATED_INCREMENT_MS` (`server-engineer` `_create_game`). §5.1 rule 4's predicate `time_control_ms != TIME_CONTROL_MS` is the rule that makes exhibition games unrated, and it is written against a symbol no interface declares. Round 3's over-engineering item 8 asked for a single constants table for exactly this reason; it was never added, and the harmonisation pass reported zero constant drift. Add the table to design §5 with the canonical `_NS` names and the two named `ms_to_ns` / `ns_to_ms` boundary helpers.

**R4-S13. The `arena_reports` retention prune deletes the newest rows when `created_at` ties.**
Executed against SQLite 3.51.3: with 25 rows sharing one `created_at` value, `DELETE … WHERE id NOT IN (SELECT id … ORDER BY created_at DESC LIMIT 20)` **kept ids 1–20 and deleted 21–25** — the five most recent. `GET /bots/{id}/arena-reports`, which orders the same way, then returns the five oldest as "most recent". `created_at` is `TEXT` and an arena run posting several reports in one second is the normal case. Order by `id DESC` in both the prune subquery and the read. Same defect class as round 3's N3-S4: an ordering that looks right and silently is not.

**R4-S14. `arena_reports` "display-only" is asserted, not enforced, and the payload has no semantic validation.**
The invariant appears in prose in three documents and in `AGENTS.md`, which is good — but nothing prevents a future leaderboard query joining it, and the brief asks whether it is *enforceable*. Two cheap mechanisms: (i) a test that greps `chess_server/` for `arena_reports` and asserts it appears only in `ArenaReportRepo` and the two route handlers; (ii) name the table `arena_reports_display_only`, so a join reads wrong on a projector. On validation, `422 "Invalid payload"` is the only stated error and there is no rule requiring `wins + draws + losses == games`, no upper bound on `games` / `mean_move_ms` / `p95_move_ms`, and no length cap on the two name fields (see R4-C7). An attendee posting `games: 10**12, wins: 10**12` gets a `win_rate` of 1.0 rendered on twenty laptops. **Nothing in the design depends on these numbers being honest** — no rating, matchmaking or leaderboard path reads the table — so fabrication buys only a lie in one's own My Bot panel, which is the right answer; say so explicitly in §14 so nobody later "improves" it.

**R4-S15. Rate limiting is keyed on the raw bearer token, in an unbounded dict, and does not cover the endpoint that needs it most.**
`buckets: Dict[str, TokenBucket] = defaultdict(...)` keyed by `token`. §16.2 says tokens never appear in logs, errors or SSE — a dict key is not a log, but it puts plaintext tokens in a long-lived process structure for no benefit; key on `token_hash`, which auth already computes. The `defaultdict` also grows one entry per *invalid* token presented, so a loop with random tokens grows memory unbounded. And §8.5 requires `POST /bots` to be rate-limited **by IP**, which a per-token bucket cannot do — no IP limiter is specified anywhere.

**R4-S16. `/admin/reset` (round 3 N3-S6, not fixed) is now also wrong about `arena_reports`.**
Still no statement of what happens to `bots.rating` — zeroing it and leaving it both violate §10.2 immediately; it must be 1200 for competitors and benchmarks and the calibrated constant for anchors. Still no delete order, and with `foreign_keys = ON` the order is forced (seats before games — verified in round 3). `arena_reports` references `bots(id)`, survives a reset, and is not in the wipe list, so a dry run's self-reported numbers persist into the real workshop. Still not stated: reset runs in one transaction under the lock and wakes every waiter.

**R4-S17. `/admin/consistency` still fails on all three anchors, every start** (round 3 N3-S7, not fixed). `server-engineer` §4.2 and design §10.2 assert `rating == 1200 + Σ deltas` unconditionally. Anchors have fixed non-1200 ratings and, by §10.3's one-sided rule, zero `rating_history` rows. The startup check logs loudly on day one for three bots that are correct, and the operator learns the alarm means nothing. Exempt `is_anchor` and assert instead: zero rating rows and rating equal to the configured constant.

**R4-S18. Recovery leaves stale monotonic values and logs the wrong row count** (round 3 N3-S8, not fixed).
`recovery.py` does not null `last_poll_mono` / `last_agent_action_mono`, and §9.1 does not treat `NULL` as ineligible; after a host reboot the monotonic origin resets and `now − last_poll_mono` is negative for every bot ever registered, so all of them become pool-eligible at once and are voided as `no_show` fifteen seconds later — in front of the room, at the moment the server comes back. Separately, `logger.info(f"...{cursor.rowcount} games aborted...")` reads `rowcount` after `DELETE FROM seats` and `DELETE FROM mailbox` have run on the same cursor, so it reports the mailbox count.

**R4-S19. The matchmaker is still not an algorithm** (round 3 N3-S3, half-fixed). `unpaired_ticks` was added to `PoolEntry` ✓, but `chess-domain-engineer` §2.4 restates rule 2 as prose identical to the design spec's: after skipping `(1,2)`, is the next candidate `(1,3)` — leaving 2 for 4 — or `(2,3)`, leaving 1 unpaired? Different ladders, and this is the one function §18 puts under strict TDD with seeded determinism. "One relaxed side suffices for a relaxed pair" is stated in none of the three documents, so two same-owner bots alone in the pool at 09:15 never play. `pair_bots(pool, seed)` also takes a seed that no step of the algorithm uses.

**R4-S20. `seats.bot_id` still accepts `NULL`, and a `NULL` insert silently steals bot 1's seat** (round 3 gap 2, asked twice, not fixed). Design §4.3 and `server-engineer`'s schema both declare `bot_id INTEGER PRIMARY KEY REFERENCES bots(id)`. Executed: `INSERT INTO seats(bot_id, game_id) VALUES (NULL, 7)` is **accepted**, auto-assigns rowid **1**, and `PRAGMA foreign_key_check` returns clean — so the phantom row occupies bot 1's seat slot and bot 1 can never be paired again, with no constraint violation to notice. Declare `bot_id INTEGER PRIMARY KEY NOT NULL REFERENCES bots(id)`.

---

## 4. Minor issues and gaps

1. **`no_seat` is still in the `reason` enum with no definition** in any of the nine documents (round 3 gap 6). Define it or delete it.
2. **No `reason` meaning "your game was aborted"** after `/admin/games/{id}/abort` or `server_restart` (round 3 gap 7). The bot silently returns to `waiting_for_pairing`.
3. **`strikes_remaining` is not in the turn payload**, yet `client-engineer` §7 specifies the message *"Strike 1 of 3. Three illegal moves in one game forfeits."* — data the wire does not carry. Either add it to `TurnResponse`/the 400 `details`, or delete the sentence.
4. **`challenges.status` still has seven values, two unreachable** (round 3 gap 9): `accepted` is never written (accept marks `queued`) and `cancelled` has no endpoint.
5. **§10.3 still says "under 2 points"**; executed at K=24, 1400 vs 1000 gives **+2.18** on a win and **−21.82** on a loss (round 3 gap 10). Also worth one sentence: at the ±400 boundary an anchor game is almost pure downside for a bot near its ceiling.
6. **Duplicate registration name returns `400`** in interfaces Part 5; round 3 asked for `409` (it is a conflict, not a malformed request). Cosmetic but it is in the pinned seam.
7. **Decision 7 says `is_provisional` appears in "SSE `rating_changed`"**, but interfaces Part 2's `rating_changed` payload has no such field.
8. **`/state` sampling order is still unstated** (round 3 N3-S4 second half): `/state` must sample `seq` **before** it reads the database, or writes committed in between are both absent from the snapshot and filtered out of the buffer.
9. **The supervisor still has no action**, only detection (round 1 C1 residue) — which R4-C2 makes load-bearing.
10. **`health_tick` omits `stalled_games`, `db_writable` and `consecutive_tick_errors`** that `/health` carries; `dashboard-engineer` §8.1 documents the full set for both. Harmless, but the dashboard polls `/health` every 5 s *and* receives `health_tick` every 3–5 s — pick one.
11. **"Local data never appears in Big Screen mode" is a client-side `if`.** `arena_report_posted` is broadcast to every SSE client including the projector. That is fine, but say it is enforced in the renderer, not in the transport, so nobody assumes otherwise.
12. **`?bot=` overwrites the recipient's `localStorage` permanently.** §7.3 encourages sharing the URL; whoever opens it has their own bot identity silently replaced and now sees someone else's bot badged "YOU". Only persist when the user sets it deliberately, or add a visible "showing: X — change" control.
13. **`analyze_game` has a third error** ("Game {id} is still in progress…") in `mcp-engineer` §4.1 that interfaces Part 6 does not list. Minor, but §10.4's exactly-once test spans both.
14. **`roles/README.md`'s coverage table has no row for §13.3, §16, §18 or §22**, which is how R4-C3 survived a coverage check that explicitly looked for unclaimed requirements.

---

## 5. Over-engineering — what to cut

1. **`arena.py --serve`.** Superseded by `--report`, still specified, and its rationale argues against `--report`. Delete the section. *(Also fixes R4-S9.)*
2. **`mailbox` as a database table** (round 3 over-eng 1, not taken). §7.1 clears it on every start and nothing outside the process reads it, so it is process state paying for a write on the hottest path inside `write_lock`. `dict[int, TurnPayload]` mutated in the same critical section. Removes one table, one write per delivery, and one repository.
3. **Two authorities for the featured game.** Decision 8 gives selection to the dashboard; interfaces still ships `/state.featured_game_id` and `move_played.is_featured`, and §14's coalescing needs a server-side notion. Keep the server's internal choice for coalescing only and drop `is_featured` from the wire. *(Also fixes R4-S2.)*
4. **`should_offer_anchor` as a separate exported function**, currently called by nothing (R4-S11). Fold the ±400 gate into `pair_bots`.
5. **`pair_bots`'s `seed` parameter**, used by no step of the algorithm.
6. **`challenges.status` at seven values.** Ship `open | queued | consumed | expired | declined`.
7. **`games.source`**, display-only, and `challenges.game_id` already records the link.
8. **Dashboard trimmings that will not be used once and cost test surface:** leaderboard sort-by-name/games toggles, `bot_connected`/`bot_disconnected` toasts, `challenge_updated` toasts, and the sparkline hover tooltip. Big Screen legibility and a working board are what matter at six metres.
9. **The constants table is still missing** (round 3 over-eng 8). Fourteen tunables now, spread across nine documents, and R4-S12 is the second inconsistency to slip through because they are never written down together. This is the cheapest remaining defect-prevention measure in the set.

---

## 6. Verification method

Executed against `sqlite3` library **3.51.3** and CPython's `asyncio`. Nothing below was inferred.

| Test | Result |
|---|---|
| `INSERT INTO seats(bot_id, game_id) VALUES (NULL, 7)` with `bot_id INTEGER PRIMARY KEY REFERENCES bots(id)` | **Accepted**; row stored as `(1, 7)`; `PRAGMA foreign_key_check` clean → phantom seat occupies bot 1's slot *(R4-S20)* |
| §6.2 delivery UPDATE applied three times to a `pending` game | rowcount `1, 0, 0`; `status` → `active` on the first; `turn_started_mono` pinned at the first value; `started_at` set once → **N3-C1 fix is correct** |
| Seat collision inside `SAVEPOINT pairing`, then `ROLLBACK TO` | `UNIQUE constraint failed: seats.bot_id`; `in_transaction` still `True`; after `ROLLBACK TO` + `COMMIT`, the orphan game and stray seat are gone and the earlier pairing survives → **N3-C2 fix is correct** |
| `DELETE … WHERE id NOT IN (SELECT id … ORDER BY created_at DESC LIMIT 20)` over 25 rows with identical `created_at` | Kept ids **1–20**, deleted **21–25** — the five newest *(R4-S13)* |
| `SELECT id … ORDER BY created_at DESC LIMIT 5` over the same ties | Returned `1,2,3,4,5` — the five oldest, labelled "most recent" *(R4-S13)* |
| Re-acquiring one `asyncio.Lock` from inside its own `async with` | Inner acquire never completes (0.5 s timeout expired) → **`critical_section` is not reentrant** *(R4-C2)* |
| Elo K=24, 1400 vs anchor 1000 | `E = 0.90909`; win `+2.18`, loss `−21.82` → design §10.3's "under 2 points" is wrong *(gap 5)* |
| Integer-rounded exchange zero-sum, 1001 ratings vs 1200, decisive and drawn | **0 violations** — `round()`'s banker's rounding is symmetric about zero, so §18's zero-sum property test survives integer storage. Worth keeping; it is not obvious. |

---

## 7. What the design gets right

Brief, and only where this round's scrutiny earned it.

- **§4's transaction and CAS contract survived the split intact.** `server-engineer` §3.1–3.4 restates it faithfully, including the two non-obvious async details, and both SQLite fixes from round 3 verify correct under execution. The failures above are in what calls it, not in what it says.
- **§6.2's idempotent delivery UPDATE, with the `pending → active` `CASE`, is exactly right** and was verified three ways. It is also good projector material.
- **Replacing `turn_started_mono` with `turn_elapsed_ms` computed at emit (§14)** is a better fix than round 3 asked for: it removes a whole class of client-side clock arithmetic rather than making it tractable.
- **`AGENTS.md`'s framing of the `arena_reports` invariant** — "display-only", named as an invariant rather than a preference, with the specific code paths that may not read it — is the right way to write a constraint that a future change will be tempted to break. It needs the enforcement of R4-S14, but the sentence is correct.
- **The orchestrator's §10.4 catch** ("a rule that every role assumed someone else owned") is genuine, and the error prose written for it is the best in the set. The coverage check's mistake was stopping at one.

---

## 8. Prioritised recommendations

### Must change before phase 3b begins

Phases 1 and 2 unblock after items 8 and 10 and should start now.

1. **Name the delivery trigger** in design §6.2 and add the two call sites to `server-engineer` §2.2 and §5. *(R4-C1 — without this no game is ever played.)*
2. **State that `write_lock` is acquired at exactly one place per call stack**, split every mutation helper into a locked outer and an unlocked inner form, and have the ticker call only the inner forms. Give the supervisor an action. *(R4-C2)*
3. **Add `POST /bots/me/control`, `GET /bots/me`, `GET /games/{id}/moves` and `GET /bots/{bot_id}/rating_history` to design §8.1, interfaces Part 5 and `server-engineer`'s route list**, and assign §13.3's server half to `server-engineer` in `roles/README.md`. *(R4-C3)*
4. **Buffer SSE events and flush after commit**; discard on rollback. State it in §4.1. *(R4-C4)*
5. **Restore the `controller='client'` check on challenge consumption and creation.** *(R4-C5)*
6. **Write `rated` at creation from §5.1 rules 2–6**; only rule 1's terminations override it. *(R4-C6)*
7. **Constrain `name`, `owner`, `candidate_name`, `opponent_name`; mandate `textContent` in the dashboard.** *(R4-C7)*
8. **Fix the flag predicate to `remaining <= 0` in §6.4, §18 and `chess-domain-engineer` §3.** *(R4-C8)*
9. **Declare `seats.bot_id … NOT NULL`.** *(R4-S20 — verified; asked for twice already.)*
10. **Write matchmaker rule 2 as pseudocode and state that one relaxed side suffices.** *(R4-S19 — this is what unblocks phase 1.)*
11. **Delete the `--serve` paragraph and `dashboard-engineer` §13's stale bullets**, so the override is applied once rather than twice. *(R4-S9)*
12. **Repair the interfaces document's three corrupted regions.** *(R4-S10 — it is the pinned-seams document.)*
13. **Add `fen`, `to_move` and `status` to `ActiveGameSummary`.** *(R4-S1)*
14. **Add the constants table with canonical `_NS` names, and fix §5.1 rule 4's predicate.** *(R4-S12)*

### Handle during implementation

15. Anchor pool eligibility and the `should_offer_anchor` call site. *(R4-S11)*
16. Supersede back-off in the SDK and a supersede flag on the waiter. *(R4-S4)*
17. `join_code` as a required `register_bot` parameter; specify the IP limiter. *(R4-S5)*
18. Canonicalise the controller-mismatch prose to one string that names `take_control()`. *(R4-S6)*
19. `get_game` annotation, or make `get_legal_moves` the sole delivery trigger. *(R4-S7)*
20. Remove the resign-on-illegal-move clause from `client-engineer` §6. *(R4-S8)*
21. Clear the clock interval before re-init; buffer until after `applyBufferedEvents`. *(R4-S3)*
22. Order `arena_reports` by `id`, not `created_at`, in the prune and the read. *(R4-S13)*
23. Semantic validation on the arena payload; the display-only grep test; state plainly that nothing depends on the numbers being honest. *(R4-S14)*
24. Key rate limiting on `token_hash`, bound the bucket map, add the IP limiter. *(R4-S15)*
25. `/admin/reset`: rating restore, delete order, `arena_reports`, one transaction, wake waiters. *(R4-S16)*
26. Exempt anchors from `/admin/consistency`. *(R4-S17)*
27. Null the monotonic columns in recovery; treat `NULL` as ineligible; fix the `rowcount` log. *(R4-S18)*
28. Resolve the featured-game authority to one side. *(R4-S2, over-engineering 3)*
29. Fill gaps 1–8 and 12–14 in §4.
30. Take over-engineering cuts 1, 2, 4, 5, 6 and 9 now, while §5 and §14 are still being edited. Each removes a defect as a side effect.
