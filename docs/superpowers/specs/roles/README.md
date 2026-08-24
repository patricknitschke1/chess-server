# Role Specs — Orchestrator Coverage Record

**Date:** 2026-08-24 (Harmonization Revision 4)
**Commit at harmonization:** `0167b91`

Six role specs distilled from [the design spec](../2026-08-23-chess-arena-design.md) and [the interfaces document](../2026-08-23-chess-arena-interfaces.md), one per build track. **All decisions resolved, all seams met.** Each spec is written to be built from without re-reading the design spec.

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
| §13 MCP | `mcp-engineer` (tool surface) | `workshop-author` (documents it) |
| §13.3 control handoff | `server-engineer` (`POST /bots/me/control` and the routes behind it) | `mcp-engineer` (tools), `client-engineer` (SDK idling) |
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

## All Decisions Resolved (Harmonization Revision 4)

All interface-affecting decisions have been resolved and applied to the affected role specs and interfaces document:

| Decision | Resolution | Owner |
|---|---|---|
| `controller` field schema | `TEXT NOT NULL DEFAULT 'client'` in `bots` table | `server-engineer` |
| SSE coalescing | Per-game 500ms throttle for non-featured moves | `server-engineer` |
| Illegal move strikes | Per-game columns in `games` table (already in schema) | `server-engineer` |
| Featured game policy | Highest rating sum, ≥20s hold, tie-break on `game_id` | `dashboard-engineer` |
| `analyze_game` format | Markdown: PGN + timing table + event log | `mcp-engineer` |
| Provisional annotation | Computed field `games_played < 10` | `server-engineer` / `dashboard-engineer` |
| Local arena reporting | Opt-in `arena.py --report` POSTs to server; display-only table | `server-engineer` / `client-engineer` / `dashboard-engineer` |
| View any game | Click grid cells to feature locally (client-side) | `dashboard-engineer` |
| Opening book | Mainline openings, internal implementation | `client-engineer` (non-blocking) |

**New requirements added (post-harmonization override):**
1. **View any server game** — Dashboard grid cells clickable in My Bot mode (§7.2 in dashboard-engineer)
2. **Identify "my bot"** — URL param `?bot=BotName` or `localStorage` for visual highlighting (§7.3 in dashboard-engineer)
3. **Opt-in local arena reporting** — `arena.py --report` POSTs summary to server (design spec §8.1, §14; owned by server-engineer for endpoints and table, client-engineer for --report flag, dashboard-engineer for My Bot panel display)

**Deprecated decisions (overridden):**
- ~~Local stats via `arena.py --serve`~~ — replaced by opt-in reporting to main dashboard

**Implementation decisions (non-blocking):**
- Baseline bot depth, re-poll interval, exception handling, clock measurement, arena defaults — all owned by `client-engineer` with recommendations in §10
- Arena table format, stretch agents — owned by `workshop-author`, deferred to build time
