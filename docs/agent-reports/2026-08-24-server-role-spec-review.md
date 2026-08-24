# Server role spec — adversarial review

| | |
|---|---|
| **Reviewed** | `docs/superpowers/specs/roles/server-engineer-spec.md` (revision 1 + revision-5 errata) |
| **Against** | design spec §4, §5, §6, §7.1, §8–§16, §20–§22; interfaces Parts 1/2/5; `chess_core/` as built; `AGENTS.md` |
| **Commit** | `12686dc8e09a43df11ceb412ffc8ebf1a37e27ce` |
| **Date** | 2026-08-24 |
| **Role** | design-adversary |
| **Verdict** | **Phase 3a (`store/`) must not begin.** Nine schema-level decisions are absent or wrong, and every later track binds to the schema. **Phase 3b (`api/` + ticker) must not begin.** As written, the ticker deadlocks on its first termination, then — if that is fixed — permanently rolls back every tick from the moment the first game finishes; anchors have no execution path; and the mover's mailbox is never cleared, which puts every bot in a 409 hot loop after its first move. |

The three most expensive findings are C1 (mailbox never cleared on side switch), C3 (delivery-grace query is missing the non-terminal filter) and C5 (anchors cannot move). None of them raise. All three present as "matchmaking has stopped" or "my bot won't play", which is the diagnosis that costs a workshop afternoon.

---

## 1. Verification of prior rounds

Round 4's eight criticals were addressed by an **errata block** at the top of this document. In six of the eight cases the errata is correct and the **body of the document still contains the defective text, in a code block**. Round 5 §2.3 already asked for the stale pseudocode to be deleted rather than overridden; it was not.

This matters more than it sounds. An errata is prose; a code block is a template. The plan author who copies `_tick()` reproduces R4-C2, R4-C4 and R4-C6 verbatim, and every one of them is silent at runtime.

| Prior finding | Status here | Justification |
|---|---|---|
| R4-C1 — nothing calls delivery | **Fixed** (errata 1) | Two call sites named, `get_game()` correctly excluded. But see C5: it also means *nothing delivers to an anchor*. |
| R4-C2 — ticker deadlocks on nested `write_lock` | **Not fixed in body** | Errata 2 states the rule. §2.2 `_tick()` still calls `abort_game(...)`, `finalise_game(...)` and `mailbox.deliver_position(...)`, all of which open their own `critical_section`. **No `*_locked` symbol appears anywhere in the document.** |
| R4-C3 — control-handoff surface has no producer | **Partially fixed** | Errata 4 assigns the four routes; `roles/README.md` line 29 agrees. The document body describes none of them: `take_control` appears 0 times, `POST /bots/me/control` is absent from §2.3 and from the §4.1 inventory. Interfaces Part 5 still models only `RatingHistoryResponse` of the four. |
| R4-C4 — SSE emitted inside the transaction | **Not fixed in body** | Errata 3 states the rule. `emit_sse` is still called inline at four executable sites (lines 334, 339, 394, 424), one of which is inside `SAVEPOINT pairing`. |
| R4-C5 — `controller='client'` check on challenge consumption | **Not fixed in body** | Errata 6 says "restore". §2.2 `_tick` step 1 and §5 step 1 still check seats only. |
| R4-C6 — every game broadcast as `rated: true` | **Not fixed in body** | Errata 5 is correct. `_create_game` still writes `'rated': 1,  # will be recomputed at finalisation`, and `finalise_game` still says "Determine `rated` per §5.1 rules". |
| R4-C7 — XSS on attendee strings | **Fixed** | Errata 9 carries the regex and the semantic bounds, matching design §14 and §5. |
| R4-C8 — flag predicate stated two ways | **Fixed, no regression** | Both §2.2 step 4 and §5 step 4 say `remaining_ns <= 0`. Round 5's `< 0` blocker did not leak into this document. |
| R5 — `seats` needs `WITHOUT ROWID` | **Not fixed in body** | Errata 7 is correct and verified. The §2.1 DDL is still `bot_id INTEGER PRIMARY KEY REFERENCES bots(id)` with no `WITHOUT ROWID`. `WITHOUT`/`ROWID` each appear once in the file — in the errata. |
| R5 — arena-report retention orders by `id DESC` | **Not fixed in body** | Errata 8 is correct. The §2.1 SQL is still `ORDER BY created_at DESC`, and the `GET /bots/{bot_id}/arena-reports` description still says "ordered by `created_at` descending". Design §5 requires `id DESC` in *both* the prune and the read. |
| R5 — supervisor must act, not only observe | **Not fixed** | Design §4.6 requires cancel-and-restart at 15s plus a `ticker_restarts` field in `/health`. §2.2 `supervise_ticker` and §3.6 log CRITICAL and stop. |
| R5 — use §5.2 canonical constants | **Not fixed in body** | Errata 10 is correct. `POLL_RECENCY_NS`, `CHALLENGE_TTL_NS`, `POLL_HOLD_NS` and `TICK_INTERVAL_NS` appear **zero** times; the code blocks hardcode `interval_ms=1000`, `timeout=20.0`, "within 5s" and `60_000_000_000`. |

**Recommendation before anything else: delete every stale code block the errata overrides.** Ten of the eleven items above would close by deletion alone. Leaving a corrected rule next to the uncorrected artefact it corrects is the mechanism that produced this list.

---

## 2. Critical

### C1. The mover's mailbox is never cleared on the side switch. Every bot enters a 409 hot loop after its first move.

*(§2.2 `runner.py` `apply_move`; §2.2 `mailbox.py` `wait_for_turn`; design §8.4)*

Design §8.4: *"The mailbox is cleared when the side switches or the game ends."*

Every mailbox-clearing site in this document (lines 203, 282, 291, 541, 756, 1107, 1158) is a **finalisation, abort or recovery** path. `apply_move`'s success bullet reads:

> If not flagged: persist move, update `games` (fen, ply, clocks, side switch, clear delivery), check `chess_core.rules.detect_termination`

No mailbox clear. And `wait_for_turn` returns whatever is in the mailbox with no ply check:

```python
payload = mailbox_repo.read_mailbox(bot_id)
if payload:
    return json.loads(payload)
```

Runtime:

```
t=0.00  White polls, mailbox[white] := payload{ply:12}      -> bot moves
t=0.40  POST /games/42/moves {ply:12}  -> 200, games.ply := 13, side switches
t=0.41  SDK re-polls /bots/me/turn
        mailbox[white] still holds payload{ply:12}          -> returns instantly
t=0.42  bot computes a move for ply 12, submits {ply:12}
        CAS `AND ply=12` -> rowcount 0 -> 409
t=0.43  §8.3: "discard the move and re-poll"                -> back to t=0.41
```

No timeout, no error log, no rate-limit trip below 20 req/s. The bot burns its whole clock in a poll/409 loop and flags; the attendee sees "my bot never moves" and a `flag` termination. **This is the single highest-cost finding in the review.**

Fix: clear `mailbox[bot_id]` for the side that just moved, inside the same critical section as the side-switch UPDATE. Belt and braces: have the turn endpoint discard a mailbox payload whose `ply` no longer matches `games.ply`.

### C2. `_create_game` writes nanoseconds into millisecond columns. Clocks never flag.

*(§2.2 `_tick` step 2 and `_create_game`; design §5.2)*

```python
_create_game(pairing.white_bot_id, pairing.black_bot_id,
             RATED_TIME_CONTROL_NS, RATED_INCREMENT_MS, 'matchmaker')
#            ^ ns into a parameter named time_control_ms   ^ does not exist
```

and `_create_game` writes it straight through: `'white_ms': time_control_ms`.

```
$ .venv/bin/python /tmp/adv_check.py
=== B. spec passes RATED_TIME_CONTROL_NS into a *_ms parameter ===
  games.white_ms written        : 180000000000
  ms_to_ns(white_ms) years      : 5.707762557077626
  intended                      : 180000 ms = 3 min
```

Every rated game starts with 5.7 years on each clock. Flag-fall never fires, the delivery grace still works, so games end only by mate, draw, resignation or abandonment — and the ply cap is never applied either (see M8), so a shuffling pair runs until someone closes a laptop. It reads correctly: `white_ms` is populated, non-null, monotonically decreasing.

Round 5 flagged `RATED_INCREMENT_MS` as an abolished *name*. The unit error underneath it is the part that produces wrong behaviour.

Fix: `_create_game` takes `time_control_ns`/`increment_ns` and converts at the DB boundary with `ns_to_ms`, per §5.2's "converted **only** at the boundary". State the boundary explicitly (see M9).

### C3. The delivery-grace query has no non-terminal filter. The ticker stops doing anything, permanently, from the first finished game onward.

*(§2.2 `_tick` step 3; §5 step 3; design §6.3)*

Design §6.3: *"for any **non-terminal** game where `delivered_to_mover = 0` …"*.

This spec, twice: *"Query games with `delivered_to_mover=0`"* — no status predicate. Step 4 correctly filters `status='active'`; step 3 does not.

Now recall §3.7: `delivered_to_mover` is cleared to 0 in the side-switch UPDATE. So **every finished game ends with `delivered_to_mover = 0`** and a `to_move_since_mono` receding into the past.

```
t=0        game 7 ends in checkmate.  delivered_to_mover=0, status='finished'
t=+15s     tick: get_undelivered_games() returns game 7
           check_delivery_timeout -> True
           finalise_game(7, ..., 'abandoned')
           CAS  ... WHERE id=7 AND status='active'  -> rowcount 0
           -> CASConflict -> propagates out of _tick
           -> critical_section except: ROLLBACK
```

Two compounding consequences:

1. Because the whole tick is one transaction (C4), that rollback discards **everything else the tick did** — challenge consumption, all pairings, all other finalisations.
2. Game 7 is finished forever, so this repeats every tick, for the rest of the workshop. Add game 8, 9, 10 and it never recovers.

Observable symptom: `consecutive_tick_errors` climbs, `last_tick_age_ms` stays healthy (the loop is running), pairing silently stops. The supervisor watches age, not errors, so the red banner never appears.

Fix: `AND status IN ('pending','active')` on the undelivered query, and clear `delivered_to_mover`/`turn_started_mono` at finalisation for good measure.

### C4. The whole tick is one transaction, so any single CAS conflict discards every other game's work in that tick.

*(§2.2 `_tick`; design §4.2, §4.3)*

`_tick` opens exactly one `critical_section` around all six steps. §4.2 says a failed CAS "aborts the transaction". Design §4.3 already reasoned this through for pairing — *"Aborting the whole tick would discard every other valid pairing"* — and prescribed a `SAVEPOINT` per pairing. The same argument applies to every other per-game action in the tick and is made nowhere.

Concretely, a bot's flag-fall and its final move landing in the same instant is the ordinary race, not an exotic one:

```
t=0.000  ticker: BEGIN IMMEDIATE
t=0.002  ticker: finalise game 3 'flag'   -> CAS ok
t=0.003  ticker: finalise game 5 'flag'   -> CAS 0 (move committed 1ms earlier)
t=0.004  ROLLBACK
         game 3 is un-flagged again; the two challenges consumed at step 1 are
         un-consumed; three pairings are discarded; and the SSE events for all
         of them were already pushed (C6).
```

Fix: `SAVEPOINT` per game / per challenge / per pairing, `ROLLBACK TO` on `CASConflict`, continue the loop. State it once as a rule covering all six steps rather than only pairing.

### C5. Anchors have no execution path. `reference_bots.choose_move` is never invoked, and nothing delivers to an anchor.

*(§2.2 `reference_bots.py`; §2.2 `_tick` step 2; errata 1; design §9.1, §9.3, §10.3, §21)*

Four independent gaps, any one of which is fatal:

1. **Role.** §2.1's DDL comments `role TEXT NOT NULL, -- 'competitor' | 'benchmark'`, and registration says `anchor` is not registrable. Design §21 says the server depends on `role='anchor'` handling. Design §9.1 requires `role='competitor'` for pool eligibility. `role='anchor'` appears exactly once in the whole spec tree — in design §21. So an anchor is either not in the pool at all, or is misfiled as a competitor and appears on the leaderboard.
2. **Gating.** `should_offer_anchor` appears twice in this document, both times in a list of functions consumed, never in a call site. `_tick` step 2 calls `pair_bots(pool)` and nothing else. `pair_bots` only prevents anchor-vs-anchor (`chess_core/matchmaker.py:88`); the "only when the competitor would otherwise sit idle", "fewest-games eligible bot" and ±400 rules of §9.3 live entirely in `should_offer_anchor`. Nothing calls it, so none of §9.3 is enforced.
3. **Delivery.** Errata 1: *"Delivery has exactly two call sites … `GET /bots/me/turn` … `get_legal_moves()` … Nothing in the ticker delivers."* An anchor has no HTTP client. So an anchor's position is never delivered, `delivered_to_mover` stays 0, and §6.3 aborts the game `no_show` 15 seconds later — the exact failure R4-C1 was raised to fix, reintroduced for the one bot class that cannot poll.
4. **Move execution.** Nothing anywhere calls `RefRandomBot.choose_move`. No tick step, no route, no coroutine.

Consequence: a lone attendee, or the last unpaired bot in an odd pool, either never gets a game or gets a 15-second `no_show` cycle. The three reference bots are dead code.

Fix (server spec + design §9.3): add a ticker step between pairing and delivery-grace that, for every active game whose side to move `is_anchor`, calls `choose_move` and applies the move through the same `*_locked` move path a client would use, and treats the in-process call itself as delivery. Then state which role value anchors carry and how §9.1's filter admits them.

### C6. `PoolEntry`'s four history fields have no storage and no derivation. `unpaired_ticks` is permanently 0, which deadlocks exactly the case relaxation exists for.

*(§2.2 `_build_pool_snapshot`; design §9.2; `chess_core/types.py:PoolEntry`)*

`pair_bots` consumes `PoolEntry(bot_id, owner, rating, games_played, is_anchor, last_color, white_count, last_opponent_id, unpaired_ticks)`. In this document:

```
$ grep -c 'last_color\|white_count\|last_opponent_id\|unpaired_ticks' server-engineer-spec.md
0   0   0   0
```

There is no column for any of them, and `_build_pool_snapshot`'s docstring covers only the eligibility filters. `last_color`, `white_count` and `last_opponent_id` are at least derivable from `games` — expensively, per tick, and three builders would derive them three ways. `unpaired_ticks` is not derivable from anything: design §9.2 says it *"is carried in the snapshot and incremented by the caller for any bot that ends a tick unpaired"*, and no such counter is specified.

A builder who cannot find it will pass `0`. Then `_allowed()` never relaxes, and design §9.2's stated motivating case fails:

> *"Requiring both would deadlock the common case: a lone attendee with two bots, where neither can pair with anyone else and the same-owner rule blocks the only available game."*

Two bots, one owner, `unpaired_ticks=0` forever → `a.owner == b.owner and not relaxed` → never paired. Both poll happily. `/health` shows `pooled_bots: 2`, `active_games: 0`. Nothing errors.

Fix: decide where these live. Cheapest that satisfies §9.2: an in-process `dict[int, int]` for `unpaired_ticks` (it is per-run state and §7.1 discards it anyway), and `last_color` / `last_opponent_id` / `white_count` denormalised onto `bots` and updated in the finalising transaction — a schema decision, so it must land **before phase 3a**.

### C7. Flag and validation are ordered backwards against normative §6.4, and `chess_core` provides no flag predicate for the correct order.

*(§2.2 `runner.py`; design §6.4, §1.2 of this spec)*

Design §6.4 is explicit and normative:

> *"Flag takes precedence over an illegal move: step 3 precedes validation. A bot that submits an illegal move after its flag has fallen has flagged."*

This spec's `apply_move` validates first, then accounts:

```
- Call chess_core.rules.validate_and_apply_move(fen, move_uci)
- If rejected: increment strikes, return rejection; if 3rd strike, call forfeit_game
- If accepted and delivered: call chess_core.clock.account_move_and_switch(...)
```

A bot that flags and then sends garbage receives a strike and stays in the game until the next tick; three of them terminate the game `illegal_forfeit` rather than `flag`. Wrong termination on the wire, wrong story for the attendee, and §6.4's ordering was written precisely because getting it backwards is "silently wrong forever".

**This is a `chess_core` gap, not just a wording error.** The correct order needs "has this side flagged?" as a *separate, prior* question, and `account_move_and_switch` is deliberately atomic — deduct, flag, increment, switch — so it cannot answer it without also mutating. The full surface of `chess_core/clock.py` is:

```
create_clock  deliver_position  account_move_and_switch  check_delivery_timeout
compute_turn_elapsed_ms  ms_to_ns  ns_to_ms
```

No flag or remaining-time helper exists. So the server must inline `remaining_ns = mover_ns - (now_mono - turn_started_mono); if remaining_ns <= 0` — which §2.2 step 4 already does, in the ticker. That puts §6.4's predicate in two hand-written places in `chess_server/`, in direct violation of this spec's own §1.2 (*"If you find yourself … computing Elo deltas, **stop**"*), and it is the exact predicate round 5 caught stated two ways.

Fix: **`chess_core/clock.py` gains `remaining_ns(clock, color, now_mono) -> int`** (or `has_flagged(clock, now_mono) -> bool`), the ticker and the move endpoint both call it, and nothing in `chess_server/` subtracts monotonic timestamps. Requires a change to interfaces Part 1 and `chess-domain-engineer-spec.md`, and a small addition to phase 1 — cheap now, and it removes a whole bug class.

### C8. `critical_section` does not roll back on cancellation, and the design's own remedy for a stalled ticker then bricks the writer permanently.

*(§2.1 `lock.py`; design §4.1, §4.6)*

The specified code:

```python
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

Its own docstring claims *"Entire block is `asyncio.shield()`ed"*. It is not — only the `BEGIN` is, and a `yield` inside a context manager cannot be shielded by wrapping a statement before it. Two verified consequences:

```
=== C. `except Exception` does not catch cancellation (py3.14) ===
  issubclass(CancelledError, Exception) : False

=== D. SQLite: a transaction leaked by an uncaught cancel ===
  second BEGIN IMMEDIATE -> OperationalError: cannot start a transaction within a transaction
  conn.in_transaction still  : True
```

A client disconnecting during a move, or an `asyncio.wait_for` firing, cancels the task inside the `yield`. `except Exception` does not fire, no `ROLLBACK` runs, `write_lock` is released by `__aexit__` — and the single writer connection is left mid-transaction. **Every subsequent `BEGIN IMMEDIATE` on that connection raises.** No moves, no pairings, no finalisations, for the rest of the process. `/health`'s `db_writable` would catch it only if `db_writable` is a *write* probe, which is unspecified.

It compounds badly with design §4.6, which now requires the supervisor to **cancel and restart the ticker task** at `last_tick_age_ms > 15000`. If the ticker is wedged for any reason while holding the lock mid-transaction, the prescribed remediation is a cancellation delivered exactly where this handler does not catch it. The fix for a stall is what makes the stall permanent.

Fix: `except BaseException:` (or `except (Exception, asyncio.CancelledError):`), shield the `ROLLBACK` and `COMMIT` awaits themselves, and put the whole yielded body inside a task that the caller shields. Add a `db_writable` probe that actually attempts a write.

### C9. Illegal-move strikes: "return rejection" versus `raise IllegalMove`. One reading makes the three-strike rule unreachable.

*(§2.2 `runner.py` vs §2.3 `submit_move`; design §8.3)*

`runner.py`: *"If rejected: increment strikes, **return** rejection"*.
The route: `except IllegalMove as e: raise HTTPException(400, ...)`.

If `apply_move` raises through `critical_section`, `except Exception: ROLLBACK` fires and **the strike increment is rolled back with it**. `white_strikes` never leaves 0, `illegal_forfeit` never fires, and §8.3's three-strike rule silently does not exist. Design §8.3 is unambiguous that a rejected move both increments the counter and does not stop the clock, so the strike must commit.

Nothing in the document resolves which it is. Fix: the strike path commits (return a result object; the route converts it to a 400). Say so, and add a test that submits three illegal moves and asserts `illegal_forfeit` — a test the rollback reading fails.

### C10. `detect_termination`'s `history_fens` has no specified source, and the obvious source is off by one position.

*(§2.2 `runner.py`; §7; `chess_core/rules.py:148`)*

The spec correctly says to call `detect_termination` rather than trusting `move_result.is_terminal` — good, and the only place in the document that gets a moved seam right. But it passes no arguments and never says where the history comes from, whether it includes the current position, or whether it includes ply 0.

`detect_termination` counts `position_key` matches in `history_fens` and requires `>= 3`, so the current position must itself be in the list. The natural server implementation, `SELECT fen_after FROM moves WHERE game_id=? ORDER BY ply`, satisfies that but **omits the ply-0 position**, which is the one repeated in the commonest repetition of all:

```
=== A. threefold when the starting position is omitted from history ===
  current key occurs in full history   : 3
  current key occurs in moves-only      : 2
  detect_termination(full)      -> (True, TerminationReason.THREEFOLD, GameResult.DRAW)
  detect_termination(moves_only)-> (False, None, None)
```

(`Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8` — the third occurrence of the start position is not claimed.)

The established convention is `starter-kit/arena.py:95`, `history_fens = [fen]` before the loop plus an append after every move. The server must match it, or offline results stop predicting live behaviour — which `AGENTS.md` names as load-bearing.

Also unspecified: whether history is read from `moves` on every move under the lock (an O(ply) read per move inside the critical section) or cached per game in process. Say which. Restart makes it moot only because §7.1 aborts all games.

Fix: pin the contract in interfaces Part 1 — *`history_fens` is `[starting_fen] + [fen_after for each ply in order]`, including the current position* — and in this spec say the server passes `[STARTING_FEN] + fen_after list`.

### C11. Every terminal path that is reachable from the ticker still deadlocks, and every SSE event still escapes an uncommitted transaction.

*(carried R4-C2 and R4-C4; see §1)*

Not re-argued — round 4 proved both by execution. Recorded as Critical because the defective code blocks are still what a plan author will copy, and both failures are silent: the deadlock leaves `consecutive_tick_errors` at 0 and `last_tick_age_ms` climbing with no exception anywhere, and a rolled-back `game_created` puts a game on the projector that `GET /games/{id}` 404s.

**Fix by deletion**, per round 5 §2.3.

---

## 3. Major

### M1. The mailbox is a table here and process state in the design spec.

§2.1 defines `CREATE TABLE mailbox`, `MailboxRepo` with five methods, and `mailbox_repo.write_mailbox(...)` inside `deliver_position`. Design §5 explicitly rejects this:

> *"**The mailbox is process state, not a table.** `mailbox: dict[int, TurnPayload]` … a table bought a write on the hottest path under `write_lock` and a repository, in exchange for durability that recovery deliberately discards."*

Also, `wait_for_turn` calls `mailbox_repo.read_mailbox` outside the lock without saying on which connection. Per the errata's own precedence rule the design spec wins; delete the table, the repo, and the DDL. **Schema decision — must land before phase 3a.**

### M2. `challenges` is missing the `reason` column the spec's own code writes, and carries two statuses the design deleted.

DDL comment: `'open'|'accepted'|'queued'|'consumed'|'declined'|'expired'|'cancelled'`. Design §5 deleted `accepted` and `cancelled` by name and gives the reason. Meanwhile `update_challenge_status(ch.id, 'expired', reason='seat_unavailable')` is called three times against a table with no `reason` column (design §5 has one; this DDL does not). §12's "an SSE event explains why. No silent drop" depends on it. **Schema — before 3a.**

### M3. `TerminationReason.CRASH` has no producer anywhere in the spec tree.

`crash` appears **0 times** in this document. `client-engineer-spec.md:971` resolves a bot exception as *"resign immediately"*, which reaches `POST /games/{id}/resign` and is recorded as `resignation`. So the value exists in `chess_core/types.py` and in interfaces Part 1, and no code path can ever write it. Design §5's rationale — *"an attendee reading `illegal_forfeit` goes looking for a move-generation bug, while `crash` sends them to the traceback"* — is defeated.

Fix (design §8.1 + this spec + client spec): `POST /games/{id}/resign {ply, reason?: "crash"}`, or drop `crash` from the taxonomy. Either is fine; the current state is a documented feature nothing produces.

### M4. `/admin/consistency` flags every anchor as a violation, permanently.

§2.3 and acceptance criterion 17: *"assert `bots.rating == 1200 + sum(rating_history.delta)` for all bots"*. Anchors have fixed ratings that are not 1200 and, being rated one-sidedly, accumulate no `rating_history` rows of their own. So `800 != 1200 + 0` for every anchor, on every run.

This is the one alarm in the system that catches double-rating (design §10.2). An alarm that is red on a healthy server is an alarm nobody reads. Fix: scope the assertion to `is_anchor = 0`, or baseline it on each bot's seeded starting rating. **Design §10.2 should state which.**

### M5. §7.1 recovery leaves three monotonic-derived fields pointing at a clock that no longer exists.

*(design §7.1 must change)*

Recovery aborts games, deletes seats, clears mailboxes, regenerates `run_id`. It does **not** touch `bots.last_poll_mono`, `bots.last_agent_action_mono`, or `bots.controller`, all of which survive in SQLite and are compared against the new process's `time.monotonic_ns()`.

- If the new baseline is **larger** (same boot), ages are huge — bots are excluded from the pool until they poll again. Self-healing.
- If it is **smaller** (reboot, container restart, DB copied to another machine), `now - last_poll_mono` is negative, therefore `< POLL_RECENCY_NS`, therefore **every bot ever registered looks like it is polling right now**. They get paired, never take delivery, and churn through `no_show` aborts every 15 seconds, forever. `/health` shows `pooled_bots: 20`, `active_games` sawtoothing, no errors.
- `controller='agent'` surviving a restart is worse in the common case: §9.1 excludes the bot from matchmaking, and auto-release is keyed on `now - last_agent_action_mono`, which may be negative and never expire. **An attendee who used `take_control()` before a restart never plays again**, with nothing in any log.

Fix, three lines in the same recovery transaction: `UPDATE bots SET last_poll_mono=NULL, last_agent_action_mono=NULL, controller='client'`. Design §7.1 must say so — `time.monotonic_ns()` not surviving a restart is confronted nowhere in the tree.

### M6. The §13.3 server half is assigned and not specified.

`roles/README.md:29` assigns `POST /bots/me/control` and the routes behind it to server-engineer; errata 4 agrees. The body specifies none of: refusing `take_control` while a seat is held (409 + prose); waking the held poll with `reason='agent_has_control'`; updating `last_agent_action_mono` on *every* agent tool call; the `controller` check *inside the same transaction as the CAS* on the move endpoint; and `controller='client'` on challenge consumption (R4-C5). `take_control` appears 0 times. All four routes are also absent from the §4.1 inventory, so acceptance criterion 8 ("All endpoints implemented — per §4 inventory") passes without them. The `/arena-reports` routes are missing from the table too.

Interfaces Part 5 models exactly one of the four (`RatingHistoryResponse`). No `MyBotResponse`, no control request/response, no `GameMovesResponse` — and `POST /bots/me/control` is `{action: "take"|"release"}` in design §8.1 but `{mode: ...}` in `mcp-engineer`. **Interfaces must change.**

### M7. `ActiveGameSummary` still lacks `fen`, `to_move` and `status`.

Verified at this commit. `dashboard-engineer` errata 5 claims they were added; they were not. Not this spec's defect, but this spec is the producer, so the field set must be settled before `/state` is built. **Interfaces must change.**

### M8. `PLY_CAP` / `transition_after_move` are listed as consumed and used nowhere.

Both appear only in the §7 seams list and the §12 summary. `apply_move` goes `validate_and_apply_move` → `detect_termination`, neither of which applies the cap; `transition_after_move` (`chess_core/match.py:46`) is the only thing that does, and its comment says the terminal-before-cap ordering is load-bearing. Without it, §22 adjudication never fires and a shuffling pair plays until flag — or forever, given C2.

More generally: the spec consumes `create_match`, `transition_to_active`, `can_transition`, `is_terminal` and `transition_to_terminal` in a list, and calls none of them; the state machine is expressed only as SQL CAS predicates. Pick one. If `MatchState` is used, say it is reconstructed per call and never stored, and note that `transition_to_terminal` now **raises** on an already-terminal state (`chess_core/match.py:107`) — inside a critical section that is an exception, not a 409, unless caught.

### M9. The ms↔ns boundary is never located.

`games.white_ms` is milliseconds; `ClockState.white_ns` is nanoseconds. `_clock_from_game(game)` is referenced three times and defined nowhere. Design §5.2 says conversion happens "**only** at the boundary" — this spec must name the boundary as the repository layer and show one worked round trip. Related: flooring in `ns_to_ms` charges the mover up to 1ms per move:

```
=== E. ms<->ns truncation per move ===
  ns deducted                   : 1999999
  round-tripped through ms      : 2000000
```

≈100ms over a 100-move game. Acceptable, but it should be a stated accepted limit rather than a surprise, and the direction must be consistent.

### M10. `game.to_move` and `game.controller` are read as game columns; neither exists.

`_tick` step 4 uses `game.to_move`; `games` has no such column (derivable from the FEN's second field or from ply parity — say which, and say which is authoritative when they disagree). Step 3 uses `game.controller` to pick the grace period; `controller` is on `bots`, and the relevant one is the bot *to move*, so this needs a join the spec does not describe.

### M11. Supersede cannot produce `reason='superseded'`, and two waiters can be handed the same position.

```python
if bot_id in mailbox_waiters:
    mailbox_waiters[bot_id].set()   # supersede old waiter
```

The old waiter's `await event.wait()` returns, it re-reads the mailbox, and either returns the payload (so **both** connections are told to move — design §8.4's "one waiter per bot" defeated) or returns `None`, which the route maps to `waiting_for_pairing`, not `superseded`. There is no flag distinguishing "woken by supersede" from "woken by delivery". Design §8.4 requires the first to receive `{"game_id": null, "reason": "superseded"}`.

Separately, `wait_for_turn` reads the mailbox *before* registering its event, and that read is awaited. A `wake_waiters` firing in the gap — from `_create_game`, or from agent auto-release — is lost, and the poll hangs the full 20s. Register first, then check.

Also: `NoGameResponse.reason` has six values in design §8.2; the route handler covers three.

### M12. Every constant in the code blocks is hardcoded.

`interval_ms: int = 1000`, `timeout: float = 20.0`, "within 5s", `60_000_000_000`. All four now exist in `chess_core/clock.py` (`TICK_INTERVAL_NS`, `POLL_HOLD_NS`, `POLL_RECENCY_NS`, `CHALLENGE_TTL_NS`) per §5.2's sole-declaration rule, and none appears in this document. Errata 10 says use them; the code blocks do not.

Related: `get_expired_open_challenges(now_mono, 60_000_000_000)` compares a monotonic nanosecond count against `challenges.created_at`, which is `TEXT` wall clock. There is no `created_mono` column. A builder must invent one or convert — **schema decision, before 3a**.

### M13. SSE emission is specified for challenges and game creation, and for nothing else.

`emit_sse` has four executable call sites: three `challenge_updated`, one `game_created`. Part 2's catalog also requires `game_started`, `move_played`, `game_ended`, `rating_changed`, `arena_report_posted`. None is emitted on any specified path — not in `apply_move`, not in `finalise_game`, not at delivery. The dashboard's entire live surface has no producer. Also `def emit_sse(event_type: str, data: dict)` is called everywhere with one argument.

### M14. `POST /admin/reset` is one line and has at least five undefined behaviours.

*"wipe games/moves/rating_history/seats/mailboxes, reset bot counters to zero, keep bot identities"*. Unspecified: whether `bots.rating` resets to 1200 (and whether anchors keep their fixed ratings — they must); whether in-flight games are CAS-aborted first or simply deleted under the feet of held polls; whether waiters are woken; whether `run_id` is regenerated (SSE clients otherwise keep applying `seq` against a wiped world); whether `last_poll_mono` is cleared (same trap as M5). This runs on workshop day, between the dry run and the real thing, with twenty bots connected.

### M15. Rate limiting is keyed on the raw bearer token, unbounded, and does not cover `POST /bots`.

`buckets: Dict[str, TokenBucket]` keyed on `credentials.credentials` puts plaintext tokens in a long-lived global that appears in any traceback frame that touches it — against §16.2's "never logged". Key on `token_hash` or `bot_id`. The dict is an unbounded `defaultdict`, so unauthenticated garbage tokens grow it without limit. And design §8.5 requires `POST /bots` to be **rate-limited by IP**; `rate_limit.py` is per-token only, and registration has no token.

### M16. The supervisor observes and does not act.

Design §4.6 now requires: warn at 5s, **error + cancel + restart the ticker** at 15s, log the tick number it died on, and expose `ticker_restarts` in `/health`. §2.2 and §3.6 stop at `logger.critical`. Round 1's "a detector wired to no remediation" residue is still open — and given C8, the restart must be designed together with the cancellation handling or it makes things worse.

`/health`'s field list also differs three ways: §2.3 omits `stalled_games`, §3.6 includes it, design §4.6 adds `ticker_restarts`.

### M17. `account_move_and_switch` raises on an undelivered position, and no branch handles it.

`chess_core/clock.py:113` raises `ValueError("Cannot account move on undelivered position")`. The spec says *"If accepted **and delivered**"* and specifies no `else`. An agent-controlled bot that calls `make_move` without first calling `get_game()`/`get_legal_moves()` reaches exactly this state. Runtime: unhandled `ValueError` → rollback → 500 with no actionable prose, which `AGENTS.md` forbids. Decide: deliver-then-account, or 409 with "you must read the position before moving".

---

## 4. Minor

- **§5.1 vs §5.3.** The spec cites "§5.1 rules" for `rated` in three places. The design spec has no §5.1; the rules are in **§5.3**. Round 4 flagged a symbol mismatch under the same heading; the citation is still wrong.
- **`HTTPAuthCredentials`** should be `HTTPAuthorizationCredentials`.
- **"Constant-time compare" is not what `auth.py` does**, and cannot be — the lookup is an index hit on `token_hash`. Acceptance criterion 11 requires `secrets.compare_digest` on a path where it is unnecessary and unimplementable. `get_admin` genuinely does need it. Reword.
- **`datetime.utcnow()`** is deprecated; `datetime.now(timezone.utc)`.
- **`GameRepo`'s method list** does not contain `get_undelivered_games`, `get_delivered_active_games` or `get_game_for_bot`, all of which the ticker and mailbox call. Same for `BotRepo.get_agent_controlled_bots`.
- **`/state` returns `featured_game_id`** while §11.4 delegates featured selection to the dashboard. Drop it or explain the redundancy.
- **Resign requires "it's their turn"** in §2.3. Neither design §8.1 nor §8.3 says that, and resigning on the opponent's move is ordinary chess. If it is deliberate, say why; otherwise drop the check.
- **`bots.controller` index.** §11.1 says "indexed"; the DDL has no index on it. (At 20 bots it does not matter, which is itself an argument for deleting the claim.)
- **`cas.py`'s `rowcount > expected` branch** is unreachable — every CAS predicate names a primary key.

---

## 5. Over-engineering — what to cut

1. **The whole `arena_reports` vertical.** Its producer (`arena.py --report`) is deferred with interfaces Part 3, so `POST /arena-reports` has **no client**. Deferring it with its producer removes a table, a repo, retention pruning, semantic validation, two routes, an SSE event, a display-only grep test and the "keep 20 by `id DESC`" trap that has now been got wrong twice. It is the largest chunk of phase-3 surface with zero consumers. If it is kept, keep it whole — but keep it in phase 6 with the dashboard panel that renders it, not in 3a with the schema.
2. **`MailboxRepo` and the `mailbox` table** — deleted by M1.
3. **`chess_core.match` as a consumed seam.** Six functions listed, zero called; the CAS predicates already encode §7 in SQL. Keeping both is two sources of truth for the state machine. Keep `transition_after_move` (it owns `PLY_CAP` and the terminal-before-cap ordering) and drop the rest from the seam list.
4. **The reader connection pool's 5-permit semaphore.** Twenty bots and a handful of browsers. One reader connection per request with WAL is fine; the semaphore is machinery guarding a limit nobody reaches. Keep the reader/writer *split* — that one is load-bearing — and drop the limiter.
5. **§11's "All Decisions Resolved" and §12's "Summary Report"** are ~100 lines restating what §2–§9 already say, and they have already drifted from it (§12 still omits §13.3 from the claimed sections; §11.1 claims an index the DDL lacks). Duplicated normative text is what round 5 found rotting. Delete both.

---

## 6. What I checked and found sound

Coverage, not padding:

- **The §7 seam list is right about the four moved signatures.** `compute_one_sided_exchange(competitor_rating, anchor_rating, competitor_score: float)` is listed with the score semantics *and* the "anchor draws are rated; do not branch around them" note. `detect_termination(fen, history_fens)` is listed correctly and `apply_move` calls it rather than trusting `move_result.is_terminal` — the trap the user found by accident is **not** present here. `pair_bots(pool)` is called without a seed. `ANCHOR_RATING_WINDOW` is imported from `matchmaker`, not redeclared. (The *call sites* fail — C6, M8, and the score derivation in §7.2 below — but the seam list itself is accurate.)
- **The flag predicate did not regress.** Both §2.2 step 4 and §5 step 4 say `remaining_ns <= 0`. Round 5's phase-1 blocker (`< 0`) is absent from this document.
- **§3.7's delivery UPDATE is reproduced verbatim and correctly**, including the `status = CASE WHEN status='pending' THEN 'active' END` inside the same statement — round 3's fix, which is easy to lose in a copy and was not lost.
- **Delivery idempotency reasoning is correct** and matches `chess_core.clock.deliver_position` (`clock.py:70`): re-delivery returns the identical clock and never touches `turn_started_mono`.
- **Seats as storage-level enforcement**, `PRAGMA foreign_keys = ON` forcing the game insert first, statement-level-abort semantics, `SAVEPOINT` per pairing, and "a game is only reachable through `seats`" are all carried from design §4.3 accurately and with the reasoning intact.
- **CAS as a principle** (§3.2) — predicate names the state transitioned *from*, `rowcount` asserted, roll back and abandon on 0 — is stated exactly right. The failures are all at specific call sites, not in the rule.
- **§10.4, one competitor per owner**, is the best-written section in the document: the rule, the farming vector it closes, the transaction requirement against a double-registration race, and error prose that points the attendee at `role='benchmark'` rather than just refusing.
- **Token handling** matches §16.2 throughout: `secrets.token_urlsafe(32)`, `sha256` indexed, plaintext returned once, admin-only re-issue refused while a seat is held, and an acceptance criterion that greps for leaks.
- **Input validation** (errata 9): the `^[A-Za-z0-9 _-]{1,32}$` regex and the semantic arena bounds match design §5 and §14 exactly — still the only round-4 item applied coherently across every layer.
- **Every route handler `async def`**, writer on a dedicated single-thread executor, display reads off the writer — all correct per §4.5.
- **Error prose is actionable everywhere it appears**, and the §4.1 status-code inventory matches design §8.3 including the 409-carries-`{ply, fen, status}` contract.
- `transition_to_terminal` now raising on an already-terminal state (ground-truth item 7) **breaks nothing here** — no specified path relies on the old overwrite, because CAS gates every terminal transition. Worth a sentence in §7 so nobody reintroduces the assumption.

---

## 7. Build-order risk

### 7.1 Must be resolved before phase 3a (`store/`) — these are schema decisions, and everything binds to the schema

| # | Item |
|---|---|
| M1 | Mailbox: delete the table and `MailboxRepo` (design §5 wins) |
| M2 | `challenges.reason` column; drop `accepted` and `cancelled` from the status set |
| M12 | `challenges.created_mono` (or a stated wall-clock TTL comparison) |
| C2/M9 | `games.*_ms` are milliseconds; name the ms↔ns boundary and define `_clock_from_game` |
| C6 | Where `last_color`, `white_count`, `last_opponent_id`, `unpaired_ticks` live |
| M10 | `games.to_move`, or a single stated derivation |
| C5 | The anchor `role` value, and how §9.1's filter admits anchors |
| §1 | `seats` DDL gains `WITHOUT ROWID`; arena retention SQL gains `id DESC` — **fix the code blocks, not the errata** |
| Cut 1 | Decide now whether `arena_reports` exists at all in this build |
| M4 | The consistency-check baseline for anchors |

### 7.2 Must be resolved before phase 3b (`api/` + ticker)

| # | Item |
|---|---|
| C1 | Mailbox cleared on side switch, plus a ply guard on the turn endpoint |
| C3 | Non-terminal filter on the undelivered query |
| C4 | `SAVEPOINT` per unit of work across all six tick steps |
| C5 | An anchor-move tick step, `should_offer_anchor` wired in, delivery for in-process bots |
| C7 | `remaining_ns` / `has_flagged` added to `chess_core.clock`; flag checked before validation |
| C8 | `critical_section` catches `BaseException`, shields the rollback |
| C9 | Strikes commit on the rejection path |
| C10 | `history_fens` contract pinned, including ply 0 |
| C11 | Delete the stale `_tick`, `finalise_game`, `abort_game`, `deliver_position` and `emit_sse` code blocks; specify `*_locked` forms and buffered SSE flush |
| M6 | The four control routes specified, added to the §4.1 inventory, modelled in interfaces Part 5 |
| M8 | `transition_after_move` for `PLY_CAP`, or an explicit cap check |
| M11 | Supersede semantics and the register-before-check ordering |
| M13 | Every Part 2 event given an emission site |
| M17 | The undelivered-move branch |
| — | Also settle §7.2's rating derivation: which participant is "the competitor" in a one-sided exchange, how `GameResult` maps to `competitor_score`, whether anchors get a `rating_history` row, and where `compute_draw_exchange` is called. `finalise_game` currently names two of the three Elo functions in a parenthesis. |

### 7.3 Can be handled during the build

M3 (crash producer — needs a decision, not a design), M5 and M14 (recovery/reset field clearing — small, isolated, but write the tests first), M15 (rate-limit keying), M16 (supervisor remediation, once C8 lands), M7 (`ActiveGameSummary` — blocks phase 6, not phase 3), and everything in §4.

---

## 8. Which document must change

| Change | Document |
|---|---|
| C7 — a flag/remaining helper in `clock.py` | **interfaces Part 1** + `chess-domain-engineer-spec.md` (+ a small phase-1 addition) |
| C10 — the `history_fens` contract | **interfaces Part 1** |
| M5 — recovery clears `last_poll_mono`, `last_agent_action_mono`, `controller` | **design §7.1** (normative; the monotonic-baseline problem is confronted nowhere) |
| M3 — a producer for `crash`, or delete it | **design §8.1 / §22** + client-engineer |
| M4 — consistency-check baseline excludes anchors | **design §10.2** |
| C5 — the anchor `role` value; §9.1 vs §21 | **design §9.1 / §21** |
| M14 — `/admin/reset` semantics | **design §15** |
| M6, M7 — models for the four routes; `ActiveGameSummary` fields | **interfaces Part 5** |
| Everything else | `server-engineer-spec.md` |

The interfaces document is again the one that received the least of the last fix pass — three of the four routes assigned to this track by `roles/README.md` still have no model. That is now the fourth consecutive round in which a fix landed in the design spec and skipped the seam document.

---

## 9. Reproduction

Every executed claim in this report comes from one script:

```
$ .venv/bin/python /tmp/adv_check.py
=== A. threefold when the starting position is omitted from history ===
  current key occurs in full history   : 3
  current key occurs in moves-only      : 2
  detect_termination(full)      -> (True, TerminationReason.THREEFOLD, GameResult.DRAW)
  detect_termination(moves_only)-> (False, None, None)

=== B. spec passes RATED_TIME_CONTROL_NS into a *_ms parameter ===
  games.white_ms written        : 180000000000
  ms_to_ns(white_ms) years      : 5.707762557077626
  intended                      : 180000 ms = 3 min

=== C. `except Exception` does not catch cancellation (py3.14) ===
  issubclass(CancelledError, Exception) : False

=== D. SQLite: a transaction leaked by an uncaught cancel ===
  second BEGIN IMMEDIATE -> OperationalError: cannot start a transaction within a transaction
  conn.in_transaction still  : True

=== E. ms<->ns truncation per move ===
  ns deducted                   : 1999999
  round-tripped through ms      : 2000000
```

Document-level claims are checkable with:

```bash
S=docs/superpowers/specs/roles/server-engineer-spec.md
grep -c 'unpaired_ticks\|last_color\|white_count\|last_opponent_id' $S   # 0 0 0 0  (C6)
grep -c 'take_control\|crash\|POLL_HOLD\|TICK_INTERVAL' $S               # 0 0 0 0  (M6, M3, M12)
grep -n 'WITHOUT\|ROWID\|id DESC' $S            # errata only; DDL and SQL unchanged  (§1)
grep -n 'clear.*mailbox' $S                     # finalise/abort/recovery only        (C1)
grep -n 'delivered_to_mover=0' $S               # line 971: no status filter          (C3)
grep -n 'emit_sse' $S                           # 4 inline sites inside the tx        (C11)
```
