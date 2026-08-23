# AGENTS.md

## The goal

Chess Arena is a competition server for an **agentic AI workshop** (~20 attendees). Attendees write chess bots with Claude's help, connect them to this server, and watch their bots climb a live ELO leaderboard.

Two things must be true of this repository, and every change should serve both:

1. **The server is finished infrastructure.** Attendees consume it — they never debug it. Anything an attendee hits at 2pm on workshop day must fail with a message that tells them what to do next.
2. **The repository is a worked example of agentic development.** How this code was built is teaching material: spec first, TDD on pure logic, skills and subagents used where they genuinely pay off. The git history gets shown to attendees.

When a decision trades cleverness against clarity, choose clarity. This code gets read on a projector.

## Current state

Design is complete and approved; implementation has not started. The spec is the source of truth:
[docs/superpowers/specs/2026-08-23-chess-arena-design.md](docs/superpowers/specs/2026-08-23-chess-arena-design.md)

Build order is §14 of the spec. Commands below describe the intended layout and will become real as phases land — do not assume a command works until the phase that creates it is done.

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
- **Every move submission carries its `ply`** and is applied compare-and-swap. Mismatch returns `409`. This is what makes double-moves impossible.
- **Bot tokens are stored hashed and never logged.** Not in errors, not in debug output.
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

## Working here

- Read the spec section for the area you are touching before changing it.
- Changes that contradict the spec need the spec updated in the same change.
- Never commit tokens, `.env`, or `*.db`.
