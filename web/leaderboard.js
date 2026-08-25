// Top-N leaderboard rail. Rating comes from `rating_changed` between /state
// refreshes; the row is rebuilt rather than patched because 10 rows is nothing.

class LeaderboardRail {
  constructor(container, topN) {
    this.container = container;
    this.topN = topN || 10;
    this.bots = [];
    this.flash = new Map();
  }

  render(bots) {
    this.bots = bots.slice();
    this._paint();
  }

  applyRatingChange(data) {
    const bot = this.bots.find((b) => b.bot_id === data.bot_id);
    if (bot) {
      bot.rating = data.rating_after;
      bot.games_played += 1;
      bot.is_provisional = bot.games_played < 10;
    }
    this.flash.set(data.bot_id, { delta: data.delta, until: Date.now() + 6000 });
    this._paint();
  }

  _paint() {
    const rows = this.bots
      .slice()
      .sort((a, b) => b.rating - a.rating || a.bot_name.localeCompare(b.bot_name))
      .slice(0, this.topN);

    const now = Date.now();
    this.container.replaceChildren(...rows.map((bot, index) => {
      const row = document.createElement('div');
      row.className = 'lb-row';

      const rank = document.createElement('span');
      rank.className = 'lb-rank';
      rank.textContent = String(index + 1);

      const name = document.createElement('span');
      name.className = 'lb-name';
      name.textContent = bot.display_name || bot.bot_name;          // attendee-controlled
      if (bot.is_provisional) name.classList.add('provisional');

      const rating = document.createElement('span');
      rating.className = 'lb-rating';
      rating.textContent = bot.is_provisional ? `${bot.rating}?` : String(bot.rating);

      const record = document.createElement('span');
      record.className = 'lb-record';
      record.textContent = `${bot.wins}-${bot.losses}-${bot.draws}`;

      const delta = document.createElement('span');
      delta.className = 'lb-delta';
      const flash = this.flash.get(bot.bot_id);
      if (flash && flash.until > now) {
        delta.textContent = flash.delta > 0 ? `+${flash.delta}` : String(flash.delta);
        delta.classList.add(flash.delta >= 0 ? 'up' : 'down');
      }

      row.append(rank, name, rating, record, delta);
      return row;
    }));
  }
}
