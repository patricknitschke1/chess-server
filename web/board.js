// One board panel: names, ratings, clocks, 8x8 grid. Every string that came
// from an attendee is written with textContent. innerHTML never touches them.

class BoardPanel {
  constructor(container) {
    this.container = container;
    this.gameId = null;
    this.squares = [];
    this._build();
  }

  _build() {
    this.container.classList.add('board-panel');
    this.container.replaceChildren();

    this.blackLine = this._makePlayerLine();
    this.grid = document.createElement('div');
    this.grid.className = 'board-grid';
    for (let i = 0; i < 64; i++) {
      const cell = document.createElement('div');
      const rank = Math.floor(i / 8);
      const file = i % 8;
      cell.className = 'sq ' + ((rank + file) % 2 === 0 ? 'light' : 'dark');
      this.grid.appendChild(cell);
      this.squares.push(cell);
    }
    this.whiteLine = this._makePlayerLine();

    this.badge = document.createElement('div');
    this.badge.className = 'badge';

    this.container.append(this.badge, this.blackLine.row, this.grid, this.whiteLine.row);
  }

  _makePlayerLine() {
    const row = document.createElement('div');
    row.className = 'player';
    const name = document.createElement('span');
    name.className = 'player-name';
    const rating = document.createElement('span');
    rating.className = 'player-rating';
    const clock = document.createElement('span');
    clock.className = 'player-clock';
    row.append(name, rating, clock);
    return { row, name, rating, clock };
  }

  setEmpty() {
    this.gameId = null;
    this.container.classList.add('empty');
    this.container.classList.remove('ended', 'featured');
    this.badge.textContent = 'waiting for a game';
    for (const line of [this.whiteLine, this.blackLine]) {
      line.name.textContent = '';
      line.rating.textContent = '';
      line.clock.textContent = '';
    }
    for (const cell of this.squares) cell.textContent = '';
  }

  setGame(game) {
    this.gameId = game.game_id;
    this.container.classList.remove('empty', 'ended');
    this.container.classList.toggle('featured', !!game.is_featured);
    this.badge.textContent = game.is_featured ? 'FEATURED' : `game #${game.game_id}`;
    this.whiteLine.name.textContent = game.white_bot_name;
    this.whiteLine.rating.textContent = String(game.white_rating);
    this.blackLine.name.textContent = game.black_bot_name;
    this.blackLine.rating.textContent = String(game.black_rating);
    this.renderFen(game.fen);
    this.setTurn(game.to_move);
  }

  renderFen(fen) {
    const grid = fenToGrid(fen);
    for (let rank = 0; rank < 8; rank++) {
      for (let file = 0; file < 8; file++) {
        this.squares[rank * 8 + file].textContent = pieceGlyph(grid[rank][file]);
      }
    }
  }

  setTurn(toMove) {
    this.whiteLine.row.classList.toggle('to-move', toMove === 'w');
    this.blackLine.row.classList.toggle('to-move', toMove === 'b');
  }

  setClocks(whiteMs, blackMs) {
    this.whiteLine.clock.textContent = formatClock(whiteMs);
    this.blackLine.clock.textContent = formatClock(blackMs);
  }

  setResult(text) {
    this.container.classList.add('ended');
    this.badge.textContent = text;
  }
}
