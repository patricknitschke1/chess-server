# Open questions

Decisions taken while you were away, and things I could not answer from the specs.
Each has my recommendation and what I actually built, so you can skim and either
confirm or redirect. Nothing here is blocking — the server track is buildable as it
stands — but every item is a decision that nobody but me has reviewed.

Ordered by how expensive it would be to change later.

---

## 1. I invented a route: `GET /games/{id}/legal_moves` FINE

**Status:** built, and added to design §8.1 and interfaces Part 5 in the same change.

Design §5.2, §13.3 and interfaces Part 6 all require the *behaviour* — it is the
sole delivery trigger when `controller='agent'`, which is what makes `get_game()`'s
`readOnlyHint` honest by contrast. But no route inventory listed it. It cannot fold
into `/bots/me/turn` because the agent and the SDK share one bearer token, so the
server cannot tell them apart from the credential alone.

**This is the only route I added that no document asked for by name.** If you would
rather the MCP layer got there another way, it is much cheaper to change now than
after the MCP track binds to it.

---

## 2. Should anchors appear on the leaderboard? FINE

**Currently: no.** Anchors carry `role='anchor'` and the leaderboard filters to
`role='competitor'`, which leaves `LeaderboardEntry.is_anchor` a field that is
always false.

The argument for showing them: an attendee at 1150 learns much more from seeing
`ref-greedy 1000` and `ref-depth2 1200` on the same board than from a number in
isolation. They are yardsticks, and the whole point of anchors is calibration.

The argument against: they are not competitors, they never move, and they push
real attendees down the visible list on a projector with limited rows.

**My recommendation:** show them, visually distinguished, and keep `is_anchor` alive
to do it. But this is a room-feel decision and yours to make. It is a one-line filter
change plus whatever the dashboard does with the flag.

---

## 3. `POST /admin/reset` — confirm the position

**Not yet built** (it is the next task). The plan takes this position and I think it
is right:

- **Requires matchmaking paused first**, `409` otherwise. Without that, the next tick
  re-pairs the same twenty still-polling bots and the response you just read describes
  a slate that no longer exists.
- **Wipes:** `games`, `moves`, `rating_history`, `seats`, `challenges`, all in-process state.
- **Keeps:** every `bots` row — ids, names, tokens, roles. Nobody re-registers.
- **Anchors keep their seeded rating; competitors and benchmarks return to 1200.**
- **`rating_history` is deleted, not archived** — `/admin/consistency` asserts
  `rating == 1200 + sum(deltas)`, so keeping history while resetting ratings turns
  that alarm red for every competitor, which is the same as switching it off.
- Regenerates the run id, emits one `server_run_started`, held polls wake with
  `reason='paused'`.

**Confirm or adjust before I build it.** The "requires pause first" precondition is
the part most likely to annoy you in the room at 2pm.

---

## 4. Numbers I invented because no document gave one

Each is defensible, none is derived from anything:

| Constant | Value | Where | Basis |
|---|---|---|---|
| `REGISTER_PER_IP_PER_MIN` | 10 | `api/rate_limit.py` | §8.5 gives the rule, not the number |
| `FEATURED_HOLD_NS` | 20s | `api/featured.py` | how long a board stays on the projector |
| `DISCONNECT_AFTER_NS` | 30s | `engine/ticker.py` | the `bot_disconnected` edge |
| `SUPERVISOR_PERIOD_SECONDS` | 2 | `engine/supervisor.py` | how often the supervisor decides |
| `CANCEL_WAIT_SECONDS` | 5 | `engine/supervisor.py` | before refusing to respawn a ticker |
| `PROBE_TIMEOUT_SECONDS` | 1 | `engine/supervisor.py` | `db_writable` probe timeout |

`REGISTER_PER_IP_PER_MIN = 10` is the one worth a thought: twenty attendees behind
one conference NAT share an IP, and ten registrations per minute across all of them
could bite during the opening rush.

**Featured-game policy** is also mine: highest combined rating wins, ties to the
lowest game id, held for 20s so the projector does not flip mid-move. Ranking alone
re-sorts every time a rating moves.

---

## 5. Anchor identity: `owner='server'`

Anchors need an owner and nothing specified one. I used `'server'`, and registration
now rejects both that owner and the three anchor names, case-folded, so an attendee
cannot impersonate a reference bot on the leaderboard.

Fine unless you wanted anchors attributed to you by name.

---

## 6. Anchor ratings are placeholders, and calibration is deferred with bot development

`ref-random 800`, `ref-greedy 1000`, `ref-depth2 1200` are **not measurements**. Design
§10.3 originally required them to be calibrated from a seeded ladder and said guessed
anchors "would bias every rating in the room" — they are currently guessed.

I have marked them provisional everywhere and recorded the deferral, and nothing in
the architecture depends on the numbers being right. But when bot work resumes this is
the first thing to do, and it is a single arena run.

Related, and also deferred: the shipped `bot.py` beats `ref-greedy` about 94% of the
time, where the spec wanted attendees to lose to it and see their rating move in both
directions on day one.

---

## 7. Smaller drift I noticed but did not chase

- **`stalled_games`** is named in design §4's `/health` payload but never defined —
  what counts as stalled is the builder's choice, and now mine. Worth a look, since
  it is one of the numbers you will stare at on the health banner.
- **`health_tick` cadence** is 2s in the supervisor; interfaces Part 2 says "~3–5s".
- **Presence** is edge-triggered off a bot query that was originally the leaderboard
  query. It now has its own `list_presence_candidates()`, but the semantics of
  "connected" are mine, not specified.

---

## 8. Where the build stands

Phase 3c is 18 of 21 tasks done. Remaining: `POST /admin/reset` (question 3 above),
the fake-bot harness that plays complete games over real endpoints, and a final
discipline sweep. After that the server track is complete and the remaining tracks
are the SDK, MCP, dashboard and the attendee-facing Claude layer.

**738 tests passing, tree clean.**

One thing worth knowing about how this went: eleven times now, a mutation the plan
specified could not actually fail the test it was attached to. The worst was a test
that asserted its own mutation and could never have passed, and another where the
planned mutation killed nothing because the code was wrong in a way that masked it —
that one turned into a real fix. Nearly every one of those was found because the
build agents were asked to run the mutation rather than trust a green suite. It is
the single highest-yield thing in the process and worth keeping.
