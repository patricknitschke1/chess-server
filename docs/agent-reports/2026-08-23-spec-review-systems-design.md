# Spec Review — Chess Arena Design (Systems Design)

| | |
|---|---|
| **Reviewed** | `docs/superpowers/specs/2026-08-23-chess-arena-design.md`, cross-checked against `AGENTS.md` |
| **Commit** | `e034ebd` ("Add AGENTS.md with project goal and invariants") |
| **Date** | 2026-08-23 |
| **Reviewer role** | Senior distributed-systems engineer — adversarial design review, pre-implementation |
| **Verdict** | **Do not start implementation as written.** The product shape is right and the pedagogical framing is genuinely good, but the concurrency model, the clock model, and the rating model each contain defects that produce *silently wrong* behaviour — the exact failure class `AGENTS.md` says must be avoided. Roughly a day of spec work fixes them; discovering them in phase 3 does not. |

**Issue counts:** 10 critical · 12 significant · 12 minor/gaps · 8 over-engineering findings.

The three highest-value changes, if you read nothing else: (1) define a **single writer** for all game-state mutation and extend compare-and-swap to terminal transitions (§C1, §C2); (2) define **exactly when a clock starts and stops**, and the restart policy for in-flight games (§C3, §C4); (3) fix the **Elo K-factor asymmetry** and the **rated-ness of benchmark games**, which as written are both exploitable and self-contradictory with §13 (§C7, §C8).

---

## Critical issues

### C1. The single ticker is an unsupervised single point of failure

**What is wrong.** §7 states "No per-game background tasks… the single ticker handles only what happens without a request — pairing and flag-fall. One loop for the whole server." The spec never says what happens when that loop raises.

**Scenario.** At 14:07 the ticker's pairing pass hits a `sqlite3.OperationalError: database is locked` (see C5), or an `IndexError` in the widening-window code when the pool has exactly one bot, or a `KeyError` from a bot that deregistered mid-snapshot. The coroutine dies. FastAPI does not restart it and `asyncio` swallows the exception into a never-awaited task.

**Impact.** Total, silent loss of two functions with **no error surface anywhere**: no new games are ever paired again, and no game ever flags again. Existing games continue to advance on move submission, so the dashboard keeps showing live moves — the server looks healthy. Every bot in a game whose opponent has stopped polling hangs forever. The room's experience degrades over ten minutes with no signal, and diagnosis requires reading a server log that nobody is watching. This is the highest-blast-radius defect in the document.

**Fix.**
- Wrap the tick body in `try/except Exception`, log with the tick number, and continue. Never let the loop exit.
- Record `last_tick_at` and `consecutive_tick_errors` in process state.
- Expose `GET /health` returning `{last_tick_age_ms, active_games, pooled_bots, held_polls, db_writable}`, and render a red banner on the dashboard when `last_tick_age_ms > 5000`. The operator must be able to see the heartbeat from the back of the room.
- Add a supervisor: if the task object is ever `done()`, restart it.

---

### C2. No concurrency control between the ticker and the move handler

**What is wrong.** The spec's only stated concurrency primitive is ply-CAS on move submission (§6, and `AGENTS.md` invariant 2). Flag-fall, game finalisation, Elo application and pairing have **no stated synchronisation at all**. FastAPI async handlers and the ticker coroutine interleave at every `await`, and every DB access is an `await` point.

**Scenario (double finalisation).**

```
t=0.000  ticker:  SELECT * FROM games WHERE status='active'   -> game 42, black to move,
                  black_ms=120, turn_started_at=T0, now-T0=125ms over
t=0.002  ticker:  await -> yields to event loop
t=0.003  handler: POST /games/42/moves {ply: 37, move: "e7e5"} arrives
t=0.004  handler: SELECT game 42 -> status='active', ply=37. CAS passes.
t=0.006  handler: await -> yields
t=0.008  ticker:  UPDATE games SET status='finished', result='1-0', termination='flag'
t=0.009  ticker:  INSERT rating_history (white +16, black -16); UPDATE bots ratings
t=0.011  handler: INSERT INTO moves (42, 37, 'e7e5', ...)
t=0.013  handler: UPDATE games SET fen=..., ply=38, status='active'   <- resurrects the game
t=0.015  handler: (later) game ends by checkmate -> second rating_history row for game 42
```

**Impact.** A finished game becomes active again; two `rating_history` rows exist for one `game_id`; ratings are applied twice; the leaderboard is quietly wrong for the rest of the day. Variants of the same interleaving produce a move applied to a finished game, a flag applied to a game that already ended in checkmate, or `bots.rating` diverging from the sum of its `rating_history` deltas. Every one of these is invisible until someone reconciles the tables.

**Fix (fits the "readable on a projector" constraint).**
- Introduce **one process-wide `asyncio.Lock` guarding all game-state mutation**. Ticker and handlers both acquire it. At 20 bots the contention is irrelevant and the code is four lines. Do not build per-game locks — the bookkeeping outweighs the benefit at this scale.
- Independently, make every state transition a **conditional UPDATE**, not just move submission. Extend the CAS invariant in `AGENTS.md`:
  `UPDATE games SET status='finished', ... WHERE id=? AND status='active' AND ply=?` and assert `rowcount == 1`. If it is 0, the game already transitioned — drop the work.
- Add a `UNIQUE` constraint on `rating_history(game_id, bot_id)` so double-application is impossible at the storage layer, not just the application layer.
- Add `PRIMARY KEY (game_id, ply)` on `moves` (§4 declares no keys at all) — the database-level backstop for the ply invariant.

---

### C3. When a clock starts is undefined, and the definition implied by §7 is wrong

**What is wrong.** §4 stores `turn_started_at`; §7 makes a bot pool-eligible if it "has polled within the last 60s"; §8 says "Client stops polling → clock simply runs out". Nowhere does the spec say at what instant White's clock starts on move 1, or whether it starts before the position has been delivered.

**Scenario.** Alice's bot long-polls at 14:03:00 and the poll returns empty at 14:03:20. Alice hits Ctrl-C to edit `bot.py`. At 14:03:50 the ticker sees `last_seen` 50s ago — inside the 60s window — and pairs her against Bob. `turn_started_at` is set to now. Alice's process does not exist. Her 180s clock burns down while she edits. She restarts at 14:05:30, polls, receives a position she is to move in with 80s left and no idea why. If she takes three minutes she is flagged and the game is recorded as a `flag` loss with a full rating hit.

The same shape recurs on every pairing: the poll-eligibility window is up to 60s wide, so a paired bot can be up to 60s behind before its clock even starts, on a 180s budget. And a benchmark bot that registered at 09:00 and was never run can be challenged at 14:00, starting a clock for a process that does not exist.

**Impact.** Rated losses caused by server bookkeeping rather than bot quality — precisely the outcome §4 says the `termination` taxonomy exists to prevent ("A bot losing on time has a performance bug, not a chess bug"). Here it has neither. It also creates a real fairness gap between a bot in a held long-poll (clock effectively starts on delivery) and one that reconnects (clock started earlier).

**Fix.** Write down the clock contract explicitly, in `chess_core/clock.py` terms:

1. A game is created in status `pending`, with both clocks at 180 000 ms and `turn_started_at = NULL`.
2. `turn_started_at` is set at the instant the position is **written into a poll response** for the side to move — not at pairing, not at the previous move.
3. If a `pending` game is not delivered to the side to move within a **join deadline** (10s is generous), the ticker voids it as `termination='no_show'`, `rated=0`, and returns the other bot to the pool. No rating change for either side. The no-show bot is dropped from the pool until it polls again.
4. Tighten pool eligibility from 60s to "**has a poll request currently held, or polled within 5s**". A bot that is not there should not be paired. Sixty seconds is far too loose for a 180-second clock.
5. On move receipt: `elapsed = receive_time − turn_started_at`; `remaining −= elapsed`; if `remaining < 0` → flag; else `remaining += 2000`; switch side; set the new `turn_started_at` at delivery per (2). State this ordering in the spec — increment-before-versus-after-flag-check is exactly the kind of thing that is silently wrong forever.
6. Use `time.monotonic_ns()` for all elapsed arithmetic, and store the monotonic value alongside the wall-clock timestamp. Wall clock on a laptop is subject to NTP steps and sleep/resume; a suspended lid will otherwise flag the whole board.

---

### C4. Server restart mid-game has no defined behaviour — and the obvious behaviour flags everybody

**What is wrong.** §14 and the deployment row assume a single process, but there is no startup recovery policy. `turn_started_at` is an absolute timestamp, so the naive reload charges the entire downtime to whoever was to move.

**Scenario.** At 14:12 you fix a dashboard CSS bug and restart. Fifteen games are active. Restart takes 25 seconds. On startup the ticker's first flag pass computes `now − turn_started_at` for each and immediately flags up to 15 games — a wave of simultaneous rating hits, all attributed to `flag`, all of which look to attendees like their bot crashed.

**Impact.** A routine operator action corrupts the leaderboard and destroys attendee trust at the worst possible moment. And the recovery question is not academic: at a one-day workshop you *will* restart the server.

**Fix.** Choose one and write it into §8, plus a test in the fake-bot harness:
- **Preferred (simple, honest):** on startup, mark every `active`/`pending` game `status='aborted'`, `termination='server_restart'`, `rated=0`, and emit an SSE event. Bots re-poll, get "no game", and are re-paired within a tick. Twenty seconds of lost play, zero rating damage, four lines of code.
- **Alternative (preserves games):** on startup set `turn_started_at = now` for all active games, forgiving the gap. Requires the clock to already be monotonic-safe (C3) and leaves a window where a genuinely-flagged bot is forgiven. More code, more edge cases, marginal benefit.

Also confirm the data model can actually reconstruct a game: it can — `moves` plus `games.fen` is sufficient — but only if `fen` is written in the same transaction as the move row. Say so.

---

### C5. The SQLite + async execution model is unspecified and the default is a stall

**What is wrong.** §2 commits to "SQLite" and "single process"; §3 to FastAPI; nothing says how database calls are executed relative to the event loop, or what pragmas are set.

**Scenario A (event-loop stall).** The stdlib `sqlite3` driver is blocking. If repositories call it directly from async handlers, a single 40 ms write (fsync on a conference-room laptop with a slow disk, or the OneDrive-synced directory this repository lives in) stalls the event loop. During that stall, all ~20 held long-polls are frozen and the ticker cannot run. Under the sustained write load of 20 concurrent games this manifests as tick drift, which manifests as late flag detection, which manifests as bots surviving past their time.

**Scenario B (`SQLITE_BUSY`).** Default journal mode is rollback, not WAL. The ticker's write transaction and a move handler's write transaction collide; with default `busy_timeout=0` one raises `database is locked` immediately. Combined with C1 that error kills the ticker permanently.

**Impact.** Intermittent, load-dependent, and near-impossible to diagnose live. This is the classic "worked in testing with two bots, fell over with twenty" failure.

**Fix.** Make these explicit in §2/§3 and set them at connection open:
```
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```
Then either use `aiosqlite`, or keep stdlib `sqlite3` behind `anyio.to_thread.run_sync` with a **single connection and the global write lock from C2** — which also removes `SQLITE_BUSY` entirely, since there is exactly one writer by construction. One writer, WAL for concurrent readers, is both correct and the most projector-legible option.

Separately: long-poll waiters must block on an `asyncio.Event`, never on a thread. If a waiter ever occupies an `anyio` worker thread, 20 held polls exhaust the default 40-thread pool alongside DB work and the server deadlocks.

---

### C6. The challenge path can create a second concurrent game for a bot, and challenges are absent from the data model

**What is wrong.** §6 lists `POST /challenges`. §4 has no `challenges` table. There is no accept, decline, expire, or list endpoint. §7 says "One concurrent game per bot", but game creation now has two independent code paths — the ticker and the challenge handler — with no stated mutual exclusion.

**Scenario.**
```
t=0.00  ticker:    snapshot pool -> [alice, bob, carol, dave]; decides alice v bob
t=0.01  handler:   POST /challenges {opponent: "bob"} from carol
t=0.02  handler:   bob is idle -> INSERT games (carol, bob)
t=0.03  ticker:    INSERT games (alice, bob)
```
Bob is now in two active games. His next `/bots/me/turn` returns one of them nondeterministically; the other burns its clock and flags. Bob takes a rated loss for a game he was never shown. Two attendees challenging the same benchmark bot in the same second produce the same result.

**Impact.** Violates a stated invariant, produces phantom rated losses, and the underlying game — the one the bot *did* play — may itself be discarded.

**Fix.**
- Make the ticker the **only** creator of games. `POST /challenges` inserts a row into a `challenges` table (`id, from_bot, to_bot, status, created_at, expires_at`); the ticker consumes pending challenges at the top of each tick, before pairing, and removes both bots from the pool snapshot. One writer, no race, and it costs less code than the race-free two-path version.
- Add a database-level guarantee regardless: `CREATE UNIQUE INDEX one_active_game_per_bot ON ...` is awkward across two columns, so instead add `bots.current_game_id` with the game insert conditional on `WHERE current_game_id IS NULL` for **both** bots. Assert `rowcount == 2`.
- Specify challenge expiry (30s), decline, and what happens when the target is mid-game. Right now none of this exists.
- Also consider cutting challenges entirely — see O-7.

---

### C7. The K-factor tier breaks Elo's zero-sum property, is exploitable, and contradicts §13

**What is wrong.** §7: "K=32 for the first 30 games then K=16." §13: "Elo gets a property test asserting the exchange is zero-sum and symmetric." **These cannot both hold.** The property test as specified will fail against the rating rule as specified. That is an internal contradiction that will surface as an argument between the domain engineer and the reviewer in phase 1.

**Scenario (deliberate).** At 15:00, in a closed pool that has been playing since 09:30, most bots have 30+ games and K=16. Alice registers `alice-v9` fresh: K=32. Each win takes 32 points off nobody — her opponent loses 16. Every game she plays injects up to +16 points into the pool. Ten wins and she is at ~1500 while the pool's mean has risen. The ranking is now partly a function of registration time.

**Scenario (accidental).** Attendees restart bots all day. §5 says re-registering with the same name+token preserves identity — good — but anyone who loses a token or renames to `-v2` (which the spec actively encourages: "alice-v2 as a sparring partner for alice-v3") starts a fresh K=32 identity. The population of high-K bots is refreshed continuously and the pool inflates all afternoon.

**Impact.** The leaderboard — the single artefact the whole workshop is built around — drifts for structural reasons unrelated to bot strength, and the drift favours whoever re-registered most recently. Nobody will notice, which is worse.

**Fix.** For a one-day, 20-bot closed pool, use **a single K for both sides of every game**. Either:
- `K = 24` flat, drop the provisional tier entirely (simplest, one fewer concept to explain, and the property test passes); or
- if you want faster early convergence, use `K = min(K_white, K_black)` so the exchange stays symmetric, and accept slower calibration.

Delete "provisional under 10 games" as a rating rule; keep it purely as a **display** annotation on the leaderboard (`1340?`), which is what it is actually for.

---

### C8. "Benchmark bots are unrated" is ambiguous, and both readings are broken

**What is wrong.** §5 defines `benchmark` as "unrated, hidden from leaderboard, challenge-only", and §4 has `games.rated`. The spec never says whether a *competitor* gains rating from beating a *benchmark*.

- **Reading A — the game is unrated for both sides.** Then §5's claim that reference bots "provide a calibration ladder" is false in rating terms, and the cold-start argument collapses: at 09:15 with two competitors registered, the only rated games available are those two playing each other repeatedly. A two-player closed Elo pool with K=32 oscillates wildly and diverges from any meaningful scale. The first ten people to deploy get a leaderboard made of noise, and it is the first thing they see.
- **Reading B — the competitor's rating updates, the benchmark's does not.** This is a **one-sided Elo exchange, i.e. a rating pump**. And §5's structural-safety argument is exactly inverted: the spec says making benchmarks unrated "removes the leaderboard-farming exploit structurally", but reading B *is* the exploit. Register `alice-punchbag` as a benchmark that resigns immediately, challenge it in a loop, and gain K points per game with no counterparty loss. Nothing in §6/§7 rate-limits challenges or caps games per hour.

**Impact.** Reading A gives a meaningless morning leaderboard; reading B gives a trivially farmable one. The spec as written permits an implementer to choose either.

**Fix.** Split the concept — this also solves cold start properly:
- **Attendee-registered benchmark bots:** games are `rated=0` for **both** sides, always. Purely sparring. State it in one sentence.
- **Server reference bots (`ref-*`): make them rating *anchors*.** Give them fixed, hand-calibrated ratings (e.g. `ref-random` 800, `ref-greedy` 1100, `ref-depth3` 1450) that **never update**, and make them auto-pairable, not challenge-only. Games against them are rated for the competitor side only. The one-sided exchange is safe here because the anchor rating is correct and fixed: Elo self-limits farming, since a 1450-rated bot beating an 800-rated anchor gains under 1 point. This anchors the whole scale to a known reference, fixes cold start at 09:00 (there are always three opponents), and gives the calibration ladder §5 wants.
- Note and accept the tradeoff: anchors inject and remove points from the pool. For a one-day closed pool that is a feature — it prevents the drift you would otherwise get from a fully closed system.

---

### C9. 3+2 is not a viable time control for the LLM-agent bots the spec explicitly invites

**What is wrong.** §2 makes two commitments that conflict: "an LLM-agent bot is equally valid" and "3+2 blitz… increment keeps slow-but-sound bots (and agent bots) viable."

**Scenario.** An LLM-agent bot's per-move latency is one model round-trip: realistically 3–20 seconds, and the MCP path adds a hop. With a 180 s budget and a 2 s increment, the agent bot's effective budget after *n* moves is `180 − n·(latency − 2)`. At 6 s per move it flags on move ~45; at 12 s per move it flags on move ~18 — i.e. in the opening. Meanwhile a conventional alpha-beta bot moves in 30 ms and effectively never touches its clock.

**Impact.** The attendee who does the most interesting, most on-theme thing — building an *agentic* chess bot at an *agentic AI workshop* — gets a bot pinned to the bottom of the leaderboard by structural flag-fall, and the `termination` column will honestly report `flag` for every one of its games. The pool is also bimodal in a way that distorts everything else: millisecond bots and multi-second bots share one rating scale.

There is a second-order effect worth noting. If most bots are fast, games complete in **seconds**, not minutes — 20 bots × ~10 s per game means a game finishes somewhere on the server roughly every half-second. The 1 s ticker becomes the pairing bottleneck, the SSE event rate becomes high enough to make the projector's "featured game" flicker unwatchably, and `moves` grows by hundreds of thousands of rows. The spec's implicit mental model (games take minutes) is wrong in both directions at once.

**Fix.** Pick one, and state the reasoning in §2:
- **Simplest:** raise the increment substantially — e.g. **60+5** or a per-move budget of 10 s with a generous total. Fast bots are unaffected; agent bots become viable. Blitz drama is a *dashboard pacing* problem, not a time-control problem.
- Or run **two divisions** (`fast` 3+2 and `agent` 120+15) with separate leaderboards. More honest, more code, more explaining.
- Independently, add a **minimum game duration for display purposes** or a dashboard "featured game" that holds a game on screen for ≥20 s regardless of churn, so the big screen is watchable.

---

### C10. MCP caller identity is undefined — the single most likely 2pm failure

**What is wrong.** §9 says the MCP server "is a client of the same HTTP API" and "holds no privileged path to the database". §2 promises "one-line attendee setup, no local install". But `get_my_bot()`, `make_move()`, `take_control()` and `challenge()` are all per-bot operations, and the spec never says how a `/mcp` request is bound to a bot token.

**Scenario.** Twenty attendees point Claude at the same `http://<host>:8000/mcp`. Streamable HTTP is a shared endpoint. Either (a) the token is configured as a header in each attendee's `.mcp.json` — which is not one line, must be re-edited after `register_bot` returns a token, and is the step that will go wrong on twenty laptops simultaneously; or (b) there is a per-session mapping keyed on `Mcp-Session-Id`, which is not in the spec and is stateful in a way §9 implicitly denies; or (c) it is unauthenticated, in which case **any attendee's Claude can resign on any other bot's behalf** — the precise attack §5 says the token exists to prevent.

**Impact.** This is on the critical path for the workshop's headline moment and there is no design for it. It is also a live security hole under reading (c).

**Fix.**
- Specify the mechanism concretely: `Authorization: Bearer <token>` as a header in `.mcp.json`, with `register_bot` returning setup instructions **as prose in the tool result** telling the attendee exactly which file to edit and what to paste. Make this a documented two-step flow rather than pretending it is one step.
- The MCP server must forward that header to the REST API and never hold a default/admin token.
- **`register_bot` returning a token via MCP puts the token into the Claude transcript**, which contradicts `AGENTS.md`'s "never logged" invariant in spirit. Either accept and document it, or have `register_bot` write the token to a local file via the SDK and return only a confirmation.
- Add CORS and `Access-Control-Expose-Headers: Mcp-Session-Id` to the deployment notes; Streamable HTTP fails opaquely without them.

---

## Significant concerns

### S1. Duplicate concurrent long-polls per bot; client/server timeout skew unspecified

§6 says the poll "holds up to 20s" and says nothing about client timeout. If the SDK uses a 20 s HTTP timeout against a 20 s server hold, they race: the client aborts and reissues while the server still holds a waiter. Two waiters now exist for one bot. Both wake on the same event, both return the same turn payload; if the SDK's poll loop is not strictly serial, `choose_move` runs twice and two POSTs are sent for the same ply — one 200, one 409.

Fix: (a) server hold 20 s, client timeout **30 s**, stated in both §6 and the SDK; (b) the server keeps **one waiter per bot** — a new poll cancels and immediately returns the older one with `{"superseded": true}`; (c) the SDK's poll loop is strictly serial by construction.

### S2. "CAS retry" in the SDK is underspecified and is an accidental hot loop

§3 lists "CAS retry" as an SDK responsibility. Retrying *what*? A 409 means the position moved on — usually because the opponent moved, meaning it is no longer this bot's turn. Retrying the same move is always wrong; retrying `choose_move` against a stale board is also wrong. A naive `while True: submit()` on 409 is a tight loop against the server.

Fix: define 409 handling as "discard the move, return to polling". And make the **409 body carry `{ply, fen, status}`** so the client can distinguish "my move already landed" (network retry after a partition) from "opponent moved" from "game over". Without that field the client genuinely cannot tell, and this is a two-line change with a large diagnostic payoff.

### S3. Control handoff has a race that defeats its own justification

§6 says MCP `make_move` "is refused while the SDK client is actively polling". "Actively polling" is not a well-defined predicate, and the handoff is not atomic:

```
t=0.0  agent:  take_control()  -> UPDATE bots SET controller='agent'
t=0.0  server: alice's long-poll is already held and a move event fires
t=0.1  server: poll returns the turn to the SDK (controller read was 'client' at delivery time)
t=0.4  SDK:    POST /moves ply=12  -> 200 OK
t=8.0  agent:  make_move(...)      -> 409
```
The attendee's Claude gets a 409 in the exact moment the mechanism exists to prevent, and the board moved without them.

Fix: `take_control` must (1) set the controller and (2) **wake and terminate every in-flight poll for that bot** with a `controller_changed` response, atomically under the C2 lock. The move endpoint must additionally reject submissions whose source does not match `controller`, with prose. Also add auto-release: if the controller is `agent` and no move arrives within ~30 s, release back to `client` — otherwise an attendee who wanders off flags their bot.

Finally, `controller` lives on `bots` in §4 but the handoff is meaningful per-game. Decide which, and say what happens to `controller` when a game ends.

### S4. SSE has no backpressure, no resume, and no reconnect reconciliation

§11 says "Live updates via SSE" and nothing else. With 20+ clients (every attendee will open the dashboard) fanning out from the ticker:
- A slow or suspended client's queue grows unbounded, or blocks the emitter.
- No `id:` field means no `Last-Event-ID` resume; a laptop lid closed for 30 s misses every event and the UI silently diverges from reality for the rest of the day.
- No heartbeat means proxies and browsers close idle streams.

Fix: bounded per-client queue (size ~100, **drop-oldest**), a `: keepalive` comment every 15 s, monotonic `id:` on every event, and a `GET /state` snapshot endpoint that the dashboard fetches on connect and on any reconnect. The dashboard should treat SSE as a *hint to refresh*, not as the source of truth — that is both more robust and simpler to explain.

### S5. No health, no admin surface, no reset — the operator has nothing

For an unattended 2pm run the spec provides no way to answer "is it working?" or to intervene. Missing, in rough priority order:
- `GET /health` (see C1).
- **Void/abort a specific game** — for the stuck game that will happen.
- **Pause/resume matchmaking** — so the organiser can stop new games while talking over a quiet room, and during a restart.
- **Reset the day** — wipe games and ratings, keep bots. You will want this after the morning's shakedown.
- **Remove or rename a bot**, and **re-issue a lost token** (see M2).
- A structured server log of every game start/end with the reason.

These should be a small admin router behind a single `ADMIN_TOKEN` env var. This is maybe 60 lines and it is the difference between a recoverable incident and a dead workshop.

### S6. No rate limiting; one buggy attendee client can saturate the server

An attendee's bot crashes in `choose_move`, and their retry loop has no backoff — a very likely outcome of "Claude wrote my bot at 14:30". A tight `while True` poll or a 409 hot loop (S2) from one client competes with the ticker for the event loop and the write lock.

Fix: a cheap per-token token-bucket (e.g. 20 requests / 10 s) returning `429` with `Retry-After` and actionable prose; mandatory exponential backoff in the SDK. Both are small and both are teaching material.

### S7. Illegal-move strike counters are not in the data model, and their clock cost is unspecified

§8's three-strike rule is load-bearing (the spec argues for it well), but `games` has no strike columns, so strikes live in memory and reset on restart. Worse: the spec does not say whether a rejected illegal move **consumes clock**. If it does not, a bot can retry illegal moves for free between strikes; if it does, that must be explicit because it interacts with `thinking_ms` and increment ordering (C3.5).

Fix: add `white_strikes`, `black_strikes` to `games`. State that a rejected submission **does** consume elapsed time but does **not** apply increment and does **not** advance `ply`. Add a fake-bot-harness test for "two illegal then one legal move" — the case where an off-by-one strike counter forfeits a valid game.

### S8. The rating window is decorative and same-owner avoidance is unverifiable

§7: window starts at ±100 and widens 100 per tick. At a 1 s tick, the window is ±400 after three seconds and ±1000 after nine — wider than the entire spread of a 20-bot pool. Effectively the window never binds, so it is complexity with no behavioural effect (see O-3).

Separately, `owner` is free text supplied at registration with no verification, so "same-owner pairs avoided" is advisory at best, and it is explicitly waived when the pool is under 4 — which is exactly the state at 09:15 and at 16:30, i.e. exactly when self-pairing is most attractive and most damaging.

Fix: either widen per 10 s (so it binds), or delete it. For same-owner: make same-owner games **`rated=0`** rather than merely avoided, and cap **one `competitor` role per owner** (the sparring use case is served by `benchmark` and by the local arena). Structural, not policed — the same reasoning §5 already applies elsewhere.

### S9. `thinking_ms` conflates think time with network time, and the long-poll claim is overstated

§2 claims long-polling "avoids charging bots for network latency". It removes the *inbound* leg only; the submission leg is still charged, as it must be. On conference wifi that is 20–200 ms per move — up to 8 s across a 40-move game, i.e. 4% of the budget, and unevenly distributed across attendees depending on where they sit.

This is normal and acceptable, but (a) the spec should not claim more than it delivers, and (b) `thinking_ms` recorded in `moves` should be documented as *server-observed* time, because `benchmarking-a-bot` (§12) teaches attendees to read it and they will compare it against local arena timings that exclude the network.

### S10. Two-player and small-pool Elo behaviour is pathological and unaddressed

Between 09:00 and roughly 10:00 the competitor pool will be 2–5 bots. §7 permits immediate rematches when the pool is under 4. Two bots trading K=32 games every 15 seconds produces a rating that oscillates by hundreds of points within minutes and is on the projector the whole time. C8's rating anchors mitigate this substantially; a games-per-pair cap (e.g. no more than 3 rated games against the same opponent per hour) closes it, and also blunts farming generally.

### S11. Adjudication and the 150-move rule need custom code with no stated semantics

§8's "150 moves → adjudicated on material; draw if within a pawn" is not something `python-chess` provides. Undefined: is it 150 moves or 150 ply? What material values? What is "within a pawn" — is a rook-for-bishop-and-pawn imbalance a draw? Who is credited with the win in `rating_history`? This is a bespoke rule in `chess_core`, the one module `AGENTS.md` says must be under strict TDD, with no acceptance criteria written down. At 3+2 it is also nearly unreachable (see O-5).

### S12. `analyze_game`'s "eval swing" implies an engine dependency that is nowhere in the spec

§9 promises "PGN with per-move timing and **eval swing**". Eval requires an evaluator. Either you bundle Stockfish — a binary dependency, a Docker layer, a per-move analysis cost across hundreds of games, and a startup failure mode on workshop day — or you use a home-grown material evaluator, in which case "eval swing" mostly reports captures and the diagnostic value largely evaporates. §9 calls `analyze_game` "the workshop's central moment", so this is not a detail to leave implicit.

---

## Minor issues and gaps

- **M1. `moves` and `rating_history` have no declared keys.** See C2. Also no index on `games(status)`, which the ticker scans every second.
- **M2. No token recovery.** §5 ties identity to name+token with no re-issue path. An attendee who closes their terminal, or whose Claude rewrites the config file, loses their rating and must re-register — which then feeds C7's inflation. Add an admin re-issue endpoint.
- **M3. `last_seen` semantics are undefined.** Updated on every request, or only on poll? Pool eligibility (§7) depends on it, so it must be poll-specific.
- **M4. No `challenges` table, no accept/decline/expire.** See C6.
- **M5. `GET /events` is "dashboard only" but has no auth and no stated payload schema.** Confirm explicitly that no event ever carries a token, an `owner` email, or a bot's internal id in a way that enables impersonation.
- **M6. Colour alternation vs. "no immediate rematch" is unspecified when they conflict.** If a bot's last game was White and the only legal pairing gives it White again, what wins? Pick one and put it in the matchmaker's docstring — it is one of the pure-function behaviours the seeded tests should pin.
- **M7. No pagination or bounds on `get_leaderboard` / `analyze_game`.** Fine at 20 bots; state the assumption so nobody adds it speculatively.
- **M8. No deployment guidance for long-poll + SSE behind a proxy.** nginx `proxy_buffering off`, `proxy_read_timeout ≥ 60s`; Cloudflare's 100 s cap. If wifi forces a hosted deployment (§2 anticipates this), both transports break silently without it.
- **M9. `games.rated` exists but §7 never says what sets it.** Make the rules explicit: benchmark participant → 0; same owner → 0; aborted/no-show/server-restart → 0; else 1.
- **M10. Resignation is exposed but draw offers are not.** Correct call, but a bot in a dead-drawn king-and-pawn ending will grind to the 150-move rule or flag. Interacts with S11.
- **M11. No clock display semantics for the dashboard.** Clocks tick client-side between SSE events or they look frozen; that needs the last-known values plus `turn_started_at` in the event payload.
- **M12. The spec never states the wire format for a "no game right now" poll response.** `204`? `200` with `{"game_id": null}`? Twenty attendees' Claude-written clients will each guess differently. Nail it down — this is the highest-traffic response in the system.

---

## Over-engineering — cut this

- **O-1. Postgres-capable via env (§2).** Zero workshop value, and it forces the store layer into dialect-agnostic abstraction — more code, more indirection, worse on a projector. Keep SQLite. It is already in §15 as deferred; delete it from §2.
- **O-2. `analyze_game` eval swing (§9, §12).** See S12. Cut to PGN + per-move timing + explicit flag/illegal/strike markers. That alone answers "why did my bot lose?" for the overwhelming majority of real losses, which are timeouts and blunder-by-shallow-search, not subtle eval drift.
- **O-3. Rating-window widening (§7).** Non-binding within three seconds (S8). Replace with: pair by fewest-games-played, then nearest rating. Two lines, same behaviour, no parameters.
- **O-4. Provisional / two-tier K (§7).** Actively harmful (C7). Cut the rating rule; keep provisional as a leaderboard annotation.
- **O-5. The 150-move material adjudication (§8).** At 3+2 with real bots, essentially unreachable — someone flags first. It is bespoke, untested, semantically undefined (S11), and it is dead code you will nonetheless have to explain. Replace with a hard cap: at 200 ply, `termination='adjudicated'`, result **draw**, unconditionally. One line, no material heuristic, no arguments.
- **O-6. Two dashboard modes with a toggle (§11).** "Big Screen" is what the room needs; "My Bot" is the leaderboard plus a sparkline, which the leaderboard page can carry inline. Ship one page with an optional `?bot=alice` highlight. Cuts a mode, a toggle, and a layout.
- **O-7. The `challenge` + attendee-`benchmark` subsystem (§5, §6, §7).** This is the largest single complexity reduction on the table. It adds a second game-creation path (C6), a table, at least four endpoints, an MCP tool, a role, a rated-ness rule, and a race — all to serve "keep alice-v2 as a sparring partner", which the **local arena already does better**: offline, instant, no rating risk, seeded, 100 games in seconds. §10 makes exactly this argument for the arena. Cut attendee challenges and the attendee `benchmark` role; keep `ref-*` as auto-pairable rating anchors per C8. If challenges survive at all, defer them to after phase 6.
- **O-8. Six build-time agents, four attendee skills, `/improve-bot`, and `eval-tuner` (§12).** The roster is teaching material and the reasoning about skills-vs-subagents is genuinely good, but this is a large amount of prose to author alongside a server that does not exist yet. §14's phasing already places it last, which is right; make it explicit that `eval-tuner` and `/improve-bot` are **stretch** and that `chess-engine-techniques` is the one skill that must exist, since it is what unblocks a non-chess-player at 13:00.

---

## What the design gets right

Short, and only where earned:

- **Client-side bot execution.** This single decision removes sandboxing, resource limits, and the entire untrusted-code threat model. Everything else in the design is affordable because of it.
- **Shared `chess_core` between the arena and the server, with enforced purity.** The stated reason — that a simplified local harness would let attendees tune against subtly different rules — is the correct reason, and passing time in rather than reading the clock is what makes the clock and Elo testable at all.
- **ply-CAS as the anti-double-move primitive.** The right shape. It needs the database unique index and extension to terminal transitions (C2), but the core idea is sound and it makes client retries safe, which matters more than it looks.
- **Clock-as-disconnect-detector.** Deleting a separate disconnect rule in favour of the clock is genuinely good design: one mechanism, and it matches how chess already works. The bug is in *when the clock starts* (C3), not in the idea.
- **Mandatory seeded opening randomisation in the arena (§10).** A non-obvious trap, correctly identified, with the correct reasoning about statistical illusion. Most specs get this wrong.
- **The `termination` taxonomy.** Separating flag from checkmate from illegal-move-forfeit is what lets an attendee self-diagnose without the organiser walking over. Extend it with `no_show`, `server_restart`, and `aborted` per C3/C4.
- **Phasing where phase 2 is playable with no server at all (§14).** A real, demonstrable slice and a genuine safe stopping point.

---

## Prioritised recommendations

Ordered. Items 1–7 should be resolved in the spec before any code is written; 8–12 before phase 3 ships.

1. **Write the concurrency contract.** One process-wide `asyncio.Lock` for all game mutation; every terminal transition a conditional UPDATE with a `rowcount` assertion; `PRIMARY KEY (game_id, ply)` on `moves` and `UNIQUE (game_id, bot_id)` on `rating_history`. Extend the `AGENTS.md` CAS invariant from "move submission" to "every game-state transition". *(C2)*
2. **Write the clock contract.** Clock starts on delivery, not on pairing; monotonic time; `pending` state with a 10 s join deadline and a `no_show` void; pool eligibility tightened from 60 s to "currently polling"; explicit deduct → flag-check → increment → switch ordering. *(C3)*
3. **Define restart recovery.** Recommend: abort all in-flight games as `server_restart`, unrated, on startup. Four lines, zero rating damage, and it makes restarting during the workshop a safe operator action. *(C4)*
4. **Make the ticker unkillable and observable.** try/except around the tick body, `last_tick_at`, `GET /health`, a red dashboard banner on stale ticks, and a supervisor that restarts a dead task. *(C1)*
5. **Fix the rating model.** Single symmetric K (24, flat). Delete the provisional K tier. Make `ref-*` bots fixed-rating, auto-pairable anchors; attendee benchmark games unrated on both sides; same-owner games `rated=0`; one competitor per owner; a per-pair rated-games cap. *(C7, C8, S8, S10)*
6. **Resolve the time control against the agent-bot goal.** Recommend 60+5, or two divisions. Either way, stop claiming 3+2 keeps agent bots viable — it does not. Also decide the dashboard's featured-game hold time, because fast bots will otherwise make the big screen unwatchable. *(C9)*
7. **Cut scope: O-1, O-2, O-3, O-4, O-5, O-6, O-7.** In particular, cut attendee challenges and the attendee `benchmark` role — that removes a table, four endpoints, an MCP tool, a rated-ness rule, and race C6 outright, and the local arena already serves the use case better. Mark the phase-7 Claude layer's `eval-tuner` and `/improve-bot` as stretch.
8. **Specify the SQLite/async execution model.** WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`; single writer under the C2 lock; waiters on `asyncio.Event`, never on threads. *(C5)*
9. **Specify MCP identity end to end.** Bearer token in `.mcp.json`, forwarded verbatim, no default token, documented as a two-step flow. Decide and document what `register_bot` does with the token given that MCP results land in the transcript. Add CORS and the `Mcp-Session-Id` expose header. *(C10)*
10. **Nail down the protocol edges.** No-game poll response format; server 20 s hold vs client 30 s timeout; one waiter per bot with supersede; 409 body carrying `{ply, fen, status}`; 409 handling defined as "discard and re-poll"; per-token rate limit with `429` and prose. *(S1, S2, S6, M12)*
11. **Make the control handoff atomic.** `take_control` wakes and terminates in-flight polls; the move endpoint enforces controller match; auto-release after ~30 s of agent inactivity. Decide whether `controller` is per-bot or per-game. *(S3)*
12. **Harden SSE and add the admin surface.** Bounded drop-oldest queues, 15 s heartbeat, event `id:` with `Last-Event-ID`, `GET /state` snapshot as source of truth; admin router behind `ADMIN_TOKEN` with abort-game, pause-matchmaking, reset-day, re-issue-token. *(S4, S5, M2)*

Two further items worth carrying into the plan even though they are not spec defects: persist illegal-move strike counters in `games` and define whether rejected moves burn clock *(S7)*; and pin down the 150-move rule's semantics or replace it with the flat 200-ply draw *(S11, O-5)*.
