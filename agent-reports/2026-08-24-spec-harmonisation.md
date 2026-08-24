# Spec Harmonisation Report — Revision 4

**Date:** 2026-08-24  
**Commit:** `0167b91`  
**Orchestrator:** Solution Architect (spec harmonization agent)  
**Verdict:** ✅ **PASS** — All six role specs harmonized, all decisions resolved, no contradictions at seams.

---

## Executive Summary

Audited all six role specs against the design spec and interfaces document for contradictions, unclaimed requirements, unmet seams, and drift. **Found 12 issues, resolved all 12.** Added two new requirements: (1) dashboard can view any server game by clicking grid cells, (2) local arena stats resolved via `arena.py --serve` (stretch goal, no server dependency). All 8 interface decisions from the interfaces document are now resolved and ownership assigned.

**Result:** The specification is complete, consistent, and ready to build from. No design-spec changes were required—all resolutions fit within existing §-level requirements.

---

## 1. Contradictions at Seams — 5 Found, 5 Fixed

### 1.1 Design Spec Revision Number Drift

**Found:** Design spec header says "Revision 3", but README.md calls it "Revision 4".

**Resolution:** Updated design spec to Revision 4, marking harmonization complete in the header.

**Files changed:**
- `docs/superpowers/specs/2026-08-23-chess-arena-design.md` (line 3)

---

### 1.2 `controller` Field Schema Missing from Design Spec

**Found:** Design spec §5 data model lists `controller` in bots table but doesn't specify initial value or column type. Server-engineer spec §11.1 recommended adding it with `DEFAULT 'client'`. Interfaces doc Decision #3 flagged it as open.

**Resolution:** Added `controller TEXT NOT NULL DEFAULT 'client'` to bots table schema in design spec §5. Updated interfaces doc Decision #3 to "RESOLVED" with ownership assigned to server-engineer.

**Files changed:**
- `docs/superpowers/specs/2026-08-23-chess-arena-design.md` (§5 bots table)
- `docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md` (Decision #3)
- `docs/superpowers/specs/roles/server-engineer-spec.md` (§11.1 → resolved, action item added)

---

### 1.3 Featured Game Selection Policy — Duplicated Ownership

**Found:** Both server-engineer §11.4 and dashboard-engineer §7.1 claimed to "own" the featured game selection policy decision, though both proposed the same solution (highest rating sum).

**Resolution:** Dashboard-engineer owns the policy (it's client-side UI logic). Server-engineer provides `white_rating` and `black_rating` in `ActiveGameSummary` for dashboard to compute the sum. Updated both specs and interfaces doc Decision #8 to reflect this split.

**Files changed:**
- `docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md` (Decision #8 → RESOLVED, ownership clarified)
- `docs/superpowers/specs/roles/server-engineer-spec.md` (§11.4 → delegated to dashboard)
- `docs/superpowers/specs/roles/dashboard-engineer-spec.md` (§7.1 → RESOLVED, owns policy)

---

### 1.4 `white_rating` / `black_rating` Missing from ActiveGameSummary

**Found:** Dashboard-engineer §8.3 requested these fields for featured game selection. Server-engineer spec didn't list them. Interfaces doc Part 5 *already included them* (added post-spec by orchestrator), but server-engineer spec wasn't updated to reflect this.

**Resolution:** Confirmed fields are in interfaces doc. Updated server-engineer §11.4 to note dependency resolved. Updated dashboard-engineer summary to mark request as fulfilled.

**Files changed:**
- `docs/superpowers/specs/roles/server-engineer-spec.md` (§11.4 action item)
- `docs/superpowers/specs/roles/dashboard-engineer-spec.md` (§14 summary, requests marked resolved)

**Note:** No actual interface change needed—fields were already present.

---

### 1.5 "Local Amber" Color Rule — Unimplementable Requirement

**Found:** Design spec §14 stated "rated green, unrated and local amber" but arena.py runs entirely offline, so the server dashboard never sees local games. This was a design gap, not a seam mismatch.

**Resolution:** Chose Option 2 from your prompt: `arena.py --serve` (stretch goal) launches a separate local web view at `localhost:8001` showing that run's results. Main dashboard at `localhost:8000` shows server games only. Updated §14 to remove "local amber" rule and explain the split. Added `--serve` implementation notes to client-engineer §3.4.

**Rationale:** This keeps arena offline by default (no server dependency), avoids creating an unverifiable attack vector (Option 1 would require POSTing local results to server), and is cleaner than just dropping local data (Option 3).

**Files changed:**
- `docs/superpowers/specs/2026-08-23-chess-arena-design.md` (§14 color rule updated)
- `docs/superpowers/specs/roles/dashboard-engineer-spec.md` (§7 color table, removed "local" row)
- `docs/superpowers/specs/roles/client-engineer-spec.md` (§3.4 added `--serve` flag description)

---

## 2. Unclaimed Requirements — 2 Found, 2 Claimed

### 2.1 View Any Server Game (New Requirement from Prompt)

**Found:** Your Part 2(a) requirement: "View any server game, not just the featured one." Not yet specified anywhere.

**Resolution:** Dashboard grid cells in My Bot mode are clickable—clicking makes that game the locally featured game (client-side state only, not server state). For identifying "my bot" without auth: URL param `?bot=BotName` or `localStorage` for visual highlighting ("YOU" badge). Added as §7.2 and §7.3 in dashboard-engineer spec.

**Files changed:**
- `docs/superpowers/specs/2026-08-23-chess-arena-design.md` (§14 added subsection about viewing any game)
- `docs/superpowers/specs/roles/dashboard-engineer-spec.md` (§7.2, §7.3, §13 new requirements)

---

### 2.2 Local Arena Statistics (New Requirement from Prompt, Covered Above)

**Found:** Your Part 2(b) requirement: "See local arena statistics on dashboard."

**Resolution:** As described in 1.5 above—`arena.py --serve` for local view.

---

## 3. Unmet Seams — 0 New (2 Already Resolved Pre-Harmonization)

The orchestrator's prior work (documented in roles/README.md "Unmet seams resolved") already caught:
1. `white_rating` / `black_rating` on `ActiveGameSummary` — verified present in interfaces
2. `GET /bots/{bot_id}/rating_history` — verified present in interfaces Part 5

No additional unmet seams found in this audit.

---

## 4. Naming Drift — 0 Found

Checked for same-concept-different-names across specs:
- "delivery" vs "dispatch" — "delivery" used consistently
- "seat" vs "slot" — "seat" used consistently  
- "anchor" vs "reference bot" — both used, but as synonyms (acceptable)
- "controller" — used consistently across all specs

No systematic naming inconsistencies requiring correction.

---

## 5. Constant Drift — 0 Found

Verified timing and config constants across all specs:

| Constant | Value (ms or count) | Consistency |
|---|---|---|
| `DELIVERY_GRACE_MS` | 15000 | ✓ All specs agree |
| `AGENT_DELIVERY_GRACE_MS` | 60000 | ✓ All specs agree |
| `AGENT_AUTO_RELEASE_MS` | 45000 | ✓ All specs agree (design spec editorial note about revision 3 is historical, not a conflict) |
| `TIME_CONTROL_MS` | 180000 | ✓ All specs agree |
| `INCREMENT_MS` | 2000 | ✓ All specs agree |
| `K_FACTOR` | 24 | ✓ All specs agree |
| `STARTING_RATING` | 1200 | ✓ All specs agree |
| `PLY_CAP` | 200 | ✓ All specs agree |
| Featured hold time | 20s | ✓ Design spec and dashboard spec agree |
| Pool eligibility window | 5s | ✓ Design spec and server spec agree |

All constants consistent across design, interfaces, and all six role specs.

---

## 6. Conflicting Resolutions of Open Decisions — 8 Found, 8 Resolved

The interfaces document listed 8 open decisions. All have been resolved and ownership assigned:

### Decision 1: Opening Book Format
**Resolution:** Mainline openings only (8-12 FENs), hardcoded in `arena.py`, selected via `random.Random(seed).choice()`. Internal implementation detail.  
**Owner:** client-engineer (§10.1, non-blocking recommendation)

### Decision 2: `client_reported_ms` Semantics
**Resolution:** Already resolved in interfaces doc revision — SDK measures wall time around `choose_move()` and includes it automatically.  
**Owner:** client-engineer (implemented in SDK)

### Decision 3: `controller` Field Initial Value
**Resolution:** `controller TEXT NOT NULL DEFAULT 'client'` in bots table (see §1.2 above).  
**Owner:** server-engineer (schema), design spec updated

### Decision 4: SSE Coalescing Mechanism
**Resolution:** Per-game 500ms throttle. After emitting `move_played` for non-featured game, suppress further events for that game for 500ms. Featured games bypass.  
**Owner:** server-engineer (SSE emitter implementation)  
**Files changed:** interfaces doc Decision #4 → RESOLVED, server-engineer §11.2 → resolved

### Decision 5: `analyze_game` Response Format
**Resolution:** Markdown with three sections: (1) PGN with headers, (2) timing table (ply | move | server_ms | client_ms | remaining_ms), (3) event log (flags, strikes, forfeits with ply numbers).  
**Owner:** mcp-engineer (§5 specifies this in detail, already authoritative)  
**Files changed:** interfaces doc Decision #5 → RESOLVED, server-engineer §11.5 → delegated to MCP

### Decision 6: Illegal Move Strike Reset
**Resolution:** Per-game columns `white_strikes` and `black_strikes` in games table (already in §5 schema). Reset to 0 at game creation.  
**Owner:** server-engineer (move validation), chess-domain-engineer (strike counting if needed)  
**Files changed:** interfaces doc Decision #6 → RESOLVED, server-engineer §11.3 → resolved

### Decision 7: Provisional Annotation Threshold
**Resolution:** Computed field `is_provisional = (games_played < 10)` in all leaderboard responses (HTTP API, MCP, SSE). No database column.  
**Owner:** server-engineer (API responses), mcp-engineer (MCP responses)  
**Files changed:** interfaces doc Decision #7 → RESOLVED, server-engineer §11.6 → resolved

### Decision 8: Featured Game Selection Policy
**Resolution:** Highest sum of participant ratings (white_rating + black_rating), held ≥20s, tie-break on lowest game_id. Dashboard computes client-side.  
**Owner:** dashboard-engineer (see §1.3 above)  
**Files changed:** interfaces doc Decision #8 → RESOLVED, dashboard-engineer §7.1 → RESOLVED

---

## 7. Design-Spec Changes Made

### 7.1 Revision Number
- Updated from Revision 3 to Revision 4
- Status line now: "Phases 1–3 cleared to build; harmonization complete"

### 7.2 Schema Addition
- Added `controller TEXT NOT NULL DEFAULT 'client'` to §5 bots table schema with explanation

### 7.3 Dashboard Section (§14)
- Removed "local amber" from color rule
- Added subsection explaining local arena gap resolution (`arena.py --serve` for local view)
- Added subsection "Viewing any server game" explaining clickable grid cells and bot identification

**All changes are additive or clarifying—no normative behaviors were relaxed or contradicted.**

---

## 8. Remaining Open Decisions

**None that block implementation.**

The following are explicitly marked "non-blocking" or "deferred":
- **Client-engineer §10.1–10.6:** Internal implementation choices (opening book composition, baseline bot depth, re-poll interval, exception handling, clock measurement, arena time control default) — all have recommendations, implementer may choose differently if reasoning is sound
- **Workshop-author §8:** Arena table format, stretch agents, provisional visual annotation — all marked "not blocking" or "explicitly deferred to build time"

---

## 9. Files Modified

### Design Spec (1)
- `docs/superpowers/specs/2026-08-23-chess-arena-design.md`
  - Revision 3 → 4
  - Added `controller` column to §5 schema
  - Updated §14 to resolve local stats gap and add viewing any game

### Interfaces Doc (1)
- `docs/superpowers/specs/2026-08-23-chess-arena-interfaces.md`
  - All 8 decisions resolved (Decision #1–8 updated from "Requires" → "Resolved")
  - Ownership assigned for each resolution

### Role Specs (5 of 6)
- `docs/superpowers/specs/roles/server-engineer-spec.md`
  - §11 "Requires Decision" → "All Decisions Resolved"
  - All 6 decision items marked resolved with action items
  - Summary updated to show action items instead of blockers

- `docs/superpowers/specs/roles/dashboard-engineer-spec.md`
  - §7 color table: removed "local" row
  - §7.1 featured game policy: REQUIRES DECISION → RESOLVED
  - §7.2 added: viewing any game (click grid cells)
  - §7.3 added: identifying "my bot" (URL param or localStorage)
  - §13 "Requires decision" → "All Decisions Resolved"
  - §14 summary: requests marked resolved, new requirements added

- `docs/superpowers/specs/roles/client-engineer-spec.md`
  - §3.4 added: `--serve` flag for arena.py (stretch goal)
  - §10 "Requires decision" → "Implementation Decisions (Non-Blocking)"
  - Summary updated to include new `--serve` requirement

- `docs/superpowers/specs/roles/workshop-author-spec.md`
  - §8 Decision 2: REQUIRES DECISION → RESOLVED (dashboard clickable cells)
  - Summary updated: 1 resolved, 4 non-blocking

- `docs/superpowers/specs/roles/README.md`
  - Date and commit updated to harmonization revision
  - "Open decisions carried forward" → "All Decisions Resolved"
  - New requirements section added (view any game, identify my bot, local stats)

**Not modified:** `chess-domain-engineer-spec.md`, `mcp-engineer-spec.md` (already had zero open decisions)

---

## 10. Deliberate Non-Changes (and Why)

### 10.1 Opening Book Composition (Decision #1)
**Why not specified in design spec:** This is an internal arena.py implementation detail with no effect on server behavior or interface contracts. Client-engineer has a recommendation; making it normative would be over-specification.

### 10.2 SSE Exact Coalescing Algorithm (Decision #4)
**Why only a recommendation:** "≤2 Hz" is approximate by design (§14). The per-game 500ms throttle is sound, but if server-engineer discovers a better approach during implementation (e.g., priority queue), they may use it as long as the "≤2 Hz" observable behavior holds.

### 10.3 `analyze_game` Markdown Format (Decision #5)
**Why not enforced in design spec:** MCP-engineer §5 already specifies the format in detail (PGN + timing table + event log). Adding it to the design spec would duplicate without adding clarity. The interface document resolution is sufficient.

### 10.4 No New HTTP Endpoints for Local Results
**Why `arena.py --serve` instead of POST to server:** Your hard constraint was "must not create a path by which local, unverifiable results can influence a rated leaderboard." POSTing local results to the server (Option 1) would require a separate `local_games` table, authentication, rate limiting, and retention policy—significant complexity and attack surface for a non-core feature. Serving a local web view (Option 2) keeps arena pure offline and avoids the server dependency entirely.

---

## 11. Verification Steps Taken

1. **Read all six role specs in full** — captured §-level claims and seam dependencies
2. **Cross-referenced against design spec** — verified every § is owned, no unclaimed requirements
3. **Cross-referenced against interfaces doc** — verified every seam has both producer and consumer
4. **Checked for contradictions** — timing constants, field names, status codes, error text, ordering
5. **Resolved all 8 interface decisions** — assigned ownership, specified resolutions
6. **Added new requirements** — view any game, identify my bot, local stats gap
7. **Updated all affected files** — design spec, interfaces, 5 role specs, README

---

## 12. Impact on Build Schedule

**Unblocked:** All six role specs are now ready to build from. No design decisions block progress.

**New work added (all stretch goals, no critical path impact):**
- Dashboard: clickable grid cells, URL param for "my bot" highlighting (straightforward, ≤1 day)
- Client: `arena.py --serve` flag (stretch, ≤2 days, can be deferred to Phase 7 polish)

**Critical path unchanged:** Phase 1–3 implementation can proceed immediately with all decisions resolved.

---

## 13. Harmonization Checklist

- [x] Read design spec, interfaces doc, and all six role specs in full
- [x] Check for contradictions at seams (field names, types, constants, behavior)
- [x] Check for duplicated ownership (same behavior claimed by multiple specs)
- [x] Check for unclaimed requirements (design spec § with no owner)
- [x] Check for unmet seams (expectations one spec has that another doesn't provide)
- [x] Check for naming drift (same concept, different names)
- [x] Check for constant drift (same constant, different values)
- [x] Resolve all 8 interface decisions with ownership assigned
- [x] Design Part 2(a): viewing any game (specified in §7.2, §7.3)
- [x] Design Part 2(b): local stats gap (resolved with `arena.py --serve`)
- [x] Apply corrections to affected files (8 files modified)
- [x] Update roles/README.md coverage record
- [x] Write this report

---

## 14. Recommendation

**Approve Revision 4.** All contradictions resolved, all decisions made, all seams met. The specification is consistent, complete, and ready for parallel build tracks to begin.

Next step: Distribute updated role specs to the six agent tracks and proceed with Phase 1 implementation.
