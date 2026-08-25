# Spec Review — Chess Arena Design, Round 2 (Systems Design)

| | |
|---|---|
| **Reviewed** | `docs/superpowers/specs/2026-08-23-chess-arena-design.md` **revision 2**, cross-checked against `agent-reports/2026-08-23-spec-review-systems-design.md` (round 1) and `AGENTS.md` |
| **Commit** | `ff45454` |
| **Date** | 2026-08-23 |
| **Reviewer role** | Senior distributed-systems engineer — second-round adversarial design review, pre-implementation |
| **Verdict** | **Begin implementation, with conditions.** Phases 1 and 2 of §19 (`chess_core`, `arena.py`, starter-kit bot) are **unblocked and should start today** — none of the remaining defects touch pure logic, and the clock ordering in §6.4 is correct where it is specified. **Phase 3 (the server) is blocked** on six new critical defects plus one round-1 critical that revision 2 claims to fix and does not. The blocking set is concentrated in §4.3, §4.4, §6.2/§6.5, §8.4 and §13.3 and is roughly half a day of spec work. |

**Round 1 fix status:** 9 Fixed · 12 Partially fixed · 1 Not fixed · 0 Ignored · 0 Regressed (22 findings).
**New issues in revision 2:** 6 critical · 10 significant · 9 gaps · 8 over-engineering.
**Round 1 cuts O-1 … O-5:** all five verified genuinely gone, with no dangling dependencies (details in §1.23).

The three things that matter most, if you read nothing else:

1. **§4.3's partial unique index does not enforce what §4.3 says it enforces.** A bot may be White in one non-terminal game and Black in another simultaneously. Verified by execution, not by reading. Round 1's C6 is therefore still open, and §4.2's compare-and-swap has no storage backstop for the invariant it is protecting.
2. **§6.2 restarts the clock on every delivery.** There is no `delivered_to_mover = 0` guard on the write and no statement that the flag is cleared at side switch. A bot that polls while thinking gets unlimited time; a bot whose connection drops during delivery loses the game to a `flag` it never saw. The same unguarded write is what makes §8.4's supersede rule lose a committed delivery.
3. **`controller='agent'` has no delivery channel at all.** §6.2 says the clock starts when the position is written into a *poll* response, and §13.3 makes `take_control` terminate that poll. So an agent-controlled game never starts a clock, never flags, and — via §4.3 — pins both bots out of the pool for the rest of the day. This is the headline MCP feature deadlocking the headline invariant.

---

## 1. Round 1 fix verification

| # | Round 1 finding | Status | One line |
|---|---|---|---|
| C1 | Unsupervised ticker | **Partially fixed** | §4.5 covers *death*; it does not cover *hang*, and a supervisor watching `done()` cannot see a stalled tick. |
| C2 | No ticker/handler concurrency control | **Partially fixed** | Lock + CAS + constraints are right, but there is no transaction boundary and the storage backstop it leans on (C6) is broken. |
| C3 | Clock-start instant undefined | **Partially fixed** | "Starts on delivery" is the right rule, written unguarded: no idempotence, no reset at side switch, no timeout for a position that is never delivered. |
| C4 | Restart mid-game undefined | **Fixed** | §7.1 aborts in-flight games unrated. Only defect is startup *ordering* (§2.10). |
| C5 | SQLite/async execution model | **Partially fixed** | Pragmas and `asyncio.Event` waiters ✓. §4.4's "cannot stall" claim is unsound (§2.6). |
| C6 | Challenge path creates a second game | **Not fixed** | The index does not do what it claims; challenge lifecycle is still absent. Detail below. |
| C7 | K-factor tier | **Fixed** | Flat K=24, provisional demoted to display, §17 property test now consistent. |
| C8 | Benchmark rated-ness ambiguous | **Fixed** | §5.1 + §10.4 resolve both readings explicitly. |
| C9 | 3+2 not viable for agent bots | **Partially fixed** | The false claim is honestly withdrawn; the replacement (§11.2 exhibitions) is unimplementable as specified and §13.3 contradicts it. |
| C10 | MCP caller identity | **Fixed** | §13.1 is end-to-end and honest about the transcript. New security gaps are separate findings (N-S7). |
| S1 | Duplicate concurrent long-polls | **Partially fixed** | One-waiter-per-bot ✓; the supersede rule introduces a delivery-loss race (N-C4) and `superseded` is missing from §8.2's enum. |
| S2 | "CAS retry" underspecified | **Fixed** | §8.3 defines 409 body and "discard and re-poll". |
| S3 | Control handoff race | **Partially fixed** | Atomicity ✓; auto-release is unmeasurable, and agent control has no delivery path (N-C3). |
| S4 | SSE backpressure/resume | **Partially fixed** | Bounded queues and heartbeat ✓; resume is decorative and the snapshot/stream handoff has a gap (N-S2). |
| S5 | No admin surface | **Partially fixed** | §15 exists; `paused` is undefined and abort has no stated interaction with waiters or the ticker (N-S8). |
| S6 | No rate limiting | **Partially fixed** | §8.5 is per-token; the one unauthenticated endpoint is the one that needs it most (N-S7). |
| S7 | Strike counters / clock cost | **Fixed** | Columns added; "rejected moves do not stop the clock" is stated and is the right call. One open edge (§3.4). |
| S8 | Decorative rating window; unverifiable owner | **Partially fixed** | Window cut ✓, same-owner `rated=0` ✓. "One competitor per owner" is now load-bearing but `owner` is still unverified free text. |
| S9 | `thinking_ms` conflation | **Fixed** | Split into `server_elapsed_ms` / `client_reported_ms` with the reasoning stated. |
| S10 | Small-pool Elo pathology | **Partially fixed** | Anchors mitigate cold start; no per-pair cap, and §9.2 rule 3 still permits rematch loops below 4 bots. |
| S11 | 150-move adjudication semantics | **Fixed** | Flat 200-ply draw. One interaction to note (§3.5). |
| S12 | `analyze_game` eval swing | **Fixed** | Cut, with the dependency argument stated. |

### 1.6 C6 — **Not fixed.** The partial unique index does not enforce one game per bot

§4.3 claims:

> `games`: partial unique index enforcing **at most one non-terminal game per bot**:
> `CREATE UNIQUE INDEX one_active_white ON games(white_bot_id) WHERE status IN ('pending','active');` and the same for `black_bot_id`.

Two separate single-column indexes enforce "at most one non-terminal game *as White*" and "at most one *as Black*". They do not compose into "at most one non-terminal game". Verified by execution rather than by reading:

```
python sqlite3 module -> 3.51.3
PARTIAL INDEX WITH 'IN': accepted
T1 white-twice: blocked -> UNIQUE constraint failed: games.white_bot_id
T2 bot 10 white in g1 AND black in g3: ALLOWED  <-- HOLE, two concurrent games
bot 10 non-terminal games: [(3, 40, 10, 'active'), (1, 10, 20, 'active')]
T3 second game after finish: allowed (correct)
```

Answering the three sub-questions directly:

- **Does SQLite support it?** Yes. Partial indexes are supported and the `IN (literal-list)` predicate is accepted (checked on 3.51.0 CLI and 3.51.3 via the `sqlite3` module). The `OR` form is equivalent if you want to be conservative. Syntax is not the problem.
- **Does it do what the spec thinks?** No. It closes exactly half the race. Round 1's C6 interleaving — ticker pairs `alice v bob` while a challenge handler inserts `carol v bob` — is blocked only if bob draws the *same colour* in both. §9.2's colour rule alternates from each bot's last colour, so bob drawing opposite colours in two concurrently-created games is not a corner case; it is roughly half of them.
- **The second game a bot plays?** Fine. Once game 1 is `finished`/`aborted` it leaves the index's predicate and the next insert succeeds (T3). No defect there.

Consequences, all of which are round 1's C6 verbatim: bob is in two games, `GET /bots/me/turn` returns one nondeterministically, the other burns its clock — and because §6.5 has no undelivered timeout (N-C2), the other one does not even flag; it hangs, holding the index slot, and bob plays no further games all day.

**Fix (choose one, both are small):**

- **Preferred — a seat table.** `CREATE TABLE seats(bot_id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL);` Insert two rows in the same transaction as the game insert and assert `rowcount`/catch `IntegrityError`; delete both on terminal transition. One primary key expresses the invariant exactly, works regardless of colour, and reads correctly on a projector. Note that it needs the transaction boundary from N-C5 to be safe.
- **Alternative — keep the index but make the predicate colour-free** by denormalising: two rows per game in a `game_participants(game_id, bot_id, colour)` table with `CREATE UNIQUE INDEX ON game_participants(bot_id) WHERE ...` — but the status lives on `games`, so the partial predicate cannot see it. This pushes you back to the seat table. Take the seat table.

Also still missing from round 1's C6, and still unaddressed: revision 2 never says **who creates a game from an accepted challenge**. `games.source ∈ matchmaker | challenge` implies two writers. Round 1's recommendation — the ticker consumes accepted challenges at the top of each tick and is the *only* game creator — is not adopted and not rejected. Adopt it; it is strictly less code than making two writers safe, and it is what makes the seat-table insert a single uncontended path.

### 1.1 C1 — Partially fixed. The supervisor watches the wrong failure mode

§4.5 wraps the tick body in `try/except Exception` and never lets the loop exit. Correct. It then adds a supervisor that "checks `ticker_task.done()` every 5s and restarts it". Given the first measure, the task can essentially never be `done()` — the supervisor is a backstop for a case the try/except already eliminated, and it does not cover the case that is now *more* likely:

**The tick hangs rather than raising.** The ticker awaits `store.write_lock`; a request handler holds it and is blocked inside `anyio.to_thread.run_sync` waiting for a worker token that never arrives (N-C6). The ticker task is not `done()`, raises nothing, logs nothing, and `consecutive_tick_errors` stays at 0. `last_tick_age_ms` climbs and the dashboard banner fires — which is genuinely valuable and is the part §4.5 got right — but nothing recovers.

**Fix:** make the supervisor watch `last_tick_age_ms`, not `done()`. If the age exceeds ~15s, log the current lock holder and the thread-limiter's `borrowed_tokens`, and (optionally) cancel and restart the ticker task. Two extra lines, and it turns an invisible stall into a loud one. Also record `last_tick_duration_ms` in `/health` — a tick that is slow but alive is the leading indicator of the same problem.

### 1.2 C2 — Partially fixed. No transaction boundary

§4.1 (one lock), §4.2 (CAS on every transition), §4.3 (storage backstops) are the right three primitives and the reasoning is correct. Two things are missing.

First, §4.3's backstop for the one-game invariant does not work (C6 above), so the CAS is currently unbacked for that specific case.

Second, **the spec never says a critical section is a transaction.** §10.2 says ratings are "computed and applied in the same critical section that finalises the game". That critical section is at minimum five statements: `UPDATE games` (the CAS), two `INSERT rating_history`, two `UPDATE bots`. `write_lock` provides mutual exclusion; it does not provide atomicity. This is a new critical (N-C5) and is detailed there.

### 1.3 C3 — Partially fixed. Detail in N-C1, N-C2, N-C4

§6.4's arithmetic ordering is correct and the "no increment on flag" rule is right. §6.6's tightening to 5s is right. §5's monotonic-vs-wall-clock split is right. What is wrong is the *lifecycle* of the delivery flag, and the fact that "delivery" is not observable to the server. Both below.

One thing to state plainly, because §6.2 and `AGENTS.md` both assert it as an invariant:

> "A clock starts on delivery, not on pairing. … A bot is never charged for time before it has seen the position."

**The second sentence is not implementable over HTTP.** The server can observe that it *wrote* a response; it cannot observe that the bot *read* one. The commit point (`turn_started_mono := now`, under `write_lock`) necessarily precedes the socket write, and the socket write can fail:

```
t=0.000  ticker (holds write_lock): pairs alice(W) v bob(B), INSERT games pending
t=0.001  ticker: alice has a held waiter -> UPDATE games SET status='active',
                 turn_started_mono=M0, delivered_to_mover=1; waiter.event.set()
t=0.002  ticker: releases write_lock                      <-- alice's clock is running
t=0.003  alice's laptop: attendee pressed Ctrl-C at t=-0.5; RST already sent
t=0.004  poll handler task wakes, returns the JSON response
t=0.005  Starlette writes to a dead socket -> ClientDisconnect
                                              alice never saw the position
t=180.0  ticker: alice flags. Rated loss, termination='flag'.
```

The window is now bounded by the client's liveness rather than by a 60s eligibility slop, which is a real improvement over revision 1 — but the invariant as written is false, and `AGENTS.md` restates it as absolute. Do not weaken the *design*; weaken the *claim*, and bound the damage:

**Fix.** Rephrase §6.2 as "a clock starts on delivery, not on pairing" and stop there. Then add one rule that costs almost nothing: **if a game's first flag occurs on a ply for which the server received no request whatsoever from the flagging bot for that game, record `no_show` (`rated=0`) rather than `flag`.** This cannot become a clock-stop exploit because it applies only when the count of received requests is exactly zero — a bot that polls even once for that game is charged normally. It converts the overwhelmingly common case (attendee restarted their bot at the wrong second) from a rated loss into a void, which is precisely the outcome §12's termination taxonomy exists to produce.

### 1.5 C5 — Partially fixed. §4.4's safety claim is unsound

Pragmas, single writer connection, `asyncio.Event` waiters: all correct, and round 1's thread-per-waiter hazard is genuinely gone. The claim under review is:

> Blocking `sqlite3` calls run via `anyio.to_thread.run_sync` so a slow fsync cannot stall the event loop and freeze every held long-poll. Never block the loop on a thread join while holding the lock.

The first sentence is true of the *event loop* and false of the *system*. See N-C6 for the thread-pool analysis. The second sentence appears to forbid the thing the design actually requires — the ticker will hold `write_lock` across `await to_thread.run_sync(...)`, which is correct and necessary, and which is not "blocking the loop". Reword it; as written, an implementer following it literally would move DB calls outside the lock and reintroduce C2.

Two mechanical omissions that will fail on the first run: `sqlite3.Connection` is thread-affine unless opened with `check_same_thread=False`, and §4.4's "separate read connections for display queries" (plural, unqualified) will be shared across arbitrary worker threads with interleaved cursors. Specify: writer opened `check_same_thread=False` and used only from its own dedicated single-thread executor; readers either one connection per request or a small pool.

### 1.9 C9 — Partially fixed. The replacement mechanism does not exist

§11's withdrawal of the revision-1 claim is exactly the right move and the arithmetic (`180 + 2n − 12n < 0 → n ≈ 18`) is correct. The resolution has a hole and a contradiction:

- **Hole.** §11.2 says a challenge between two benchmark bots "may specify `time_control='exhibition'` (300+10)". There is no place to put it. `POST /challenges` takes `{opponent}` (§8.1). `challenges` has no time-control column (§5). `games` has no time-control column (§5). §11 defines `TIME_CONTROL_MS`/`INCREMENT_MS` as *server config*, i.e. one global value. The MCP `challenge(opponent)` tool (§13.2) has no parameter either. As specified, exhibitions cannot be created. See N-S3.
- **Contradiction.** §11's whole resolution is "agents play unrated exhibitions" — and §13.3 gives an agent `take_control()` over a bot's *rated* game with no restriction. By §11's own arithmetic, an agent that takes control of a rated 3+2 game flags it. See N-S1.

### 1.11 / 1.13 / 1.14 / 1.15 / 1.16 / 1.18 / 1.20

S1, S3, S4, S5, S6, S8 and S10 are all "the mechanism was added, an edge was not closed". Each is detailed as a new finding rather than duplicated here: S1 → N-C4 and N-S9; S3 → N-C3 and N-S1; S4 → N-S2; S5 → N-S8; S6 → N-S7; S10 → N-S6. S8's residue is one sentence: §10.4's "one competitor per owner is enforced at registration" is now **structurally load-bearing** (it is what closes the feed-my-alt vector), but `owner` remains unverified free text supplied by the registrant. Typing a different owner string defeats it. Either accept it explicitly as an honour-system control and say so, or bind it to something the attendee cannot trivially vary (a per-attendee join code issued on the workshop slide, checked at registration — one column, one env var, and it also solves N-S7's open registration).

### 1.23 Verification of the accepted cuts (O-1 … O-5)

| Cut | Gone? | Anything still depending on it? |
|---|---|---|
| O-1 Postgres capability | Yes — §2 "SQLite only", §20 | No. §4.4 is written in SQLite terms throughout; no dialect abstraction survives in §3. |
| O-2 `analyze_game` eval swing | Yes — §13.2, §20 | No. §18's `diagnosing-bot-losses` skill is scoped to timing and strike markers, which is consistent. |
| O-3 Widening rating window | Yes — §9.2, §20 | No — but its replacement is ill-defined (N-S5). |
| O-4 Two-tier K | Yes — §10.1, §20 | No. §17's zero-sum property test is now consistent with §10.1; "provisional" survives only as §10.1 display text. |
| O-5 150-move material adjudication | Yes — §12, §20 | No. §12's flat 200-ply cap is self-contained. |

All five are genuinely gone with no dangling references. This was checked, not assumed.

---

## 2. New critical issues introduced by revision 2

### N-C1. §6.2 restarts the clock on every delivery, and `delivered_to_mover` has no defined lifecycle

§6.2 says, without qualification:

> `turn_started_mono` is set at the instant the position is written into a poll response for the side to move, and `delivered_to_mover` set to 1.

There is no guard. The write is `set`, not `set if not already set`. And `delivered_to_mover` is a single column on `games` (§5), so it must be cleared at each side switch — §6.4's step 5 says only "new `turn_started_mono` is set on delivery (step 2)" and never mentions clearing the flag.

**Exploit (deliberate).** A bot receives the position, starts thinking, and re-polls every 500ms while it thinks. Each poll re-delivers the current turn (§8.2 returns a turn when one is available) and each re-delivery resets `turn_started_mono` to now. The bot has **unlimited time**. This is the same class of defect §8.3 correctly closes for rejected moves ("a rejected move does not reset `turn_started_mono` … otherwise an illegal-move loop is a free clock stop") — the identical hazard exists on the delivery path and is not closed there.

**Accident (much more likely).** The SDK's poll loop is superseded by a reconnect (§8.4), or an attendee runs `curl /bots/me/turn` while their bot is running to see what is happening. Either resets the opponent-facing clock. The leaderboard silently measures who reconnected most.

**Inverse defect.** Because the flag is never specified as cleared, a literal implementation that *does* guard on `delivered_to_mover = 0` will deliver ply 0 and then never start a clock again for the rest of the game.

**Fix.** Make delivery idempotent per ply, and say so normatively in §6:

```
under write_lock:
    if game.delivered_to_mover == 0:
        game.turn_started_mono = monotonic_ns()
        game.delivered_to_mover = 1
        if game.status == 'pending': game.status = 'active'
    return payload            # same payload on every re-delivery, clock untouched
```
and in §6.4 step 5, explicitly: `delivered_to_mover = 0` at side switch, in the same UPDATE as the ply increment. Add the table-driven test to §17: "re-poll during think time does not reset the clock".

### N-C2. There is no timeout for a position that is never delivered mid-game

§6.3's join deadline applies to `pending` games only. §6.5 asserts:

> Flag detection is the ticker's job for the *undelivered* case (a bot that stops polling mid-game).

The ticker has nothing to measure. If the position was never delivered, `turn_started_mono` is NULL by §6.2, so `now − turn_started_mono` is undefined. There is no `undelivered_since` column in §5 and no rule that gives the ticker a deadline. §6.5 describes a mechanism that does not exist.

**Scenario.** Alice(W) and Bob(B) are paired at 11:04. Both are polling, so the pairing is legal under §9.1. The position is delivered to Alice; the game goes `active`. Alice moves at 11:04:03. Bob's laptop lid closed at 11:04:02. Bob is now the side to move with nothing delivered. `turn_started_mono` is NULL. The join deadline does not apply (status is `active`, not `pending`). The game sits there.

**Impact.** This is the worst-blast-radius bug in revision 2 because of how it compounds:
- The game never terminates. Neither bot is ever finalised, so neither is ever released.
- Via §4.3, both bots hold a non-terminal-game slot, so **Alice plays no further games for the rest of the day** — punished for her opponent's lid.
- `/health` reports it as an `active_game`, so the operator's dashboard says the server is healthy. There is no `stalled_games` counter.
- §6.3's no-show void cannot help: it only ever fires on `pending`.

Note this is also the *sole* protection against a bot that dies mid-game, which round 1 and §2 both treat as the normal case ("Clock-as-disconnect-detector" was called out as one of the design's strengths). Revision 2 removed the 60s disconnect rule (§20, "the separate 60s disconnect rule" is listed as cut) on the grounds that the clock subsumes it — but the clock only subsumes it if the clock runs, and after §6.2 the clock only runs on delivery.

**Fix.** Give the ticker something to measure. Add `to_move_since_mono` to `games`, set in the same UPDATE as the side switch (and at game creation for ply 0). Then one deadline covers both cases:

```
undelivered_for = now_mono - to_move_since_mono   (only when delivered_to_mover = 0)
if undelivered_for > JOIN_DEADLINE_MS:
    if ply == 0: abort, termination='no_show', rated=0      # nobody has played
    else:        finish, termination='flag', loser = side to move, rated as-is
```
The asymmetry is deliberate and worth one line of comment in the spec: at ply 0 nobody has invested anything, so void; mid-game the opponent has played real moves, so a void would punish the present bot. This also lets you delete §6.3 as a special case — it becomes the `ply == 0` branch of one rule.

### N-C3. `controller='agent'` has no delivery channel: agent-controlled games never start, never flag, and pin both bots

Three of revision 2's rules compose into a deadlock:

- §6.2: the clock starts when the position is written **into a poll response**.
- §13.3: `take_control()` sets `controller='agent'` **and wakes any held poll**, which returns `{"game_id": null, "reason": "agent_has_control"}` — i.e. it guarantees the poll response contains no position.
- §9.1: a bot with `controller='agent'` is not pool-eligible.

So while an agent holds control, `/bots/me/turn` structurally cannot deliver. The MCP `get_game(game_id?)` tool (§13.2) returns "an ASCII board plus FEN, SAN history, clocks and turn" — this is obviously the agent's delivery path, but §13.2 classifies it as an **Observe** tool and nothing anywhere says it sets `turn_started_mono` or `delivered_to_mover`. Read literally, revision 2 specifies a game that cannot progress.

Combined with N-C2, an agent-controlled game is not merely stalled but *permanently* stalled — no clock to flag, no join deadline (it is `active`), and both bots hold their §4.3 slots. One attendee experimenting with `take_control()` at 13:30 removes two bots from the ladder for the afternoon.

**Interaction with the join deadline (the case asked for).** Worse in the `pending` window:

```
t=0.0   ticker: pairs alice(W) v bob(B) -> pending. alice's poll is held, not yet delivered.
t=0.4   alice's Claude calls take_control()
t=0.4   §13.3: controller='agent', alice's held poll wakes with reason='agent_has_control'.
                delivered_to_mover stays 0. Nothing is delivered.
t=10.0  §6.3: pending, undelivered -> ticker voids as no_show, rated=0.
                "The present bot returns to the pool; the absent bot is removed
                 from the pool until it polls again."
```
Which bot is "absent"? Alice was present — she was holding a poll, and it was the *server's own MCP surface* that terminated it. Bob was never asked for anything, so his presence was never tested. §6.3's disposition rule is undefined for the case its own feature creates. And §9.1 then keeps Alice out of the pool until auto-release (30s) anyway, so the void gains nothing.

**Fix.** Make control-transfer transfer the delivery channel, not remove it:

1. State in §6.2 that **delivery is to the bot, over whichever channel its `controller` designates**: the long-poll when `controller='client'`, and `get_game()` / `get_legal_moves()` when `controller='agent'`. Both set `turn_started_mono` under `write_lock` with the N-C1 idempotence guard. This is one sentence and it makes §13.3 coherent.
2. Exclude games from the §6.3 void when the controller changed during the pending window — or, better, adopt N-C2's unified rule, under which the position remains undelivered and the deadline fires identically regardless of channel. That is the simpler outcome: one deadline, one rule, no special case.
3. Refuse `take_control()` while the bot has a `rated=1` game in progress (see N-S1), which removes the interaction from the rated ladder entirely.

### N-C4. §8.4's supersede rule can discard a delivery that has already been committed

§8.4 introduces one waiter per bot with supersede, and §8.4's last line commits delivery under the lock:

> Delivery happens under `write_lock`, in the same critical section that sets `turn_started_mono`.

Those two rules race. The critical section commits the *state change* atomically; it cannot commit the *response*, which is written by a different task after the lock is released.

```
t=0.000  alice's SDK holds poll A (waiter registered)
t=0.900  network blip; SDK's socket dies, SDK immediately reissues -> poll B in flight
t=1.000  ticker (holds write_lock): delivers to waiter A —
             UPDATE games SET status='active', turn_started_mono=M0, delivered_to_mover=1
             waiterA.payload = turn; waiterA.event.set()
t=1.001  ticker releases write_lock                       <-- clock running
t=1.002  poll B's handler runs: "one waiter per bot" -> supersedes A.
             A is cancelled / returns {"reason": "superseded"}; its payload is dropped.
             B registers as the waiter and blocks on its Event.
t=1.003  B waits for the *next* delivery event. There will not be one:
             delivered_to_mover is already 1.
t=181.0  alice flags a game she never received.
```

The reverse ordering (B supersedes A *before* delivery) is fine. The ordering above is not, and it is the common one — supersede exists precisely because clients reconnect, and reconnection is most likely exactly when a delivery is in flight.

Note §6.3 does not rescue this: delivery flipped the game to `active`, so the join deadline no longer applies. N-C2 would convert it from a hang into a `flag`, which is correct-but-unfair.

A related smaller race: `last_poll_at` is updated by the turn endpoint (§8.4) including on a `superseded` return. A client with two parallel poll loops — a very plausible product of "Claude wrote my bot at 14:30" — keeps superseding itself, remains pool-eligible under §9.1 the whole time, is paired repeatedly, and every pairing dies the same way.

**Fix — the mailbox, which resolves N-C1, N-C4 and half of the C3 delivery hazard together.** Deliver into a per-*bot* slot, not a per-*poll* waiter:

```
bot_mailbox[bot_id] = turn_payload     # written under write_lock, alongside
                                       # turn_started_mono / delivered_to_mover
```
Any poll — the original, a superseding one, or one that arrives a second later — drains the mailbox under the lock and returns its contents. The clock is set once (N-C1's guard), so re-delivery is free of charge-side effects. A dropped response costs the bot its reconnect latency instead of the game. This is roughly fifteen lines, it removes the "which waiter owns the payload" question entirely, and it is easier to explain on a projector than the supersede rule it replaces.

### N-C5. No transaction boundary: an exception mid-finalisation is unrepairable by construction

§4.1 gives mutual exclusion. §4.5 guarantees the ticker survives exceptions. §10.2 puts rating application "in the same critical section that finalises the game". Nothing in revision 2 says a critical section is a **SQLite transaction**, and §4.4's pragmas do not imply one — Python's `sqlite3` in default `isolation_level` mode only opens an implicit transaction around DML and commits on `.commit()`, which nobody has been told to call, and `autocommit`/`isolation_level=None` gives statement-level commits.

A finalisation is at minimum five statements:

```
UPDATE games SET status='finished', result=?, termination=?
  WHERE id=? AND status='active' AND ply=?         -- the CAS, rowcount asserted 1
INSERT INTO rating_history (game_id, white_id, ...)
INSERT INTO rating_history (game_id, black_id, ...)
UPDATE bots SET rating=?, wins=wins+1,   games_played=games_played+1 WHERE id=?
UPDATE bots SET rating=?, losses=losses+1, games_played=games_played+1 WHERE id=?
```

If anything raises after statement 1 — a disk error, a constraint violation from the still-unresolved N-S4, a `KeyError` in the Elo call, a `CancelledError` because the requesting client disconnected — §4.5 dutifully logs and continues, and the database is left with a **finished game that has no rating rows**. Recovery is impossible through the normal path: §4.2's CAS predicate is `status='active'`, which no longer matches. The game is permanently half-applied. §10.2's startup consistency check will report the discrepancy the next morning, which is exactly the "silently wrong until someone reconciles the tables" class `AGENTS.md` says must not exist.

The same applies to the seat-table fix in C6: the seat delete and the status change must be one transaction or a crash leaks a slot.

**Fix.** One sentence in §4.1, and it is genuinely four lines of code:

> A critical section is a transaction. Every acquisition of `write_lock` opens `BEGIN IMMEDIATE` and either commits or rolls back before releasing. The `rowcount` assertion on a CAS aborts the transaction; it does not merely skip the remaining work.

Then in §4.4, add the two rules that make this survivable under async: **never pass `cancellable=True` to `to_thread.run_sync` for DB work** (an abandoned worker thread would keep writing after the lock is released), and **shield the critical section from cancellation** (`asyncio.shield` or an `anyio.CancelScope(shield=True)`) so that a client disconnecting mid-request cannot tear down a half-applied transition. Both are exactly the kind of non-obvious async correctness detail that a workshop repository benefits from showing.

### N-C6. §4.4's "cannot stall" claim is unsound: one thread pool serves display reads, the writer, and possibly route handlers

§4.4 concludes that thread-offloading the blocking driver means "a slow fsync cannot stall the event loop and freeze every held long-poll". True of the loop. Not true of the game.

`anyio.to_thread.run_sync` uses a **default capacity limiter of 40 tokens per event loop**, and Starlette's `run_in_threadpool` — which is what FastAPI uses for any `def` (non-`async`) route handler, sync dependency, or sync `BackgroundTask` — shares that same limiter. So every one of these competes for the same 40 slots:

- the single writer connection's `UPDATE`/`INSERT` calls, executed **while `write_lock` is held**;
- every display read (`GET /state`, `GET /leaderboard`, `GET /games/{id}`, `GET /health`);
- every route handler an implementer happens to write as `def` instead of `async def`.

**Stall.** Twenty attendees open the dashboard; §14 tells them to refetch `/state` on any SSE gap, and §14's drop-oldest queue makes gaps routine (see N-S2). A burst of `/state` refetches occupies all 40 tokens. The ticker, **holding `write_lock`**, awaits a token to run its CAS. Every move handler awaits `write_lock`. The entire game loop is now queued behind display queries — the classic "worked with two bots, fell over with twenty", and §4.5's `last_tick_age_ms` will show it but §4.5's `done()`-based supervisor will not act on it (C1).

**Deadlock.** Upgrade the stall to a deadlock by writing one mutating handler as `def`. It runs in the pool, so it holds a token; it needs the write lock, which it cannot `await` from a thread, so an implementer reaches for a `threading.Lock` or `asyncio.run_coroutine_threadsafe`. Either way a token is held pending a lock held by a coroutine pending a token. This is not a hypothetical failure mode for AI-assisted FastAPI code; omitting `async` is one of the most common generated defects.

**Fix — three lines of policy, and it is better teaching material than the current text:**

1. **All route handlers are `async def`, without exception.** The only thing that ever enters a thread is a `sqlite3` call. State this as a rule in §4.4 and add it to `AGENTS.md`'s conventions.
2. **The writer gets its own dedicated single-thread executor** (its own `CapacityLimiter(1)` or a `ThreadPoolExecutor(max_workers=1)` used via `loop.run_in_executor`). Display reads cannot then starve the writer, and it satisfies `sqlite3`'s thread affinity for free.
3. **Cap the read path** with its own limiter (4 is ample at 20 clients) so a `/state` stampede degrades reads instead of the ladder.

Then replace §4.4's "Never block the loop on a thread join while holding the lock" — which is ambiguous and, read literally, forbids the correct design — with "the write lock is held across the writer thread hop; that is intended, and it is why the writer has a dedicated thread".

---

## 3. New significant concerns

### N-S1. `take_control` on a rated 3+2 game contradicts §11, and auto-release cannot be implemented from the data model

Three distinct problems in §13.3, all from new material:

- **It contradicts §11.** §11's resolution to C9 is "rated play is for programs; agent bots play unrated exhibitions". §13.3 lets an agent seize any game, including a rated 3+2 one, whose entire budget is 180s. §11's own arithmetic says one agent move costs 3–20s. `take_control()` on a rated game is a rated loss with extra steps. **Fix:** refuse `take_control()` while the caller's bot has a `rated=1` game in progress, with prose that points at the exhibition path. Do *not* solve it by flipping `rated=0` on control — that would let a losing bot void its loss on demand.
- **Auto-release is unmeasurable.** §13.3 specifies "auto-release after 30s of **agent inactivity**". `bots` (§5) has `controller` and `control_taken_at` and nothing else. There is no `last_agent_action_at`, so the only implementable reading is "30s from `take_control`", which is a different and worse rule: an agent actively making moves gets kicked at 30s regardless. Add the column.
- **The release is invisible to the SDK, and the SDK has no defined idle behaviour.** §13.3 says the SDK "logs `Control taken by agent; waiting.` and idles". Idles for how long? The `agent_has_control` response returns *immediately* (it is not a 20s hold), so a poll loop with no sleep becomes a hot loop and trips §8.5's 429 within two seconds; and a poll loop with a 30s sleep burns up to 30s of a 180s clock after release. Specify: when `reason='agent_has_control'`, the server holds the poll for the normal 20s and wakes it on release, exactly as `take_control` wakes it on acquisition. Symmetric, no client-side timer, no hot loop.

There is a fourth interaction worth pinning: **auto-release versus an agent mid-move.**
```
t=0    agent: take_control(); bot is to move with 40s left
t=29   agent: model returns a move; agent begins make_move(...)
t=30   ticker/auto-release: controller='client'; the held poll wakes with the position
t=30.1 SDK: choose_move -> POST /moves ply=12 -> 200
t=30.2 agent: make_move ply=12 -> 403 (controller mismatch) or 409 (ply moved)
```
The agent's move is lost and the attendee's Claude sees a bare failure at the moment the feature exists to serve. Auto-release must be evaluated under `write_lock` in the same critical section as any pending controller check, and the release response to the SDK should carry the reason so the SDK can log it rather than silently racing.

### N-S2. SSE resume is decorative, event ids are not restart-unique, and the snapshot/stream handoff has a gap

§14 is a real improvement over revision 1's silence, but three of its five bullets do not compose:

- **`Last-Event-ID` cannot be honoured.** §14 keeps a bounded 256-event per-client queue with drop-oldest and no server-side history. A reconnecting client sends `Last-Event-ID`, and the server has nothing to replay from. §14 half-admits this ("On any gap the client refetches `/state`"), which means the only correct server behaviour is *always* refetch — so the `Last-Event-ID` machinery is a concept with no behaviour. **Fix: delete it.** Reconnect ⇒ refetch `/state` ⇒ resume the stream. One fewer moving part, and it is honest.
- **Event ids must survive a restart.** §7.1 makes restarting a routine operator action. A browser that has been open since 09:30 will send `Last-Event-ID: 84213` to a process whose sequence restarts at 1, and (if resume were honoured) would conclude there is no gap. Prefix ids with a per-process run id — `"<run_id>-<seq>"` — even if you take the delete-resume fix, because the dashboard should be able to detect "the server restarted under me".
- **`/state` and `/events` have an ordering gap.** §14 calls `/state` the source of truth and `/events` the deltas, but never specifies the handoff. Fetching `/state` and *then* connecting to `/events` loses every event in between; the correct order is connect → buffer → snapshot → discard buffered events at or below the snapshot's id. That requires `/state` to **return the current event id**, which §14 does not say it does. One field, and without it the dashboard diverges silently — which is exactly the failure §14 was written to prevent.
- **Volume.** Round 1's C9 second-order observation stands and revision 2 addressed only half of it. §11's featured-game 20s hold fixes the *visual* churn; it does not fix the *event rate*. At 3+2 with alpha-beta bots, games finish in seconds — roughly one game per second across ten concurrent boards, with hundreds of plies per second in aggregate. A 256-event queue at that rate overflows a briefly-descheduled tab in under two seconds, every tab then refetches `/state`, and §14's drop-oldest policy has converted a slow client into a `/state` stampede against the shared thread pool (N-C6). **Fix:** emit move-level events only for the featured game; emit per-game coalesced state at ≤2 Hz for the grid; emit game start/end unconditionally.

### N-S3. §11.2's exhibition time control has nowhere to live

Detailed in §1.9. Concretely, all four of these need to change together or §11.2 should be cut: `games.time_control_ms` / `games.increment_ms` columns (games must carry their own clock, not read a global); `challenges.time_control`; `POST /challenges {opponent, time_control?}`; `challenge(opponent, time_control?)` in §13.2. Also add `time_control` to §8.2's turn payload — clients need it for time management, and with two controls in play a client cannot assume 180000/2000.

Given the product decision to keep challenges, this is small and should be specified now rather than discovered when someone tries to run the exhibition game on the projector.

### N-S4. The challenge lifecycle is still not usable end to end

§5 defines `challenges` with `status ∈ open | accepted | declined | expired | cancelled`. §8.1 exposes exactly two endpoints: create and accept. Consequences:

- **A bot cannot discover a challenge.** There is no `GET /challenges`, and §8.2's turn payload has no challenge field. The `/accept` endpoint requires an `{id}` the bot has no way to learn. As specified the feature is unreachable from the SDK; only the MCP path works, and only because a human is reading the transcript. **Fix:** either add `pending_challenges` to the no-game poll response (best — it costs one array on a response the bot is already making) or add `GET /challenges`.
- **Three of five statuses have no transition.** `declined` (no decline endpoint), `expired` (no TTL is specified anywhere — round 1 suggested 30s and revision 2 neither adopted nor rejected it), `cancelled` (no cancel endpoint).
- **No rule for challenging a bot that is mid-game**, which is the normal state of a competitor. Reject at create time, or queue until free? Queueing needs the ticker to consume it; rejecting is one line and is almost certainly right.
- **The `IntegrityError` path has no prose.** Once C6 is fixed with a seat table, a challenge accepted for a bot that has just been paired raises a constraint violation. §4.3 presents these constraints as backstops but §8 never says what an attendee sees. It must be a 409 with actionable prose ("`bob` just started a game; challenge again when it finishes"), not a 500.

### N-S5. §9.2's replacement pairing rule is not well defined, and it breaks the matchmaker's purity

§9.2 rule 1: "Sort the eligible pool by `games_played` ascending, then `|rating difference|` ascending." **`|rating difference|` is not a property of a bot**; it is a property of a pair. You cannot sort a list of bots by it. The intent is presumably "sort by `games_played`, then by `rating`, and pair adjacent entries" — say that, because it is a pure function under strict TDD (§18) and an ambiguous docstring is exactly how it ends up silently wrong.

Separately, §3 and §18 declare `chess_core/matchmaker.py` a **pure function over a pool snapshot**, and §9.2 then requires four pieces of state that a naive snapshot does not contain: each bot's **last colour** and **count of games as White** (rule 4), the **previous pairing** (rule 3), and **how long the pool has produced no games** (rule 2's ">30s" escape). None of these are listed as snapshot contents anywhere in §5 or §9. Define the snapshot's shape explicitly in §9.2 — it is the input type of the one function §18 says must be seeded-testable, and it is the boundary `AGENTS.md` cares most about.

One dead clause while you are there: rule 2's escape ("unless the pool would otherwise produce no games for >30s") can never fire, because §10.3 makes three anchors permanently pairable. The pool always produces a game. Either delete the clause or exclude anchors from the condition.

### N-S6. The anchor model is sound in the mean, mis-stated in §10.3, and biased in §9.2

The prompt asks whether §10.3's self-limiting claim is true given that competitors also play each other. Modelled properly:

Let `Δ = K(S − E)`, `E = 1/(1 + 10^((A−R)/400))`. Competitor-vs-competitor games conserve the pool total `T = ΣR`. Anchor games do not: they change `T` by `Δ` with no counterparty. A competitor whose true score rate against anchor `A` is `p` converges to `R* = A + 400·log₁₀(p/(1−p))`, and `Δ → 0` as `R → R*`.

So the claim is **half right**. Each individual rating converges — that part of §10.3 is correct and is standard practice. But §10.3's stated conclusion, "It does not inflate the pool", is the wrong claim in two ways:

- `T` genuinely does drift; what is bounded is each bot's rating, not the sum. Harmless (the leaderboard is ranks) but the spec should not assert conservation it does not have. §17's zero-sum property test is about `chess_core/elo.py` as a pure function and remains valid — worth one clarifying sentence so a reader does not think the *system* is zero-sum.
- **The fixed point is only as good as the anchor constant, and there are three of them, guessed independently.** §10.3 gives `~800 / ~1000 / ~1400` with no calibration procedure. If `ref-greedy` and `ref-depth3` are in truth 150 points apart but pinned 400 apart, then a competitor of *fixed* true strength has two different fixed points and its rating **oscillates** between them depending on which anchor it draws, with amplitude equal to the anchors' mutual calibration error. That is not inflation; it is worse, because it is visible on the projector as a bot whose rating swings for no reason. **Fix:** calibrate the three anchors against each other in the arena (§16 already does exactly this — `--bots ref_random.py ref_greedy.py ref_depth3.py --games 200 --seed 7`) and derive the three ratings from one measured ladder so they are mutually consistent. Do it before the workshop and pin the numbers with the measurement in a comment.

Two further defects in how anchors are *used*:

- **Anchor games are all downside for a strong bot.** At R=1600 against `ref-random` (800), `E = 0.9986`: a win is `+0.03`, a loss is `−23.97`, and there is no counterparty gain to offset it. The dominant loss mode for any bot all day is `flag` — a GC pause, a wifi blip, an attendee restarting their process. So the top of the leaderboard is decided substantially by who drew the fewest anchor games while unlucky. **Fix:** only pair a competitor with an anchor when `|R − A| ≤ 400`, or only while `games_played < 10`. One predicate, and it preserves the entire cold-start benefit that §10.3 correctly identifies.
- **Anchor pairing is deterministically biased toward the fastest bot.** §9.2 sorts ascending by `games_played` and pairs greedily, so with an odd eligible count the leftover is the *last* element — the bot with the **most** games played, i.e. the fastest-moving bot in the room. §9.2's "anchors are never chosen while two competitors can be paired" therefore does not distribute anchor games; it concentrates them on one bot. **Fix:** pick the anchor opponent as the bot with the *fewest* games played (drop the first element, not the last), or shuffle the leftover selection with the seeded RNG.

### N-S7. Registration is unauthenticated and unrate-limited; the REST auth scheme is never stated; the token hashing scheme invites an O(n·KDF) lookup

Four related items, all new surface in revision 2:

- **`POST /bots` has no token by definition**, so §8.5's per-token bucket cannot cover it. A runaway `while True: requests.post("/bots", ...)` — the same accident §8.5 exists to contain — fills the leaderboard with garbage bots and, once C6's seat table exists, fills the pool. **Fix:** rate-limit `/bots` by client IP *and* require a workshop join code (one env var, one form field, on the slide). The join code also gives §10.4's "one competitor per owner" something real to key on (see §1.18).
- **§8 never states the bot authentication scheme.** §13.1 specifies `Authorization: Bearer <token>` for the MCP path; §8.1's endpoint list says nothing about how `/bots/me/turn` identifies the caller, nor whether a bad token is 401 or 403, nor what the error body looks like. Round 1's M12 argued this exact case for §8.2's no-game shape and revision 2 fixed that one; auth is the same class of problem with twenty independently-guessing clients.
- **`token_hash` has no stated algorithm, and the obvious "secure" choice is a performance trap.** Bearer tokens are looked up *by* their hash on every request. A per-row-salted KDF (bcrypt/argon2) makes that lookup O(n) KDF evaluations per request — at 20 bots and 20 rps that is 400 KDF calls/second on the writer's box, and it will be blamed on SQLite. **Fix:** state it — `secrets.token_urlsafe(32)` for the token, indexed `sha256(token)` for the hash. A 256-bit random token needs no stretching, and saying so prevents the wrong instinct.
- **Bearer tokens over cleartext HTTP on shared conference wifi.** §8.6 anticipates a reverse proxy; nothing mentions TLS. Any attendee running a packet capture recovers every token on the network and can then resign anyone's games — the precise attack §13.1 says the token exists to prevent. Either terminate TLS at the proxy (§8.6 is the natural place to say so) or accept it explicitly in §1's non-goals. Silence is the one option that is wrong.

### N-S8. `paused` is three different things; admin abort has no defined interaction with waiters or the ticker

- **`paused` is undefined.** §15 offers a global `POST /admin/matchmaking/pause`. §9.1 lists "not paused" as a **per-bot** eligibility condition. §8.2 lists `paused` as a per-bot poll `reason`. `bots` (§5) has no `paused` column and `games` has no global-state table. Pick one — global-only is almost certainly right for a 20-person workshop — and delete the other two readings.
- **Admin abort needs a different CAS predicate.** §4.2's normative template is `WHERE id=? AND status='active' AND ply=?`. An admin aborting a stuck game does not know the ply, and the stuck game may be `pending`. §15's abort therefore needs `WHERE id=? AND status IN ('pending','active')` with `rowcount` asserted. §4.2 should state that the predicate is "the state you believe you are transitioning *from*", not literally "status and ply" — otherwise the normative section forbids the admin endpoint it later specifies.
- **Abort must wake the held poll and free the seats.** A bot holding a 20s poll on a game the operator just aborted will sit there; and both bots must be returned to the pool (which, post-C6-fix, means deleting their seat rows in the same transaction). Neither is stated.
- **`/admin/reset` is under-specified in a way that will corrupt the ladder.** "Wipe games/ratings, keep bots" must also reset `bots.rating` to 1200 and `wins/losses/draws/games_played` to 0, must run under `write_lock` in one transaction, must wake every held poll, and should refuse while `active` games exist unless forced. As written, the most likely implementation deletes `games` and `rating_history` and leaves `bots.rating` at its current value — at which point §10.2's invariant (`rating == 1200 + Σ deltas`) is violated for every bot on the first check.
- **Token re-issue has no rule for a bot mid-game.** The previous token holder is still polling. Invalidate immediately (the old client gets 401 and the attendee learns something), but say so.

### N-S9. §8.2's `reason` enum is already incomplete, and the turn payload has an undefined field

- §8.2 fixes `reason ∈ waiting_for_pairing | not_your_turn | agent_has_control | paused`. §8.4 then returns `reason: "superseded"`, which is not in the set. Two sections of the same revision disagree about a closed enum on the highest-traffic response in the system — precisely the failure round 1's M12 was about. Add `superseded`, and add whatever N-C2's abort path returns (`game_voided` or similar) so a bot learns its game was voided rather than inferring it from a `null`.
- **`poll_token` appears once**, in §8.2's example payload, and is never mentioned again — not in §8.3's move submission, not in §8.4, not in §5. Either it is a delivery-acknowledgement mechanism (in which case it is the missing half of the C3 delivery hazard and should be specified properly) or it is a leftover. Decide.
- The turn payload carries no `increment_ms` / `time_control`, which clients need (see N-S3), and no `strikes_remaining`, which would make §8.3's three-strike rule discoverable rather than surprising.

### N-S10. Startup and reset ordering are stated relative to the ticker, not relative to the socket

§7.1 says recovery runs "before the ticker starts". The condition that actually matters is **before the server accepts requests** — if the abort sweep runs concurrently with request serving, a bot can be handed a game that is about to be aborted, or submit a move into a game mid-sweep. Placing it in the FastAPI lifespan startup satisfies both; placing it in a startup *task* satisfies neither, and that is an easy and silent mistake. Say "in the lifespan startup, before uvicorn serves".

Minor, same section: §7.1 emits SSE events for the restart-aborted games. There are no SSE clients connected during lifespan startup. Harmless, but it reads as a requirement and someone will implement it and wonder why nothing appears; the dashboard learns about it from `/state` on reconnect, which is correct and sufficient.

---

## 4. Remaining gaps

1. **§5.1's table has no stated evaluation order.** Row 1 (`benchmark` ⇒ 0) and row 4 (anchor ⇒ 1 one-sided) can both match if a benchmark bot challenges `ref-depth3`, which nothing forbids. State "first matching row wins".
2. **The `role` / `is_anchor` relationship is never defined.** §9.1 requires `role='competitor'` for pool eligibility and §9.2 says anchors are pairable, so anchors must be `role='competitor', is_anchor=1` — but that is inference, not specification. Also undefined: do anchors appear on the leaderboard (they should — it is the ladder made visible), and do their `wins/losses/games_played` counters move?
3. **§10.2's consistency check must exempt anchors.** Their rating never changes and they have no `rating_history` rows, so a naive `rating == 1200 + Σdeltas` check fails on all three every startup and trains the operator to ignore the alarm.
4. **Flag versus strike precedence is undefined.** §6.4 orders the flag check before applying a move; §8.3 handles an illegal move with a 400 and a strike. If a bot submits an *illegal* move while already over time, does it flag or take a strike? Pin it (flag should win — it happened first) and put it in §17's table-driven clock tests.
5. **`python-chess` draw-claim semantics are unstated.** §12 lists `fifty_move` and `threefold` as terminations, which requires `claim_draw=True` on the outcome check; that is materially more expensive (threefold requires replaying the move stack) and is called on every move. Note it, and note that the fifty-move rule fires at exactly 200 ply — the same number as §12's adjudication cap — so the two rules collide and the tie must be broken deliberately.
6. **Registration collision behaviour is undefined.** Revision 1 said name+token preserves identity; revision 2 dropped that and did not replace it. What does `POST /bots` do when `name` already exists? 409 is right, but twenty Claude-written clients will handle it differently unless it is written down.
7. **Dashboard clock drift.** §14 sends `turn_started_at` (wall clock) so the browser can tick locally, while §5/§6 charge from `turn_started_mono`. After an NTP step the displayed clock and the authoritative clock disagree, and the visible one is the wrong one. Send a monotonic-derived `remaining_ms_at_event` plus the event's own timestamp instead.
8. **§14's "local/unrated render amber"** — "local" games (the §16 arena) never reach the server, so half that rule has no data source. Presumably it means unrated server games; say so.
9. **§19's phasing puts the admin surface last (phase 7), after the dashboard.** `/health`, abort, and pause are what you need at 11:00 on workshop day; the dashboard is what you need at 16:00. Move abort + pause + health to the tail of phase 3 — they are ~40 lines and they are the difference between a recoverable incident and a dead room. Also note phase 3 is three or four times the size of any other phase (store + API + supervised ticker + reference bots + fake-bot harness); consider splitting it at the store/API boundary so there is a real checkpoint inside it.
10. **§17 does not test the new machinery.** Add: re-poll-during-think does not reset the clock (N-C1); undelivered position mid-game terminates (N-C2); supersede during in-flight delivery still delivers (N-C4); an exception mid-finalisation leaves no half-applied game (N-C5); a `/state` stampede does not starve the ticker (N-C6); two concurrent game-creation paths for one bot produce exactly one game (C6).

---

## 5. Over-engineering

Ordered by how much they cost relative to what they buy at 20 bots for one day. The two product decisions the owner has ruled on (challenges/`benchmark`, two dashboard modes) are excluded — only defects in them are reported, above.

1. **`Last-Event-ID` / SSE resume (§14).** Cannot work with drop-oldest and no server history (N-S2). Delete it: reconnect ⇒ refetch `/state`. Removes a concept from the dashboard and from the explanation.
2. **`pending` + `JOIN_DEADLINE_MS` + `no_show` (§6.3, §7).** All of it exists because a game can be created for a bot that is not there. §9.1 already permits pairing only bots with a **currently held poll** — tighten it to *only* that, deliver to both waiters inside the same critical section that creates the game, and `pending` collapses into `active`, the join deadline disappears, `no_show` disappears, and the take_control-versus-join-deadline race in N-C3 stops existing. You still need N-C2's mid-game undelivered rule, but it becomes the *only* delivery deadline instead of the second one. This is the single largest simplification available and it removes a status, a termination, a config knob and two race interactions.
3. **The supersede rule (§8.4).** Correct instinct, more moving parts than the mailbox that replaces it (N-C4). Keep "one waiter per bot"; drop "supersede returns a distinct reason" — with a mailbox the second poll simply gets the payload.
4. **`/admin/consistency` (§15).** Duplicates §10.2's startup check. If the startup check is loud, the endpoint adds nothing an operator will use during a workshop.
5. **`/admin/reset` (§15).** Genuinely useful after the morning shakedown, but it is the most dangerous endpoint in the system (N-S8) and it competes for the same afternoon that everything else needs. If time is short, deleting the `.db` file and restarting is the same operation with no code.
6. **`challenges.status` with five values (§5)** when two have endpoints (N-S4). Ship `open | accepted | expired` and add the rest if anyone asks.
7. **The token-bucket rate limiter (§8.5)**, 20/s sustained with burst 40, is more mechanism than a fixed-window counter at this scale. Not worth a fight — it is ~15 lines either way — but the *unprotected* endpoint (N-S7) matters far more than the sophistication of the protected ones.
8. **Five orthogonal classification flags on a game or bot** — `role`, `is_anchor`, `rated`, `source`, `controller` — where `role`+`is_anchor` are not independent (gap 2) and `source` is used nowhere except display. Fold `is_anchor` into `role` (`competitor | benchmark | anchor`) and §9.1/§10.3/§5.1 each get shorter.

---

## 6. Prioritised recommendations

### Must change in the spec before phase 3 begins

Phases 1 and 2 (§19) are unaffected by every item here and should start now.

1. **Fix the one-game-per-bot invariant.** Replace §4.3's two partial indexes with a `seats(bot_id PRIMARY KEY, game_id)` table written in the same transaction as the game insert and deleted on terminal transition. Make the ticker the only creator of games; challenges enqueue and the ticker consumes them. Specify the constraint-violation response as a 409 with prose. *(C6, N-S4)*
2. **Rewrite §6.2 as an idempotent delivery.** Set `turn_started_mono` only when `delivered_to_mover = 0`; clear `delivered_to_mover` in the same UPDATE as the side switch in §6.4 step 5; state that re-delivery returns the same payload and never touches the clock. *(N-C1)*
3. **Give every undelivered position a deadline.** Add `to_move_since_mono`; the ticker voids at ply 0 (`no_show`, unrated) and flags mid-game. This subsumes §6.3 and is the only thing standing between a closed laptop lid and two bots being dead for the afternoon. *(N-C2)*
4. **Replace the waiter payload with a per-bot mailbox.** Delivery writes the payload under the lock; any poll drains it. Fixes the supersede race and makes a dropped HTTP response survivable. *(N-C4, S1)*
5. **Make agent control a delivery channel.** State in §6.2 that delivery goes over the channel designated by `controller`, with `get_game()` setting the clock under the same guard; refuse `take_control()` while a `rated=1` game is in progress; add `last_agent_action_at`; hold the poll and wake it on release. *(N-C3, N-S1)*
6. **State that a critical section is a transaction.** `BEGIN IMMEDIATE` on lock acquisition, commit or rollback before release, failed CAS aborts the transaction. Add: never `cancellable=True` for DB work; shield the critical section from cancellation. *(N-C5)*
7. **Rewrite §4.4's execution model.** All route handlers `async def`; the writer connection gets a dedicated single-thread executor (`check_same_thread=False`); reads get their own small limiter; delete the ambiguous "never block the loop on a thread join" sentence. *(N-C6, C5)*
8. **Correct §6.2's and `AGENTS.md`'s delivery claim** to "a clock starts on delivery, not on pairing", and add the zero-requests ⇒ `no_show` rule that bounds the unavoidable delivery-drop window. *(C3)*
9. **Make §9.2 implementable.** Sort by `games_played` then `rating`, pair adjacent; define the pool snapshot's fields explicitly (last colour, white count, previous pairing); delete or fix rule 2's dead 30s escape; select the anchor opponent as the *fewest*-games bot; gate anchor pairing on `|R − A| ≤ 400`. *(N-S5, N-S6)*
10. **Decide the exhibition mechanism or cut §11.2.** If kept: `time_control_ms`/`increment_ms` on `games` and `challenges`, a parameter on both the REST and MCP challenge calls, and the values echoed in §8.2's turn payload. *(N-S3, C9)*
11. **Close the auth surface.** State the REST bearer scheme and its error shapes; rate-limit and join-code-gate `POST /bots`; specify `secrets.token_urlsafe(32)` + indexed `sha256`; decide TLS explicitly in §8.6. *(N-S7)*
12. **Take the §5 simplification while you are in there:** define `paused` as global-only, add `superseded` to §8.2's enum, resolve or delete `poll_token`, and state that §5.1's table is first-match. *(N-S8, N-S9, gaps 1–2)*

### Can be handled during implementation

13. Supervisor watches `last_tick_age_ms` rather than `done()`; add `last_tick_duration_ms` and a `stalled_games` counter to `/health`. *(C1)*
14. SSE: delete `Last-Event-ID`, add a run-id prefix to event ids, have `/state` return the current event id and specify connect-then-snapshot ordering, coalesce non-featured move events to ≤2 Hz. *(N-S2)*
15. Admin: correct the CAS predicate for abort, wake held polls and free seats on abort and reset, define reset's effect on `bots` counters, define token re-issue mid-game. *(N-S8)*
16. Move `/health`, abort and pause to the end of phase 3; consider splitting phase 3 at the store/API boundary. *(gap 9)*
17. Calibrate the three anchor ratings from one seeded arena ladder before the workshop and record the measurement next to the constants. *(N-S6)*
18. Add the seven tests in gap 10 to §17, and pin flag-versus-strike precedence and the `claim_draw` decision. *(gaps 4, 5, 10)*
19. Apply the over-engineering cuts in §5 — items 1–3 there are the ones that also remove defects; items 4–8 are optional.

### For `AGENTS.md`, in the same change

- The CAS invariant should say "compare-and-swap on the state you are transitioning *from*", not "`status=? AND ply=?`", so it covers abort and reset.
- Add "a critical section is a transaction" alongside the existing write-lock invariant.
- Correct "A clock starts on delivery, not on pairing. A bot is never charged for time before it has seen the position" — keep the first sentence, drop the second, which the transport cannot guarantee.
- Add "every route handler is `async def`; only `sqlite3` calls enter a thread" to Conventions. It is a one-line rule that prevents the deadlock in N-C6, and it is exactly the kind of thing attendees should see written down.

---

## 7. What revision 2 got right

Briefly, and only where it was earned by this round's scrutiny:

- **§6.4's move accounting order** is correct — deduct, flag-check, apply, then increment — and stating it in the spec because "getting it backwards is silently wrong forever" is the right reason to state it.
- **§8.3's "rejected moves do not stop the clock"** identifies and closes a free-clock-stop exploit that most specs miss. The irony is that §6.2 leaves the identical exploit open on the delivery path (N-C1) — the reasoning was right, it was just applied in one place instead of two.
- **§11's withdrawal of the revision-1 agent-viability claim**, with the arithmetic shown. A spec that says "that claim was wrong and is withdrawn" is worth more as workshop material than one that quietly edits it.
- **§10.1's flat K**, with the zero-sum contradiction called out explicitly rather than papered over.
- **§12's flat 200-ply draw** replacing a bespoke material heuristic. One line, no arguments, nothing to explain.
- **§4.5's `last_tick_age_ms` on `/health` with a red banner** is the right observability primitive for a room with a projector, even though the supervisor attached to it watches the wrong signal.
- **§13.1's honesty about the token landing in the transcript.** Documenting the leak and scoping it ("not a secret from the attendee's own Claude, a secret from other attendees") is better than pretending it does not happen.

The design is close. The remaining defects are concentrated in four sections and share one root cause: revision 2 specified each new mechanism correctly in isolation and did not walk the interactions between them. That is what round 3 — or better, the phase-3 implementation itself, with §17's failure-path tests written first — should be organised around.
