# Spec Review — Round 5 (scoped verification of the round-4 fix pass)

| | |
|---|---|
| **Reviewed** | `2026-08-23-chess-arena-design.md` (rev 5), `2026-08-23-chess-arena-interfaces.md`, the six role specs in `docs/superpowers/specs/roles/` plus `roles/README.md`, `AGENTS.md`. Scope limited to verifying [round 4](2026-08-24-spec-review-round4.md)'s 8 criticals and 14 must-change items, cross-document consistency on the surface round 4 touched, and phase 1–2 readiness. |
| **Commit** | `98eac23` |
| **Date** | 2026-08-24 |
| **Reviewer role** | `design-adversary` — scoped verification, not a fresh attack |
| **Verdict** | **Phases 1 and 2 are not yet buildable. Two blockers, both small.** The flag predicate (round-4 item 8) was changed in no document body and the one errata that states it correctly defers to the design spec, which states it wrongly. The matchmaker algorithm (item 10) is unchanged. Every other phase-1 input is now correct. **Phases 3–8 not assessed**, per brief. |

**Fix verification:** of round 4's 14 must-change items — **3 Fixed, 6 Partially fixed, 5 Not fixed, 0 Regressed.** Of the 8 criticals — 2 Fixed, 5 Partially fixed, 1 Not fixed.

**One new defect inside scope, found by execution:** item 9's fix does not work. `bot_id INTEGER PRIMARY KEY NOT NULL` still accepts a `NULL` insert in SQLite 3.51.3 — the rowid alias is auto-assigned *before* the `NOT NULL` constraint is evaluated. The design spec now asserts a protection it does not have. §5 gives the DDL that does work.

The brief's prediction was right: **the design spec is ahead of the interfaces document and the role-spec bodies.** Where a fix landed, it landed in `2026-08-23-chess-arena-design.md` and in the errata blocks. The interfaces document received almost nothing.

---

## 1. Round-4 fix verification

### 1.1 The eight criticals

| # | Critical | Status | Justification |
|---|---|---|---|
| R4-C1 | Nothing calls delivery | **Partially fixed** | `server-engineer` errata 1 names both call sites and states the ticker never delivers — correct and sufficient for the server track. But design §6.2 and §13.3 still say delivery happens on `get_game()` **and** `get_legal_moves()`, contradicting both errata that say `get_game()` never delivers (§2.1 below). Design §6.2's body still never names the poll as a call site; only §8.1's `DELIVERS (§6.2)` annotation does. |
| R4-C2 | Ticker deadlocks on nested `write_lock` | **Partially fixed** | Design §4.1 now carries the `_locked` rule normatively and explains the silent-wedge mechanism; `server-engineer` errata 2 restates it. The *remediation* half is only half-applied: design §4.6 now cancels and restarts a stalled ticker, but `server-engineer`'s `supervise_ticker()` still only calls `logger.critical`, with no errata item correcting it. Its `_tick()` body still calls `abort_game`/`finalise_game`/`deliver_position` — the exact deadlocking pseudocode — with no inline marker. |
| R4-C3 | Control-handoff routes have no producer | **Partially fixed** | Ownership is now unambiguous: design §8.1 lists all four and states `server-engineer` owns every route; `roles/README.md` line 29 assigns §13.3's server half; `mcp-engineer` errata 1 and `server-engineer` errata 4 agree. But the *contract* still does not exist: interfaces Part 5 has models for `GET /bots/{bot_id}/rating_history` only — `POST /bots/me/control`, `GET /bots/me` and `GET /games/{id}/moves` have no request/response model anywhere, and `server-engineer` §4.1's endpoint inventory still lists none of the four. |
| R4-C4 | SSE emitted inside the transaction | **Fixed** | Design §4.1 states the rule normatively, covers `ROLLBACK TO SAVEPOINT`, and pins `seq` assignment to flush time in commit order. `server-engineer` errata 3 restates it. |
| R4-C5 | `controller` check dropped on challenge consumption | **Partially fixed** | Design §13.3 is correct (unchanged) and `server-engineer` errata 6 restores it for creation *and* consumption. The `_tick()` step-1 pseudocode still checks seats only. No document states the `expired` reason string round 4 proposed. |
| R4-C6 | `rated` broadcast as true during play | **Partially fixed** | Design §5.3 is now exactly right, including the "rule 1 can only move `rated` from 1 to 0" clause, and `server-engineer` errata 5 restates it. `_create_game` still writes `'rated': 1,  # will be recomputed at finalisation` and `finalise_game` still says "Determine `rated` per §5.1 rules". No test was added. |
| R4-C7 | XSS in the dashboard | **Fixed** | Design §14 carries both layers — server-side charset `^[A-Za-z0-9 _-]{1,32}$` on all four fields, and mandatory `textContent` for every attendee-controlled string including `?bot=` — with the reasoning for keeping both. `server-engineer` errata 9 and `dashboard-engineer` errata 2 restate their halves. Minor: §14 cites §8.5 for the charset, and §8.5 does not mention it; the interfaces models still carry no validation and no `422`. |
| R4-C8 | Flag predicate stated two ways | **Not fixed** | See §2.2. Not one document body changed. The single correct statement is `chess-domain-engineer` errata 1, and that same errata block says the design spec wins where they conflict — and the design spec says `< 0`. |

### 1.2 The fourteen must-change items

| # | Item | Status | One line |
|---|---|---|---|
| 1 | Name the delivery trigger | **Partially fixed** | Named in `server-engineer` errata 1 and design §8.1; design §6.2/§13.3 still contradict it on `get_game()`. |
| 2 | One lock acquisition per call stack; supervisor acts | **Partially fixed** | Lock rule fixed in design §4.1; supervisor action fixed in design §4.6 but not in `server-engineer`'s supervisor pseudocode. |
| 3 | Four routes into §8.1, interfaces Part 5 and the route list | **Partially fixed** | §8.1 ✓, `roles/README.md` ✓; interfaces Part 5 ✗ (3 of 4 missing), `server-engineer` §4.1 ✗ (4 of 4 missing). |
| 4 | Buffer SSE, flush after commit | **Fixed** | Design §4.1. |
| 5 | `controller='client'` on challenge creation and consumption | **Partially fixed** | Errata only; `_tick()` body unchanged. |
| 6 | `rated` written at creation | **Partially fixed** | Design §5.3 ✓; `_create_game`/`finalise_game` bodies unchanged. |
| 7 | Constrain names; mandate `textContent` | **Fixed** | Design §14 plus both errata; interfaces models not updated (minor). |
| 8 | Flag predicate `<= 0` in §6.4, §18 and `chess-domain-engineer` §3 | **Not fixed** | Zero of three changed. **Phase-1 blocker.** |
| 9 | `seats.bot_id … NOT NULL` | **Not fixed** | Applied to design §4.3, not to `server-engineer`'s DDL — **and the applied form does not work.** Verified, §5. |
| 10 | Matchmaker rule 2 as pseudocode; one relaxed side suffices | **Not fixed** | No pseudocode in any document; the skip-walk ambiguity is untouched. **Phase-1 blocker.** |
| 11 | Delete `--serve` and `dashboard-engineer` §13's stale bullets | **Fixed** | Verified, §2.6. |
| 12 | Repair the interfaces document's three corrupted regions | **Not fixed** | All three intact and verified, §2.4. |
| 13 | Add `fen`, `to_move`, `status` to `ActiveGameSummary` | **Not fixed** | And `dashboard-engineer` errata 5 asserts the change was made. §2.5. |
| 14 | Constants table; fix §5.1 rule 4's predicate | **Partially fixed** | Table added at §5.2 and rule 4 now tests `RATED_TIME_CONTROL_NS` ✓; the abolished names survive in three sections of the same document. §2.7. |

---

## 2. Cross-document consistency on the changed surface

### 2.1 Delivery triggers — one contradiction, in both directions

| Document | `GET /bots/me/turn` | `get_legal_moves()` | `get_game()` | Ticker |
|---|---|---|---|---|
| design §6.2, §13.3 | delivers | delivers | **delivers** | — |
| design §8.1 | `DELIVERS (§6.2)` | — | — | — |
| `server-engineer` errata 1 | delivers | delivers | **never delivers** | never delivers |
| `mcp-engineer` errata 2 | — | sole agent trigger | **never delivers** | — |
| `mcp-engineer` §7 body (l. 608, 627) | — | delivers | **delivers** | — |

Two role specs adopted round 4's item 19 — make `get_legal_moves()` the sole agent trigger — and the design spec did not. Both errata blocks open with "where this spec and design spec revision 5 conflict, **the design spec wins**", which selects the version they were written to overturn. `get_game()` is annotated `readOnlyHint`, so under the design spec's wording Claude starts a rated clock without seeking permission. Out of phase 1–2 scope, but it must be resolved in the design spec, not in errata, before phase 5.

### 2.2 The flag predicate — the phase-1 blocker

| Location | Statement |
|---|---|
| design §6.4 step 3 (normative) | `if remaining < 0 -> flag` |
| design §18 | "flag on **exact zero**" |
| interfaces, `account_move_and_switch` docstring step 3 | `if remaining < 0 -> flag` |
| interfaces Part 4, test convention 1 | "Flag on exact zero" |
| `chess-domain-engineer` errata 1 | "`remaining_ns <= 0`, not `< 0`" |
| `chess-domain-engineer` §2.2 step 3 | `if remaining < 0 → flag` |
| `chess-domain-engineer` §3 | "Flag on **exact zero** — not '≤ 0', strictly `< 0` after deduction" |
| `chess-domain-engineer` §7 test list | "Flag on exact zero — `remaining = 0` after deduction → flagged" |
| `server-engineer` §2.2 flag detection, §5 step 4, §7 table | `remaining_ns < 0` (no errata correction) |

Executed: with `elapsed == remaining`, `remaining - elapsed == 0`; `< 0` yields `flagged=False`, `<= 0` yields `flagged=True`. The named test and the normative rule cannot both pass.

This is now **worse than round 4 found it**, not merely unfixed. Round 4's version was a contradiction between documents. Revision 5 added, to the top of the one document that owns this module, an errata that states the correct predicate and, three lines above it, a precedence rule that resolves the conflict in favour of the wrong one. An implementer who reads the errata carefully and obeys it implements `< 0`.

`chess-domain-engineer` §3's sentence — "not '≤ 0', strictly `< 0`" — is the exact string round 4 quoted. It survived a fix pass that claims in its own changelog header to have changed it.

### 2.3 `_locked` discipline and SSE buffering

Both rules are correctly and fully stated in design §4.1, which is the right place — they are concurrency invariants and §4 is normative. `AGENTS.md` carries both as invariants, correctly worded. `server-engineer` errata 2 and 3 restate them.

The gap is that `server-engineer` §2.2's pseudocode is the round-4 text verbatim: `_tick()` opens `critical_section` and then calls the outer `abort_game`, `finalise_game`, and `mailbox.deliver_position` (which opens its own `critical_section`), and `emit_sse` is called inline at six sites. A plan author working from the errata will write it correctly. One working from the code blocks — which is what code blocks are for — will reproduce the deadlock. Recommend deleting the stale pseudocode rather than leaving it to be overridden.

Separately, `mailbox.deliver_position(bot_id, payload, now_mono)` and `chess_core.clock.deliver_position(clock, now_mono, ply)` are two different functions with one name, and `server-engineer` imports both.

### 2.4 The four new routes, and the interfaces document's corruption

Interfaces Part 5 contains models for none of `GET /bots/me`, `POST /bots/me/control`, `GET /games/{id}/moves`. `MyBotResult` exists only in Part 6 as an MCP tool return. Design §8.1 specifies `POST /bots/me/control {action: "take" | "release"}`; round 4 and `mcp-engineer` §7.1 use `{mode: ...}`. Nothing pins the shape.

Round-4 item 12 (repair the corrupted regions) is **Not fixed**; all three verified at this commit:

- `EVENT_ARENA_REPORT_POSTED = "arena_report_posted"` duplicated at lines 601–602; the `arena_report_posted` payload block duplicated at 795 and 820.
- `### Arena Reports` (line 1543) is still spliced into the body of `class ResetResponse` (line 1540) inside an unterminated code fence; `wiped_rating_history`, `wiped_seats`, `wiped_mailboxes`, `reset_bots` reappear as orphaned text at line 1604, sixty lines later.
- The Decisions section still interleaves 5–8: Decision 7's issue is followed by Decision 6's resolution, and line 1896 runs Decision 7's owner line into Decision 8's resolution on a single line. Four `Resolution:**` fragments are missing their opening `**`.

This is the pinned-seams document and it is the one that received the least of the fix pass.

### 2.5 `rated` at creation, and `ActiveGameSummary`

`rated` written at creation: design §5.3 is correct and well-argued. Not propagated to `server-engineer`'s two pseudocode sites.

`ActiveGameSummary` (interfaces Part 5) still carries no `fen`, no `to_move`, no `status` — item 13 **Not fixed**. `dashboard-engineer` errata 5 states: "`ActiveGameSummary` now carries `fen`, `to_move` and `status` — render from those rather than reconstructing." **The errata asserts a change to another document that was not made.** A plan author would build the dashboard against fields that do not exist, and `dashboard-engineer` §7.1's `activeGames.filter(g => g.status === 'active')` still matches nothing. An errata that misreports the state of a seam is worse than no errata; it defeats the reason for checking.

### 2.6 `seats.bot_id`, arena-report ordering, `--serve`

- **`seats.bot_id NOT NULL`** — design §4.3 changed, `server-engineer` §2.2's DDL not (errata 7 covers it). Both are moot: the fix does not work. See §5.
- **Arena-report retention ordering** — design §5 is now exactly right and says `id DESC` in the prune *and* the read, with the verification recorded. `server-engineer` errata 8 restates it. **Interfaces Part 5 still says of `GET /bots/{bot_id}/arena-reports`: "Returns most recent 20 reports, ordered by `created_at` descending."** Executed at this commit against 25 rows with one timestamp: `ORDER BY created_at DESC LIMIT 20` returns ids 1–20 (the oldest); `ORDER BY id DESC LIMIT 20` returns 25–6. The interfaces document still specifies the defective read. Partially fixed.
- **`arena.py --serve`** — **Fixed.** The only surviving occurrences are three negations (`client-engineer` errata 3 and §11, `dashboard-engineer` errata 4) and one strikethrough in `roles/README.md`. `--report` → `POST /arena-reports` is the single normative path in design §14, §17, and `client-engineer` §11. `dashboard-engineer` §13 was rewritten; the "remove all local amber" and "`--serve` (stretch)" bullets are gone.

### 2.7 Constants

Design §5.2 is a genuine improvement: one table, one declaration site per constant, `_NS` internal / `_ms` at the boundary, and §5.3 rule 4 now tests `time_control_ns != RATED_TIME_CONTROL_NS`. That closes R4-S12's substance.

The abolished names were not purged, including from the document that abolishes them:

| Location | Uses |
|---|---|
| design §6.3 | `DELIVERY_GRACE_MS = 15000`, `AGENT_DELIVERY_GRACE_MS = 60000` |
| design §11 | `TIME_CONTROL_MS=180000`, `INCREMENT_MS=2000` — the two names §5.2 says do not exist |
| design §13.3 | `AGENT_AUTO_RELEASE_MS = 45000`, `AGENT_DELIVERY_GRACE_MS` |
| `server-engineer` §2.2 | `RATED_TIME_CONTROL_MS`, `RATED_INCREMENT_MS` in `_create_game`'s caller |
| `chess-domain-engineer` §1 | "client-engineer uses your constants (`TIME_CONTROL_MS`, `INCREMENT_MS`…)" |
| interfaces l. 363 | `grace_ns: … (DELIVERY_GRACE_MS or AGENT_DELIVERY_GRACE_MS)` |

Also: interfaces `chess_core/clock.py` declares seven constants; design §5.2 assigns eleven to `clock.py`. `POLL_RECENCY_NS`, `CHALLENGE_TTL_NS`, `POLL_HOLD_NS` and `TICK_INTERVAL_NS` are in the table and in no interface. Phase-1 work can proceed from §5.2's values, but the interfaces list should be completed rather than left as a second, shorter source of truth.

### 2.8 Input validation and `textContent`

Consistent across design §14, `server-engineer` errata 9 and `dashboard-engineer` errata 2, including the same regex in all three. Semantic validation of the arena payload is stated in design §5 and in both role errata. The display-only grep test is now specified as enforcement in design §5 rather than asserted in prose. This item was applied coherently — it is the only one of the fourteen for which that is true across all three layers.

---

## 3. Is the errata-block approach sufficient?

**For four of the six role specs, yes. For `chess-domain-engineer`, no — and that is the one that blocks phase 1.**

The approach works where an errata item *adds* a rule that the body simply lacks, or corrects an ownership claim: `server-engineer` errata 2, 3, 4 and 9, `mcp-engineer` errata 1, `client-engineer` errata 1–6, `dashboard-engineer` errata 2. A plan author reading top-down gets the right answer.

It fails in three identifiable ways, all present here:

1. **When the errata contradicts the design spec and defers to it.** Every errata block ends its header with "the design spec wins". `chess-domain-engineer` errata 1 (`<= 0`) and `mcp-engineer` errata 2 (`get_game()` never delivers) both contradict design revision 5, so the precedence rule cancels them. Both are self-defeating as written. An errata may only correct a role spec *toward* the design spec; when the design spec is the thing that is wrong, the design spec must change.
2. **When the errata misreports another document.** `dashboard-engineer` errata 5 claims `ActiveGameSummary` carries `fen`/`to_move`/`status`. It does not.
3. **When the errata is an instruction rather than a specification.** `chess-domain-engineer` errata 2 says rule 2 "must be written as explicit pseudocode". It does not supply the pseudocode, and neither does design §9.2. The blocker is restated, not removed.

A fourth, milder problem: where an errata overrides a code block, the code block still reads as authoritative to anyone who skips to the implementation section — `_tick()`, `_create_game`, `supervise_ticker` and the `seats` DDL are all still the defective revision-4 text. For pseudocode specifically, delete rather than override.

`workshop-author-spec.md` has no errata block at all. Correct for phase 8, but it maintains `AGENTS.md`, which is currently consistent with revision 5 and should be re-checked when the flag predicate and constants are settled.

---

## 4. Phase 1 and 2 readiness

### Phase 1 — `chess_core` — **No. Two blockers.**

**Blocker 1 — the flag predicate (§2.2 above).** Four documents say `< 0`, three say exact zero, one says both in the same sentence, and the errata that says `<= 0` defers to the document that says `< 0`. `chess-domain-engineer` §7's named test contradicts `chess-domain-engineer` §3's stated rule. This is the module the design spec isolates precisely because being wrong here is silent, and it is the single line where being wrong is silent. **One character, in five places:** design §6.4 step 3, design §18, interfaces `account_move_and_switch` docstring step 3 and Part 4 test convention 1, `chess-domain-engineer` §2.2 step 3, and delete the "not '≤ 0', strictly `< 0`" clause from §3. `server-engineer` §2.2/§5/§7 should stop restating the predicate at all and call `chess_core` instead.

**Blocker 2 — the matchmaker (round-4 item 10).** Design §9.2 rule 2 is unchanged prose: "Walk the sorted list pairing **adjacent** entries. Skip a candidate pair if same `owner`, or if it repeats `last_opponent_id`; try the next adjacent candidate instead." After skipping `(1,2)`, "the next adjacent candidate" is still either `(1,3)` or `(2,3)` — different ladders, and §18 puts this function under seeded-determinism TDD, so the ambiguity is directly a test that cannot be written. "One relaxed side suffices" now appears once, in `chess-domain-engineer` errata 2, phrased as an instruction to write it down. `pair_bots`'s `seed` parameter is still used by no step (round-4 over-engineering 5).

Everything else phase 1 needs is in place: `rules.py`, `elo.py` and `match.py` are unambiguous across design, interfaces Part 1 and `chess-domain-engineer`; §5.2 gives every constant one name and value; `PoolEntry` carries `unpaired_ticks` in both interfaces and the role spec; the K=24 zero-sum property test is achievable under integer rounding.

### Phase 2 — `arena.py` + starter kit — **No. Inherits blocker 1 only.**

The arena runs the same `chess_core.clock`, so it cannot be built ahead of the flag predicate. It does not need `matchmaker.py`.

Phase 2's own inputs are otherwise ready. `--serve` is gone and `--report` is the single normative path (§2.6). `client-engineer`'s errata resolves the resign-on-illegal contradiction, adds supersede back-off, requires `join_code`, and pins the arena-report semantic validation to match the server's. Opening randomisation is mandated in design §17. The one caveat is presentational: `SubmitArenaReportRequest` is inside the corrupted region of interfaces Part 5 (§2.4) — the fields are legible, but the code fence is unterminated and the section sits inside another class body.

**Phases 3–8 not assessed**, per the brief. Note only that the five Not-fixed and six Partially-fixed items above concentrate in the interfaces document and the `server-engineer`/`dashboard-engineer` bodies, which is where phase 3b onward will be built from.

---

## 5. New defect inside scope: the `seats.bot_id` fix does not work

Round-4 item 9 asked for `NOT NULL`. Design §4.3 now declares:

```sql
bot_id  INTEGER PRIMARY KEY NOT NULL REFERENCES bots(id),
```

and adds a paragraph explaining that `NOT NULL` "is load-bearing and is not decoration". Executed against `sqlite3` 3.51.3, with `PRAGMA foreign_keys = ON`:

| DDL | `INSERT INTO seats(bot_id, game_id) VALUES (NULL, 7)` |
|---|---|
| `bot_id INTEGER PRIMARY KEY REFERENCES bots(id)` (revision 4, still in `server-engineer` §2.2) | **accepted**, stored as `(1, 7)` |
| `bot_id INTEGER PRIMARY KEY NOT NULL REFERENCES bots(id)` (**design revision 5**) | **accepted**, stored as `(1, 7)` |
| `bot_id INTEGER NOT NULL PRIMARY KEY REFERENCES bots(id)` | **accepted**, stored as `(1, 7)` |
| `bot_id INTEGER PRIMARY KEY REFERENCES bots(id) CHECK(bot_id IS NOT NULL)` | **accepted**, stored as `(1, 7)` |
| `bot_id INTEGER NOT NULL PRIMARY KEY REFERENCES bots(id) … ) WITHOUT ROWID` | **rejected** — `NOT NULL constraint failed: seats.bot_id` |
| `bot_id INTEGER NOT NULL UNIQUE REFERENCES bots(id)` (no `PRIMARY KEY`) | **rejected** — `NOT NULL constraint failed: seats.bot_id` |

For a rowid-alias column SQLite substitutes the next rowid for `NULL` *before* constraint evaluation, so `NOT NULL` and even a `CHECK` never see the `NULL`. The failure round 4 described is unchanged under revision 5's DDL: the phantom row takes bot 1's seat, `PRAGMA foreign_key_check` reports clean, and bot 1 can never be paired again — verified again here, the subsequent `INSERT … VALUES (1, 7)` fails `UNIQUE constraint failed: seats.bot_id`.

**Fix.** Append `WITHOUT ROWID` to the `seats` DDL in design §4.3 and in `server-engineer` §2.2. Verified to preserve everything the table exists for: `NULL` rejected, second seat for the same bot rejected with `UNIQUE constraint failed`, seat for a nonexistent bot rejected with `FOREIGN KEY constraint failed`. `bot_id INTEGER NOT NULL UNIQUE` (dropping `PRIMARY KEY`) works identically; `WITHOUT ROWID` is the smaller diff and keeps the "primary key makes it a storage-layer invariant" argument intact. Replace §4.3's paragraph about `NOT NULL` being load-bearing with one about `WITHOUT ROWID` being load-bearing — the current paragraph teaches the wrong lesson on a projector, which is worse than saying nothing.

Add a schema test: a `NULL` `bot_id` insert must raise.

---

## 6. Verification method

Executed against `sqlite3` 3.51.3 / CPython at commit `98eac23`. Nothing in §5 or §2.6 was inferred.

| Test | Result |
|---|---|
| Six `seats` DDL variants vs `INSERT … VALUES (NULL, 7)` | Four accept (including design revision 5's), two reject — table in §5 |
| `WITHOUT ROWID` variant: second seat for bot 1; seat for nonexistent bot 99 | `UNIQUE constraint failed`; `FOREIGN KEY constraint failed` — both invariants preserved |
| Interfaces Part 5's `ORDER BY created_at DESC LIMIT 20`, 25 rows one timestamp | Returned ids 1–20, the twenty **oldest**, labelled "most recent" |
| Same data, `ORDER BY id DESC LIMIT 20` | Returned 25–6, correct |
| `elapsed == remaining` under `< 0` and under `<= 0` | `flagged=False` / `flagged=True` — the two predicates are not equivalent at the boundary the named test asserts |
| Grep for `--serve` across all nine documents | Four hits, all negations or a strikethrough — item 11 confirmed Fixed |

---

## 7. What the fix pass got right

Short, and only where verified.

- **Design §4.1 is now the strongest section in the set.** The `_locked` rule and the SSE-after-commit rule are both stated with the mechanism of failure, not just the rule, and both name that the failure is silent. `AGENTS.md` carries them as invariants in the same words.
- **§5.2's constants table** does what round 3 and round 4 both asked for, and §5.3 rule 4 now tests a symbol that exists. The remaining drift is textual residue, not a second opinion.
- **§5.3's `rated`-at-creation rewrite** is better than the recommendation: it states the direction of the override ("rule 1 can only ever move `rated` from 1 to 0, never back"), which is the part an implementer would otherwise have to infer.
- **The arena-report retention paragraph in §5** records the SQLite verification inline, so the next person to touch it cannot revert it by reasoning.
- **Input validation and escaping (§2.8)** is the one item applied coherently across the design spec and both affected role specs, with the two-layer argument written down.

---

## 8. Prioritised recommendations

### Before phase 1 begins — four edits, none large

1. **`remaining <= 0`** in design §6.4 step 3 and §18; interfaces `account_move_and_switch` docstring step 3 and Part 4 convention 1; `chess-domain-engineer` §2.2 step 3, and delete §3's "not '≤ 0', strictly `< 0`" clause. *(Blocker 1.)*
2. **Write matchmaker rule 2 as pseudocode in design §9.2**, resolving the skip-walk and stating that relaxing one side of a blocked pair suffices; copy it into `chess-domain-engineer` §2.4 and interfaces `pair_bots`. Drop the unused `seed`. *(Blocker 2.)*
3. **`WITHOUT ROWID` on `seats`** in design §4.3 and `server-engineer` §2.2, and rewrite §4.3's `NOT NULL` paragraph. *(§5.)*
4. **Fix the two self-defeating errata items** — `chess-domain-engineer` 1 and `mcp-engineer` 2 — by changing the design spec, then restating the errata as agreement rather than override. Correct `dashboard-engineer` errata 5, which misreports a seam.

### Before phase 3b — carried from round 4, plus what this round adds

5. Resolve `get_game()` delivery in **design §6.2 and §13.3**, not in errata. *(§2.1.)*
6. Add models for `GET /bots/me`, `POST /bots/me/control` and `GET /games/{id}/moves` to **interfaces Part 5** and to `server-engineer` §4.1's inventory, and pin the control request shape (`{action}` vs `{mode}`). *(Item 3.)*
7. Add `fen`, `to_move`, `status` to `ActiveGameSummary`. *(Item 13.)*
8. Repair the interfaces document's three corrupted regions. *(Item 12.)*
9. Correct the arena-reports read ordering in **interfaces Part 5** to `id DESC`. *(§2.6.)*
10. **Delete the stale pseudocode** in `server-engineer` §2.2 — `_tick`, `_create_game`, `supervise_ticker`, `deliver_position`, and the `seats` DDL — rather than leaving errata to override it, and rename one of the two `deliver_position`s. Same for `client-engineer` §6's resign clause and `chess-domain-engineer` §3's flag sentence. Errata that overrides prose is workable; errata that overrides a code block is not, because the code block is what gets copied.
11. Purge `TIME_CONTROL_MS`, `INCREMENT_MS`, `*_GRACE_MS`, `AGENT_AUTO_RELEASE_MS` and `RATED_TIME_CONTROL_MS` from design §6.3, §11, §13.3, `server-engineer` §2.2, `chess-domain-engineer` §1 and interfaces line 363; complete interfaces' `clock.py` constant list to §5.2's eleven. *(§2.7.)*
12. Propagate items 5 and 6 (controller check, `rated` at creation) into `server-engineer`'s bodies when 10 is done, and give `server-engineer`'s supervisor the cancel-and-restart action design §4.6 now specifies.

### Process

13. **Re-run this verification against the interfaces document specifically after the next fix pass.** Of the fourteen items, the design spec received twelve and the interfaces document received one. Two consecutive rounds have now found that the pinned-seams document is the least-maintained document in the set, which inverts its purpose.
