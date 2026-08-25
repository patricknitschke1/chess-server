# AGENTS.md

## The goal

Chess Arena is a competition server for an **agentic AI workshop** (~20 attendees). Attendees write chess bots with Claude's help, connect them to this server, and watch their bots climb a live ELO leaderboard.

Two things must be true of this repository, and every change should serve both:

1. **The server is finished infrastructure.** Attendees consume it — they never debug it. Anything an attendee hits at 2pm on workshop day must fail with a message that tells them what to do next.
2. **The repository is a worked example of agentic development.** How this code was built is teaching material: spec first, TDD on pure logic, skills and subagents used where they genuinely pay off. The git history gets shown to attendees.

When a decision trades cleverness against clarity, choose clarity. This code gets read on a projector.

## Current state

`chess_core/`, `starter-kit/` and `chess_server/` (store, engine, and most of the API) are built and green.

**The scope was cut for cost.** MCP (§13, including control handoff), challenges (§12), benchmark bots (§10.4), exhibition time control (§11), My Bot dashboard mode (§14) and most of the admin surface (§15) are **gone**. Cut sections are tombstoned in the design spec rather than deleted — renumbering would break every `§x.y` reference. **Do not build anything marked CUT.** §21 has the full table.

Bot strength and anchor calibration are **deferred** — also §21. Open decisions taken without review are in [docs/open-questions.md](docs/open-questions.md).

The spec is the source of truth:
[docs/superpowers/specs/2026-08-23-chess-arena-design.md](docs/superpowers/specs/2026-08-23-chess-arena-design.md)

Module boundaries — `chess_core` signatures, SSE events, bot/SDK surface, HTTP models, test layout — are pinned in
[docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md](docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md). Bind to it rather than inventing a signature.

§4 (concurrency), §6 (clock and delivery) and §7.1 (restart recovery) are normative and may not be relaxed for convenience. Build order is §20.

**Fix passes keep landing in the design spec and skipping the interfaces document.** Four rounds running. When a fix changes a seam, edit the pinned document explicitly — not by proxy.

## Architecture

```
chess_core/      Pure logic. No I/O, no clock reads, no network.
                 Shared by BOTH the server and the local arena.
chess_server/    store/ engine/ api/ — SQLite, one background ticker, FastAPI
web/             Dashboard. Plain HTML/CSS/JS, no build step.
starter-kit/     What attendees clone. bot.py is the only file they edit.
```

`chess_core` being shared is load-bearing: offline results must predict live server behaviour. Never fork the rules into a "simplified" local version.

## Non-negotiable invariants

- **No untrusted code runs on the server.** Bots are client-side. The only exception is `chess_server/engine/reference_bots.py`, which we wrote.
- **Every game-state transition is compare-and-swap on the state you are transitioning _from_** — moves, flags, finalisation, abort, reset. Assert `rowcount == 1`; on 0, roll back and abandon the work. This is what makes double-finalisation impossible.
- **All game mutation happens under the single `store.write_lock`, and a critical section is a transaction.** `BEGIN IMMEDIATE` on acquire, exactly one commit or rollback before release, shielded from cancellation.
- **`write_lock` is acquired at exactly one place per call stack.** Every mutating helper has an inner `*_locked` form that assumes the lock and never acquires it; the ticker calls only those. `asyncio.Lock` is not re-entrant — a nested acquire wedges the coroutine silently, raising nothing.
- **No SSE event is visible before the transaction that produced it commits.** Buffer inside the critical section, flush after commit, discard on rollback.
- **Attendee-controlled strings are validated server-side and rendered with `textContent`.** Bot names, owners and arena labels reach a projector; `innerHTML` never touches them.
- **One non-terminal game per bot is enforced by the `seats` table**, not by application logic and not by partial indexes.
- **Every route handler is `async def`.** Only `sqlite3` calls enter a thread — a sync handler can deadlock against the writer.
- **Elapsed time is `time.monotonic_ns()`, never wall clock.** A laptop suspend must not flag the board.
- **A clock starts on delivery, not on pairing**, and delivery is idempotent — re-reading a position never restarts the clock.
- **Bot tokens are stored hashed and never logged.** Not in errors, not in debug output, not in SSE payloads.
- **`chess_core` stays pure.** If you need the time, pass it in. This is what keeps ELO and matchmaking testable.
- **Errors aimed at attendees are actionable prose**, not bare status codes. `"No bot registered for this token. Call register_bot first."`
- **The local arena randomises openings.** Two deterministic bots otherwise replay one identical game, and "100 games" becomes a statistical illusion.

## Conventions

- Python, `python-chess` for rules. Do not hand-roll move generation.
- Small, focused files. A growing file usually means tangled responsibilities.
- Test failure paths first — illegal moves, flag-fall, disconnects, CAS conflicts. Those are what break live.
- **Watch each test fail before implementing.** That is ordinary TDD and costs nothing. **Mutation testing is reserved for silent failures** — the clock, CAS, lock discipline, SSE ordering — where wrong code still returns a plausible answer. Elsewhere a passing suite is good enough. This is a deliberate reduction for cost; the full discipline found eleven real defects, so expect a few to slip through.
- `chess_core` gets straight unit tests, no fixtures, no mocks.

## Local gotchas

Verified the hard way; each has cost real time here.

- **Always `.venv/bin/pytest` and `.venv/bin/python`.** Never bare `pytest`/`python`.
- **Never pipe a long test run through `tail`.** It buffers all output, the run looks idle, the terminal gets backgrounded as a suspected hang, and killing it dumps the whole scrollback into context.
- **Clear `__pycache__` between mutation steps**, on the rare occasions you mutate. A `cp` restore inside the same second leaves a stale `.pyc` that Python reuses, so the "restored" run silently executes the mutant.
- **Server tests use a file-backed database under `tmp_path`, never `:memory:`.** Two connections to `:memory:` are two separate databases, so the reader/writer split, WAL and `BEGIN IMMEDIATE` contention become unobservable — exactly what §4 needs the tests to exercise.
- **Verify library and SQLite claims by executing them.** Reading missed all of: `INTEGER PRIMARY KEY NOT NULL` still accepting NULL, `asyncio.CancelledError` not being an `Exception` subclass, and `ORDER BY created_at` with tied timestamps deleting the newest rows.

## Skills and agents

Build-time agents live in `.claude/agents/`; attendee-facing skills ship in `starter-kit/.claude/`. See §12 of the spec for the roster and who owns what.

The split is deliberate and is itself workshop content:
**subagents isolate noisy work; skills inject knowledge into work you are already doing.** If a tool's output is small enough to read inline, it does not need a subagent — fix the tool's output instead.

Extract a new skill when a pattern has recurred, not in anticipation of it.

Dispatching a subagent to build, fix or plan anything here goes through
[.github/skills/dispatching-build-subagents/SKILL.md](.github/skills/dispatching-build-subagents/SKILL.md) — scope fencing, the mutation rule, the environment facts, and the report format. It exists because the same preamble was being retyped into every dispatch, which is how it drifts.

Subagent reports go in `docs/agent-reports/` — see [docs/agent-reports/README.md](docs/agent-reports/README.md) for naming and conventions.

## How this project is built

The specs are written. **They are no longer under active review** — the design spec went through five adversarial rounds and the server spec through a pre-build round, and that is enough. Build from them, and when one is wrong, fix it in the same change rather than opening another review pass.

**The arena must still be verifiable through the specs.** If a behaviour exists only in someone's head, it does not exist — a role spec that omits something means that thing does not get built.

Reviews are **on request, not by default.** Ask the user rather than dispatching a reviewer.

## Working here

- Read the spec section for the area you are touching before changing it.
- Changes that contradict the spec need the spec updated in the same change.
- Never commit tokens, `.env`, or `*.db`.
