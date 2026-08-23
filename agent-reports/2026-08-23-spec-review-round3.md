# Spec Review — Chess Arena Design, Round 3 (Verification)

| | |
|---|---|
| **Reviewed** | `docs/superpowers/specs/2026-08-23-chess-arena-design.md` **revision 3**, against round 2 (`2026-08-23-spec-review-round2.md`), round 1 (`2026-08-23-spec-review-systems-design.md`) and `AGENTS.md` |
| **Commit** | `fde74a2` |
| **Date** | 2026-08-23 |
| **Reviewer role** | Senior distributed-systems engineer — third-round verification review, pre-implementation |
| **Verdict** | **Conditionally cleared.** Revision 3 is a genuine fix, not a restatement: the four hardest round-2 criticals (transaction boundary, mailbox, idempotent delivery, thread-pool model) are correctly closed and were verified by execution. **Phase 3 is still blocked, but by four defects totalling ~15 lines of spec, not by half a day of design.** Three of the four are residues of round-2 fixes that were applied to the pseudocode but not to the surrounding text. |

**Round 2 fix status (48 tracked findings):** 26 Fixed · 17 Partially fixed · 4 Not fixed · 1 Regressed · 0 Ignored.
**New issues in revision 3:** 4 critical · 11 significant · 12 gaps · 8 over-engineering.

### Per-phase build readiness (§20)

| Phase | Ready? | Blocker |
|---|---|---|
| 1 `chess_core` — rules, clock, elo, match | **Yes** | §6.4 and §22 are complete and testable. Build today. |
| 1 `chess_core/matchmaker.py` | **No** | **N3-S3** — §9.2 rule 3 cannot be evaluated from the declared `PoolEntry`, and rule 2's skip-walk is not an algorithm. One paragraph. |
| 2 `arena.py` + starter kit | **Yes** | Depends only on rules/clock/elo. Build today. |
| 3a `store/` | **No** | **N3-C1**, **N3-C2** — the delivery UPDATE never reaches `active`, and the seat-insert failure path is unspecified in a way SQLite makes wrong by default. |
| 3b `api/` + ticker | **No** | **N3-C3**, **N3-C4**, N3-S1, N3-S9, N3-S11 in addition to the above. |
| 4 SDK | **No** | N3-S1 — the poll loop's shape depends on whether a `reason` response is held or immediate. |
| 5 MCP | **No** | N3-S9 — five of ten MCP tools have no REST route to call. |
| 6 Dashboard + SSE | **No** | N3-S4, N3-S5 — both one-line fixes, both silently wrong if left. |
| 7 Admin | **No** | N3-S6, N3-S7 — reset semantics and the anchor-exempt consistency check. |
| 8 Claude layer | **Yes** | `AGENTS.md` is already consistent with revision 3; all four round-2 `AGENTS.md` items landed correctly. |

**Everything I assert about SQLite below was executed against `sqlite3` 3.51.3, not reasoned about.** The script and its output are reproduced in §5.

The three things that matter most:

1. **§6.2's delivery UPDATE never sets `status='active'`.** Round 2's recommended pseudocode contained `if status=='pending': status='active'`; revision 3 kept the CAS predicate and dropped that line. §7's state diagram still says delivery causes the transition, so the diagram and the normative SQL disagree. Every first move then fails the §4.2 CAS (`status='active'`), the game is invisible to the flag sweep, **and** §6.3's abandonment sweep cannot rescue it because `delivered_to_mover` is already 1. A game that is delivered but never played is permanently stalled — the exact failure §6.3 was added to eliminate, reintroduced one section earlier.
2. **A constraint violation in SQLite aborts the statement, not the transaction** (verified). §4.3 says seats are inserted "in the same transaction as the game insert" and §4.1 says a failed CAS "aborts the transaction" — but foreign keys force the *game* row to be written first, so a losing seat race leaves an orphan `games` row and possibly one stray `seats` row inside a transaction that is still open and will commit. §4.3 never says whether the tick aborts wholesale or per pairing, and neither answer is safe as written. `SAVEPOINT` per pairing is the fix and it is three lines.
3. **§13.3's `take_control` refusal moves the round-2 N-C3 hazard rather than removing it.** The predicate is `rated=1 game in progress`, but §5.1 row 1 makes `rated` depend on how the game *ends*, so it is not knowable at the moment the refusal is evaluated; "in progress" is undefined for a `pending` game; and §12's challenge consumption performs no `controller` check at all, so a rated game can be created *for* an agent-controlled bot after the refusal has been passed.

---

## 1. Round 2 fix verification

### 1.1 Round 1 rows that round 2 had marked Fixed

C4, C7, C8, C10, S2, S7, S9, S11, S12 — all nine re-checked against revision 3 text. **All still fixed, no regressions.** §5.1's first-match ordering strengthened C8; §22's flat 200-ply cap and §10.1's flat K are unchanged and internally consistent with §18's zero-sum property test.

### 1.2 Round 1 rows that round 2 had marked Partial or Not fixed

| # | Round 1 finding | Status in rev 3 | One line |
|---|---|---|---|
| C1 | Unsupervised ticker | **Partially fixed** | §4.6 watches the right signal (`last_tick_age_ms`) and `/health` carries the right fields — but the supervisor's **action** on trigger is never stated. "The dashboard shows a red banner" is detection, not remediation. Say whether it cancels and restarts the ticker task, and log the lock holder. |
| C2 | Ticker/handler concurrency | **Fixed** | §4.1's "a critical section is a transaction", `BEGIN IMMEDIATE`, one commit-or-rollback, `asyncio.shield`, no cancellable DB calls. Correct and complete. |
| C3 | Clock-start instant | **Partially fixed** | §6 is a strong rewrite. Residue: the delivery **trigger** is never named (N3-C4), and §6.5 explicitly declines round 2's "zero requests ⇒ `no_show`" bound. The decline is defensible; it should be labelled as a decision, since it means a lid closing between pairing and first turn is a rated loss at ply 1 and a void at ply 0. |
| C5 | SQLite/async execution model | **Fixed** | §4.5 adopts all three: `async def` everywhere, dedicated single-thread writer executor with `check_same_thread=False`, separate read limiter. The ambiguous sentence is deleted and its deletion is documented. |
| C6 | Two game-creation paths / one game per bot | **Partially fixed** | `seats` is the right primitive and "the ticker is the only creator" is the right consolidation. What is missing is the failure path (N3-C2) and how a poll finds a bot's current game — via `seats` or via `games`? The spec never says, and the two differ exactly on the orphan row. |
| C9 | 3+2 vs agent bots | **Partially fixed** | The mechanism now exists end to end (columns, challenge parameter, echoed payload). The routing that makes it *safe* has holes — N3-C3. |
| S1 | Duplicate concurrent polls | **Fixed** | Mailbox + one waiter + 20s/30s skew. Supersede can no longer discard a delivery. |
| S3 | Control handoff race | **Partially fixed** | `last_agent_action_mono` added ✓; controller checked inside the CAS transaction ✓; refusal added ✓. Round 2's request that a poll be **held** on `agent_has_control` was not adopted — see N3-S1. |
| S4 | SSE backpressure/resume | **Partially fixed** | Resume deleted ✓, run id ✓, `/state` returns event id ✓, connect-then-snapshot ✓, coalescing ✓. Two mechanical defects remain (N3-S4, N3-S5). |
| S5 | Admin surface | **Partially fixed** | Abort is now correct (CAS from current status, frees seats, clears mailboxes, wakes waiters). Reset is not (N3-S6). |
| S6 | Rate limiting | **Fixed** | Per-token bucket ✓, and §8.5 closes the unauthenticated endpoint with a join code and an IP limit. |
| S8 | Unverifiable `owner` | **Partially fixed** | A join code exists but it is a **single shared** `JOIN_CODE`, so it authenticates *the room*, not *the attendee*. §10.4's "one competitor per owner" — which is what structurally closes feed-my-alt — is still defeated by typing a different `owner` string. Either issue per-attendee codes and derive `owner` from the code, or state in §10.4 that it is an honour-system control. |
| S10 | Small-pool Elo | **Partially fixed** | Anchors, the ±400 gate and rematch avoidance are in. No per-pair cap; acceptable at this scale, but §9.2 rule 3 drops rematch avoidance after three ticks, which in a 2–3 bot pool at 09:15 is immediately. |

### 1.3 Revision 2's new criticals

| # | Finding | Status | One line |
|---|---|---|---|
| N-C1 | Delivery restarts the clock | **Partially fixed** | The guard is correct and idempotent — verified: three successive delivery UPDATEs return `rowcount` 1, 0, 0 and `turn_started` stays at the first value. §6.4 step 5 clears the flag in the same UPDATE as the side switch ✓. **But the round-2 pseudocode's `pending → active` line was dropped** → N3-C1. |
| N-C2 | No deadline for an undelivered position | **Fixed** | §6.3's `DELIVERY_GRACE_MS` with the ply-0/mid-game split, and the asymmetry is explained. Two residues: `to_move_since_mono` is not declared `NOT NULL` and its being set at game creation is only implied by §6.1's prose, not by §4.3's insert. |
| N-C3 | Agent control has no delivery channel | **Fixed** (the channel) | §6.2's last line — "delivery goes over the channel named by `controller`" — is exactly right and is one sentence. The *gating* around it is not (N3-C3). |
| N-C4 | Supersede discards a committed delivery | **Fixed** | The `mailbox` table plus "any poll drains it" removes the question entirely. |
| N-C5 | No transaction boundary | **Fixed** | §4.1, verbatim what was asked for, plus shielding and non-cancellable DB calls. |
| N-C6 | One thread pool serves everything | **Fixed** | §4.5 adopts all three policies and states the writer-holds-the-lock-across-the-thread-hop rule that replaced the ambiguous sentence. |

### 1.4 Revision 2's new significant concerns

| # | Finding | Status | One line |
|---|---|---|---|
| N-S1 | `take_control` on rated games; auto-release unmeasurable; SDK idle | **Partially fixed** | Refusal ✓ (with holes, N3-C3); `last_agent_action_mono` ✓; 120s auto-release ✓ — but it is **unreachable**, because §6.3's 60s agent grace fires first (N3-S2). The SDK's behaviour on `agent_has_control` is still unspecified (N3-S1). |
| N-S2 | SSE resume / event ids / snapshot handoff | **Fixed** | All four adopted. Two implementation defects follow from the chosen id format (N3-S4). |
| N-S3 | Exhibition has nowhere to live | **Fixed** | Columns on `games` and `challenges`, parameter on REST and MCP, echoed in the turn payload. Complete. |
| N-S4 | Challenge lifecycle unusable | **Partially fixed** | `GET /challenges` ✓, decline ✓, 60s TTL ✓, mid-game rule ✓, 409 prose ✓. Two statuses are now unreachable and the feature is practically unusable against a competitor (N3-S10). |
| N-S5 | Pairing rule not well defined / breaks purity | **Partially fixed** | The sort is fixed and `PoolEntry` is declared — but the declared tuple does not contain the field rule 3 needs (N3-S3). |
| N-S6 | Anchor model mis-stated | **Fixed** | §10.3 is now honest about the injection and explains the self-limiting mechanism correctly; calibration is mandated; §9.3 adds the ±400 gate and fewest-games offer. One numeric nit in §7 gaps. |
| N-S7 | Auth surface | **Partially fixed** | Bearer scheme ✓, join code + IP limit ✓, `token_urlsafe(32)` + indexed `sha256` with the O(n)-KDF reasoning ✓, TLS decided explicitly ✓. The **error shape for a bad or missing token is still unstated** — 401 or 403, and what body? |
| N-S8 | `paused`; abort; reset; re-issue | **Partially fixed** | `paused` is global-only ✓, §4.2 reworded to "the state you are transitioning *from*" ✓, abort is complete ✓, re-issue refused while seated ✓. Reset is still wrong (N3-S6). |
| N-S9 | `reason` enum incomplete; `poll_token` | **Partially fixed** | `superseded` added ✓, `poll_token` deleted ✓, time control echoed ✓. `no_seat` was **added without a definition**, there is still no reason meaning "your game was aborted", and `strikes_remaining` is still absent. |
| N-S10 | Startup ordering relative to the socket | **Fixed** | §7.1 says lifespan, before the socket accepts. The spurious "emit SSE during startup" line is gone. |

### 1.5 Revision 2's gaps 1–10

| # | Gap | Status |
|---|---|---|
| 1 | §5.1 evaluation order | **Fixed** — "Evaluated first match wins, top to bottom". |
| 2 | `role` / `is_anchor` relationship | **Not fixed** — still inference. Anchor leaderboard visibility and counter behaviour still undefined. |
| 3 | Consistency check must exempt anchors | **Not fixed** — §10.2 still asserts `rating == 1200 + Σdeltas` unconditionally. Anchors have fixed non-1200 ratings and no `rating_history` rows, so the check fails on all three at every startup and trains the operator to ignore it. |
| 4 | Flag vs strike precedence | **Fixed** — §6.4 states it and gives the reason. |
| 5 | `claim_draw` semantics | **Fixed** — §22 auto-claims. (Round 2's supporting arithmetic was itself wrong; see §4.) |
| 6 | Duplicate registration name | **Not fixed** — `bots.name` is `UNIQUE` in §5 and §8.5 says nothing about the collision. |
| 7 | Dashboard clock drift | **Regressed** — §14 now sends `turn_started_mono`, a server monotonic value the browser cannot interpret at all. This is strictly worse than revision 2's `turn_started_at`. See N3-S5. |
| 8 | "local/unrated amber" | **Not fixed** — "local" games never reach the server; the rule still has half a data source. |
| 9 | Phasing | **Partially fixed** — §20 splits 3a/3b ✓ (good call, and the reason given is right). Admin is still phase 7 and the `/health` **endpoint**'s phase is ambiguous — phase 6 lists the *banner*. |
| 10 | Missing tests | **Partially fixed** — §18 gained re-delivery, mailbox drain, seats race, recovery. Still missing: half-applied finalisation, `/state` stampede vs ticker, mid-game abandonment. |

### 1.6 Revision 2's recommendations 1–19

7 Fixed (3, 4, 6, 7, 10, 14, 17) · 12 Partially fixed (1, 2, 5, 8, 9, 11, 12, 13, 15, 16, 18, 19) · 0 Ignored. Each partial maps to a row above. Recommendation 8's two halves split: the claim in §6.5 and `AGENTS.md` is corrected ✓; the `no_show` bound was declined without being labelled a decision.

**`AGENTS.md` items: all four landed correctly** — CAS-from-state, critical-section-is-a-transaction, the corrected delivery claim, and `async def` handlers.

---

## 2. New issues introduced by revision 3

### Critical — must be fixed before phase 3

#### N3-C1. §6.2's delivery UPDATE never transitions `pending → active`

§7's state diagram: `pending --> active: position delivered to side to move`. §6.2's normative SQL:

```sql
UPDATE games SET turn_started_mono=?, delivered_to_mover=1
 WHERE id=? AND ply=? AND delivered_to_mover=0
```

No `status`. No `started_at`. Verified — after three deliveries the row reads `('pending', 0, delivered=1, turn_started=100)`. The game is delivered, its clock is running, and it is still `pending`.

Everything downstream then fails, and fails *quietly*:

- §4.2's normative CAS predicate is `status='active'`. The first move submission never matches, returns **409**, and §8.3's defined client behaviour is "discard the move and re-poll" — so the SDK re-polls, drains the same mailbox payload, resubmits, and 409s again. A permanent 409/re-poll loop at whatever rate the SDK polls, against §8.6's 20 req/s bucket.
- The flag sweep scans `active` games. This game never flags.
- §6.3's grace sweep requires `delivered_to_mover = 0`. This game is 1. **Neither deadline applies.**
- Both bots hold `seats` rows forever. This is precisely round 2's N-C2 blast radius, reintroduced.
- `/health`'s `active_games` reads 0 while ten games are in flight; `pending_games` climbs, which is at least a visible signal, but nothing acts on it.

**Fix (one clause):**

```sql
UPDATE games
   SET turn_started_mono = :now,
       delivered_to_mover = 1,
       status     = CASE WHEN status = 'pending' THEN 'active'  ELSE status     END,
       started_at = CASE WHEN status = 'pending' THEN :wall     ELSE started_at END
 WHERE id = :id AND ply = :ply AND delivered_to_mover = 0 AND status IN ('pending','active')
```

Note the `CASE` on `status` must be evaluated before the `status` assignment takes effect — SQLite evaluates all RHS expressions against the pre-update row, so this is safe, but write it as two ordered assignments in the spec text so no one has to know that. Add `status IN ('pending','active')` to the predicate as well: without it, an aborted game can still be delivered.

#### N3-C2. The seat-insert failure path is unspecified, and SQLite's default behaviour makes both plausible readings wrong

Three facts, all verified:

- `PRAGMA foreign_keys = ON` with `seats.game_id REFERENCES games(id)` **rejects a seats-first insert** (`FOREIGN KEY constraint failed`). The ordering is forced: `games` row first, seats second. There is **no circular dependency** — `games` does not reference `seats` — so the ordering is well defined. That part of §4.3 is fine.
- **A `UNIQUE` violation aborts the statement, not the transaction.** After a failed second-seat insert, `in_transaction` is still `True`, the `games` row from the failed pairing is **still present**, and the first seat row is **still present**. A handler that catches `IntegrityError` and moves on will commit an orphan game.
- `SAVEPOINT` / `ROLLBACK TO` isolates a failed pairing cleanly while preserving earlier pairings in the same transaction.

§4.3 says only "two rows are inserted in the same transaction as the game insert". §4.1 says "a failed CAS aborts the transaction rather than leaving partial work" — but a constraint violation is not a CAS, and the question the prompt asks (*is the whole tick aborted, or just that pairing?*) has no answer in the text. Both answers are wrong as written:

- **Abort the tick.** `ROLLBACK` discards every pairing already made in that tick, plus every challenge consumed, plus any flag or abandonment finalisation the tick had already performed. One unlucky seat race silently voids a whole tick's work, and the next tick retries into the same race.
- **Catch and continue.** Leaves the orphan `games` row above. Whether that orphan is harmless depends on a question the spec never answers: **how does a poll find a bot's current game?** If the lookup is `SELECT ... FROM seats WHERE bot_id = ?`, the orphan is invisible and merely litters `/health`'s `pending_games`. If the lookup is over `games` by participant and non-terminal status, the orphan **is deliverable** — and the bot is now in two games, which is the exact invariant `seats` exists to enforce, defeated through the back door.

**Fix — three sentences in §4.3:**

1. The ticker takes a `SAVEPOINT` per pairing and per consumed challenge; on `IntegrityError` it issues `ROLLBACK TO` and abandons **that pairing only**. The tick's other work commits.
2. Because there is exactly one writer holding `write_lock` for the whole tick, the ticker may equivalently `SELECT bot_id FROM seats WHERE bot_id IN (?,?)` before inserting and skip the pairing — the constraint stays as the backstop, but the normal path never raises. Prefer this; the savepoint is then genuinely a backstop rather than control flow.
3. **State that a bot's current game is found through `seats`, never by scanning `games`.** One sentence, and it makes the invariant unbypassable rather than merely enforced at insert time.

Also: `seats.bot_id INTEGER PRIMARY KEY` is a rowid alias, so it **accepts `NULL` and auto-assigns a rowid** (verified: inserting `NULL` produced `(1, 1)`). Declare `bot_id INTEGER PRIMARY KEY NOT NULL REFERENCES bots(id)`.

#### N3-C3. §13.3's `take_control` refusal is evaluated on an unknowable predicate and is bypassed by the challenge path

The refusal is: *"`take_control()` is refused while a `rated=1` game is in progress."* Three distinct failures.

**(a) `rated` is not knowable when the refusal is evaluated.** §5.1's table is "first match wins, top to bottom", and row 1 is `Game ends no_show, server_restart, admin_abort → 0`. That row can only be evaluated at termination. Rows 2–6 are creation-time. So `games.rated` is a column whose value is partly a function of the future, and the spec never says whether it is written at creation, at termination, or both. `take_control` tests it mid-game.

*Fix:* state that `rated` is written at creation from rows 2–6, and that rows 1's terminations **override it to 0** in the finalising transaction. Two sentences, and it also tells §10.2 which games to expect `rating_history` rows for.

**(b) "In progress" is undefined for a `pending` game.** Interleaving:

```
t=0.0  ticker (write_lock): consumes/pairs alice(W) v bob(B) -> games row, rated=1, status='pending'
                            seats(alice), seats(bob) inserted. No delivery yet (no held waiter).
t=0.3  alice's Claude: take_control()
                       -> is a 'pending' rated game "in progress"? Text does not say.
                       -> if allowed: controller='agent' on a rated 3+2 board
t=0.4  delivery now goes over get_game() (§6.2 last line), AGENT_DELIVERY_GRACE_MS applies
t=..   §11's own arithmetic: the agent flags the rated game around move 18
```

*Fix:* the predicate should be **"the bot holds a `seats` row"**, not "a rated game is in progress". Refuse `take_control` whenever the bot is seated in a game with `rated=1` at creation, regardless of `status`. Cheap, uses the table that already exists, and has no ambiguity.

**(c) §12's challenge consumption performs no `controller` check, so the refusal can be walked around.**

```
t=0     alice: take_control()   -> no game, no seat -> ALLOWED. controller='agent'.
        §9.1 correctly keeps alice out of the matchmaking pool (controller != 'client').
t=5     bob: POST /challenges {opponent: 'alice', time_control: 'rated'}  -> 201 open
t=8     alice's Claude: accept via MCP -> queued
t=9     ticker: consumes the queued challenge. Both seats free. Creates a RATED 3+2 game
                for a bot whose controller is 'agent'.
```

Nothing in §12, §9 or §13.3 blocks this. The answer to the prompt's question — *does §13.3 fully remove the N-C3 hazard, or just move it?* — is **it moves it**: matchmaking is closed, the challenge path is not.

*Fix:* two rules in §12. (i) The ticker refuses to consume a `rated` challenge if either bot has `controller='agent'`; mark it `expired` with prose. (ii) `POST /challenges` rejects `time_control='rated'` when either bot is agent-controlled, with prose pointing at exhibition. The check must be at consumption as well as creation, because control can change in between.

**(d) The exhibition case the prompt asks about — agent goes idle mid-exhibition-game — is handled**, but by the wrong mechanism; see N3-S2.

#### N3-C4. The delivery *trigger* is never named, and the two plausible triggers produce different clocks

§6.2 says what delivery *does* and over which channel. It never says **who calls it**. There are three candidate triggers, and the spec supports all three by implication and mandates none:

1. **A poll arrives.** The handler, under the lock, sees it is this bot's turn with `delivered_to_mover=0`, delivers, drains, returns.
2. **The opponent's move commits** while a waiter is held. The move handler, still under the lock, delivers into the opponent's mailbox and sets the waiter's event.
3. **The ticker** sweeps for undelivered positions and pushes.

These are not interchangeable. Under (1) the clock starts when the bot's own request is being served — close to the honest ideal, and §6.3's grace covers the gap. Under (2) the clock starts at the opponent's move commit, which is what §6.5's "unavoidable window" paragraph is describing and therefore what §6.5 assumes. Under (3) the clock can start for a bot that has no request in flight at all, which is what §6.3's grace was written to catch — so (3) and §6.3 would fight each other.

The spec needs (1) **and** (2) — (1) alone would mean a bot holding a 20s poll never receives its opponent's move until the hold expires, which is the whole point of long-polling; (2) alone leaves a reconnecting bot undelivered. §8.4's "any poll for that bot drains the mailbox" implies both. But an implementer reading §6.2 in isolation has no reason to know that, and (3) is the natural reading of "each tick, for any non-terminal game where `delivered_to_mover = 0`" in §6.3.

**Fix — one paragraph in §6.2:**

> Delivery is attempted at exactly two moments, both already inside `write_lock`: (a) when the position becomes available — game creation, or the opponent's move committing — **if and only if** the side to move has a waiter held or, for `controller='agent'`, never; and (b) when a poll or an agent read arrives for a bot whose current position is undelivered. The ticker never delivers; it only enforces §6.3's deadline. Delivery is idempotent, so (a) and (b) racing is free.

Without this, §6.5's honesty paragraph describes a window that trigger (1) does not have and trigger (2) does, and two implementers will build two different clocks.

---

### Significant

#### N3-S1. It is undefined whether a `reason` response is held or returned immediately — the SDK's poll loop is a hot loop under three of six reasons

§8.4: "Server holds a poll for **20s**." §8.2 defines six `reason` values. The spec never maps one onto the other.

- `superseded` **must** return immediately (that is its purpose).
- `waiting_for_pairing` should be held — otherwise an unpaired bot spins.
- `agent_has_control`: §13.3 says `take_control()` "wakes any held poll, which returns `agent_has_control`" — that is the *first* response. What about the second, third, and hundredth? Round 2 (N-S1) asked explicitly that subsequent polls be held for 20s and woken on release. Revision 3 did not adopt it. As written, an SDK in its normal `while True: poll()` loop gets an immediate response and burns §8.6's 20 req/s bucket in two seconds, then takes 429s for the rest of the agent's session — while the attendee is watching their Claude drive the bot. This is a workshop-floor failure with an audience.
- `not_your_turn`, `paused`, `no_seat`: same question, unanswered.

**Fix:** one sentence in §8.4 — *"Every no-game response is returned only when the 20s hold expires or a state change wakes the waiter. `superseded` is the sole exception; it returns immediately by definition."* Then `release_control()`, `/admin/matchmaking/resume` and pairing must all wake waiters, which §13.3 and §15 already do for two of the three.

#### N3-S2. `AGENT_DELIVERY_GRACE_MS` (60s) fires before agent auto-release (120s), so auto-release is unreachable

§6.3 gives agent-controlled games 60s to take delivery. §13.3 releases control after 120s of agent inactivity. An agent that stops acting is, by construction, also not taking delivery. So at t=60 the game is finished `abandoned` with a loss for the agent-controlled side, and the 120s release fires — if at all — against a bot with no game.

This answers the prompt's exhibition-idle question: the game does **not** wait for release; it is lost at 60s. Since §13.3 confines agent play to unrated exhibitions the rating damage is nil, but the feature as documented (release control back to the SDK and let the bot carry on) can never happen, and the attendee sees their game end while their Claude is still mid-thought.

**Fix:** either set `AGENT_DELIVERY_GRACE_MS > ` the release timeout plus one grace period (e.g. release at 45s, grace at 60s — so release *rescues* the game and hands it back to the SDK, which is clearly the intent), or exempt agent-controlled games from §6.3 until release has fired. The first is better and is a constant change.

#### N3-S3. §9.2 rule 3 cannot be evaluated from the declared `PoolEntry`, and rule 2 is not an algorithm — this blocks phase 1

`PoolEntry = (bot_id, owner, rating, games_played, is_anchor, last_color, white_count, last_opponent_id)`.

Rule 3: *"If a bot cannot be paired for three consecutive ticks, its same-owner and rematch constraints are dropped in that order."* There is no `unpaired_ticks` field. The parenthetical says "tick count is passed in", but a global tick number does not tell the function how long *this bot* has been unpaired — that requires per-bot state the caller must carry. So the answer to the prompt's question — *is rule 3 deterministic and pure given the tick count is passed in?* — is **it is pure but not evaluable**: with only a tick number, the function cannot compute the predicate at all.

Two further ambiguities in the same function, which §18 puts under strict TDD:

- **"Dropped in that order" for a *pair*.** Relaxation is described per bot, but the constraints are pairwise. If alice has been unpaired for four ticks and bob for one, is the same-owner pair alice–bob permitted? Unstated. (Answer should be: yes, one relaxed side is enough — otherwise two same-owner bots alone in the pool never play.)
- **Rule 2's skip-walk.** "Walk the sorted list pairing adjacent entries. Skip a candidate pair if …; try the next adjacent candidate instead." If `(1,2)` is skipped, is the next candidate `(1,3)` — leaving 2 to pair with 4 — or `(2,3)`, leaving 1 unpaired this tick? Different answers give different ladders, and this is the one function §18 says must be seeded-testable.

**Fix:** add `unpaired_ticks` to `PoolEntry` (the store increments it for any pooled bot the matchmaker did not pair); state that one relaxed side suffices; and write rule 2 as explicit pseudocode — five lines. This is the only thing standing between today and a green phase 1.

#### N3-S4. §14's event ids are strings, and the prescribed comparison is lexicographic

§14: ids are `"{run_id}:{seq}"`; clients "apply buffered events with `id > state.event_id`". Verified: `"r7:9" > "r7:10"` is `True`, and lexicographic sort gives `['r7:1', 'r7:10', 'r7:100', 'r7:11', 'r7:2', 'r7:9']`. A dashboard following §14 literally will drop every buffered event whose seq has fewer digits than the snapshot's, for the first ~100 events of every run, and again at every decade boundary.

Second defect in the same rule: **`/state` must capture the event id *before* it reads the database.** If it reads the DB and then samples the counter, writes committed in between are both absent from the snapshot and filtered out of the buffer — permanent divergence, which is the exact failure connect-then-snapshot exists to prevent. If it samples first, the snapshot may include changes from events the client will replay, which is harmless because event application is state-replacement.

So the answer to the prompt's question — *does connect-then-snapshot close the gap it claims to?* — is **yes in ordering, no in mechanics**: the client-side ordering is right, the comparison and the server-side sampling order are both wrong.

**Fix:** send SSE `id:` as `"{run_id}:{seq}"` for restart detection, and carry `seq` as an **integer field in the event payload** (and in `/state`) for comparison. State that `/state` samples `seq` before its read, and that events with a different `run_id` than the snapshot force a full refetch.

#### N3-S5. §14 sends `turn_started_mono` to the browser — a value the browser cannot interpret

*"Clock values plus `turn_started_mono` are included so the browser ticks locally."* `turn_started_mono` is `time.monotonic_ns()` from the server process: an arbitrary origin with no relationship to any clock the browser has. There is no correct arithmetic the browser can do with it. This is a **regression** on round 2's gap 7, which at least sent a wall-clock value that was merely subject to NTP skew.

**Fix:** every clock-bearing event carries `{white_ms, black_ms, to_move, server_wall_ts}` sampled at emission. The browser computes `offset = local_now − server_wall_ts` once on the first event and ticks the side to move down from the sampled value. Round-trip skew is tens of milliseconds on a LAN, and the authoritative clock is unaffected either way.

#### N3-S6. `/admin/reset` will violate the FK and leave ratings inconsistent

*"wipe games/ratings/seats/mailboxes, reset bot counters to zero, keep bot identities."*

- **Order is forced.** Verified: `DELETE FROM games` before `DELETE FROM seats` fails with `FOREIGN KEY constraint failed`. Seats first, then games. Either state the order or declare `seats.game_id ... REFERENCES games(id) ON DELETE CASCADE`.
- **"Reset bot counters to zero" does not say what happens to `rating`.** Zeroing it violates §10.2 immediately; leaving it at its current value violates §10.2 immediately. It must be set to **1200 for competitors and benchmarks, and to the fixed calibrated value for anchors**. Say so — the naive implementation is the one that breaks the invariant the very endpoint next to it checks.
- Not stated: reset runs under `write_lock` in one transaction, wakes every held waiter, and should refuse while non-terminal games exist unless forced.

#### N3-S7. `/admin/consistency` and the startup check fail on all three anchors, every start

Round 2 gap 3, not fixed. §10.2 asserts `bots.rating == 1200 + Σ rating_history.delta` unconditionally; §10.3 gives anchors fixed ratings that never change and one-sided updates that produce no anchor rows. Every startup will log loudly for `ref-random`, `ref-greedy`, `ref-depth2`, and the operator will learn on day one that the alarm means nothing.

**Fix:** exempt `is_anchor` bots from the check and assert instead that they have **zero** `rating_history` rows and a rating equal to their configured constant. That is a stronger check and costs the same line.

#### N3-S8. Persisted monotonic values on `bots` survive a restart into a different monotonic epoch

`bots.last_poll_mono` and `bots.last_agent_action_mono` are `time.monotonic_ns()` values stored in a table. §7.1 clears games, seats and mailboxes but **not these**. Within one boot the values remain comparable, so a plain process restart is benign. After a host reboot the monotonic origin resets, so a value written at 16:00 yesterday is far in the future relative to today's clock: `now − last_poll_mono` is negative, §9.1's "within 5s" predicate is satisfied, and **every bot ever registered becomes pool-eligible at startup**. The ticker pairs twenty absent bots, and §6.3 voids them all fifteen seconds later. Self-healing, but it burns a tick's worth of pairings and fills the results ticker with `no_show` at exactly the moment the room is watching the server come back.

**Fix:** §7.1 sets `last_poll_mono = NULL` and `last_agent_action_mono = NULL` for all bots, and §9.1 treats `NULL` as ineligible. One line each.

#### N3-S9. Five MCP tools have no REST route to call

§13.1: the MCP server "has no default token and no privileged path" and §3 calls it "an HTTP client of `api/`, no privileged access". §8.1's endpoint list contains no route backing `get_my_bot()`, `get_legal_moves(game_id)`, `analyze_game(game_id)`, `take_control()` or `release_control()`. `get_game(game_id?)` is only half-backed: `GET /games/{id}` exists but the "defaults to the caller's current game" behaviour has no authenticated no-argument form.

This is not merely a phase-5 problem: `take_control` and `release_control` mutate state under `write_lock` and must be designed in phase 3b alongside the move endpoint's controller check.

**Fix:** add `GET /bots/me`, `GET /bots/me/game`, `GET /games/{id}/legal_moves`, `GET /games/{id}/analysis`, `POST /bots/me/control`, `DELETE /bots/me/control` to §8.1. Six lines, and it keeps §13.1's "no privileged path" claim true.

#### N3-S10. Challenge creation 409s against exactly the bots people want to challenge

§12: `201 open`, or `409 if either seat is taken`. §9.1 pools every idle competitor, and the ticker pairs on every tick. A competitor's steady state is therefore *seated*; the window in which a challenge can be created is roughly the sub-second gap between one game finishing and the next tick pairing it. "Grudge matches", one of the two stated reasons the feature exists, will essentially never work. Only `benchmark` bots — excluded from auto-matchmaking by §10.4 — are reliably challengeable, which is the self-play use case and does work.

Also in §12: **`expired` carries two unrelated meanings** — the 60s TTL, and "the seat was gone when the ticker consumed it". A bot cannot distinguish them, and the promised explanation is delivered "via an SSE event", but §14 is the dashboard stream and bots are not SSE clients. The `GET /challenges` inbox is the right channel and needs a `reason` field.

**Fix:** allow a challenge to be created against a seated bot (`201 open`), and resolve seat availability only at consumption. Add `challenges.resolution_reason` and surface it in the inbox. This costs nothing and makes the feature usable; the seat invariant is already enforced at the one place that matters.

#### N3-S11. §12's consumption ordering is airtight in one respect and unspecified in three

*Airtight:* because the ticker is the only game creator (§4.3) and it consumes challenges before pairing within a single tick, an accepted challenge cannot lose its seat to matchmaking. Between accept and the next tick nothing else can create a game. That reasoning holds and §12 deserves credit for it — **with one condition that is not stated**: the pool snapshot must be taken **after** challenge consumption, inside the same transaction. §4.1's "reads that inform writes happen inside the lock" implies it but does not order it. If an implementer snapshots the pool at the top of the tick (the natural shape), a bot seated by a consumed challenge is still in the snapshot and the pairing insert raises — landing straight in N3-C2.

*Unspecified:* (a) the order in which multiple queued challenges are consumed — say `ORDER BY id`, so it is deterministic and testable; (b) any cap on **accepted** challenges per bot — §12 caps only one `open` *outgoing* challenge, so a bot can accept challenges from three different opponents and the ticker will consume one and expire two; (c) whether a bot with a `queued` challenge remains pool-eligible in the tick *before* consumption — it must not be, or it gets paired at tick N and its challenge expires at tick N+1 for no reason the attendee can see. Add "no `seats` row **and** no `queued` challenge" to §9.1.

---

### Gaps

1. **`to_move_since_mono` is not `NOT NULL`,** and §4.3's game-insert never says it is set. If it is ever `NULL`, §6.3's comparison is `NULL` → false → the game is immortal. Declare it `NOT NULL` and name it in the insert.
2. **`seats.bot_id INTEGER PRIMARY KEY` accepts `NULL`** and auto-assigns a rowid (verified). Add `NOT NULL REFERENCES bots(id)`.
3. **`role` / `is_anchor` relationship still undefined** (round 2 gap 2). §9.1 requires `role='competitor'`; §9.3 pairs anchors; therefore anchors are `role='competitor', is_anchor=1` by inference only. Also undefined: do anchors appear on the leaderboard (they should), and do their `wins/losses/games_played` counters move?
4. **Duplicate registration name** (round 2 gap 6). `bots.name UNIQUE` with no stated behaviour. 409 with prose naming the taken name.
5. **Auth failure shape is unstated** — 401 vs 403 for a missing/invalid bearer token, and the body. §8.3 already spends 403 on controller mismatch, so auth should be 401.
6. **`no_seat` is in §8.2's enum with no definition.** When is it returned rather than `waiting_for_pairing`? They appear to be the same condition.
7. **No `reason` for "your game was aborted".** After `/admin/games/{id}/abort` or `server_restart`, the bot's next poll returns `game_id: null` with no explanation. Add `game_ended`.
8. **`strikes_remaining` still not in the turn payload** (round 2 N-S9). §8.3's three-strike forfeit is invisible until it fires.
9. **`challenges.status` has seven values, two unreachable.** `accepted` is never written (§12 says accept marks it `queued`); `cancelled` has no endpoint in §8.1.
10. **§10.3's arithmetic is slightly off.** "A bot at 1400 beating `ref-greedy` (1000) gains under 2 points" — computed, it is **2.18**. Trivial, but the spec is asserting a calculated figure and someone will check it. Also worth noting the asymmetry the ±400 gate does *not* remove: at the gate boundary a win is +2.18 and a loss is −21.82, so anchor games remain almost pure downside for a bot near the ceiling. Round 2's alternative — anchors only while `games_played < 10` — is still the cleaner rule.
11. **§14's "unrated/local amber"** (round 2 gap 8). "Local" arena games never reach the server.
12. **§18 still lacks three tests:** an exception mid-finalisation leaves no half-applied game (round 2 N-C5); a `/state` burst does not starve the ticker (N-C6); a mid-game undelivered position terminates as `abandoned` (N-C2). The first two are the tests that verify the two largest fixes revision 3 made.

**One round-2 finding that revision 3 was right to ignore:** round 2's gap 5 claimed "the fifty-move rule fires at exactly 200 ply — the same number as §22's adjudication cap — so the two rules collide". That arithmetic is wrong. The fifty-move rule triggers on `halfmove_clock >= 100`, i.e. 100 ply *since the last pawn move or capture*, which is not a total-ply count at all. There is no systematic collision. §22 is correct as written.

**One design consequence of §22 worth a sentence for attendees:** auto-claiming threefold removes the standard blitz technique of repeating once to gain increment, and force-draws a winning bot that repeats a position three times while shuffling. That is almost certainly the right call for a workshop, but it is a deviation from FIDE (where the claim is optional) and it will surprise anyone who plays chess. `chess-engine-techniques` should mention it.

---

## 3. Over-engineering and simplification

Revision 3 is 627 lines against revision 1's much shorter draft. Most of that growth earns its keep — §4, §6 and §7.1 are now the strongest parts of the document and they are the parts that will be read on the projector. These are the parts that do not.

1. **`mailbox` as a database table.** It is process state: §7.1 clears it on every start, so it never survives a restart usefully, and it is not read by anything outside the process. Making it a table adds a write to the hottest path in the system, inside `write_lock`, for zero benefit. Make it `dict[int, TurnPayload]` mutated inside the same critical section. **Removes one table and one write per delivery.**
2. **`§13.3`'s control-handoff subsystem is now five mechanisms serving a feature it has itself restricted to unrated exhibitions.** `take_control` / `release_control` / 120s auto-release / an agent delivery channel / `AGENT_DELIVERY_GRACE_MS` / poll-wake-on-take — plus the 403-vs-409 race and N3-S2's constant inversion. **Simplification: permit `take_control` only when the bot holds no `seats` row, and let agent-controlled bots play only exhibition games created by challenge.** That deletes mid-game handoff, auto-release, the poll-wake, the agent grace constant and two race interactions, while keeping the demo attendees actually want (Claude drives my bot in a slow game). This is the single largest reduction available in revision 3.
3. **`role` + `is_anchor` as separate flags** (round 2 over-eng 8, not taken). Fold into `role ∈ competitor | benchmark | anchor`. §9.1, §9.3, §10.3, §10.4 and §5.1 each get shorter, and gap 3 stops existing.
4. **`challenges.status` with seven values,** two of which are unreachable. Ship `open | queued | consumed | expired | declined`.
5. **`/admin/consistency`** duplicates §10.2's startup check, currently misfires on all three anchors (N3-S7), and is an endpoint nobody will curl mid-workshop. Keep the startup check; delete the route.
6. **`games.source`** is used nowhere except display, and `challenges` already records `game_id`.
7. **`moves.client_reported_ms`** requires SDK support and an `analyze_game` column, to serve a diagnostic that `server_elapsed_ms` already answers well enough for a one-day workshop. Cheap, but it is a field on the hot path and a thing to explain.
8. **Thirteen independent tunables** — `DELIVERY_GRACE_MS`, `AGENT_DELIVERY_GRACE_MS`, the 120s auto-release, the 60s challenge TTL, the 20s poll hold, the 30s client timeout, the 5s pool window, the 5s tick-age threshold, the 20s featured hold, the 200-ply cap, the ±400 anchor gate, and the two rate-limit numbers — spread across nine sections. At least one pair is already mutually inconsistent (N3-S2) and nobody noticed, because they are never written down together. **Add a single constants table to §5 or §11.** This is the cheapest defect-prevention measure left in the document.

---

## 4. Prioritised recommendations

### Must fix in the spec before phase 3 begins

Phases 1 (minus `matchmaker.py`), 2 and 8 are unblocked by everything below and should start now.

1. **Add the `pending → active` transition and `started_at` to §6.2's delivery UPDATE,** and add `status IN ('pending','active')` to its predicate. *(N3-C1 — without this, no game can ever be played.)*
2. **Specify the seat-insert failure path in §4.3:** check `seats` by `SELECT` inside the tick's transaction before inserting a game; keep the primary key as the backstop; wrap each pairing and each consumed challenge in a `SAVEPOINT` so one failure abandons one pairing rather than the tick. **And state that a bot's current game is found through `seats`, never by scanning `games`.** Declare `seats.bot_id ... NOT NULL REFERENCES bots(id)`. *(N3-C2)*
3. **Fix the `take_control` gate:** make it "the bot holds a `seats` row in a game created `rated=1`"; state that `rated` is written at creation from §5.1 rows 2–6 and overridden to 0 by row 1's terminations; and add a `controller` check to §12's challenge consumption *and* creation. *(N3-C3)*
4. **Name the delivery trigger in §6.2** — poll/agent-read arrival, and opponent-move commit when a waiter is held; the ticker never delivers. *(N3-C4)*
5. **State that every no-game poll response is held for 20s** and woken by a state change, `superseded` excepted. *(N3-S1)*
6. **Add `unpaired_ticks` to `PoolEntry`, write rule 2 as pseudocode, and state that one relaxed side suffices for a relaxed pair.** *(N3-S3 — this is what unblocks phase 1's matchmaker.)*
7. **Order `AGENT_DELIVERY_GRACE_MS` after the agent auto-release timeout** so release can rescue the game. *(N3-S2)*
8. **Add the five missing REST routes** backing `get_my_bot`, `get_legal_moves`, `analyze_game`, `take_control`, `release_control`. *(N3-S9)*
9. **§12: state consumption order (`ORDER BY id`), state that the pool snapshot is taken after consumption in the same transaction, cap accepted-unconsumed challenges, and add "no `queued` challenge" to §9.1's eligibility.** *(N3-S11)*
10. **Take simplification 1 (mailbox in memory) and simplification 3 (fold `is_anchor` into `role`) now**, while §4 and §5 are still being edited. Both remove a defect as a side effect.

### Handle during implementation

11. Reset: seats before games (or `ON DELETE CASCADE`); rating restored to 1200 / the anchor constant; under the lock, one transaction, wakes waiters, refuses while games are live. *(N3-S6)*
12. Exempt anchors from the consistency check and assert zero `rating_history` rows instead. *(N3-S7)*
13. `/state` samples `seq` before its read; carry `seq` as an integer alongside the string event id; force a full refetch on `run_id` change. *(N3-S4)*
14. Send `{white_ms, black_ms, to_move, server_wall_ts}` to the browser, never `turn_started_mono`. *(N3-S5)*
15. §7.1 nulls `last_poll_mono` and `last_agent_action_mono`; §9.1 treats `NULL` as ineligible. *(N3-S8)*
16. Allow challenge creation against a seated bot and resolve seats at consumption; add `resolution_reason` to the inbox. *(N3-S10)*
17. Define the supervisor's action on a stalled tick, not just its detection. *(round 1 C1 residue)*
18. Fill gaps 1, 3–9, 11: `NOT NULL` on `to_move_since_mono`, anchor role/leaderboard semantics, duplicate-name 409, 401 auth shape, define or delete `no_seat`, add `game_ended`, add `strikes_remaining`, trim `challenges.status`, fix the "local" amber wording.
19. Add §18's three missing tests — half-applied finalisation, `/state` burst vs ticker, mid-game abandonment. The first two verify revision 3's two largest fixes and currently nothing does.
20. Correct §10.3's "under 2 points" to 2.18, and consider gating anchor pairing on `games_played < 10` in addition to ±400. *(gap 10)*
21. Add the single constants table. *(over-engineering 8)*
22. Move `/health`, abort and pause to the tail of phase 3b — round 2's gap 9, taken only halfway.

---

## 5. Verification method

Every database claim above was executed against Python's `sqlite3` (library version **3.51.3**), not inferred.

| Test | Result |
|---|---|
| `INSERT seats` before `games`, `foreign_keys=ON` | `FOREIGN KEY constraint failed` — ordering is forced games→seats; no circular dependency exists |
| `INSERT games` then two `seats` in one txn | Accepted |
| Second seat violates `PRIMARY KEY` mid-transaction | `UNIQUE constraint failed: seats.bot_id`; **`in_transaction` still True; the orphan `games` row and the first `seats` row both survive** |
| `SAVEPOINT` / `ROLLBACK TO` per pairing | Isolates the failed pairing; earlier pairings commit intact |
| `DELETE FROM games` before `DELETE FROM seats` | `FOREIGN KEY constraint failed`; seats-first succeeds |
| `seats(bot_id INTEGER PRIMARY KEY)` insert with `NULL` | **Accepted**, rowid auto-assigned |
| CAS `UPDATE ... AND ply=<stale>` | `rowcount = 0`; correct ply gives 1 |
| §6.2 delivery UPDATE applied three times | `rowcount` 1, 0, 0; `turn_started` pinned at the first value ✓; **`status` still `'pending'`** |
| Lexicographic comparison of `"{run_id}:{seq}"` | `"r7:9" > "r7:10"` is `True`; sort order is `1, 10, 100, 11, 2, 9` |
| Elo at K=24, `R=1400` vs anchor `1000` | win `+2.18`, loss `−21.82` (spec says "under 2 points") |

---

## 6. What revision 3 got right

Earned by this round's scrutiny, and worth saying because most of the round-2 fix list is genuinely correct:

- **§4.1's transaction contract** is exactly what was asked for, including the two non-obvious async details (never `cancellable=True`, shield the critical section). It is also good teaching material as written.
- **§4.3's seats table, plus "game creation has exactly one creator"**, is the right pair of decisions. Consolidating the writer is what makes the constraint an uncontended backstop rather than a race, and §4.3 says so.
- **§4.5's execution model** adopts all three policies and, unusually, documents the deletion of the previous ambiguous sentence and why. A spec that records what it removed is more useful than one that quietly edits.
- **§6.2's idempotent delivery guard** is correct — verified — and the one-line generalisation "delivery goes over the channel named by `controller`" resolves round 2's headline critical elegantly.
- **§6.3's ply-0/mid-game asymmetry**, with the reason stated ("at ply 0 nobody has invested anything"), is the right rule and the right justification.
- **§6.5's honesty paragraph**, including the instruction to *`AGENTS.md`* not to overclaim, is the best thing in the document. Specs that name the window they cannot close are rarer than they should be.
- **§10.3's rewrite** — conceding that one-sided anchor updates do inject points, and explaining precisely what bounds the injection — replaced a false claim with a true and more useful one.
- **§20's split of phase 3 at the store/API boundary**, with the reason given, is exactly the right instinct for the highest-risk code in the project.

The failure mode of revision 2 was specifying each mechanism correctly in isolation without walking the interactions. Revision 3 largely fixed that. Its own failure mode is narrower and more mechanical: **three of the four blocking defects are places where a round-2 fix was applied to the pseudocode or the CAS predicate but not propagated to the sentence next to it** (the `pending → active` line, the seat failure path, the `rated` predicate). That is a proofreading class of error, not a design one, and it is why the remaining work is an hour rather than a day.
