// Main controller: SSE first, then the /state snapshot, then the buffered gap.

const SLOT_COUNT = 4;
const CLOCK_TICK_MS = 100;
const STATE_REFRESH_MS = 15000;
const RESYNC_DEBOUNCE_MS = 2000;
const TICKER_MAX = 8;

class Dashboard {
  constructor() {
    this.runId = null;
    this.lastSeq = -1;
    this.ready = false;
    this.buffer = [];
    this.games = new Map();     // game_id -> live summary + clock anchor
    this.slots = new Array(SLOT_COUNT).fill(null);
    this.featuredGameId = null;
    this.resyncTimer = null;
    this.source = null;

    this.panels = [];
    const grid = document.getElementById('board-grid');
    for (let i = 0; i < SLOT_COUNT; i++) {
      const cell = document.createElement('div');
      grid.appendChild(cell);
      const panel = new BoardPanel(cell);
      panel.setEmpty();
      this.panels.push(panel);
    }
    this.leaderboard = new LeaderboardRail(document.getElementById('leaderboard'), 10);
    this.health = new HealthBanner(document.getElementById('health-banner'));
    this.ticker = document.getElementById('ticker');
  }

  async init() {
    this.connect();
    await this.snapshot();
    this.drainBuffer();
    setInterval(() => this.tickClocks(), CLOCK_TICK_MS);
    setInterval(() => this.snapshot(), STATE_REFRESH_MS);
  }

  // --- SSE -----------------------------------------------------------------

  connect() {
    const source = new EventSource('/events');
    this.source = source;
    const types = [
      'server_run_started', 'game_created', 'game_started', 'move_played',
      'game_ended', 'rating_changed', 'bot_registered', 'bot_connected',
      'bot_disconnected', 'health_tick',
    ];
    // Named events, so `onmessage` never fires — the server sets `event:` on
    // every frame.
    for (const type of types) {
      source.addEventListener(type, (msg) => this.receive(type, msg));
    }
    source.onerror = () => {
      this.health.disconnected();
      // The browser retries on its own; a full resync on reopen repairs the gap.
      this.scheduleResync();
    };
  }

  receive(type, msg) {
    let envelope;
    try {
      envelope = JSON.parse(msg.data);
    } catch (err) {
      return;
    }
    if (!this.ready) {
      this.buffer.push(envelope);
      return;
    }
    if (envelope.run !== this.runId) {
      this.scheduleResync();     // a restart: every board we hold is stale
      return;
    }
    if (Number(envelope.seq) <= Number(this.lastSeq)) return;
    this.lastSeq = Number(envelope.seq);
    this.handle(type, envelope.data);
  }

  drainBuffer() {
    const pending = this.buffer;
    this.buffer = [];
    this.ready = true;
    for (const envelope of pending) {
      if (!isNewer(envelope, this.runId, this.lastSeq)) continue;
      this.lastSeq = Number(envelope.seq);
      this.handle(envelope.event_type, envelope.data);
    }
  }

  // --- snapshot ------------------------------------------------------------

  async snapshot() {
    let state;
    try {
      state = await (await fetch('/state')).json();
    } catch (err) {
      return;
    }
    this.runId = state.run_id;
    this.lastSeq = Number(state.event_id);
    this.featuredGameId = state.featured_game_id;

    this.games.clear();
    for (const game of state.active_games) this.games.set(game.game_id, this.adopt(game));
    this.leaderboard.render(state.leaderboard);
    this.reslot();
  }

  scheduleResync() {
    if (this.resyncTimer !== null) return;
    this.resyncTimer = setTimeout(async () => {
      this.resyncTimer = null;
      await this.snapshot();
    }, RESYNC_DEBOUNCE_MS);
  }

  adopt(game) {
    return { ...game, anchor: performance.now() };
  }

  // --- events --------------------------------------------------------------

  handle(type, data) {
    switch (type) {
      case 'server_run_started':
        this.scheduleResync();
        break;
      case 'game_created':
      case 'game_started':
        // Neither event carries a FEN or ratings, so the snapshot supplies them.
        if (!this.games.has(data.game_id)) this.scheduleResync();
        break;
      case 'move_played':
        this.onMove(data);
        break;
      case 'game_ended':
        this.onEnd(data);
        break;
      case 'rating_changed':
        this.leaderboard.applyRatingChange(data);
        break;
      case 'health_tick':
        this.health.update(data);
        break;
      default:
        break;
    }
  }

  onMove(data) {
    const game = this.games.get(data.game_id);
    if (!game) {
      this.scheduleResync();
      return;
    }
    // Always from the event's FEN. Non-featured boards are coalesced to 2 Hz,
    // so applying moves incrementally would drift a board silently wrong.
    Object.assign(game, {
      fen: data.fen,
      to_move: data.to_move,
      ply: data.ply,
      white_ms: data.white_ms,
      black_ms: data.black_ms,
      turn_elapsed_ms: 0,
      is_featured: data.is_featured,
      anchor: performance.now(),
    });
    const panel = this.panelFor(data.game_id);
    if (panel) {
      panel.renderFen(game.fen);
      panel.setTurn(game.to_move);
    }
  }

  onEnd(data) {
    const panel = this.panelFor(data.game_id);
    if (panel) panel.setResult(this.resultText(data));
    this.games.delete(data.game_id);
    this.pushTicker(data);
    // Held briefly so the final position is readable, then the slot refills.
    setTimeout(() => this.reslot(), RESYNC_DEBOUNCE_MS);
    this.scheduleResync();
  }

  resultText(data) {
    if (data.result === 'white_win') return `white wins \u2014 ${data.termination}`;
    if (data.result === 'black_win') return `black wins \u2014 ${data.termination}`;
    if (data.result === 'draw') return `draw \u2014 ${data.termination}`;
    return `${data.status} \u2014 ${data.termination}`;
  }

  pushTicker(data) {
    const row = document.createElement('div');
    row.className = 'ticker-row ' + (data.rated ? 'rated' : 'unrated');

    const white = document.createElement('span');
    white.textContent = data.white_bot_name;      // attendee-controlled
    const verb = document.createElement('span');
    verb.className = 'ticker-verb';
    verb.textContent = data.result === 'white_win' ? ' beat '
      : data.result === 'black_win' ? ' lost to '
        : data.result === 'draw' ? ' drew with '
          : ' vs ';
    const black = document.createElement('span');
    black.textContent = data.black_bot_name;      // attendee-controlled
    const how = document.createElement('span');
    how.className = 'ticker-how';
    how.textContent = ` · ${data.termination}${data.rated ? '' : ' · unrated'}`;

    row.append(white, verb, black, how);
    this.ticker.prepend(row);
    while (this.ticker.childElementCount > TICKER_MAX) {
      this.ticker.lastElementChild.remove();
    }
  }

  // --- slots and clocks ----------------------------------------------------

  panelFor(gameId) {
    const index = this.slots.indexOf(gameId);
    return index === -1 ? null : this.panels[index];
  }

  reslot() {
    const ids = [...this.games.keys()].sort((a, b) => a - b);
    this.slots = assignSlots(this.slots, ids, this.featuredGameId, SLOT_COUNT);
    this.slots.forEach((gameId, index) => {
      const panel = this.panels[index];
      const game = gameId === null ? null : this.games.get(gameId);
      if (!game) {
        if (panel.gameId === null || !this.games.has(panel.gameId)) panel.setEmpty();
        return;
      }
      panel.setGame({ ...game, is_featured: game.game_id === this.featuredGameId });
    });
  }

  tickClocks() {
    const now = performance.now();
    for (let i = 0; i < this.slots.length; i++) {
      const gameId = this.slots[i];
      const game = gameId === null ? null : this.games.get(gameId);
      if (!game) continue;
      const elapsed = now - game.anchor;
      const running = game.status !== 'pending';
      const white = game.to_move === 'w' && running
        ? remainingMs(game.white_ms, game.turn_elapsed_ms, elapsed)
        : remainingMs(game.white_ms, 0, 0);
      const black = game.to_move === 'b' && running
        ? remainingMs(game.black_ms, game.turn_elapsed_ms, elapsed)
        : remainingMs(game.black_ms, 0, 0);
      this.panels[i].setClocks(white, black);
    }
  }
}

new Dashboard().init();
