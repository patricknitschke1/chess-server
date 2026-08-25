# Phase 1 & 2 review — `chess_core` and `starter-kit`

**Reviewed:** `chess_core/` (6 modules), `starter-kit/` (`arena.py`, `bot.py`, `opening_book.py`, `chess_client/`, `ref_bots/`), `tests/chess_core/`, `tests/arena/`, `starter-kit/tests/`, `pyproject.toml`.
**Against commit:** `a8fa86f`, tree clean, `.venv/bin/pytest -q` → **139 passed** (reproduced).
**Authorities:** design spec rev 5 (§4/§6/§7.1 normative), interfaces doc Parts 1/3/4, chess-domain and client-engineer role specs, AGENTS.md.

**Verdict: two Critical silent failures, both in `starter-kit/ref_bots/` and `starter-kit/arena.py`. `chess_core` itself is in good shape — one purity violation and one missing-constant gap, no arithmetic errors found.** The reference-bot ladder that anchors every rating in the room is not what the docstrings say it is.

I did not write a file to `docs/agent-reports/` — you instructed read-only. Throwaway scripts are in `/tmp/chk1.py` … `/tmp/chk9.py` if you want to re-run any of this.

---

## Critical

### C1. `ref_depth3` searches with an inverted min/max flag — it assumes the opponent plays the move best *for it*

[starter-kit/ref_bots/ref_depth3.py](starter-kit/ref_bots/ref_depth3.py#L120)

`evaluate_position` is scored from a fixed White perspective (correct). So `maximizing` must be true exactly when **White is to move at that node**. The call site passes `not board.turn`, evaluated *after* `board.push(move)`:

```python
board.push(move)                                             # line 118
score = minimax(board, 1, ..., not board.turn)               # line 120
```

After a White root move, `board.turn == chess.BLACK == False`, so `not board.turn == True` → the Black-to-move node is searched **maximising for White**. Same inversion for a Black root.

The effect is not "slightly weaker": the search picks the move after which the opponent's *most helpful blunder* is largest, so it gives pieces away.

```
$ .venv/bin/python /tmp/chk2.py
queen can grab a king-defended pawn      shipped=Qxd7+   fixed=Qd6
1.e4 d5                                  shipped=exd5    fixed=Bb5+
Ruy Lopez                                shipped=Bxc6    fixed=Bc4
after shipped move, free recapture available: ['Kxd7']
```

FEN `4k3/3p4/8/8/8/8/3Q4/4K3 w - - 0 1`: the shipped bot plays `Qxd7+` and loses the queen to `Kxd7`. The one-token fix (`board.turn` instead of `not board.turn`) plays `Qd6`.

Measured impact (`/tmp/chk3.py`, 6 games, seed 7, alternating colours): shipped 2.5 – fixed 3.5.

This is the same shape as the `bot.py` mate bug you already fixed: a search parameter derived from a mutable `board.turn` at the wrong moment. Note `evaluate_position` here got it *right* (fixed White perspective) — the defect is in the flag, not the eval.

**Why it matters beyond the bot:** §10.3 says anchor ratings bias every rating in the room. `ref_depth3` is an anchor.

---

### C2. The reference ladder does not have the ordering the docstrings and the client spec claim

`ref_depth3.py` line 105 says *"Calibrated rating: 1200 (measured from seeded arena ladder)"*; `ref_greedy.py` line 21 says 1000; `ref_random.py` line 11 says 800. §10.3: *"Anchor ratings are calibrated before the workshop from one seeded arena ladder, and the measured numbers are recorded next to the constants. Guessed anchor ratings would bias every rating in the room."*

Running the ladder as specified:

```
$ cd starter-kit && ../.venv/bin/python arena.py --bots bot.py ref_bots/ref_random.py \
    ref_bots/ref_greedy.py ref_bots/ref_depth3.py --games 48 --seed 7

Bot                  Rating   W   L   D Games
bot                    1303  14   0  10    24
ref_depth3             1226   5   2  17    24
ref_greedy             1173   4   9  11    24
ref_random             1098   0  12  12    24
```

Two separate problems:

1. **The recorded numbers are not reproducible from any ladder.** The measured spread is 1098–1226, not 800–1200. `compute_rating_exchange` cannot move a bot from 1200 to 800 in 24 games. Either the numbers were guessed (which §10.3 forbids by name), or the procedure that produced them is not the one in the repo.

2. **The shipped baseline never loses.** Client-engineer spec §3.1: *"Should beat `ref-random` reliably **and lose to `ref-greedy` reliably**, so attendees see rating movement in both directions immediately."* Head-to-head, 8 games each, seed 7 (`/tmp/chk4.py`):

```
   ref_depth3 vs ref_greedy   {'ref_depth3': 5.0, 'ref_greedy': 3.0}
   ref_greedy vs ref_random   {'ref_greedy': 7.0, 'ref_random': 1.0}
 baseline_bot vs ref_greedy   {'baseline_bot': 6.0, 'ref_greedy': 2.0}   <-- spec says this should lose
 baseline_bot vs ref_random   {'baseline_bot': 6.5, 'ref_random': 1.5}
```

In the 48-game ladder, `bot` goes **14–0–10**. An attendee who changes nothing sees their rating go up and never come down. The "rating movement in both directions immediately" property is not there.

C1 is a contributing cause — fixing `ref_depth3` should raise the top of the ladder — but note `ref_greedy` is the bot the spec designates as the baseline's superior, and it is beaten 75%. Recalibrating the docstring numbers without re-checking this ordering would just record the wrong ladder more precisely.

---

### C3. `arena.py` records a checkmate delivered on the capping ply as an adjudicated draw

[starter-kit/arena.py](starter-kit/arena.py#L115) — `while ply < PLY_CAP:` with termination checked only at the *top* of the loop, then [arena.py](starter-kit/arena.py#L306) falls through to `termination="adjudicated"`, `result="draw"`.

`chess_core/match.py` goes to some length to get this exact ordering right — [match.py](chess_core/match.py#L51) checks `move_result.is_terminal` **before** the cap, with a comment saying why. The arena inverts it: it discards `move_result.is_terminal` entirely and re-derives termination on the *next* iteration, which never happens when `ply` reaches the cap.

Demonstrated by monkey-patching `arena.PLY_CAP` (same code path, smaller numbers — `/tmp/chk6.py`):

```
cap=2, mate on ply 1 : white_win checkmate ply 1
cap=1, mate on ply 1 : draw adjudicated ply 1   <-- checkmate recorded as adjudicated
chess_core.match, same situation: TerminationReason.CHECKMATE
```

Rare, but silent, and it breaks the load-bearing AGENTS.md claim that *"offline results must predict live server behaviour"* — the same game scores `1-0` on the server and `½-½` in the arena. §22 ordering is normative.

---

## Major

### M1. `chess_core.matchmaker.pair_bots` reseeds the process-global RNG, and the seed does nothing

[chess_core/matchmaker.py](chess_core/matchmaker.py#L37) — `random.seed(seed)`.

This is the only I/O-shaped statement in `chess_core` (`grep -rnE "time\.|open\(|socket|requests|httpx|sqlite|random\." chess_core/` returns exactly this one line). It violates AGENTS.md's *"`chess_core` stays pure"* and the role spec's "no global mutable state".

```
$ .venv/bin/python /tmp/chk5.py
=== D: pair_bots mutates the process-global RNG ===
  same global stream after pair_bots(seed=999)? False
=== E: pair_bots is deterministic with NO seed at all ===
  seed=42 == seed=None ? True
  seed=42 == seed=1  ? True
```

`random` is never referenced again in the module. The design §9.2 pseudocode contains no randomness either — the `seed` parameter is vestigial, pinned into the interfaces doc but unused by the algorithm.

Two consequences: (a) the ticker calls `pair_bots` once per second, so in phase 3 every tick silently resets the process-wide `random` stream — anything in `chess_server` that reaches for `random` becomes replayable from a known seed; (b) `test_seeded_determinism` and `test_seeded_different_seeds_different_pairings` cannot fail (see T1).

Escalating rather than deciding: whether `seed` should be *removed* from the pinned signature is a spec question. Deleting the `random.seed` call is not.

### M2. Four canonical §5.2 constants are missing from their only declaration site

§5.2 names `clock.py` as the sole declaration site for `POLL_RECENCY_NS`, `CHALLENGE_TTL_NS`, `POLL_HOLD_NS` and `TICK_INTERVAL_NS`, and says *"everything else imports"*.

```
$ .venv/bin/python /tmp/chk8.py
=== L: canonical §5.2 constants missing from clock.py ===
  POLL_RECENCY_NS      present: False
  CHALLENGE_TTL_NS     present: False
  POLL_HOLD_NS         present: False
  TICK_INTERVAL_NS     present: False
```

Interfaces Part 1's `clock.py` constant block omits them too, so this is spec-to-spec drift that the implementation inherited — exactly the "fix passes land in the design spec and skip the interfaces doc" pattern from prior rounds. Left as-is, server-engineer will declare them in `chess_server/`, which §5.2 forbids.

### M3. `compute_one_sided_exchange` cannot express a draw against an anchor

[chess_core/elo.py](chess_core/elo.py#L96) — `competitor_won: bool`.

§10.3 rates all anchor games one-sidedly. Draws against anchors are not exotic: the 48-game ladder above produced 12 draws in `ref_random`'s 24 games and 17 in `ref_depth3`'s. There is no representable outcome for them.

This is the one case your mode brief says to raise loudly: **the code cannot be made to satisfy §10.3 as written**, because the pinned signature has no draw arm. Either §10.3 needs a draw rule and the signature changes, or the spec must say what happens (unrated? ignored?). Phase 3 will hit this on day one.

### M4. `validate_and_apply_move` claims to detect threefold and structurally cannot

[chess_core/rules.py](chess_core/rules.py#L76) — `elif board.can_claim_threefold_repetition():`

The board is constructed from a FEN and pushed once, so `move_stack` has one entry and python-chess has no history to search.

```
$ .venv/bin/python /tmp/chk5.py
=== C: validate_and_apply_move can never report threefold ===
  final move_result.is_terminal / termination : False None
  detect_termination(fen, history)           : (True, TerminationReason.THREEFOLD)
```

Same position, two functions, opposite answers. The arena is saved only because it ignores `move_result.is_terminal` and calls `detect_termination` at the loop top — but that is a coincidence, not a design. A server that trusts `move_result.is_terminal` (the natural reading of `transition_after_move(state, move_result)`) will grind every repetition to the 200-ply cap.

The branch is not merely dead, it is *misleading*: it reads as coverage. `can_claim_fifty_moves()` on the same line 74 **does** work (it reads the halfmove clock out of the FEN), which makes the threefold line look equally trustworthy.

### M5. `arena.py` does not implement the `arena.py` surface pinned in Interfaces Part 3

```
$ grep -rn "run_arena\|ArenaStats\|HeadToHead\|ArenaResult\|--report\|head_to_head" starter-kit/
(no matches)
```

Missing against Interfaces Part 3 and client-engineer spec §3.4:

| Required | Status |
|---|---|
| `run_arena(bot_modules, num_games, seed, time_control_ms, increment_ms, verbose) -> ArenaResult` | absent |
| `ArenaStats`, `HeadToHead`, `ArenaResult` dataclasses | absent |
| **Head-to-head win rates** (§17, and in the spec's sample output) | absent |
| `--report` → `POST /arena-reports` (client spec §3.4, errata 3/4) | absent |
| Illegal-move attempts **with the offending position** (§17) | count only; [arena.py](starter-kit/arena.py#L285) prints the UCI, never the FEN, and only under `--verbose` |
| `--replay` *stepping* with clock display (§17, client spec §3.4) | prints the whole game at once, no clocks |

`--report` may legitimately be phase 3 (it needs a server). Head-to-head and the offending position are §17 requirements with no server dependency, and `run_arena` is a pinned seam. Also note the module defines its own `GameResult` dataclass that shadows the `chess_core.GameResult` enum by name — the pinned name for that type is `ArenaResult`.

### M6. `bot.py` computes a time budget and never uses it

[starter-kit/bot.py](starter-kit/bot.py#L112) — `time_budget_ms = clock.my_ms / 40`, then the only use is the `< 100` early-out at line 115. Depth is hard-coded to 2 regardless. The docstring's "Time management strategy" section describes a budget that does not exist.

Correct today (depth 3 is fast enough — measured avg 16ms/move in the ladder), but the file is *the* file attendees read and modify. An attendee who raises the depth will find the "time management" does nothing, and will flag. That is the failure mode the client spec calls out as the most common.

Also: [starter-kit/bot.py](starter-kit/bot.py#L1) is ~140 lines against the client spec's *"Must be under 50 lines including comments, readable on a projector."*

---

## Tests that cannot fail

I ran an AST pass for assertion-free tests plus targeted mutation reasoning.

### T1. Two tests contain no assertion at all

```
$ .venv/bin/python (AST scan)
  NO ASSERT: tests/arena/test_arena.py::test_ref_depth3_avoids_obvious_blunders
  NO ASSERT: tests/chess_core/test_matchmaker.py::test_seeded_different_seeds_different_pairings
```

[tests/arena/test_arena.py](tests/arena/test_arena.py#L78) — `test_ref_depth3_avoids_obvious_blunders`. The innermost branch is a bare `pass` with a comment excusing it. **This is the test that was supposed to catch C1**, and its name says so. It is the single most expensive decorative test in the tree.

[tests/chess_core/test_matchmaker.py](tests/chess_core/test_matchmaker.py#L198) — computes two pairing lists and asserts nothing.

### T2. `test_account_move_no_increment_on_flag` passes with the bug it names

[tests/chess_core/test_clock.py](tests/chess_core/test_clock.py#L61). Assertion is `new_clock.white_ns < state.increment_ns`.

```
assertion is `new_clock.white_ns < 2000000000`
correct impl  : -500000000 -> passes: True
BUGGY impl    :  1500000000 -> passes: True   <-- test cannot detect the bug
```

Mutation: delete the `if not flagged:` guard at [chess_core/clock.py](chess_core/clock.py#L131). This test still passes. §6.4's no-increment-on-flag rule is actually covered by `test_account_move_flags_on_exact_zero` (`white_ns == 0`), so you have the coverage — but the test named for the rule does not provide it.

### T3. `test_draw_exchange_symmetric` calls the same thing twice

[tests/chess_core/test_elo.py](tests/chess_core/test_elo.py#L45) — `compute_draw_exchange(1200, 1200)` twice with identical arguments and asserts the results match. Tautological. It tests nothing about symmetry; swapping never happens.

Related: `test_elo_zero_sum_symmetric` is named for two properties and asserts one. Elo is **not** swap-symmetric, so the missing half could not pass:

```
$ .venv/bin/python /tmp/chk9.py
winner=1000 loser=1400: delta=+22  |  winner=1400 loser=1000: delta= +2   swap-symmetric? False
winner= 800 loser=1600: delta=+24  |  winner=1600 loser= 800: delta= +0   swap-symmetric? False
```

The test is right to omit it; the **spec** is wrong. Role spec §3 and the interfaces docstrings both assert *"Exchange is zero-sum and symmetric"* and role spec §7 mandates a property test asserting `compute_rating_exchange(A, B) = negate(compute_rating_exchange(B, A))`. That test is unimplementable and the claim should be struck from both documents.

### T4. `test_seeded_determinism` survives deleting the seeding

[tests/chess_core/test_matchmaker.py](tests/chess_core/test_matchmaker.py#L183). `pair_bots` is deterministic with any seed, no seed, or the `random.seed` line removed (verified in `/tmp/chk5.py` §E). Mutation: delete [matchmaker.py](chess_core/matchmaker.py#L37) → passes.

### T5. `test_arena_clock_matches_chess_core` does not compare the arena clock to anything

[tests/arena/test_arena.py](tests/arena/test_arena.py#L177). The only assertions are `white_time_ms <= 180000 + 100*2000 + 1000`. Mutation: make `account_move_and_switch` deduct nothing at all → remaining stays 180000, well under the bound → **passes**. It also passes if the clock is never advanced, if increments are applied twice on one side, or if the wrong side is charged. The name promises the §17 guarantee ("a bot that flags locally flags live") and the body checks an upper bound that a broken clock satisfies more easily than a correct one.

`test_arena_detects_flags` and `test_clock_charges_correct_side_from_black_to_move_opening` are genuine and do carry weight here.

### T6. `test_fen_to_ascii_starting_position` passes on the identity function

[tests/chess_core/test_rules.py](tests/chess_core/test_rules.py#L216). Asserts `"r" in out`, `"K" in out`, `"8" in out`.

```
a fen_to_ascii that just returned its input would pass: True
```

Mutation: `return fen` → passes.

### T7. Coverage gap on the exact defect that shipped

[tests/chess_core/test_rules.py](tests/chess_core/test_rules.py#L225) — `test_san_list_to_pgn` never passes `starting_fen`. `_scholars_mate_result()` in the arena tests leaves `opening_fen=None`, so `test_arena_replay_round_trips_exported_pgn` round-trips only from the standard start.

Every real arena game starts from a book position. I verified the fix works —

```
$ .venv/bin/python /tmp/chk7.py
headers: ['[White "W"]', '[Black "B"]', '[Result "1-0"]', '[SetUp "1"]',
          '[FEN "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"]']
parse errors: []  moves read: 5
```

— but nothing in the suite would catch its regression. Mutation: drop the `[SetUp]`/`[FEN]` headers from [rules.py](chess_core/rules.py#L295) → all 139 tests still pass.

### T8. No test for §18's "flag precedes illegal-move validation"

§18 lists this among the four table-driven §6.4 clock cases. `chess_core/clock.py` alone cannot express it; the ordering lives in [arena.py](starter-kit/arena.py#L199) (flag check before `validate_and_apply_move`). No test exercises a flagged bot submitting an illegal move.

---

## Minor

- **`transition_to_terminal` is the only transition that does not validate.** [chess_core/match.py](chess_core/match.py#L88) accepts an already-terminal state and silently overwrites the result, while `transition_to_active` and `transition_after_move` both raise. `can_transition` exists and no production path calls it.
  ```
  can_transition(finished, FINISHED) = False
  transition_to_terminal(finished, FLAG, BLACK_WIN) -> FLAG BLACK_WIN (no error raised)
  ```
  Double-finalisation is prevented by DB CAS per AGENTS.md, so this is not live-breaking — but it is a silent overwrite in the one module whose job is encoding legal transitions.

- **Anti-repetition logic in both reference bots is dead.** [ref_depth3.py](starter-kit/ref_bots/ref_depth3.py#L119) and [ref_greedy.py](starter-kit/ref_bots/ref_greedy.py#L40) call `board.is_repetition(2)`. Bots receive a board built from FEN (`arena.py` rebuilds it every ply), so `move_stack` is empty:
  ```
  B. move_stack len from FEN-built board: 0
     is_repetition(2) after 1 push: False
  ```
  The ±5000 / −10000 penalties never fire. The comments above them (*"Never repeat voluntarily; a winning side that does draws its own game"*) describe a mitigation that does not exist — and the ladder shows exactly the symptom they claim to prevent (17 of `ref_depth3`'s 24 games drawn). This will also mislead attendees who copy the pattern.

- **Unit-suffix discipline is not met for four time fields/params.** `to_move_since_mono`, `turn_started_mono` ([types.py](chess_core/types.py#L97)), and the `now_mono`/`receive_mono` parameters carry `_mono`, not `_ns`/`_ms`. Role spec §8 acceptance criterion 6 says *"every time field/parameter has `_ns` or `_ms`"*; interfaces Part 1 says *"a field without a suffix is a bug"*. The code matches the pinned interfaces exactly, so **the code is conformant and the two documents contradict each other.** Worth one sentence in the interfaces doc rather than a rename.
  Relatedly, [test_types.py](tests/chess_core/test_types.py#L64) `test_clock_state_has_ns_suffix` reads two fields and checks neither the suffix nor the other six.

- **`TerminationReason.CRASH` is not in the pinned enum.** [chess_core/types.py](chess_core/types.py#L34) has it; interfaces Part 1's `TerminationReason` block does not. The decision is settled — the seam document was not updated with it. Same pattern as M2.

- **`export_to_pgn` stamps every game with post-tournament ratings.** [arena.py](starter-kit/arena.py#L449) reads `tracker.get_rating(...)` at export time, so game 1's `[WhiteElo]` shows the final rating, not the rating when it was played.

- **`compute_statistics` p95 is `sorted[int(0.95*n)]`,** which for n ≤ 20 is simply the maximum. [arena.py](starter-kit/arena.py#L327). `test_arena_computes_statistics` asserts `p95 == 300` for a 10-element list, i.e. asserts p95 == max. Fine for triage, but it is not a p95, and the flag-diagnosis prose prints it as one.

- **`_make_pairing` dead branch.** [arena…](chess_core/matchmaker.py#L67) `matched` is always `> i` (`j` starts at `i+1`), so the `else:` arm of `if matched > i:` is unreachable.

- **`_allowed` checks rematch in both directions;** the §9.2 pseudocode checks only `a.last_opponent_id == b.bot_id`. Harmless and arguably better, but it is a deviation from a block the errata declared normative.

- **`test_opening_book_is_diverse`** duplicates `test_opening_book_has_valid_fens`' length check; neither adds anything the other lacks.

- **Stray comment indentation** at [chess_core/rules.py](chess_core/rules.py#L173) — the `# Insufficient material…` comment starts at column 0 inside a function body.

---

## Over-engineering

Very little, which is worth saying. Two notes:

- `matchmaker.pair_bots`' `seed` parameter is speculative generality that turned into a purity bug (M1). The algorithm has no random component and the design pseudocode confirms it never did.
- `chess_client/__init__.py` re-exporting `ClockView` from `chess_core` is a one-line indirection with one consumer shape. It is *better* than the client spec's instruction to redefine the dataclass in `chess_client/types.py` (single source of truth beats duplication), so I would keep it and amend the spec — but note the drift: spec §3.2 says `chess_client/types.py` owns the definition.

---

## Requirements checked and found satisfied

**chess-domain-engineer spec**

- §6.4 ordering, all five steps, in the pinned atomic API — deduct → flag → no-increment-on-flag → increment → switch. [clock.py](chess_core/clock.py#L110) matches step for step.
- **Flag predicate is `remaining_ns <= 0`** per errata 1 (not `< 0`, which role spec §3 still wrongly repeats). Verified: `test_account_move_flags_on_exact_zero` asserts `flagged is True` and `white_ns == 0` with zero increment.
- Delivery idempotency — `delivered_to_mover == 1` guard returns the clock unchanged; `turn_started_mono` never restarts.
- Side switch clears delivery: `delivered_to_mover=0`, `turn_started_mono=None`, fresh `to_move_since_mono`.
- `check_delivery_timeout` boundary is strict `>`: false at exactly `grace_ns`, true at `grace_ns + 1`.
- `account_move_and_switch` raises on an undelivered position.
- **Purity:** `grep -rnE "time\.|open\(|socket|requests|httpx|sqlite" chess_core/` → nothing. Acceptance criterion 5 (`grep -r "time.monotonic" chess_core/`) passes. Only `random.seed` (M1) breaks the rule.
- Elo zero-sum, both decisive and draw, swept over 3,249 rating pairs — real property tests, both re-verified.
- Elo direction: winner favourite gains less, underdog gains more; anchor injection shrinks toward zero as the competitor approaches the anchor. No sign or perspective error in `elo.py`.
- `position_key` returns the first four FEN fields; threefold compares keys, never full FENs. `test_threefold_detection_uses_position_key` genuinely fails if you compare full FENs (the halfmove clocks differ across the cycle).
- Insufficient material outranks the claimable fifty-move draw, with a test that pins the label.
- `PLY_CAP` handling in `match.py`: terminal check precedes cap check, verified directly.
- §7 transition table in `can_transition` is correct including terminal→∅.
- `NO_SHOW`/`SERVER_RESTART`/`ADMIN_ABORT` → `aborted`; `ABANDONED` → `finished` (matches §6.3's "mid-game → finished/abandoned").
- Constants `RATED_*`, `EXHIBITION_*`, `DELIVERY_GRACE_NS`, `AGENT_*`, `PLY_CAP`, `STARTING_RATING`, `K_FACTOR`, `ANCHOR_RATING_WINDOW`, `STARTING_FEN` all match §5.2 values exactly.
- `should_offer_anchor`: `True` at exactly 400, `False` at 401, `False` when another option exists.
- `pair_bots` sort key, `b`-advances-`a`-holds walk, `i`-not-incremented-after-removal, one-side-is-enough relaxation, both-anchors-never-pair — all match the §9.2 pseudocode line for line.
- Colour precedence: alternate from `last_color`, then lower `white_count`, then lower `bot_id`.
- No hand-rolled move generation anywhere; `python-chess` throughout.
- 139 tests, no mocks, no fixtures in `chess_core` tests.

**client-engineer spec**

- `choose_move(board: chess.Board, clock: ClockView) -> chess.Move` matches Interfaces Part 3 exactly, in `bot.py` and all three reference bots.
- The baseline does not flag at 3+2: measured avg 16ms / p95 30ms per move over 24 ladder games, 0 flags. `test_baseline_bot_completes_full_games_without_flagging` is a real acceptance gate.
- The baseline makes no illegal moves in either seat.
- The mate-in-one regression test (`test_baseline_bot_plays_mate_in_one`) genuinely fails against the pre-`a8fa86f` bot — fixed-perspective evaluation is correctly parameterised at the root.
- Arena uses `chess_core` for rules, clock and Elo — no forked simplified rules anywhere.
- Opening randomisation is present and seeded; §17's mandate is met.
- Clock is built from the FEN's side to move, not assumed White — with a regression test that names the silent failure it prevents.
- Illegal-move three-strike path is server-owned; no resign-on-illegal in the client (errata 1 respected).
- Flag check precedes move validation in the arena loop, matching §6.4.
- The illegal-move `continue` path charges cumulative time (your prior finding — re-confirmed correct: `deliver_position` returns the clock unchanged, `turn_started_mono` survives).
- `crash` ≠ `illegal_forfeit`, with a test.
- Round-robin schedule alternates colours per pairing, plays exactly `--games` total, spreads the remainder.
- Error prose is actionable throughout `parse_args`, `replay_game`, `load_bot_module`, `export_to_pgn`.
- `load_bot_module`'s `sys.modules` keying handles same-stem bots in different directories.
- PGN export writes `[SetUp]`/`[FEN]` and numbers from the opening; round-trips cleanly through `chess.pgn` (verified, though untested — T7).

**pyproject.toml** — `testpaths = ["tests", "starter-kit/tests"]` collects all 139 with no shadowing; `--strict-markers`; `chess>=1.11` pinned; packages limited to `chess_core*`.

---

## Specified for phases 1–2 and not found anywhere

1. `POLL_RECENCY_NS`, `CHALLENGE_TTL_NS`, `POLL_HOLD_NS`, `TICK_INTERVAL_NS` in `clock.py` (§5.2) — **M2**.
2. `run_arena()`, `ArenaResult`, `ArenaStats`, `HeadToHead` (Interfaces Part 3) — **M5**.
3. Head-to-head win rates in arena output (§17, client spec §3.4 item 6) — **M5**.
4. Illegal-move attempts reported *with the offending position* (§17) — **M5**.
5. `--replay` interactive stepping with clock display (§17, client spec §3.4) — **M5**.
6. A draw arm for `compute_one_sided_exchange` (§10.3) — **M3**; genuinely unspecified, not merely unbuilt.
7. Elo swap-symmetry property test (role spec §7) — unimplementable as written; **T3**.
8. §18's "flag precedes illegal-move validation" test — **T8**.
9. `--report` / `POST /arena-reports` (client spec §3.4, errata 3–4) — plausibly phase 3, noting it here so it is not lost.

Not counted as gaps because they are clearly phase 3: `chess_client/client.py`, `errors.py`, `run.py`, and everything under `chess_server/` and `web/`.

---

## Prioritised — what to change before this lands

1. **C1** — `not board.turn` → `board.turn` at [ref_depth3.py](starter-kit/ref_bots/ref_depth3.py#L120). One token. Then re-run the ladder.
2. **C3** — check `move_result.is_terminal` at the point of application in [arena.py](starter-kit/arena.py#L295), before `ply` is compared to `PLY_CAP`, so the arena matches `transition_after_move`.
3. **T1** — replace `test_ref_depth3_avoids_obvious_blunders` with the queen-hang position from C1. It fails on `a8fa86f` and passes after the fix. Delete or complete `test_seeded_different_seeds_different_pairings`.
4. **C2** — after 1–3, re-measure the ladder and either fix `ref_greedy`/the baseline until the spec's ordering holds, or change the spec. Record the actual command and seed next to the constants, per §10.3.
5. **M1** — delete `random.seed(seed)` from [matchmaker.py](chess_core/matchmaker.py#L37). Decide separately whether `seed` leaves the pinned signature.
6. **M4** — remove the unreachable `can_claim_threefold_repetition()` branch from [rules.py](chess_core/rules.py#L76), or give `validate_and_apply_move` the history it would need. Leaving it is a trap for server-engineer.
7. **M3** — escalate to a spec decision now, not in phase 3.
8. **M2** — add the four constants to `clock.py` and to Interfaces Part 1.
9. **T2, T5, T6, T7** — strengthen four assertions. T7 (PGN from an opening) is the one that has already bitten you once.
10. **M5** — decide which Part 3 arena surface is in scope for phase 2 and either build it or amend the interfaces doc. Head-to-head and the offending FEN are cheap and are §17 requirements.
11. **M6** — either make `bot.py`'s time budget real or delete the variable and the paragraph describing it.
12. Minor items as convenient. `transition_to_terminal`'s missing guard and the dead `is_repetition` calls are the two most likely to mislead a reader on a projector.