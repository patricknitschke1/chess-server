// Pure helpers: no DOM, no network, no clock reads. Everything here is a
// function of its arguments, which is what makes it checkable by eye.

const GLYPHS = {
  K: '\u2654', Q: '\u2655', R: '\u2656', B: '\u2657', N: '\u2658', P: '\u2659',
  k: '\u265A', q: '\u265B', r: '\u265C', b: '\u265D', n: '\u265E', p: '\u265F',
};

// FEN placement field -> 8 ranks of 8 cells, rank 8 first. Cells are the piece
// letter or null. Never applies moves: the server sends a whole FEN per event
// because non-featured boards are coalesced and plies go missing.
function fenToGrid(fen) {
  const placement = String(fen || '').split(' ')[0];
  const grid = [];
  for (const rank of placement.split('/')) {
    const row = [];
    for (const ch of rank) {
      if (ch >= '1' && ch <= '8') {
        for (let i = 0; i < Number(ch); i++) row.push(null);
      } else {
        row.push(ch);
      }
    }
    while (row.length < 8) row.push(null);
    grid.push(row.slice(0, 8));
  }
  while (grid.length < 8) grid.push([null, null, null, null, null, null, null, null]);
  return grid.slice(0, 8);
}

function pieceGlyph(letter) {
  return letter === null || letter === undefined ? '' : (GLYPHS[letter] || '');
}

function formatClock(ms) {
  const total = Math.max(0, Math.floor(Number(ms) || 0) / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = Math.floor(total % 60);
  const tenths = Math.floor((total * 10) % 10);
  const body = `${minutes}:${String(seconds).padStart(2, '0')}`;
  return total < 20 ? `${body}.${tenths}` : body;
}

// Numeric on purpose. Lexicographic comparison makes "9" > "10" and quietly
// throws away every event after the first two digits.
function isNewer(envelope, runId, lastSeq) {
  return envelope.run === runId && Number(envelope.seq) > Number(lastSeq);
}

// A gap is expected here, not exceptional: non-featured move_played events are
// coalesced to 2 Hz server-side, so seq numbers legitimately skip.
function seqGap(prevSeq, nextSeq) {
  return Number(nextSeq) - Number(prevSeq) - 1;
}

// Featured takes slot 0; surviving games keep the slot they already hold; the
// rest fill lowest-empty-slot first. Stability is the point — reshuffling every
// board because one game ended is unwatchable from the back of the room.
function assignSlots(prevSlots, activeIds, featuredId, slotCount) {
  const count = slotCount || 4;
  const active = new Set(activeIds);
  const slots = new Array(count).fill(null);
  const placed = new Set();

  if (featuredId !== null && featuredId !== undefined && active.has(featuredId)) {
    slots[0] = featuredId;
    placed.add(featuredId);
  }
  for (let i = 0; i < count; i++) {
    const held = (prevSlots || [])[i];
    if (held !== null && held !== undefined && active.has(held)
        && !placed.has(held) && slots[i] === null) {
      slots[i] = held;
      placed.add(held);
    }
  }
  const spare = activeIds.filter((id) => !placed.has(id));
  for (let i = 0; i < count && spare.length; i++) {
    if (slots[i] === null) slots[i] = spare.shift();
  }
  return slots;
}

// Remaining time for the side to move, counted down locally between events so
// the boards do not look frozen. `elapsedSinceAnchorMs` is monotonic-ish browser
// time, never wall clock.
function remainingMs(clockMs, turnElapsedMs, elapsedSinceAnchorMs) {
  return Math.max(0, clockMs - (turnElapsedMs || 0) - Math.max(0, elapsedSinceAnchorMs));
}

if (typeof module !== 'undefined') {
  module.exports = {
    fenToGrid, pieceGlyph, formatClock, isNewer, seqGap, assignSlots, remainingMs,
  };
}
