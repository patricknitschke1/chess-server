# Open questions

Decisions taken without review, each with what was built and why, so you can
confirm or redirect cheaply. Nothing here is blocking.

**The scope reduction closed four of the eight questions that were here before.**
Those are listed at the bottom rather than deleted, so it is clear they were
answered rather than forgotten.

---

## 1. Should anchors appear on the leaderboard?

**Currently: no.** Anchors carry `role='anchor'` and the leaderboard filters to
`role='competitor'`, which leaves `LeaderboardEntry.is_anchor` a field that is
always false.

For showing them: an attendee at 1150 learns more from seeing `ref-greedy 1000`
and `ref-depth3 1200` on the same board than from a number in isolation. That is
what anchors are for.

Against: they never move, and they push real attendees down a projector with
limited rows.

**Recommendation:** show them, visually distinguished. It is a one-line filter
change plus whatever the dashboard does with the flag.

---

## 2. Anchor identity: `owner='server'`

Anchors need an owner and nothing specified one. I used `'server'`, and
registration now rejects that owner and the three anchor names, case-folded, so
an attendee cannot impersonate a reference bot.

Fine unless you wanted them attributed to you by name.

---

## 3. Numbers I invented

| Constant | Value | Where | Basis |
|---|---|---|---|
| `REGISTER_PER_IP_PER_MIN` | 10 | `api/rate_limit.py` | §8.5 gives the rule, not the number |
| `FEATURED_HOLD_NS` | 20s | `api/featured.py` | how long a board stays on the projector |
| `DISCONNECT_AFTER_NS` | 30s | `engine/ticker.py` | the `bot_disconnected` edge |
| `SUPERVISOR_PERIOD_SECONDS` | 2 | `engine/supervisor.py` | how often the supervisor decides |
| `CANCEL_WAIT_SECONDS` | 5 | `engine/supervisor.py` | before refusing to respawn a ticker |
| `PROBE_TIMEOUT_SECONDS` | 1 | `engine/supervisor.py` | `db_writable` probe timeout |

`REGISTER_PER_IP_PER_MIN = 10` is the one worth a thought: twenty attendees
behind one conference NAT share an IP, and ten registrations per minute across
all of them could bite during the opening rush.

**Featured-game policy** is also mine: highest combined rating wins, ties to the
lowest game id, held 20s so the projector does not flip mid-move.

---

## 4. Anchor ratings are placeholders

`ref-random 800`, `ref-greedy 1000`, `ref-depth3 1200` are **not measurements**.
Design §10.3 originally required calibration from a seeded ladder and warned that
guessed anchors "would bias every rating in the room" — they are currently
guessed.

Marked provisional everywhere, and nothing in the architecture depends on them
being right. When bot work resumes this is the first thing to do, and it is a
single arena run.

---

## 5. Smaller drift

- **`stalled_games`** is named in design §4's `/health` payload but never defined
  — what counts as stalled is my choice. It is one of the numbers you will stare
  at on the health banner.
- **`health_tick` cadence** is 2s in the supervisor; interfaces Part 2 says "~3–5s".
- **Presence** semantics ("connected") are mine, not specified.

---

## 6. Where the build stands

Server: store and engine complete; the API is 18 of 21 tasks. Remaining is the
fake-bot harness that plays complete games over real endpoints, and a discipline
sweep — `/admin/reset` was cut, so that task is gone.

Then: the `chess_client` SDK, the Big Screen dashboard, and a README.

**658 tests passing, tree clean.**

---

## Closed by the scope reduction

- **`GET /games/{id}/legal_moves`** — I had invented this route because §13.3 and
  the MCP tools needed an agent delivery site. MCP is cut, so the route is gone.
- **`/admin/reset` semantics** — cut. Restarting the process covers most of it
  through §7.1 recovery, which is already tested.
- **Whether to build the `arena_reports` vertical** — cut with its producer.
- **The `controller` and agent-handoff questions** — cut with §13.3.
