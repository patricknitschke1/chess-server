# AGENTS.md

## The goal

Chess Arena is a competition server for an **agentic AI workshop** (~20 attendees). Attendees write chess bots with Claude's help, connect them to this server, and watch their bots climb a live ELO leaderboard.

Two things must be true of this repository, and every change should serve both:

1. **The server is finished infrastructure.** Attendees consume it — they never debug it. Anything an attendee hits at 2pm on workshop day must fail with a message that tells them what to do next.
2. **The repository is a worked example of agentic development.** How this code was built is teaching material: spec first, TDD on pure logic, skills and subagents used where they genuinely pay off. The git history gets shown to attendees.

When a decision trades cleverness against clarity, choose clarity. This code gets read on a projector.

## Current state

Design is complete and has been through two rounds of adversarial systems-design review (`agent-reports/`). The spec is the source of truth:
[docs/superpowers/specs/2026-08-23-chess-arena-design.md](docs/superpowers/specs/2026-08-23-chess-arena-design.md)

Module boundaries — `chess_core` signatures, SSE events, bot/SDK surface, HTTP models, MCP tools, test layout — are pinned in
[docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md](docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md). Bind to it rather than inventing a signature.

§4 (concurrency), §6 (clock and delivery) and §7.1 (restart recovery) are normative and may not be relaxed for convenience. Build order is §20. Commands below describe the intended layout and will become real as phases land — do not assume a command works until the phase that creates it is done.

## Architecture

```
chess_core/      Pure logic. No I/O, no clock reads, no network.
                 Shared by BOTH the server and the local arena.
chess_server/    store/ engine/ api/ mcp/ — SQLite, one background ticker, FastAPI, MCP
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
- **`arena_reports` is display-only.** Local arena results are self-reported and unverifiable. No rating, matchmaking, leaderboard, seat, or game-finalisation code may read that table.
- **`chess_core` stays pure.** If you need the time, pass it in. This is what keeps ELO and matchmaking testable.
- **Errors aimed at attendees are actionable prose**, not bare status codes. `"No bot registered for this token. Call register_bot first."`
- **The local arena randomises openings.** Two deterministic bots otherwise replay one identical game, and "100 games" becomes a statistical illusion.

## Conventions

- Python, `python-chess` for rules. Do not hand-roll move generation.
- Small, focused files. A growing file usually means tangled responsibilities.
- Test failure paths first — illegal moves, flag-fall, disconnects, CAS conflicts. Those are what break live.
- `chess_core` gets straight unit tests, no fixtures, no mocks.

## Skills and agents

Build-time agents live in `.claude/agents/`; attendee-facing skills ship in `starter-kit/.claude/`. See §12 of the spec for the roster and who owns what.

The split is deliberate and is itself workshop content:
**subagents isolate noisy work; skills inject knowledge into work you are already doing.** If a tool's output is small enough to read inline, it does not need a subagent — fix the tool's output instead.

Extract a new skill when a pattern has recurred, not in anticipation of it.

Subagent reports go in `agent-reports/` — see [agent-reports/README.md](agent-reports/README.md) for naming and conventions.

## How this project is built

The goal is to **distil the specification according to each agent's responsibility**. From the two design documents we fan out subagents to write one role spec per track, each scoped to what that role must build.

The orchestrator acts as a **senior systems-design manager**: it does not write the role specs itself, but it is accountable for the whole. Its job is to ensure no functionality is lost in the split, that each role spec is complete enough for its owner to build from without guessing, and that the seams between roles still meet.

Sequence:

1. **Design spec** — what and why. Reviewed adversarially until findings stop being structural.
2. **Interfaces** — the seams pinned precisely, so tracks cannot invent conflicting APIs.
3. **Role specs** — one per agent in `docs/superpowers/specs/roles/`, distilled from the two documents above. This is the prerequisite for any implementation plan.
4. **Implementation plans** — written per track, against the role spec.
5. **Build** — each track owned by exactly one agent.

**The full chess arena must be verifiable through the specs.** If a behaviour exists only in someone's head, it does not exist. A role spec that omits something means that thing does not get built.

The orchestrator's checks at step 3: every §-level requirement in the design spec is claimed by exactly one role; every interface in the interfaces document has both a producer and a consumer; and no role spec contradicts another at a seam.

## Working here

- Read the spec section for the area you are touching before changing it.
- Changes that contradict the spec need the spec updated in the same change.
- Never commit tokens, `.env`, or `*.db`.
