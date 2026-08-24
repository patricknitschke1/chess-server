# Dashboard Engineer — Role Specification

> **Revision 5 errata — binding, and they override anything below that disagrees.**
> Applied from [the round-4 review](../../../../agent-reports/2026-08-24-spec-review-round4.md). Where this spec and design spec revision 5 conflict, **the design spec wins**.
>
> 1. **"Featured" belongs to the server; your click-to-watch is "watching".** The server picks the Big Screen featured game with a 20s minimum hold. Clicking a grid cell in My Bot mode sets *local view state only* and must never be called featured anywhere in code, UI or docs. Two authorities sharing one name is how a projector ends up fighting a mouse click.
> 2. **Render every attendee-controlled string with `textContent`.** Bot names, owners, `candidate_name`, `opponent_name`, and `?bot=` — never interpolated into an HTML template literal, never `innerHTML`. `?bot=` is attacker-supplied by definition.
> 3. **Colour from the `rated` field as delivered.** `rated` is now correct from creation (design §5.3), so an exhibition game no longer renders green mid-game and flips amber in the ticker.
> 4. **Delete every remaining reference to `arena.py --serve`.** Local stats arrive via `arena_report_posted` and `GET /bots/{bot_id}/arena-reports`; the override replaced `--serve` rather than joining it.
> 5. `ActiveGameSummary` now carries `fen`, `to_move` and `status` — render from those rather than reconstructing.

**Role:** dashboard-engineer  
**Date:** 2026-08-24  
**Owns:** `web/`  
**Claims from design spec:** §14 (dashboard and SSE) in full, §4.6 (health banner), §10 (ratings presentation)  
**Consumes from interfaces:** Part 2 (SSE event catalog), Part 5 (`GET /state`, `GET /leaderboard`, `GET /health`)

---

## 1. Scope and boundaries

You own **`web/`** — the single-page dashboard that runs on the projector and on attendee laptops all day.

### What you build
- `web/index.html` — single page, Big Screen and My Bot toggle
- `web/style.css` — visual design, projector-legible typography
- `web/dashboard.js` — SSE client, state management, clock ticking, event handling
- `web/board.js` — chessboard renderer (unicode pieces, FEN → grid)
- `web/leaderboard.js` — sortable leaderboard with provisional annotation and rating sparklines
- `web/health.js` — stale-tick banner

### What you consume
From `server-engineer`:
- `GET /state` — dashboard snapshot with `{run_id, event_id, active_games, leaderboard, featured_game_id}`
- `GET /leaderboard` — full leaderboard with ratings, games played, provisional flags
- `GET /health` — `{last_tick_age_ms, last_tick_duration_ms, active_games, pending_games, pooled_bots, held_polls, sse_clients, db_writable, consecutive_tick_errors}`
- `GET /events` — SSE stream carrying Part 2 event catalog

### What you do NOT build
- Any server code. You are an HTTP client only.
- New endpoints or events. If the event catalog does not carry a field you need, that is a **request to `server-engineer`**, not something you solve by polling.
- Admin surface (that is `server-engineer`'s admin router).

---

## 2. The two audiences

The navigation model is a **single toggle** between two modes. No tabs, no multi-page navigation.

### Big Screen mode

**Audience:** The whole room, from six metres away, while half-listening to someone's explanation.

**Layout:**
- **Featured game board** — large (60% of viewport width), always visible
  - Board rendered with unicode pieces: ♜♞♝♛♚♝♞♜ / ♟♟♟♟♟♟♟♟ (black) and ♖♘♗♕♔♗♘♖ / ♙♙♙♙♙♙♙♙ (white)
  - Participant names above/below board with current ratings
  - Clocks next to names, updating every 100ms while delivered
  - Last 6 moves in SAN below board
  - Rated/unrated badge (green/amber per §7 below)
- **Leaderboard rail** (right side, 30% width) — top 10 only
  - Rank, name, rating (bold), W-L-D record
  - Provisional annotation "(P)" for `games_played < 10`
  - Real-time updates on `rating_changed` events
- **Results ticker** (bottom 10% height) — last 10 game results, scrolling
  - Format: `"AlphaBot (1215) defeated BetaBot (1198) by checkmate"` for rated wins (green background)
  - Format: `"GammaBot drew with DeltaBot (unrated practice)"` for unrated (amber background)
  - Auto-scrolls on new `game_ended` events

**What is deliberately OFF the screen:**
- Other concurrent games (featured game only)
- Full move history beyond last 6
- Rating history sparklines (My Bot mode only)
- Pending games, challenges, connection status

**Visual priority:** Featured game dominates. Someone entering the room mid-afternoon must see the current position within 500ms, and the leaderboard top-3 without moving their eyes.

### My Bot mode

**Audience:** One attendee on their laptop, asking "is my bot getting better?"

**Layout:**
- **Personal panel** (top, 40% height)
  - Bot name, current rating (large), W-L-D record
  - **Rating sparkline** — last 20 games, x-axis is game number, y-axis is rating, hover shows delta
  - "Provisional (N/10 games)" banner if `games_played < 10`
  - Current game status if in a game: "Playing as White vs BetaBot, ply 12"
  - **Local arena reports** section (amber background, clearly labeled "Local · self-reported"):
    - Most recent 5 local arena reports from `arena.py --report`
    - Each entry shows: candidate name, opponent, W-L-D, win rate, avg move time, flags
    - Fetched from `GET /bots/{bot_id}/arena-reports` on page load
    - Updated live via `arena_report_posted` SSE events
    - Never shown in Big Screen mode
- **Live games grid** (middle left, 30% width, 40% height) — all active games, 4 per row
  - Small board thumbnails (8×8 grid, 30px squares)
  - Participant names (truncated to 12 chars)
  - Clocks below each name
  - Click to make featured (updates `featured_game_id` locally only, not server-side)
  - Rated games have green border, unrated amber
- **Full leaderboard** (middle right, 60% width) — scrollable, all bots
  - Rank, name, rating, W-L-D, games played
  - "You" badge next to the authenticated bot (requires token in localStorage or query param `?bot=<name>`)
  - Sort by rating descending (default), toggleable to sort by games played or name
  - Provisional annotation "(P)" for `games_played < 10`
- **Recent results** (bottom, 20% height) — last 20 games involving the authenticated bot
  - Date, opponent, result, rating delta, termination
  - Rated games green row background, unrated amber

**What is deliberately ON the screen that Big Screen omits:**
- Full leaderboard (not just top 10)
- Personal rating history sparkline
- All active games grid (not just featured)

**Visual priority:** Rating sparkline and current game status. The attendee must see their rating trend at a glance.

---

## 3. What you build — file by file

### `web/index.html`
Single page. No server-side rendering, no build step.

**Structure:**
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Chess Arena</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <!-- Mode toggle: Big Screen | My Bot -->
  <header>
    <h1>Chess Arena</h1>
    <nav>
      <button id="toggle-big-screen" class="active">Big Screen</button>
      <button id="toggle-my-bot">My Bot</button>
    </nav>
    <!-- Health banner (hidden unless stale tick) -->
    <div id="health-banner" class="hidden"></div>
  </header>
  
  <!-- Big Screen mode container -->
  <div id="big-screen-mode" class="mode active">
    <div id="featured-game"></div>
    <div id="leaderboard-rail"></div>
    <div id="results-ticker"></div>
  </div>
  
  <!-- My Bot mode container -->
  <div id="my-bot-mode" class="mode hidden">
    <div id="personal-panel"></div>
    <div id="live-games-grid"></div>
    <div id="full-leaderboard"></div>
    <div id="recent-results"></div>
  </div>
  
  <script src="board.js"></script>
  <script src="leaderboard.js"></script>
  <script src="health.js"></script>
  <script src="dashboard.js"></script>
</body>
</html>
```

**Mode toggle logic:** Clicking a mode button hides the inactive mode container and shows the active one. No URL routing. State is not persisted across page reloads (acceptable; this is ephemeral UI).

### `web/style.css`
Plain CSS. No preprocessors.

**Typography for projector legibility:**
- Featured game board: minimum 48px for piece unicode characters
- Participant names: 32px bold
- Clocks: 28px monospace
- Leaderboard: 24px for names, 20px for ratings
- Results ticker: 20px

**Color scheme per §7 below:**
- Rated games: `#2d5f2e` green backgrounds, `#4a934c` borders
- Unrated/local games: `#8b6f00` amber backgrounds, `#c9a500` borders
- Health banner (stale tick): `#cc0000` red background, white text, flashing animation

**Layout:**
- CSS Grid for mode containers
- Flexbox for leaderboard rows
- No media queries (this is not a mobile app; workshop laptops and projectors only)

### `web/board.js`
Chessboard renderer.

**Public API:**
```javascript
class ChessBoard {
  constructor(containerElement, options = {}) {
    this.container = containerElement;
    this.size = options.size || 400; // pixels
    this.flipped = options.flipped || false;
  }
  
  render(fen) {
    // Parse FEN, render 8×8 grid with unicode pieces
    // Piece unicode: white ♔♕♖♗♘♙, black ♚♛♜♝♞♟
    // Alternating square colors: #f0d9b5 (light) and #b58863 (dark)
  }
  
  flip() {
    this.flipped = !this.flipped;
    // Re-render with board orientation reversed
  }
}
```

**FEN parsing:**
- Split on `/` for ranks
- Map piece characters to unicode: `K→♔, Q→♕, R→♖, B→♗, N→♘, P→♙` (white), lowercase for black
- Numbers expand to empty squares
- Rank 8 at top (or bottom if flipped)

**Rendering:** Plain DOM manipulation. No canvas. 64 `<div>` elements with unicode text content.

### `web/leaderboard.js`
Leaderboard renderer and sorter.

**Public API:**
```javascript
class Leaderboard {
  constructor(containerElement, options = {}) {
    this.container = containerElement;
    this.topN = options.topN || null; // null = all, number = top N
    this.highlightBot = options.highlightBot || null; // bot name to highlight
  }
  
  render(bots) {
    // bots is array of {bot_id, bot_name, owner, rating, wins, losses, draws, games_played, is_provisional}
    // Sort by rating descending
    // Render table rows: rank | name | rating (bold) | W-L-D | (P) if provisional
    // Highlight row if bot_name === this.highlightBot
  }
  
  updateBot(bot) {
    // Update single bot in place (on rating_changed event)
    // Re-sort and re-render only if rank changed
  }
}
```

**Rating sparkline** (My Bot mode only):
- Inline SVG, 200×50px
- X-axis: last 20 games (game number)
- Y-axis: rating (auto-scale from min to max rating in the window)
- Line path connecting rating points
- Hover on a point shows tooltip: `"Game 42: 1215 → 1227 (+12)"`
- Data source: accumulated locally from `rating_changed` events, or fetched from a new `GET /bots/{id}/rating_history` endpoint (see §8.1 below — **requires decision**)

### `web/health.js`
Health banner controller.

**Public API:**
```javascript
class HealthBanner {
  constructor(bannerElement) {
    this.banner = bannerElement;
    this.lastTickAgeThreshold = 5000; // ms per §4.6
  }
  
  update(healthData) {
    // healthData from GET /health or health_tick event
    if (healthData.last_tick_age_ms > this.lastTickAgeThreshold) {
      this.show(`Ticker stalled: last tick ${healthData.last_tick_age_ms}ms ago`);
    } else {
      this.hide();
    }
  }
  
  show(message) {
    this.banner.textContent = message;
    this.banner.classList.remove('hidden');
    this.banner.classList.add('flash'); // CSS animation
  }
  
  hide() {
    this.banner.classList.add('hidden');
  }
}
```

**Visibility rule:** Red banner is shown whenever `last_tick_age_ms > 5000`. The operator must see the heartbeat from the back of the room per §4.6.

### `web/dashboard.js`
Main controller: SSE client, state management, event handling, clock ticking.

**Structure:**
```javascript
class Dashboard {
  constructor() {
    this.runId = null;
    this.lastSeq = -1;
    this.state = null; // cached /state response
    this.eventSource = null;
    this.eventBuffer = [];
    this.featuredGameId = null;
    this.clockTickInterval = null;
    
    this.board = new ChessBoard(document.getElementById('featured-game'));
    this.leaderboard = new Leaderboard(document.getElementById('leaderboard-rail'), {topN: 10});
    this.healthBanner = new HealthBanner(document.getElementById('health-banner'));
  }
  
  async init() {
    // 1. Connect to SSE first (§4 below)
    this.connectSSE();
    
    // 2. Fetch /state snapshot (§4 below)
    await this.fetchState();
    
    // 3. Apply buffered events with seq > state.event_id (§4 below)
    this.applyBufferedEvents();
    
    // 4. Start clock ticking (§6 below)
    this.startClockTick();
  }
  
  connectSSE() { /* §4 below */ }
  async fetchState() { /* §4 below */ }
  applyBufferedEvents() { /* §4 below */ }
  handleEvent(event) { /* §5 below */ }
  startClockTick() { /* §6 below */ }
  tickClocks() { /* §6 below */ }
  selectFeaturedGame() { /* §7 below, requires decision */ }
}

const dashboard = new Dashboard();
dashboard.init();
```

---

## 4. SSE consumption, precisely

### Connect-then-snapshot ordering

**Why this order matters:** Reverse order (snapshot then connect) creates a gap where events emitted between `GET /state` and `EventSource.open` are lost forever. Connect-first buffers those events, snapshot gives the baseline, buffered events fill the gap.

**Sequence:**
1. Open `EventSource` to `GET /events`
2. Buffer all received events in `this.eventBuffer` while fetching state
3. Fetch `GET /state`, extract `{run_id, event_id, ...}`
4. Apply buffered events where `event.run === this.runId && event.seq > state.event_id`
5. From this point, apply events immediately on receipt

**Implementation:**
```javascript
connectSSE() {
  this.eventSource = new EventSource('/events');
  
  this.eventSource.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    if (this.state === null) {
      // Still waiting for /state snapshot; buffer this event
      this.eventBuffer.push(event);
    } else {
      // State loaded; handle immediately
      this.handleEvent(event);
    }
  };
  
  this.eventSource.onerror = (err) => {
    console.error('SSE connection lost, refetching state...', err);
    this.eventSource.close();
    this.reconnectSSE();
  };
}

async fetchState() {
  const resp = await fetch('/state');
  this.state = await resp.json();
  this.runId = this.state.run_id;
  this.lastSeq = this.state.event_id;
  
  // Render initial state
  this.leaderboard.render(this.state.leaderboard);
  this.featuredGameId = this.state.featured_game_id;
  const featuredGame = this.state.active_games.find(g => g.game_id === this.featuredGameId);
  if (featuredGame) {
    this.board.render(featuredGame.fen);
  }
}

applyBufferedEvents() {
  for (const event of this.eventBuffer) {
    if (event.run === this.runId && event.seq > this.lastSeq) {
      this.handleEvent(event);
    }
  }
  this.eventBuffer = []; // Clear buffer
}
```

### Run matching and numeric seq comparison

**Why numeric comparison matters:** String comparison makes `"r7:9" > "r7:10"` (lexicographic), which silently drops event 10 because the dashboard thinks it already saw it. This is the exact bug §14 calls out.

**Implementation:**
```javascript
handleEvent(event) {
  // Check run mismatch (server restarted)
  if (event.run !== this.runId) {
    console.warn('Run ID changed, refetching state...');
    this.reconnectSSE();
    return;
  }
  
  // Check sequence gap (missed events)
  if (event.seq !== this.lastSeq + 1) {
    console.warn(`Sequence gap: expected ${this.lastSeq + 1}, got ${event.seq}. Refetching state...`);
    this.reconnectSSE();
    return;
  }
  
  this.lastSeq = event.seq; // Numeric comparison, not string
  
  // Dispatch to event-type handlers (§5 below)
  switch (event.event_type) {
    case 'game_created': this.onGameCreated(event.data); break;
    case 'move_played': this.onMovePlayed(event.data); break;
    case 'game_ended': this.onGameEnded(event.data); break;
    case 'rating_changed': this.onRatingChanged(event.data); break;
    case 'health_tick': this.onHealthTick(event.data); break;
    // ... (full catalog in §5 below)
  }
}
```

### Refetching /state on drop or gap

**Triggers:**
- `EventSource.onerror` (connection lost)
- `run` mismatch (server restarted)
- `seq` gap (missed events, probably because client queue overflowed at 256 and drop-oldest discarded events)

**Implementation:**
```javascript
async reconnectSSE() {
  if (this.eventSource) {
    this.eventSource.close();
  }
  
  // Clear stale state
  this.state = null;
  this.eventBuffer = [];
  
  // Re-run init sequence
  await this.init();
}
```

**Backpressure prevention:** A stalled browser tab must never apply backpressure to the game loop per §14. The server's per-client bounded queue (256, drop-oldest) ensures this. On the client side, we detect drops via seq gap and refetch `/state` — lossy but bounded.

### 15s heartbeat

The server sends a comment line every 15s to keep proxies from timing out idle streams. No client action required; `EventSource` ignores comment lines automatically.

---

## 5. Every event you handle

From **Interfaces Part 2**, full event catalog. For each event, what the UI does:

### `server_run_started`
```json
{"run": "abc123", "seq": 0, "event_type": "server_run_started", "data": {"run_id": "abc123", "started_at": "..."}}
```
**Action:** Set `this.runId = data.run_id`. Clear all local state (leaderboard, active games, featured game). Refetch `/state`. This event fires after a server restart per §7.1.

### `game_created`
```json
{"run": "abc123", "seq": 1, "event_type": "game_created", "data": {game_id, white_bot_id, white_bot_name, black_bot_id, black_bot_name, status: "pending", rated, source, time_control_ms, increment_ms}}
```
**Action:** Add to `this.state.active_games` array. If Big Screen mode and no featured game, make this featured (per §7 selection policy below). Render in My Bot mode live games grid.

### `game_started`
```json
{"run": "abc123", "seq": 2, "event_type": "game_started", "data": {game_id, white_bot_id, white_bot_name, black_bot_id, black_bot_name, started_at}}
```
**Action:** Update `status` from `pending` to `active` in local state. No visual change (board already rendered on `game_created`). Log to console for debugging.

### `move_played`
```json
{"run": "abc123", "seq": 3, "event_type": "move_played", "data": {game_id, ply, uci, san, fen, to_move, white_ms, black_ms, turn_elapsed_ms, server_elapsed_ms, is_featured}}
```
**Action:**
- Update game state: `ply`, `fen`, clocks (`white_ms`, `black_ms`), `to_move`
- Store `turn_elapsed_ms` and timestamp for local clock ticking (§6 below)
- If `is_featured === true`, re-render featured game board with new FEN, append `san` to move history
- If `is_featured === false`, update small board thumbnail in My Bot mode live games grid (only if that mode is active)
- Non-featured moves are **coalesced to ≤2 Hz** on the server; do not expect one event per move for non-featured games

### `game_ended`
```json
{"run": "abc123", "seq": 4, "event_type": "game_ended", "data": {game_id, white_bot_id, white_bot_name, black_bot_id, black_bot_name, status: "finished", result, termination, rated, final_ply, ended_at}}
```
**Action:**
- Remove from `this.state.active_games`
- Add to results ticker: format `"{winner_name} ({winner_rating}) defeated {loser_name} ({loser_rating}) by {termination}"` for decisive results, `"{white_name} drew with {black_name} by {termination}"` for draws
- Color-code ticker entry: green background if `rated === true`, amber if `rated === false`
- If this was the featured game, select new featured game (§7 selection policy below)
- Stop clock ticking for this game

### `rating_changed`
```json
{"run": "abc123", "seq": 5, "event_type": "rating_changed", "data": {bot_id, bot_name, rating_before, rating_after, delta, game_id}}
```
**Action:**
- Update bot's rating in leaderboard (call `leaderboard.updateBot()`)
- Re-sort leaderboard if rank changed
- In My Bot mode, if this is the authenticated bot, append `{game_id, rating_after, delta}` to rating history for sparkline
- Animate rating change (flash green for positive delta, red for negative, white for zero)

### `bot_registered`
```json
{"run": "abc123", "seq": 6, "event_type": "bot_registered", "data": {bot_id, bot_name, role, rating}}
```
**Action:** Add bot to leaderboard with `rating: 1200`, `games_played: 0`, `is_provisional: true`. Render at bottom of leaderboard.

### `bot_connected`
```json
{"run": "abc123", "seq": 7, "event_type": "bot_connected", "data": {bot_id, bot_name}}
```
**Action:** Show brief toast notification: `"{bot_name} connected"` (only in My Bot mode if this is the authenticated bot). Auto-dismiss after 2s.

### `bot_disconnected`
```json
{"run": "abc123", "seq": 8, "event_type": "bot_disconnected", "data": {bot_id, bot_name}}
```
**Action:** Show brief toast notification: `"{bot_name} disconnected"` (only in My Bot mode if this is the authenticated bot). Auto-dismiss after 2s.

### `challenge_updated`
```json
{"run": "abc123", "seq": 9, "event_type": "challenge_updated", "data": {challenge_id, status, challenger_bot_id, challenger_bot_name, opponent_bot_id, opponent_bot_name, time_control_ms, increment_ms, game_id, reason}}
```
**Action:** This event is primarily for bots polling `/challenges` inbox. Dashboard **may** show a transient notification in My Bot mode if the authenticated bot is involved: `"{challenger_name} challenged you to a {time_control} game"` on `status: "created"`. Otherwise, no UI action (challenges are not a dashboard concern per §2).

### `health_tick`
```json
{"run": "abc123", "seq": 10, "event_type": "health_tick", "data": {last_tick_age_ms, last_tick_duration_ms, active_games, pending_games, pooled_bots, held_polls, sse_clients}}
```
**Action:** Call `healthBanner.update(data)`. If `last_tick_age_ms > 5000`, show red banner: `"Ticker stalled: last tick {last_tick_age_ms}ms ago"`. Otherwise hide banner.

### `arena_report_posted`
```json
{"run": "abc123", "seq": 11, "event_type": "arena_report_posted", "data": {bot_id, bot_name, candidate_name, opponent_name, games, wins, draws, losses, win_rate, mean_move_ms, p95_move_ms, flags}}
```
**Action:** In My Bot mode only, if this is the authenticated bot (matched via `?bot=` param or localStorage):
- Add entry to local arena reports list in the personal panel
- Format: `"{candidate_name} vs {opponent_name}: {wins}-{losses}-{draws} ({games} games) · {win_rate*100}% · {mean_move_ms}ms avg · {flags} flags"`
- Render with **amber background** and visible "Local · self-reported" label
- Keep most recent 5 entries visible; older entries collapsed or scrollable
- **Never render in Big Screen mode** — local data never appears on the projector

---

## 6. Clock rendering — local ticking between events

**Problem:** If clocks only update on `move_played` events, every board looks frozen between moves. At 3+2 blitz with a 5-second move, that is 5 seconds of apparent hang. The room assumes the server is down.

**Solution:** Tick clocks locally at 100ms intervals using `turn_elapsed_ms` from the most recent `move_played` event plus `Date.now()` delta.

### Clock state per game

```javascript
class GameClockState {
  constructor(white_ms, black_ms, to_move, turn_elapsed_ms, timestamp) {
    this.white_ms = white_ms;
    this.black_ms = black_ms;
    this.to_move = to_move; // "white" or "black"
    this.turn_elapsed_ms = turn_elapsed_ms; // elapsed at event emit time
    this.timestamp = timestamp; // Date.now() when event received
  }
  
  getCurrentWhiteMs() {
    if (this.to_move === "white") {
      const now = Date.now();
      const additionalElapsed = now - this.timestamp;
      return Math.max(0, this.white_ms - additionalElapsed);
    }
    return this.white_ms;
  }
  
  getCurrentBlackMs() {
    if (this.to_move === "black") {
      const now = Date.now();
      const additionalElapsed = now - this.timestamp;
      return Math.max(0, this.black_ms - additionalElapsed);
    }
    return this.black_ms;
  }
}
```

### Updating on move_played

```javascript
onMovePlayed(data) {
  const game = this.state.active_games.find(g => g.game_id === data.game_id);
  if (!game) return;
  
  game.ply = data.ply;
  game.fen = data.fen;
  game.to_move = data.to_move;
  
  // Create or update clock state
  game.clockState = new GameClockState(
    data.white_ms,
    data.black_ms,
    data.to_move,
    data.turn_elapsed_ms,
    Date.now()
  );
  
  if (data.is_featured) {
    this.board.render(data.fen);
    this.updateFeaturedClocks();
  }
}
```

### Ticking every 100ms

```javascript
startClockTick() {
  this.clockTickInterval = setInterval(() => this.tickClocks(), 100);
}

tickClocks() {
  // Update all displayed clocks
  if (this.featuredGameId) {
    this.updateFeaturedClocks();
  }
  
  // In My Bot mode, update all clocks in live games grid
  if (this.currentMode === 'my-bot') {
    this.updateLiveGamesClocks();
  }
}

updateFeaturedClocks() {
  const game = this.state.active_games.find(g => g.game_id === this.featuredGameId);
  if (!game || !game.clockState) return;
  
  const whiteMs = game.clockState.getCurrentWhiteMs();
  const blackMs = game.clockState.getCurrentBlackMs();
  
  document.getElementById('white-clock').textContent = this.formatClock(whiteMs);
  document.getElementById('black-clock').textContent = this.formatClock(blackMs);
  
  // Flash red if under 10 seconds
  if (whiteMs < 10000) {
    document.getElementById('white-clock').classList.add('low-time');
  }
  if (blackMs < 10000) {
    document.getElementById('black-clock').classList.add('low-time');
  }
}

formatClock(ms) {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
}
```

### Undelivered positions

**Problem:** What if `turn_elapsed_ms` is `null` because the position is undelivered (per §6.2 and §6.3)?

**Solution:** Do not tick the clock for the side to move. Display the clock value from the event as static text. This is correct: an undelivered position's clock is not running per §6.1.

**Implementation:**
```javascript
class GameClockState {
  constructor(white_ms, black_ms, to_move, turn_elapsed_ms, timestamp) {
    this.white_ms = white_ms;
    this.black_ms = black_ms;
    this.to_move = to_move;
    this.turn_elapsed_ms = turn_elapsed_ms; // may be null
    this.timestamp = timestamp;
    this.delivered = (turn_elapsed_ms !== null);
  }
  
  getCurrentWhiteMs() {
    if (!this.delivered || this.to_move !== "white") {
      return this.white_ms;
    }
    const now = Date.now();
    const additionalElapsed = now - this.timestamp;
    return Math.max(0, this.white_ms - additionalElapsed);
  }
  
  getCurrentBlackMs() {
    if (!this.delivered || this.to_move !== "black") {
      return this.black_ms;
    }
    const now = Date.now();
    const additionalElapsed = now - this.timestamp;
    return Math.max(0, this.black_ms - additionalElapsed);
  }
}
```

### Flagged clocks

**Problem:** How to show a flagged clock?

**Solution:** Display `"0:00"` in red with a flag icon. A flagged clock never ticks below zero.

**Implementation:** Already handled by `Math.max(0, ...)` in `getCurrentWhiteMs()` / `getCurrentBlackMs()`.

---

## 7. Visual rules — rated green, unrated amber

**Invariant per AGENTS.md and agent definition:** Nobody should ever mistake a practice win for a ranked one.

**Note (Harmonization Revision 4):** Local arena reports (from `arena.py --report`) are displayed in the My Bot personal panel with **amber background** and a visible "Local · self-reported" label. They are fetched from `GET /bots/{bot_id}/arena-reports` and updated live via `arena_report_posted` SSE events. Local data **never appears in Big Screen mode**—only server games are shown on the projector.

### Color-coding

| Game type | Border | Background | Badge text |
|---|---|---|---|
| Rated (`rated: true`) | `#4a934c` green | `#2d5f2e` green (15% opacity) | "RATED" |
| Unrated (`rated: false`) | `#c9a500` amber | `#8b6f00` amber (15% opacity) | "UNRATED" |
| Local arena report | N/A | `#8b6f00` amber (30% opacity) | "Local · self-reported" |

**Application:**
- Featured game board: border and badge top-right corner
- My Bot mode live games grid: border on each thumbnail
- Results ticker: row background color
- Leaderboard: no color-coding (ratings are always from rated games only per §5.1)

### Provisional annotation

**Rule per §10.1:** Bots with `games_played < 10` are annotated `"(P)"` next to their rating in the leaderboard.

**Rendering:**
```javascript
renderLeaderboardRow(bot) {
  const provisionalBadge = bot.is_provisional ? ' (P)' : '';
  return `<tr>
    <td>${bot.rank}</td>
    <td>${bot.bot_name}</td>
    <td><strong>${bot.rating}</strong>${provisionalBadge}</td>
    <td>${bot.wins}-${bot.losses}-${bot.draws}</td>
  </tr>`;
}
```

**Tooltip on hover:** `"Provisional: fewer than 10 games played. Rating may be unstable."`

### Featured game minimum hold — 20s

**Rule per §11:** The featured game holds for at least 20s before switching, so blitz does not make the big screen strobe.

**Implementation:**
```javascript
class Dashboard {
  constructor() {
    // ...
    this.featuredGameSetAt = null; // timestamp when featured game was last changed
    this.MIN_FEATURED_HOLD_MS = 20000;
  }
  
  selectFeaturedGame() {
    const now = Date.now();
    if (this.featuredGameSetAt && (now - this.featuredGameSetAt) < this.MIN_FEATURED_HOLD_MS) {
      // Hold current featured game
      return;
    }
    
    // Selection policy (see §7.1 below — requires decision)
    const newFeaturedGame = this.pickFeaturedGame();
    if (newFeaturedGame && newFeaturedGame.game_id !== this.featuredGameId) {
      this.featuredGameId = newFeaturedGame.game_id;
      this.featuredGameSetAt = now;
      this.board.render(newFeaturedGame.fen);
    }
  }
  
  pickFeaturedGame() {
    // See §7.1 below — requires decision
  }
}
```

**Trigger points for featured game selection:**
- On `game_ended` if the ended game was featured
- On `game_created` if no game is currently featured
- Periodic check every 30s (optional; allows switching from a finished game that held its 20s)

### 7.1 Featured game selection policy — **RESOLVED (Harmonization Revision 4)**

**Resolution:** Feature the active game with the **highest sum of participant ratings** (white_rating + black_rating), held for at least 20s. Ties broken by lowest `game_id` (oldest game).

**Rationale:**
- Highest-rated participants = highest-stakes game = most interesting to watch
- Deterministic (testable)
- Simple to implement (one sort, no round-robin state to track)
- Self-correcting: as bots' ratings converge over the day, featured games naturally rotate

**Implementation:**
```javascript
pickFeaturedGame() {
  const activeGames = this.state.active_games.filter(g => g.status === 'active');
  if (activeGames.length === 0) return null;
  
  // Sort by sum of participant ratings descending, then by game_id ascending
  activeGames.sort((a, b) => {
    const sumA = a.white_rating + a.black_rating;
    const sumB = b.white_rating + b.black_rating;
    if (sumA !== sumB) return sumB - sumA; // descending
    return a.game_id - b.game_id; // ascending (oldest first)
  });
  
  return activeGames[0];
}
```

**Server dependency resolved:** `ActiveGameSummary` now includes `white_rating` and `black_rating` (added in interfaces harmonization).

### 7.2 Viewing any server game (click grid cells)

**New requirement (Harmonization Revision 4):** In My Bot mode, the live games grid shows all active server games as small board thumbnails. **Clicking a grid cell makes that game the locally featured game** (client-side state only, not server-side).

**Implementation:**
```javascript
// In dashboard.js
gridCellClicked(game_id) {
  this.locallyFeaturedGameId = game_id;  // Override server's featured_game_id locally
  this.renderFeaturedGame();
}

getFeaturedGameId() {
  return this.locallyFeaturedGameId || this.state.featured_game_id;
}
```

**Persistence:** Use `sessionStorage` to remember the locally featured game across page refreshes within the same session. Clear on `server_run_started` (new run = new games).

**UI affordance:** Highlight the selected grid cell with a border or shadow to show which game is currently featured locally.

### 7.3 Identifying "my bot" without authentication

**New requirement (Harmonization Revision 4):** Dashboard is unauthenticated and read-only, but attendees want to spot their own bot in grids and leaderboard. Use URL parameter `?bot=BotName` or `localStorage`.

**Implementation:**
```javascript
// In dashboard.js constructor
const urlParams = new URLSearchParams(window.location.search);
this.myBotName = urlParams.get('bot') || localStorage.getItem('myBotName');

if (this.myBotName) {
  localStorage.setItem('myBotName', this.myBotName);  // Persist across refreshes
}

// Display "YOU" badge next to that bot in leaderboard and game grids
renderLeaderboard() {
  entries.forEach(entry => {
    const isMe = entry.name === this.myBotName;
    // ... add 'you-badge' class if isMe
  });
}
```

**URL share:** Attendees can share `http://localhost:8000?bot=MyBot` to pre-set their bot name on any browser.

**No auth required:** This is display sugar only—no privileged data or actions, just visual highlighting.
```

**Alternative policies (NOT recommended, but stated for completeness):**
- **Random selection:** Not deterministic, hard to test, may switch to a less interesting game
- **Round-robin:** Requires tracking which games have been featured; complex state that does not survive page reload
- **Longest-running game:** Rewards slow play, which is not the behavior we want to highlight
- **Lowest `game_id` (oldest):** Fair, but the first game of the day might be between two 1200-rated beginners while later games are more skilled

**Decision required:** Confirm highest-sum-of-ratings policy or specify an alternative. If you need data the `active_games` array does not carry (e.g., participant ratings), that is a **request to `server-engineer`** to add those fields to the `GET /state` response.

---

## 8. Seams you consume

### 8.1 HTTP endpoints

From **Interfaces Part 5**:

**`GET /state`** — Dashboard snapshot
- **Unauthenticated**
- **Response:**
  ```json
  {
    "run_id": "abc123",
    "event_id": 42,
    "active_games": [
      {
        "game_id": 10,
        "white_bot_id": 1,
        "white_bot_name": "AlphaBot",
        "black_bot_id": 2,
        "black_bot_name": "BetaBot",
        "ply": 12,
        "white_ms": 152300,
        "black_ms": 161100,
        "is_featured": true,
        "rated": true
      }
    ],
    "leaderboard": [
      {
        "bot_id": 1,
        "bot_name": "AlphaBot",
        "owner": "alice",
        "rating": 1215,
        "wins": 3,
        "losses": 1,
        "draws": 0,
        "games_played": 4,
        "is_provisional": true,
        "role": "competitor",
        "is_anchor": false
      }
    ],
    "featured_game_id": 10
  }
  ```
- **Missing field for featured game selection (Decision #8):** `active_games` entries need `white_rating` and `black_rating` to compute sum-of-ratings. **Request to `server-engineer`:** Add these two fields to `ActiveGameSummary` in `GET /state` response.

**`GET /leaderboard`** — Full leaderboard
- **Unauthenticated**
- **Response:** Same `LeaderboardEntry` array as in `/state`, but without the `active_games` and `featured_game_id` fields
- **Usage:** Fetched once at page load in My Bot mode for full scrollable leaderboard. Kept updated via `rating_changed` events thereafter; no polling.

**`GET /health`** — Ticker heartbeat
- **Unauthenticated**
- **Response:**
  ```json
  {
    "last_tick_age_ms": 1234,
    "last_tick_duration_ms": 56,
    "active_games": 5,
    "pending_games": 2,
    "stalled_games": 0,
    "pooled_bots": 8,
    "held_polls": 12,
    "sse_clients": 3,
    "db_writable": true,
    "consecutive_tick_errors": 0
  }
  ```
- **Usage:** Fetched every 5s by the health banner controller. Also received via `health_tick` SSE events every ~3-5s. The HTTP endpoint is a fallback when SSE is down.

**`GET /events`** — SSE stream
- **Unauthenticated**
- **Response:** Server-Sent Events stream with event payloads per Interfaces Part 2
- **Headers:** `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`
- **Usage:** Opened once at page load, held open indefinitely. Reconnect on error with exponential backoff (1s, 2s, 4s, cap at 30s).

### 8.2 SSE event catalog

From **Interfaces Part 2**, all 10 event types listed in §5 above.

**Events you handle:**
- `server_run_started` — refetch `/state`, clear local cache
- `game_created` — add to active games
- `game_started` — update status `pending` → `active`
- `move_played` — update FEN, clocks, move history; re-render board if featured
- `game_ended` — remove from active games, add to results ticker, select new featured game
- `rating_changed` — update leaderboard, animate rating change
- `bot_registered` — add to leaderboard
- `bot_connected` / `bot_disconnected` — show toast in My Bot mode (optional)
- `challenge_updated` — show toast in My Bot mode (optional)
- `health_tick` — update health banner

**Events you ignore:** None. All 10 event types have defined UI actions (even if some are "log to console").

### 8.3 Fields you need that do not yet exist

**Missing from `GET /state` response (per Decision #8):**
- `active_games[].white_rating` — required for featured game selection policy (highest sum of ratings)
- `active_games[].black_rating` — required for featured game selection policy

**Request to `server-engineer`:** Add `white_rating` and `black_rating` to the `ActiveGameSummary` dataclass in Interfaces Part 5 and populate them in the `/state` endpoint handler.

**Missing endpoint for My Bot mode rating sparkline:**
- `GET /bots/{id}/rating_history` — returns last 20 `rating_changed` events for this bot
- **Alternative:** Accumulate rating history locally from `rating_changed` events. This works if the page is loaded before the bot's first game, but breaks if the page is loaded mid-workshop. A dedicated endpoint is more robust.
- **Request to `server-engineer`:** Add `GET /bots/{id}/rating_history` endpoint returning `[{game_id, rating_before, rating_after, delta, timestamp}]` limited to last 20 games.

---

## 9. Seams you produce — none in code, but health banner for operator

You do **not** produce any HTTP endpoints, SSE events, or MCP tools. You are a read-only client.

**What you owe the operator visually:**
- **Stale tick banner (red)** when `last_tick_age_ms > 5000`, visible from six metres away, per §4.6
- **Current featured game and leaderboard top-3** visible within 500ms of page load in Big Screen mode
- **No frozen clocks** — local ticking must be visually smooth (100ms update interval)

---

## 10. Failure modes

### Server restart mid-session

**Manifestation:** `server_run_started` event with new `run_id`

**Recovery:**
1. Detect `event.run !== this.runId`
2. Close old `EventSource`
3. Clear local state (`active_games`, `leaderboard`, `featuredGameId`, clocks)
4. Re-run init sequence: connect SSE, fetch `/state`, apply buffered events
5. All games are aborted per §7.1, so `active_games` will be empty initially; new games appear as bots re-poll and are re-paired

**User-visible impact:** Featured game disappears, leaderboard remains (ratings unaffected by restart per §7.1). New games appear within ~5s (one ticker cycle).

### SSE disconnect

**Manifestation:** `EventSource.onerror` fires

**Recovery:**
1. Close `EventSource`
2. Wait 1s (exponential backoff: 1s, 2s, 4s, cap at 30s)
3. Re-run init sequence: connect SSE, fetch `/state`, apply buffered events

**User-visible impact:** Brief freeze (clocks stop ticking, no new events) for 1-4s. Leaderboard and featured game state re-sync on reconnect.

### Event gap (missed events)

**Manifestation:** `event.seq !== this.lastSeq + 1`

**Cause:** Client's event buffer overflowed at 256 and drop-oldest discarded events

**Recovery:**
1. Log warning: `"Sequence gap: expected ${this.lastSeq + 1}, got ${event.seq}. Refetching state..."`
2. Close `EventSource`
3. Re-run init sequence

**User-visible impact:** Same as SSE disconnect.

**Why this is acceptable:** Per §14, a stalled browser tab must never apply backpressure to the game loop. Lossy recovery is the correct trade-off.

### Dropped client (tab backgrounded or laptop suspended)

**Manifestation:** Browser suspends `EventSource`, server keeps emitting, client buffer overflows on resume

**Recovery:** Same as event gap — detect seq gap, refetch `/state`

**User-visible impact:** Page is "behind" for 1-4s after resume, then catches up.

**Prevention:** Keep the dashboard tab in foreground during the workshop. Backgrounding is an unsupported use case.

### Stale tick (ticker wedged)

**Manifestation:** `health_tick` event with `last_tick_age_ms > 5000` OR no `health_tick` events received for 10s

**Recovery:** Show red banner: `"Ticker stalled: last tick {last_tick_age_ms}ms ago"`. No automatic recovery (this is an operator-visible failure).

**User-visible impact:** Red banner flashing at top of screen, visible from six metres away per §4.6.

**Operator action required:** Restart the server (safe per §7.1).

### No games in progress (empty room at 09:00)

**Manifestation:** `GET /state` returns `active_games: []`

**Rendering:**
- Big Screen mode: Show message `"Waiting for games to start..."` in featured game area
- My Bot mode: Live games grid is empty, show message `"No games in progress"`

**No error state:** This is normal at workshop start.

### Bot with no rated games yet

**Manifestation:** Bot in leaderboard with `games_played: 0`, `rating: 1200`, `is_provisional: true`

**Rendering:** Show in leaderboard with `(P)` annotation. Rating sparkline (My Bot mode) is empty or shows a single flat line at 1200.

### Empty leaderboard at 09:00

**Manifestation:** `GET /state` returns `leaderboard: []` (unlikely; anchors are pre-registered)

**Rendering:** Show message `"No bots registered yet"` in leaderboard rail / full leaderboard area.

---

## 11. Test obligations

### Automated tests (JavaScript unit tests)

**Framework:** Plain Mocha or Jest, no build step. Tests live in `web/test/`.

**Coverage:**
- `board.js`: FEN parsing, unicode piece mapping, flipped board rendering
- `leaderboard.js`: Sorting by rating/games/name, provisional annotation, rank updates
- `health.js`: Threshold detection, banner show/hide
- `dashboard.js`: SSE connect-then-snapshot ordering, numeric seq comparison, run mismatch detection, event buffering and replay

**Critical test cases per §18:**
1. **Numeric seq comparison:** `"7" < "10"` numerically, `"7" > "10"` lexicographically. Assert events are applied in correct order.
2. **Run mismatch:** Receive event with different `run`, assert `/state` is refetched and local state cleared.
3. **Seq gap:** Receive event with `seq` jumping from 5 to 8, assert `/state` is refetched.
4. **Clock ticking:** Mock `Date.now()`, assert clock value decreases at 1ms per 1ms elapsed (for side to move).
5. **Undelivered position:** `turn_elapsed_ms: null`, assert clock is static (not ticking).
6. **Featured game hold:** Set featured game, advance time by 10s, trigger `selectFeaturedGame()`, assert featured game unchanged. Advance by another 15s, assert featured game may now switch.

### Manual tests

**Hour-long stability test:**
1. Load dashboard in Big Screen mode
2. Leave tab in foreground for 60 minutes
3. Assert:
   - No memory leaks (DevTools Memory profiler: heap size plateaus after initial climb)
   - No clock drift (compare displayed clock to server `/state` snapshot every 5 minutes; drift < 500ms)
   - No frozen clocks (visual inspection: clocks tick smoothly)
   - No stale tick banner (unless server actually stalls)

**Six-metre legibility test:**
1. Display dashboard on projector in Big Screen mode
2. Stand six metres away
3. Assert:
   - Featured game board squares and pieces are distinguishable
   - Participant names readable (32px bold minimum)
   - Leaderboard top 3 readable (24px for names)
   - Clocks readable (28px monospace)
   - Health banner red background visible (flashing animation catches eye)

**Mode toggle test:**
1. Load dashboard, click "My Bot" toggle
2. Assert: Big Screen mode hidden, My Bot mode visible
3. Click "Big Screen" toggle
4. Assert: My Bot mode hidden, Big Screen mode visible
5. No URL change, no page reload

**SSE reconnect test:**
1. Load dashboard, wait for initial state load
2. Kill server, wait 5s
3. Restart server
4. Assert:
   - SSE reconnects within 1-4s (exponential backoff)
   - `/state` refetched
   - Leaderboard and games re-rendered
   - No stale data (old `run_id` events discarded)

**Rating change animation test:**
1. Load dashboard in My Bot mode (authenticated as "AlphaBot")
2. Trigger `rating_changed` event via server (bot wins a game)
3. Assert:
   - Rating value updates in leaderboard
   - Rating change animates (flash green for positive delta)
   - Rating sparkline appends new data point
   - Leaderboard re-sorts if rank changed

---

## 12. Acceptance criteria

### Big Screen mode
- [ ] Featured game board visible from six metres away (piece unicode, 48px minimum)
- [ ] Participant names, ratings, clocks visible from six metres (32px / 28px)
- [ ] Leaderboard top 10 visible, sorted by rating descending, updated within 1s of `rating_changed` event
- [ ] Results ticker shows last 10 game results, color-coded (green rated, amber unrated)
- [ ] Clocks tick smoothly at 100ms intervals, never frozen
- [ ] Featured game holds for ≥20s before switching
- [ ] Stale tick banner appears within 5s of ticker wedge, red and flashing
- [ ] No tokens or owner identifiers in any displayed data

### My Bot mode
- [ ] Personal panel shows bot name, current rating (large), W-L-D record
- [ ] Rating sparkline shows last 20 games, x-axis game number, y-axis rating, hover shows delta
- [ ] Provisional banner `"Provisional (N/10 games)"` shown if `games_played < 10`
- [ ] Live games grid shows all active games, 4 per row, small board thumbnails (30px squares)
- [ ] Clicking a game in live games grid makes it featured (local UI change only, not server-side)
- [ ] Full leaderboard scrollable, sorted by rating, toggleable sort by games played or name
- [ ] "You" badge next to authenticated bot in leaderboard
- [ ] Recent results table shows last 20 games involving authenticated bot, color-coded

### Both modes
- [ ] Mode toggle works instantly, no page reload
- [ ] SSE connects before `/state` fetch (connect-then-snapshot ordering)
- [ ] Buffered events applied after `/state` load with `seq > state.event_id`
- [ ] Numeric seq comparison (not string), run mismatch triggers refetch
- [ ] Dropped SSE client refetches `/state` and reconnects with exponential backoff
- [ ] Health banner shows when `last_tick_age_ms > 5000`
- [ ] Tab left open for 60 minutes: no memory leaks, no drift >500ms, no frozen clocks
- [ ] Rated games have green borders/backgrounds, unrated have amber
- [ ] Provisional bots annotated `(P)` in leaderboard

### Edge cases
- [ ] Server restart mid-session: new `run_id` triggers refetch, all games aborted, no crash
- [ ] Empty leaderboard at 09:00: show message "No bots registered yet"
- [ ] No games in progress: show message "Waiting for games to start..."
- [ ] Bot with 0 games: shows in leaderboard at 1200 (P), rating sparkline empty
- [ ] Undelivered position (`turn_elapsed_ms: null`): clock is static, not ticking
- [ ] Flagged clock: displays `0:00` in red, never ticks below zero

---

## 13. All Decisions Resolved (Harmonization Revision 4)

### Decision #1: Featured game selection policy (from Interfaces Decision #8) — **RESOLVED**

**Resolution:** Feature the active game with the highest sum of participant ratings (white_rating + black_rating), held for ≥20s. Ties broken by lowest `game_id` (oldest).

**Server dependency resolved:** `ActiveGameSummary` now includes `white_rating` and `black_rating` fields in `GET /state` response (added during harmonization).

**New requirements added in this revision:**
1. **Watch any server game:** clicking a grid cell in My Bot mode sets local view state (§7.2). The server retains sole authority over the Big Screen featured game.
2. **Identify "my bot":** URL param `?bot=BotName` or `localStorage` for visual highlighting (§7.3), rendered with `textContent`.
3. **Local stats:** delivered by `arena.py --report` → `POST /arena-reports`, surfaced through the `arena_report_posted` event and `GET /bots/{bot_id}/arena-reports`, rendered amber and labelled "Local · self-reported" in My Bot mode only.

---

## 14. Summary for report-back

**File created:** `docs/superpowers/specs/roles/dashboard-engineer-spec.md`

**§ numbers claimed:**
- §14 (dashboard and SSE) — full ownership
- §4.6 (health banner) — dashboard side only (ticker supervision is `server-engineer`)
- §10 (ratings presentation) — provisional annotation, rating sparklines (My Bot mode)
- §11 (time control) — featured game minimum hold of 20s

**Events consumed (from Interfaces Part 2):**
All 10 event types:
1. `server_run_started` — refetch `/state`, clear cache
2. `game_created` — add to active games, maybe feature
3. `game_started` — update status
4. `move_played` — update FEN/clocks, re-render board if featured, start local clock ticking
5. `game_ended` — remove from active games, add to ticker, select new featured game
6. `rating_changed` — update leaderboard, animate change, append to sparkline
7. `bot_registered` — add to leaderboard
8. `bot_connected` / `bot_disconnected` — show toast (optional)
9. `challenge_updated` — show toast (optional)
10. `health_tick` — update health banner

**Endpoints consumed (from Interfaces Part 5):**
- `GET /state` — dashboard snapshot
- `GET /leaderboard` — full leaderboard (My Bot mode)
- `GET /health` — heartbeat for health banner
- `GET /events` — SSE stream

**Requests to `server-engineer` (all resolved in Harmonization Revision 4):**
1. ✓ `white_rating` and `black_rating` fields in `ActiveGameSummary` (added to interfaces)
2. ✓ `GET /bots/{id}/rating_history` endpoint (already in interfaces Part 5)

**New requirements added in this revision:**
1. Implement click-to-feature on live game grid cells (§7.2)
2. Implement "my bot" identification via URL param `?bot=BotName` or `localStorage` (§7.3)
3. Remove all "local amber" color-coding—dashboard shows server games only

**All decisions resolved:** Featured game selection policy finalized (highest rating sum, 20s hold). No blockers remaining.

---

## 15. Build order dependencies

**Blocked on:**
- Phase 3b completion (`server-engineer`): `/state`, `/leaderboard`, `/health`, `/events` endpoints must exist
- SSE event catalog implementation (`server-engineer`): all 10 event types emitting per Part 2

**No longer blocked on:** All design decisions resolved in Harmonization Revision 4.

**Enables:**
- Workshop day (Phase 8): dashboard is the projector display and attendee monitor
- Demo dry runs (Phase 7): dashboard validates SSE stream is working

**Parallel tracks:** Can be built in parallel with `mcp-engineer` (MCP server) and `client-engineer` (SDK and arena.py). No dependencies on either.

**Internal build order within `web/`:**
1. `board.js` first (pure function, no dependencies)
2. `leaderboard.js` second (pure function, no dependencies)
3. `health.js` third (trivial)
4. `dashboard.js` last (orchestrates 1-3, depends on SSE and `/state` being live)
5. `index.html` and `style.css` in parallel with 1-4

**Test-first approach:** Write automated tests for `board.js`, `leaderboard.js`, `health.js` before implementation. Manual tests (hour-long stability, six-metre legibility) after initial integration.
