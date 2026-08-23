# Chess Arena — Design

**Date:** 2026-08-23
**Status:** Approved
**Purpose:** A chess bot competition server for an agentic AI workshop (~20 attendees), doubling as a reference example of an agentic repository.

---

## 1. Goals

1. Attendees write chess bots with Claude's help and watch them climb a live ELO leaderboard.
2. The server is finished infrastructure — attendees consume it, they do not build it.
3. The repository demonstrates Claude best practices: `AGENTS.md`, skills, subagents, spec-driven development. Both in how it was built and in what it hands attendees.

**Non-goals:** running untrusted attendee code server-side; user accounts beyond a bot token; Swiss/knockout tournaments (deferred); mobile-optimised UI.

---

## 2. Core decisions

| Decision | Choice | Rationale |
|---|---|---|
| Bot execution | **Client-side** | No sandboxing burden. Attendee edits code, restarts, sees rating move. |
| What a bot is | **Any program** implementing `choose_move` | Conventional engines by default; an LLM-agent bot is equally valid. Server only speaks the protocol. |
| Transport | **Long-polled REST** | `curl`-able, language-agnostic, no WebSocket state machine in the SDK. Long-poll avoids charging bots for network latency. |
| Time control | **3+2 blitz** | Real clock pressure; increment keeps slow-but-sound bots (and agent bots) viable. |
| Persistence | **SQLite** (Postgres-capable via env) | 20 bots does not need more. |
| Deployment | Single process, 12-factor, Dockerfile | Local by default, deployable if wifi disappoints. |
| MCP transport | **Streamable HTTP** at `/mcp` | One-line attendee setup, no local install, no version skew. |

---

## 3. Architecture

```
chess_core/          # shared, pure, no I/O — used by BOTH server and local arena
  rules.py           # python-chess wrapper: validate, apply, detect termination
  clock.py           # 3+2 blitz clock, flag detection
  elo.py             # rating math, K-factor
  matchmaker.py      # pure pairing policy over a pool snapshot
  match.py           # game state machine

chess_server/
  store/             # SQLite repositories
  engine/
    runner.py        # applies moves, transitions games, persists
    ticker.py        # THE single background loop: pair idle bots, check flags
    reference_bots.py# ref-random, ref-greedy, ref-depth2 (in-process, trusted)
  api/               # FastAPI routes + SSE
  mcp/               # MCP server — an HTTP client of api/, no privileged access

web/                 # dashboard, single page, SSE, no build step

starter-kit/
  bot.py             # choose_move(board, clock) -> move   <- ONLY file attendees edit
  chess_client/      # SDK: register, long-poll, submit, CAS retry, control handoff
  arena.py           # local offline arena
  run.py
  AGENTS.md
  .mcp.json
  .claude/skills/, .claude/agents/, .claude/commands/
```

`chess_core` is the load-bearing idea: the local arena and the live server run **the same engine**, so local results predict server behaviour. A separate simplified local harness would let attendees tune against subtly different rules.

`domain`-layer purity (no DB, no clock reads, no network) is what makes ELO and pairing trivially testable.

---

## 4. Data model

| Table | Columns |
|---|---|
| `bots` | id, name, owner, token_hash, role, rating, wins, losses, draws, games_played, controller, created_at, last_seen |
| `games` | id, white_bot_id, black_bot_id, status, result, termination, fen, white_ms, black_ms, turn_started_at, rated, started_at, ended_at |
| `moves` | game_id, ply, uci, san, fen_after, thinking_ms |
| `rating_history` | bot_id, game_id, rating_before, rating_after, delta, ts |

`rating_history` powers the leaderboard sparkline — the visible proof that an attendee's change worked.

`termination` distinguishes checkmate / resignation / flag / illegal-move-forfeit / adjudication. A bot losing on time has a performance bug, not a chess bug, and the data must say so without the organiser walking over.

---

## 5. Bots

**Roles.** `competitor` (rated, on the leaderboard, auto-matched) or `benchmark` (unrated, hidden from leaderboard, challenge-only).

Benchmark bots enable self-play: an attendee keeps `alice-v2` up as a sparring partner for `alice-v3`. Making them unrated removes the leaderboard-farming exploit structurally, so nobody has to be policed.

**Reference bots.** `ref-random`, `ref-greedy`, `ref-depth2` run in-process on the server as permanent benchmarks. They solve cold start (something to play at 9am), provide a calibration ladder, and smoke-test the pipeline. They are the *only* server-side execution, and they are code we wrote.

**Identity and auth.** Registration returns a bearer token; every move requires it. Tokens are stored hashed and never logged. Without this, any attendee's over-eager Claude can resign on someone else's behalf. Re-registering with the same name + token resumes the same identity and rating, so restarting after a code change preserves the climb — essential to the iterate-with-Claude loop.

---

## 6. Play protocol

```
POST /bots                      -> {bot_id, token}
GET  /bots/me/turn              -> long-poll (holds up to 20s)
                                   returns {game_id, ply, fen, legal_moves,
                                            history_san, white_ms, black_ms, controller}
POST /games/{id}/moves          -> {ply, move}   (ply = compare-and-swap)
POST /games/{id}/resign
POST /challenges                -> challenge a named bot
GET  /events                    -> SSE stream (dashboard only)
```

The turn response carries full game state so `choose_move` needs no second round-trip.

**Move safety.** Every submission carries the `ply` it believes it is playing. Mismatch returns `409 Position already advanced`. This makes double-moves structurally impossible and makes retries safe.

**Control handoff.** A bot's `controller` is `client` or `agent`. MCP `make_move` is refused while the SDK client is actively polling, with an actionable message; `take_control()` / `release_control()` flip it. The SDK sees `controller: "agent"` and idles with a clear log line rather than fighting for the move. Without this the attendee's terminal spews 409s and they conclude the server is broken.

---

## 7. Matchmaking and rating

**Pairing** (ticker, ~1s): idle competitors pooled; rating window starts ±100 and widens 100 per tick; tie-break by *fewest games played* so new bots get a game within seconds; no immediate rematch unless the pool is under 4; colours alternate; same-owner pairs avoided unless the pool is under 4. One concurrent game per bot.

**Pool eligibility** is distinct from in-game timeouts: a bot is eligible for pairing only if it is a `competitor`, not currently in a game, and has polled within the last 60s. A bot that stops polling *between* games simply stops being paired; a bot that stops polling *during* a game loses on the clock (§8). Two different questions, one mechanism each.

**Rating:** Elo, start 1200, K=32 for the first 30 games then K=16. Bots under 10 games marked provisional. Ratings update the moment a game ends, append to `rating_history`, and emit an SSE event.

**No per-game background tasks.** Games advance on move submission; the single ticker handles only what happens without a request — pairing and flag-fall. One loop for the whole server.

---

## 8. Game lifecycle and failure modes

| Situation | Outcome |
|---|---|
| Legal move | Applied, increment added, clock switches |
| Illegal move | `400` + legal move list; **3 strikes in a game → forfeit** |
| Flag fall | Loss on time |
| Client stops polling | Clock simply runs out — no separate disconnect rule |
| 150 moves | Adjudicated on material; draw if within a pawn |
| Stalemate / 50-move / threefold / insufficient material | Draw, per `python-chess` |

The three-strike rule matters: without it, a bot with an off-by-one in move generation hammers the server until it flags, and the attendee sees a mystifying timeout instead of the real cause. Returning the legal move list on rejection lets Claude diagnose it instantly.

Deleting the separate disconnect rule in favour of the clock is deliberate — one mechanism instead of two, and it is how chess already works.

---

## 9. MCP surface

Tools are deliberately few. Descriptions include an example call and explicit error guidance; attendees will read them to learn what good MCP design looks like.

**Observe:** `get_leaderboard()`, `get_my_bot()`, `get_game(game_id?)`, `analyze_game(game_id)`
**Act:** `register_bot(name, owner, role)`, `challenge(opponent)`, `make_move(game_id, move)`, `get_legal_moves(game_id)`, `take_control()` / `release_control()`

`get_game()` defaults to the caller's current game and returns an **ASCII board** plus FEN, SAN history, clocks and turn — Claude reasons far better over a board it can see than a 4KB JSON blob, at a fraction of the tokens. Same principle for the leaderboard.

`analyze_game` returns PGN with per-move timing and eval swing. It turns "my bot lost" into a conversation with a concrete fix, which is the workshop's central moment: not *Claude wrote code*, but *Claude closed the loop on feedback from a real system*.

Errors are actionable prose (`"No bot registered for this token. Call register_bot first."`), never bare status codes. Mutating tools carry `destructiveHint`, read-only ones `readOnlyHint`, so permission prompts carry meaning.

The MCP server holds no privileged path to the database — it is a client of the same HTTP API. Anything Claude can do, a bot can do.

---

## 10. Local arena

```bash
python arena.py --bots bot.py baseline.py ref_greedy.py --games 100 --seed 7
```

Runs bots against each other offline using `chess_core`, printing a local ELO table plus the diagnostics that matter for blitz:

- time per move (mean / p95) and **flag count** — the most common way a first bot loses
- illegal-move attempts, with the offending position
- head-to-head win rates, PGN export, `--replay <game>` for ASCII stepping

**Opening randomisation is mandatory.** Two deterministic bots otherwise play the identical game every time, making "100 games" one game repeated — an illusion of significance that looks like it is working. Games start from a random opening drawn from a small book, seeded for reproducibility.

Attendee loop: iterate in the arena (seconds, free, no rating risk) → deploy when it beats the baseline → watch the live leaderboard. Fully productive before touching the network, which matters when 20 laptops hit conference wifi at once.

---

## 11. Dashboard

One app, two modes via a toggle:

- **Big Screen** — one featured live game rendered large, leaderboard rail, results ticker. Readable from the back of the room.
- **My Bot** — leaderboard, grid of live games, personal panel with rating sparkline and recent results.

Server-rated games render **green**, local/unrated **amber**. Nobody should mistake a practice win for a ranked one.

Live updates via SSE. Plain HTML/CSS/JS, no build step — it must still work when someone clones this in two years.

---

## 12. Agentic repository layout

**Build-time agents** (`.claude/agents/`)

| Agent | Expertise | Owns |
|---|---|---|
| `chess-domain-engineer` | python-chess, clock semantics, Elo, adjudication | `chess_core/`, strict TDD |
| `server-engineer` | FastAPI, SQLite, SSE, async, long-polling, auth, CAS | `store/`, `engine/`, `api/` |
| `mcp-engineer` | MCP spec, FastMCP, tool-description ergonomics | `mcp/` |
| `dashboard-engineer` | HTML/CSS/JS, SSE, board rendering, visual design | `web/` |
| `workshop-author` | Pedagogy, Claude customization formats, writing for novices | `AGENTS.md`, skills, starter-kit docs |
| `spec-reviewer` | Diffs vs spec; security, simplicity, YAGNI | Read-only, everything |

`mcp-engineer` and `server-engineer` look redundant and are not: one designs for HTTP clients (status codes, idempotency, wire efficiency), the other for a language model (prose over JSON, self-explaining errors, names that survive a crowded namespace).

`chess-domain-engineer` is isolated because it is the only place where being wrong is *silent* — an Elo bug or mis-detected threefold repetition produces a quietly wrong leaderboard all day.

**Attendee-facing skills** (`starter-kit/.claude/skills/`)

- `writing-a-chess-bot` — the iterate loop, what to edit, how to deploy
- `chess-engine-techniques` — material values, piece-square tables, alpha-beta, move ordering, quiescence, 3+2 time management. Must be concrete and codeable; "consider king safety" is useless to a non-player.
- `benchmarking-a-bot` — sample sizes, baseline + `ref-*` ladder, reading time/flag stats, the bar for deploying
- `diagnosing-bot-losses` — reading `analyze_game`, common failure patterns

**Attendee-facing agents** — only where isolation pays for itself:

- `eval-tuner` — parameter sweeps (12 configs × 100 games → one config). Build only if attendees get that far.
- batch loss scanning (20+ games) — analysing a *single* game belongs inline, where Claude needs the moves to write the fix.

**Command:** `/improve-bot` — chains losses → diagnosis → change → benchmark → deploy.

The skill-vs-agent split is itself teaching material: **subagents isolate noisy work; skills inject knowledge into work you are already doing.** A corollary worth stating to attendees: make your tools return summaries and you need fewer subagents — reaching for a subagent is often a workaround for a CLI that dumps too much.

**The meta-layer.** This server is built using these practices — brainstorm, spec, plan, TDD on `chess_core`, skills extracted when a pattern recurs (`adding-an-mcp-tool` is already predictable). The git history becomes the teaching artifact: real commits, real spec, real skills. More convincing than slides.

---

## 13. Testing

- `chess_core` — direct unit tests, no fixtures. Elo gets a property test asserting the exchange is zero-sum and symmetric.
- Matchmaker — seeded pool snapshots, deterministic, no clock.
- API — in-process scripted **fake bot harness** playing complete games over the real endpoints.
- Failure paths first: illegal-move strikes, flag-fall, mid-game disconnect, CAS conflict, control handoff. These are what break live.

---

## 14. Implementation phasing

The spec is large; the plan should build it in demonstrable slices, each independently testable:

1. `chess_core` — rules, clock, Elo, matchmaker, match state machine
2. `arena.py` + starter-kit `bot.py` + baseline — a working offline competition with no server at all
3. Server — store, API, ticker, reference bots, fake-bot harness
4. `chess_client` SDK — the loop attendees actually run
5. MCP server
6. Dashboard
7. Claude layer — `AGENTS.md`, skills, agents, `/improve-bot`

Phase 2 is the first point where something is genuinely playable, and phase 4 the first point where the whole loop closes. Both are good stopping points if time runs short.

---

## 15. Deferred

Swiss tournament mode; Postgres; bot code upload with sandboxing; spectator chat; persistent cross-workshop leaderboards.
