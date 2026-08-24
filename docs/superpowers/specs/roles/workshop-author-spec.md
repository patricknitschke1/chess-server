# Workshop Author — Role Specification

**Date:** 2026-08-24  
**Role:** workshop-author  
**Authority:** [docs/superpowers/specs/2026-08-23-chess-arena-design.md](../2026-08-23-chess-arena-design.md) §11, §13, §17, §19  
[docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md](../2026-08-23-chess-arena-interfaces.md) Part 3 (bot signature), Part 6 (MCP tools)  
**Status:** Phase 8 — build after server, SDK, MCP, and dashboard are verifiable

---

## 1. Scope and Boundaries

### What I own

All attendee-facing prose and Claude customization files:

```
AGENTS.md                              repository guide for attendees and their Claudes
starter-kit/README.md                  quickstart to first rated game
starter-kit/.claude/skills/
  writing-a-chess-bot.md              iterate loop: change, benchmark, deploy, read results
  chess-engine-techniques.md          THE skill — concrete, codeable chess knowledge
  benchmarking-a-bot.md               sample sizes, ref-* ladder, interpreting results
  diagnosing-bot-losses.md            reading analyze_game, common failure patterns
starter-kit/.claude/agents/           attendee-facing subagents (stretch)
starter-kit/.claude/commands/
  improve-bot.md                       /improve-bot command (stretch)
```

**Root `AGENTS.md` maintenance** — kept accurate as code phases land. Claims that a command works before the phase that creates it teaches agents to ignore reality.

### Seam with `client-engineer`

`client-engineer` owns `starter-kit/chess_client/` (SDK), `starter-kit/bot.py` (the template attendees copy), and `arena.py` (local competition). **I own the words that explain them.**

**The boundary, precisely:** I do not write implementation code. I document what was actually built, read from the working artefacts rather than describing what I assume exists. If code is confusing enough to need a paragraph of explanation, I file that back as a design problem rather than papering over it in prose.

**Example of the seam in action:** `client-engineer` delivers a working `choose_move(board: chess.Board, clock: ClockView) -> chess.Move` signature (Interfaces Part 3). I write `writing-a-chess-bot.md` documenting that exact signature, showing attendees how to use `board.legal_moves`, `board.turn`, and `clock.my_ms`. I do not invent a simpler signature or claim parameters exist that do not.

### What is not mine

- Implementation code — any `.py` file in `chess_core/`, `chess_server/`, `chess_client/`, `mcp/`, or `web/`
- API design — I document the SDK and MCP surfaces; `client-engineer` and `mcp-engineer` designed them
- Build-time agent definitions (`.claude/agents/` at repository root) — those are owned by their respective track engineers
- Test code — `chess-domain-engineer`, `server-engineer`, etc. write the tests that verify their own work

---

## 2. Who I Am Writing For

**Someone forty minutes into a workshop, mildly overwhelmed, who may have never played chess and may be new to Python.**

They will not read a wall of text. They will skim, copy the first code block, and run it. By 13:00 they are stuck and need the skill that unblocks them without requiring them to have played a chess game in their life.

### Consequences for how I write

- **Every sentence either helps them make their next move or is deleted.** No preambles, no philosophical context, no "in this skill we will explore."
- **Concrete and codeable, always.** "Consider king safety" tells an attendee nothing they can code. "A king on e1 behind pawns on e2/f2/g2 adds +20 to your evaluation; an exposed king on e4 subtracts −50" is codeable.
- **Code blocks come first, explanation second.** An attendee copies before they read.
- **Never a real token in an example.** Placeholders only: `"your-bot-token-here"`, never a 43-character urlsafe string.
- **Assume Python novice-level.** Explain list comprehensions if you use them. Do not assume familiarity with `argparse`, `dataclasses`, or `asyncio`.
- **Assume chess novice-level.** Explain what a piece-square table is and why it exists before showing one. Do not assume knowledge of "tempo", "opposition", or "zugzwang" without defining them inline.

### What unblocks them at each hour

| Time  | Where they are | What unblocks them |
|-------|----------------|---------------------|
| 09:00 | Arrive, open laptop, clone starter-kit | `README.md` quickstart |
| 09:10 | First rated game playing | Relief, not panic |
| 10:00 | "My bot is losing. How do I make it better?" | `chess-engine-techniques.md` |
| 11:00 | Editing, running arena.py, seeing ratings change | `benchmarking-a-bot.md` |
| 13:00 | Stuck — bot flags every game, or makes illegal moves | `diagnosing-bot-losses.md` |
| 14:00 | Improving iteratively, deploying, checking leaderboard | `writing-a-chess-bot.md` |
| 16:00 | Grudge match against friend's bot | Challenge docs (in README or skill) |

**My biggest failure mode:** writing `chess-engine-techniques.md` as a conceptual overview rather than a paste-able code cookbook. An attendee at 13:00 who reads "consider piece activity" and closes the file has gotten zero value.

---

## 3. The Attendee Journey

Narrative: 09:00 to end-of-day, which artefact of mine is load-bearing at each step, and what each must accomplish.

### 09:00–09:10: Arrival → First Rated Game

**Artefact: `starter-kit/README.md` (quickstart section)**

**Goal:** Attendee goes from `git clone` to a bot playing rated games in **under five minutes**, unaided, without reading anything else.

**Required content:**
- One-line install: `pip install -r requirements.txt` (or `uv pip sync`, whatever `client-engineer` ships)
- `python run.py --register --name YourBotName --owner your-name` → token printed, stored automatically in `.env`
- `python run.py` → bot starts polling, gets paired, plays
- What success looks like: terminal output showing moves, a leaderboard URL to paste in browser
- What to do next: edit `bot.py`, see `writing-a-chess-bot.md`

**What must be true:** An attendee who has never used `git` or `pip` can follow these steps. No assumed knowledge of virtual environments, no unexplained flags, no "configure your editor" diversions.

**Acceptance test:** Hand the quickstart to someone who was not in the room when it was written. Time them. Five minutes or it failed.

---

### 09:10–10:00: "It's playing, but losing badly"

**Artefact: `writing-a-chess-bot.md`**

**Goal:** Teach the iterate loop — change code, benchmark locally, deploy, read the result.

**Required content:**
1. **The signature you implement** — `choose_move(board, clock) -> chess.Move`, with the exact imports and types from Interfaces Part 3. Show `board.legal_moves`, `board.turn`, `board.is_checkmate()`, and `clock.my_ms` in a working example.
2. **Local benchmarking before deploying** — `python arena.py --bots bot.py ref-greedy.py --games 50`. Explain why 50 games (sample size), what the output means (ELO delta, flag count, illegal attempts), and when to trust it (ref-greedy beaten → deploy; otherwise, iterate).
3. **Deploying** — `python run.py` resumes with the new code. Leaderboard updates within a game or two.
4. **Reading the server result** — losses show on the leaderboard, `analyze_game` via MCP shows timing and strikes.
5. **The ref-* ladder as a progress bar** — ref-random (1000) → ref-greedy (1000) → ref-depth2 (1400). Beating the next rung means your bot improved.

**Seam consumed:** `client-engineer` delivers `arena.py --bots ... --games N` and the `ClockView` type. I document those exact interfaces.

**What must be true:** An attendee reads this skill, edits `choose_move`, runs `arena.py`, sees a rating, and knows whether to deploy or keep iterating. They do not deploy blindly and hope.

---

### 10:00–13:00: "How do I make it stronger?"

**Artefact: `chess-engine-techniques.md`**

**Goal:** Unblock a non-chess-player at 13:00 with concrete, paste-able chess knowledge.

**This is my biggest deliverable and the one most likely to get hand-waved.** Vague claims are useless. "Consider king safety" does not help. Concrete values, paste-able tables, and algorithmic steps are the only things that work.

#### Required content (non-negotiable)

##### 1. Material values
Concrete numbers, justified in one sentence each:
```python
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0  # invaluable; never traded
}
```

Why these numbers: rook + pawn ~= queen; two knights < rook + pawn; bishops slightly better than knights (long-range).

##### 2. Piece-square tables
A paste-able table for at least pawns and one piece (knight or king). Show the indexing math that maps `chess.square(file, rank)` to the table. Explain in one sentence what it encodes: "Pawns are worth more the closer they get to promotion; knights are stronger in the center."

Example (show the full 64-entry array, not "..."):
```python
PAWN_TABLE = [
      0,   0,   0,   0,   0,   0,   0,   0,  # rank 1 (white's back rank, pawns never here)
      5,  10,  10, -20, -20,  10,  10,   5,  # rank 2
     ...
    50,  50,  50,  50,  50,  50,  50,  50,  # rank 7 (one square from promotion)
      0,   0,   0,   0,   0,   0,   0,   0   # rank 8 (promotion rank, pawns never here)
]

def evaluate_position(board):
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            value = PIECE_VALUES[piece.piece_type]
            if piece.piece_type == chess.PAWN:
                table_value = PAWN_TABLE[square] if piece.color == chess.WHITE else PAWN_TABLE[chess.square_mirror(square)]
                value += table_value
            score += value if piece.color == chess.WHITE else -value
    return score if board.turn == chess.WHITE else -score
```

Include the mirror indexing for Black — an attendee will otherwise index the table backwards and debug for an hour.

##### 3. Minimax → Alpha-Beta → Move Ordering
- **Minimax (2-ply example):** Show the recursion: `max` at my turn, `min` at opponent's. "Depth 2 means you see your move and their response."
- **Alpha-Beta pruning:** "Once you've found a move that scores +3, you can skip branches that will score worse than +3. This cuts the search tree by ~half without changing the result."
- **Move Ordering:** "Try captures first (`board.is_capture(move)`), then checks, then others. Alpha-beta prunes more when good moves are tried early."

Show working code, not pseudocode. The diff from minimax to alpha-beta is small — show it.

##### 4. Quiescence Search and the Horizon Effect
- **Horizon effect:** "At depth 3, you see `Qxe4`, taking a pawn, and it looks good (+100). But you stopped searching — Black plays `Nxe4` on the next move, and you lost a queen (−800). This is the horizon effect."
- **Quiescence search:** "After reaching your depth limit, keep searching if the position is 'noisy' (checks or captures available). Stop when the position is quiet. This fixes horizon blunders."

Code snippet:
```python
def quiesce(board, alpha, beta):
    stand_pat = evaluate(board)
    if stand_pat >= beta:
        return beta
    alpha = max(alpha, stand_pat)
    
    for move in board.legal_moves:
        if board.is_capture(move) or board.gives_check(move):
            board.push(move)
            score = -quiesce(board, -beta, -alpha)
            board.pop()
            if score >= beta:
                return beta
            alpha = max(alpha, score)
    return alpha
```

**Why this is the load-bearing technique:** A 3-ply search without quiescence hangs pieces; with it, the bot stops making catastrophic blunders and becomes competitive.

##### 5. Time Management for 3+2 Blitz
Rated play is 3 minutes + 2 seconds/move (§11). At 12 seconds/move (reasonable for a minimax bot), the time budget runs out around move 18:

```
180 + 2n − 12n < 0
180 − 10n < 0
n > 18
```

**Required guidance:**
- **Do not search to a fixed depth regardless of time.** You will flag.
- **Budget: spend `my_remaining_ms / 30` per move** (assumes ~30 moves remaining). Adjust dynamically: if `my_remaining_ms < 30000`, cut the per-move budget in half.
- **Iterative deepening:** Search depth 1, then 2, then 3, until time is up. Always have a legal move ready.
- **Stop early if flagging is imminent:** If `clock.my_ms < 1000`, return the first legal move immediately.

Show code:
```python
import time

def choose_move_with_budget(board, clock):
    budget_ms = max(clock.my_ms // 30, 100)  # at least 100ms
    start = time.perf_counter()
    
    best_move = list(board.legal_moves)[0]  # fallback
    for depth in range(1, 10):
        if (time.perf_counter() - start) * 1000 > budget_ms * 0.8:
            break  # 80% of budget used; stop searching
        move = minimax_search(board, depth)
        if move:
            best_move = move
    return best_move
```

**Why this matters:** Flagging is the #1 way a first bot loses (per §17). An attendee who implements minimax but not time budgeting will flag in every game and see 0-10 on the leaderboard. This section must prevent that.

#### Editorial standard for this skill

Every claim is codeable. If I write "prioritize king safety", I must immediately follow with:
- A function that computes a king-safety score
- Concrete numbers (e.g., "+20 for castled king, −50 for exposed king")
- Or a reference to the piece-square table that already encodes it

**"Consider X" without codeable next steps is a failure and must be rewritten.**

---

### 11:00–14:00: "Is my bot actually better, or did I get lucky?"

**Artefact: `benchmarking-a-bot.md`**

**Goal:** Teach attendees to interpret `arena.py` results and make deployment decisions based on evidence.

**Required content:**
1. **Sample size matters** — 10 games is noise; 50 is a weak signal; 100 is trustable. Show the ELO confidence interval widening as sample size shrinks (table or formula).
2. **The ref-* ladder** — ref-random (1000 ELO), ref-greedy (1000 ELO, material-only), ref-depth2 (1400 ELO, 2-ply search). Beating ref-greedy consistently (60%+ win rate over 50 games) means your evaluation works; beating ref-depth2 means your search is strong.
3. **Reading the stats** — flag count is the first thing to check. If `flags: 8` and `wins: 2`, time management is broken, not the evaluation.
4. **The bar for deploying** — new version beats old version in ≥60 of 100 games, **and flags ≤2 times per 100 games**, or do not deploy. A version that wins more but flags more is not better.

**Seam consumed:** `client-engineer` delivers `arena.py --bots ... --games N --seed S`, which prints a table with columns `bot_name | rating | W | L | D | flags | illegal`. I document that exact output format.

---

### 13:00–16:00: "My bot keeps losing, why?"

**Artefact: `diagnosing-bot-losses.md`**

**Goal:** Teach attendees to read `analyze_game` output (MCP tool, Part 6 of Interfaces) and recognise the three common failure patterns.

**Required content:**
1. **How to call `analyze_game`** — via MCP: `analyze_game(game_id=42)`, or show the dashboard game link if `dashboard-engineer` ships a "view game" button. The tool returns PGN, timing table, and event log (per Interfaces Part 6).
2. **Pattern 1: Flagging** — event log shows `Ply 23: White flagged at 0ms`. Diagnosis: time management is broken. Fix: implement iterative deepening and per-move budgeting (link to `chess-engine-techniques.md` §5).
3. **Pattern 2: Illegal moves** — event log shows `Ply 12: White illegal move strike (1/3)`. Diagnosis: `choose_move` returned a move not in `board.legal_moves`. Fix: always `return random.choice(list(board.legal_moves))` as a fallback, and validate your move before returning it.
4. **Pattern 3: Shallow-search blunders** — timing table shows `server_ms` under 100ms per move (you are not searching), and PGN shows material loss (e.g., hanging a queen). Diagnosis: evaluation is fast but shallow; opponent is searching deeper. Fix: implement alpha-beta and quiescence (link to `chess-engine-techniques.md` §3–4).

**Seam consumed:** `mcp-engineer` delivers `analyze_game(game_id)` returning Markdown with PGN, timing table, and event log (Interfaces Part 6). I document how to read that exact output.

---

### Stretch: Attendee-facing agents and `/improve-bot`

**Built only if server, SDK, MCP, and dashboard ship early.**

Attendee-facing agents and the `/improve-bot` command are teaching tools: they demonstrate what a well-scoped subagent looks like and when to use a command instead of a skill.

**Candidate agents (all stretch):**
- `eval-tuner` — takes a bot and a target (e.g., "beat ref-greedy"), runs grid search over piece values or PST coefficients, returns the best-performing configuration. **Isolates noisy work**: running 500+ arena games is agent-appropriate; returning a summary table is not — the agent returns numbers, not walls of text.
- `game-analyst` — reads `analyze_game` output, identifies the pattern (flagging | illegal moves | shallow blunders), suggests the fix. **Isolates diagnosis**: summarising a 60-move PGN and timing table is noisy; the output is 2-3 sentences with a link to the relevant skill section.

**`/improve-bot` command:** Chains `benchmarking-a-bot` → run arena → `diagnosing-bot-losses` → suggest next change. This is a **command** not a skill because it is procedural (a fixed sequence of steps) rather than knowledge injection.

**Decision deferred to build time:** Only ship if time permits. If shipped, all examples in skills must reference the shipped agents and command, not hypothetical ones.

---

## 4. The Teaching Layer

The skill-versus-subagent distinction is itself the lesson, and the repository must demonstrate it. This is deliberate workshop content, not incidental tooling.

### The distinction, stated once and plainly

**Location:** `AGENTS.md` (already present, owned by the orchestrator; I maintain it as code lands)

**Wording (normative):**
> **Subagents isolate noisy work; skills inject knowledge into work you are already doing.**
>
> - If a tool's output is too large to read inline, wrap it in a subagent that returns a summary.
> - If a technique or fact recurs in multiple tasks, extract it into a skill so your agent has it in context.
> - Corollary: if your tool returns concise output, it does not need a subagent — fix the tool instead.

**Example that demonstrates it:** The `chess-engine-techniques` skill contains alpha-beta pseudocode, piece values, and PST tables — knowledge that applies to any bot-improvement conversation. That is a skill. The hypothetical `eval-tuner` agent (stretch) runs 500 arena games and returns a table of coefficients — work that is too noisy to inline. That is a subagent.

### Where attendees can read the source

**Build-time agents:** `.claude/agents/` at repository root. `chess-domain-engineer.md`, `server-engineer.md`, etc. are visible to anyone who clones the repo. The README or AGENTS.md must say "read these to see how this project was built."

**Attendee-facing skills:** `starter-kit/.claude/skills/`. These ship in the kit; attendees can `cat writing-a-chess-bot.md` and see the YAML frontmatter and the skill body.

**Attendee-facing agents (stretch):** `starter-kit/.claude/agents/`. Same as above.

**Teaching moment:** At ~11:00, the workshop leader shows `.claude/agents/chess-domain-engineer.md` on the projector and explains: "This agent was used to build `chess_core`. It owns rules, clock, ELO, matchmaking — one role. You will not use this agent; it is build-time infrastructure. But you can read it to understand how roles are scoped." Then shows `starter-kit/.claude/skills/chess-engine-techniques.md` and explains: "This skill is for you. It injects chess knowledge into any conversation where you are improving your bot."

---

## 5. Seams I Consume

I document interfaces designed and built by other engineers. The following are my dependencies; I do not define them.

### From `client-engineer` (Interfaces Part 3)

1. **`choose_move` signature**
   ```python
   def choose_move(board: chess.Board, clock: ClockView) -> chess.Move
   ```
   This is the attendee-facing signature. I document the exact imports, the `ClockView` fields (`my_ms`, `opponent_ms`, `increment_ms`, `ply`), and how to use `board.legal_moves`, `board.turn`, `board.is_checkmate()`.

2. **`arena.py` CLI and output format**
   ```bash
   python arena.py --bots bot.py baseline.py ref-greedy.py --games 100 --seed 7
   ```
   Expected output: a table with columns `bot_name | rating | W | L | D | flags | illegal | mean_time_ms | p95_time_ms`. I document how to read this table and when to trust the results (sample size, flag count as first diagnostic).

3. **`run.py` CLI for registration and running**
   ```bash
   python run.py --register --name BotName --owner your-name
   python run.py  # starts polling
   ```
   I document the exact flags and what success looks like (terminal output, leaderboard URL).

4. **`ClockView` dataclass fields**
   Per Interfaces Part 1:
   ```python
   @dataclass(frozen=True)
   class ClockView:
       my_ms: int          # always the bot's remaining time, regardless of color
       opponent_ms: int
       increment_ms: int
       ply: int
   ```
   I document that `my_ms` is always the bot's time (color-agnostic), removing "am I White or Black?" as a class of bug.

**If `client-engineer` changes any of these interfaces, my docs must be updated in the same phase.**

### From `mcp-engineer` (Interfaces Part 6)

1. **MCP tool list and signatures**
   Observe tools: `get_leaderboard()`, `get_my_bot()`, `get_game(game_id?)`, `analyze_game(game_id)`
   Act tools: `register_bot(name, owner, role)`, `challenge(opponent, time_control?)`, `make_move(game_id, ply, move)`, `get_legal_moves(game_id)`, `take_control()`, `release_control()`

2. **`analyze_game` output format** (Interfaces Part 6, Decision 5)
   Returns Markdown with:
   - PGN with standard headers
   - Timing table: `Ply | Move | Server ms | Client ms | White remaining | Black remaining`
   - Event log: `Ply 15: White illegal move strike (1/3)`

   I document how to read this output and map it to the three failure patterns (flagging, illegal moves, shallow blunders).

3. **Error messages** (all tools, Interfaces Part 6)
   Actionable prose, not bare status codes. Example: `"No bot registered for this token. Add 'headers': {'Authorization': 'Bearer <token>'} to .mcp.json or call register_bot first."`

   I document these exact error messages in the quickstart so attendees know what to do when they see them.

**If `mcp-engineer` changes tool signatures or output formats, my docs must be updated in the same phase.**

### From `server-engineer` and `dashboard-engineer` (implicit)

- **Leaderboard URL** — I reference it in the quickstart ("Paste this URL in your browser"). If the dashboard ships at a different path or port, the quickstart must be updated.
- **Time control for rated play** — 3+2 (§11). My time-management guidance in `chess-engine-techniques.md` is derived from these constants.

---

## 6. Editorial Standards

Normative rules for all prose I write.

### 1. Actionable prose, always
Every error message, every diagnostic, every "what to do next" must tell the reader what action to take. "Bot is losing" is useless. "Your bot flagged in 8/10 games. Implement time budgeting (see chess-engine-techniques.md §5) before deploying" is actionable.

### 2. Never a real token in an example
Placeholders only: `"your-bot-token-here"`, `"abc123..."`, `sk-xxxxx`. A 43-character urlsafe string looks real and an attendee will copy it and wonder why it does not work.

### 3. Do not claim commands work before they exist
If Phase 4 delivers `arena.py`, the quickstart may reference `arena.py --bots ... --games 50` starting in Phase 4. Before Phase 4, the quickstart must not claim `arena.py` exists. **An `AGENTS.md` that confidently lists a `pytest` invocation against code that does not exist teaches an agent to trust instructions over reality.**

I maintain `AGENTS.md` as phases land; commands are documented when verified, not when planned.

### 4. Attendees edit `bot.py` and nothing else
State this early and often. The quickstart, `writing-a-chess-bot.md`, and every skill that references code must say: "You edit `bot.py`. The `choose_move` function is the only code you write. The SDK, server, and arena are finished infrastructure."

### 5. Extract a skill when a pattern has recurred
Do not write a skill in anticipation of a question. Write it after the same question has been answered twice in different conversations. Skills are for knowledge that has proven itself necessary.

### 6. Every code block is runnable or explicitly marked as pseudocode
If I show a code block, it must be syntactically valid Python that an attendee can copy and run, or it must be marked `# pseudocode` at the top. No "..." placeholders for "obviously you'd fill this in" — attendees do not know what is obvious.

### 7. Definitions inline, not assumed
"Quiescence search" must be defined the first time I use it, in one sentence, before showing code. Do not assume chess or CS vocabulary. "Tempo" and "alpha-beta pruning" are both jargon; define them.

---

## 7. Acceptance Criteria

### Quickstart (`starter-kit/README.md`)
- [ ] A newcomer following the quickstart reaches a first rated game in under 5 minutes, unaided
- [ ] Tested by someone who was not in the room when it was written
- [ ] No unexplained commands, no assumed knowledge of `pip` or `git` internals

### `chess-engine-techniques.md`
- [ ] A non-chess-player can implement alpha-beta search, quiescence, and time budgeting from this skill alone, without asking follow-up questions
- [ ] Every technique has working, paste-able code (not pseudocode)
- [ ] Material values, piece-square tables, and move-ordering rules are concrete numbers, not "consider X"
- [ ] Time-management section prevents flagging (tested: a bot following §5 survives 30 moves at 3+2 without flagging)

### `writing-a-chess-bot.md`
- [ ] The exact `choose_move` signature from Interfaces Part 3 is shown with correct imports
- [ ] The iterate loop (edit → benchmark → deploy → read result) is a step-by-step procedure, not conceptual
- [ ] An attendee knows when to deploy (beaten ref-greedy in 50 games, flags ≤2) and when to keep iterating

### `benchmarking-a-bot.md`
- [ ] Sample size guidance is quantified (10 games = noise, 50 = weak signal, 100 = trustable)
- [ ] Flag count is called out as the first diagnostic
- [ ] The ref-* ladder (ref-random, ref-greedy, ref-depth2) is a progress bar, not a mention

### `diagnosing-bot-losses.md`
- [ ] The three failure patterns (flagging, illegal moves, shallow blunders) are recognisable from `analyze_game` output
- [ ] Each pattern has a fix linked to the relevant skill section
- [ ] An attendee can read a PGN + timing table and know what to change next

### `AGENTS.md`
- [ ] Commands are documented only when verified (no claiming `pytest` works before tests exist)
- [ ] Accurate as of the current phase
- [ ] The skill-vs-subagent distinction is stated once, plainly, with an example

### Cross-cutting
- [ ] No real tokens in any example
- [ ] Every skill tested by someone who was not in the room when it was written
- [ ] All seams consumed (interfaces from `client-engineer`, `mcp-engineer`) match the shipped implementation

---

## 8. Requires Decision

The following design decisions affect my deliverables and must be resolved before I can complete this role spec's implementation.

### Decision 1: `arena.py` output format — columns and precision

**Issue:** Interfaces Part 3 describes `arena.py` returning an `ArenaResult` with stats including `mean_move_time_ms`, `p95_move_time_ms`, `flags`, and `illegal_attempts`, but does not specify the terminal output format (table columns, column order, decimal precision for timing).

**Affects:** `benchmarking-a-bot.md` — I must document the exact table format attendees will see so they know which column is flags and which is rating.

**Recommendation:** Terminal output is a table with columns:
```
bot_name | rating | W | L | D | games | flags | illegal | mean_ms | p95_ms
```
Right-aligned numbers, left-aligned names. Timing columns rounded to integers (no decimal places — 142ms, not 142.7ms). This is readable in a terminal and unambiguous.

**Who decides:** `client-engineer` (owns `arena.py` implementation). I document whatever format is shipped.

**Status:** Not blocking — I can write the skill generically and update with specifics once `arena.py` exists.

---

### Decision 2: Dashboard "view game" button or game detail page

**Issue:** `diagnosing-bot-losses.md` tells attendees to call `analyze_game(game_id)` via MCP, but does not say how to find `game_id` for their most recent loss. Spec §14 describes a dashboard with leaderboard and game grid, but does not specify whether game cells are clickable or show IDs.

**Affects:** `diagnosing-bot-losses.md` — if the dashboard shows game IDs or has a "view game" link, I reference that. If not, I must tell attendees to call `get_my_bot()` → get `current_game_id`, or list their recent games via... (no such endpoint exists as of Interfaces draft).

**Recommendation:** Dashboard game grid cells are clickable, opening a detail view with game ID, PGN, and timing. This removes "how do I get the game_id?" as a question. Alternatively, add `GET /bots/me/recent_games` returning the last 10 games for the authenticated bot.

**Who decides:** `dashboard-engineer` (owns the UI), or `server-engineer` if a new endpoint is needed.

**Status:** Not blocking — I can write the skill assuming MCP `get_my_bot()` → `current_game_id` works, and revise if a better path ships.

---

### Decision 3: Default opening book for `arena.py` (Decision 1 from Interfaces, restated)

**Issue:** §17 requires opening randomisation ("drawn from a small book, seeded for reproducibility") but does not specify the book format or size. Interfaces Decision 1 recommends a hardcoded list of 20-30 FENs 3-4 moves deep.

**Affects:** `benchmarking-a-bot.md` — if the book is visible to attendees (e.g., `arena.py --list-openings`), I can reference it and explain why games start from different positions. If it is internal, I document that "games use random openings for statistical validity; do not expect 1.e4 every time."

**Who decides:** `client-engineer` (owns `arena.py`).

**Status:** Not blocking — the book existing is sufficient; its format is `client-engineer`'s implementation detail.

---

### Decision 4: Stretch goals — which agents and commands to ship

**Issue:** `.claude/agents/` attendee-facing agents (`eval-tuner`, `game-analyst`) and `improve-bot` command are marked **stretch** (§19). Shipping them requires server/SDK/MCP/dashboard finishing early, and each agent must be scoped and tested. No firm decision on which (if any) to build.

**Affects:** Whether I write agent definitions and command files, and whether skills reference them.

**Recommendation:** Defer to build time. If Phase 6 finishes a week early, ship `eval-tuner` as the teaching example (demonstrates isolating noisy work). If not, skills stand alone without referencing agents.

**Who decides:** Project lead (orchestrator), based on schedule.

**Status:** Explicitly deferred — not blocking any skill work.

---

### Decision 5: Exact wording of "provisional" annotation on leaderboard

**Issue:** Interfaces Decision 7 resolves that bots with `games_played < 10` are annotated `"is_provisional": true` in leaderboard responses, but does not specify how this is displayed in the dashboard (e.g., "[P]" next to the name, "Provisional" in a column, asterisk with footnote).

**Affects:** `writing-a-chess-bot.md` and quickstart — if I tell attendees "your rating will show as provisional for your first 10 games," I should describe what that looks like so they recognise it.

**Who decides:** `dashboard-engineer` (owns the UI).

**Status:** Not blocking — I can write "your rating is provisional (marked on the leaderboard) until you've played 10 games" and leave the visual to the dashboard.

---

## Summary for Report

**File written:** `docs/superpowers/specs/roles/workshop-author-spec.md`

**Sections claimed from design spec:**
- §11 (time control — communicate why 3+2 and what it means for bot time budgeting)
- §13 (MCP surface — document the tools attendees use)
- §17 (local arena — document `arena.py` CLI and result interpretation)
- §19 (roster and rationale — communicate the skill-vs-subagent distinction, maintain `AGENTS.md`)

**Seams consumed:**
- From `client-engineer`: `choose_move` signature (Interfaces Part 3), `arena.py` CLI and output format, `run.py` registration/run flow, `ClockView` dataclass
- From `mcp-engineer`: MCP tool signatures (Interfaces Part 6), `analyze_game` output format (PGN + timing table + event log), error message wording

**Requires decision (5 items):**
1. `arena.py` terminal output format (columns, precision) — **not blocking**
2. Dashboard game ID discovery (clickable cells or new endpoint) — **not blocking**
3. Opening book format (internal vs exposed) — **not blocking** (Decision 1 from Interfaces, restated here for completeness)
4. Stretch goals — which attendee-facing agents/commands to ship — **explicitly deferred**
5. Leaderboard "provisional" visual annotation — **not blocking**

**None of the decisions block skill writing.** I can complete deliverables using current interface definitions and revise when implementation details are finalised.
