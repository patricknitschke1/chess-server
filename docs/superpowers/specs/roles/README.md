# Role Specs — Orchestrator Coverage Record

**Date:** 2026-08-24
**Commit at distillation:** `22db18c` (+ subsequent orchestrator fixes)

Six role specs distilled from [the design spec](../2026-08-23-chess-arena-design.md) and [the interfaces document](../2026-08-23-chess-arena-interfaces.md), one per build track. Each is written to be built from without re-reading the design spec, and none may contradict it. Where they restate, they cite §.

| Role spec | Lines | Owns |
|---|---|---|
| `chess-domain-engineer-spec.md` | 512 | `chess_core/` |
| `server-engineer-spec.md` | ~1260 | `store/`, `engine/`, `api/` |
| `client-engineer-spec.md` | 972 | SDK, `bot.py`, `arena.py` |
| `mcp-engineer-spec.md` | 987 | `mcp/` |
| `dashboard-engineer-spec.md` | 1201 | `web/` |
| `workshop-author-spec.md` | 561 | skills, docs, `AGENTS.md` |

## Coverage check

Every §-level requirement in the design spec is claimed by exactly one role, except where a section has a deliberate producer/consumer split:

| § | Producer | Consumer |
|---|---|---|
| §6 clock | `chess-domain-engineer` (arithmetic) | `server-engineer` (lifecycle) |
| §8 protocol | `server-engineer` | `client-engineer`, `mcp-engineer` |
| §9 matchmaking | `chess-domain-engineer` (§9.2/9.3 policy) | `server-engineer` (§9.1 eligibility) |
| §10 rating | `chess-domain-engineer` (math), `server-engineer` (application) | `dashboard-engineer` (presentation) |
| §11 time control | `server-engineer` (per-game columns) | `client-engineer`, `mcp-engineer`, `workshop-author` |
| §13 MCP | `mcp-engineer` | `workshop-author` (documents it) |
| §14 SSE | `server-engineer` (emits) | `dashboard-engineer` (consumes) |
| §17 arena | `client-engineer` (builds) | `workshop-author` (documents) |
| §4.6 health | `server-engineer` (endpoint) | `dashboard-engineer` (banner) |

§1–§3 and §20–§21 are framing and phasing; they belong to the orchestrator, not to a track.

## Gaps found and closed

**§10.4 "one competitor per owner" was claimed by no role.** It appeared in the schema as a `role` column and in the `rated` rules, but the registration-time enforcement — the thing that actually closes the farming vector — was dropped in the split. Assigned to `server-engineer` with the transaction boundary and the error prose specified.

This is precisely the failure mode the coverage check exists to catch: a rule that every role assumed someone else owned.

## Unmet seams resolved

`dashboard-engineer` requested two things from `server-engineer` that did not exist. Both approved and added to the interfaces document:

1. `white_rating` / `black_rating` on `ActiveGameSummary` — featured-game selection ranks by participant rating sum, and without these the dashboard joins against the leaderboard every tick. `turn_elapsed_ms` added at the same time for local clock ticking.
2. `GET /bots/{bot_id}/rating_history` — the My Bot sparkline cannot be reconstructed from `rating_changed` events by a client that loads mid-workshop, since there is no event backlog.

## Cross-role decision consistency

Checked for contradictions where several roles resolved the same open question independently:

- **Featured-game selection** — `server-engineer` and `dashboard-engineer` independently proposed highest participant-rating sum, ≥20s hold, tie-break on lowest `game_id`. Agree.
- **`analyze_game` format** — `server-engineer` and `mcp-engineer` both propose Markdown in three sections (PGN, timing table, event log). Agree.
- **Provisional annotation** — computed field on `games_played < 10`, consistent across `server-engineer`, `dashboard-engineer` and `workshop-author`.
- **Exhibition time control** — consistently treated as unrated per §5.1 rule 4 by all four roles that touch it.

No contradictions found at any seam.

## Open decisions carried forward

None block implementation. Each is owned by exactly one role:

| Decision | Owner | Recommendation |
|---|---|---|
| Opening book composition | `client-engineer` | Mainline openings only, no eval bias |
| Baseline bot strength | `client-engineer` | Depth 2, material only — must not flag at 3+2 |
| `choose_move` raising | `client-engineer` | Resign and log the traceback; it is a bug, not a transient |
| SSE coalescing mechanism | `server-engineer` | Per-game 500ms throttle for non-featured moves |
| Featured-game policy | `dashboard-engineer` | As above; confirmed by two roles |
| Arena table format | `workshop-author` | Document generically until the CLI exists |
| Stretch artefacts | `workshop-author` | `eval-tuner` and `/improve-bot` deferred to build time |
