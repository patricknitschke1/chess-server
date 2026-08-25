"""Featured-game selection (design §11, §14; interfaces "Decisions" §4 and §8).

Which board is on the projector is process state, not a fact about a game, which
is why the hub stamps `is_featured` at publish time and no transaction writes it.
The hold exists because ranking alone flips the display every time a rating moves,
and a board that changes mid-move is unwatchable from the back of the room.
"""
from dataclasses import dataclass, field
from typing import Optional, Sequence

from chess_core import is_within

# Numerically POLL_HOLD_NS, semantically unrelated: this is how long a board stays
# on the projector, not how long a poll is held. Importing the poll constant here
# would assert a dependency that does not exist.
FEATURED_HOLD_NS = 20_000_000_000


def _rank(summary: dict) -> tuple[int, int]:
    """Highest rating sum first, ties to the lowest game id."""
    return (-(summary["white_rating"] + summary["black_rating"]), summary["game_id"])


@dataclass
class FeaturedSelector:
    hold_ns: int = FEATURED_HOLD_NS
    held_game_id: Optional[int] = None
    held_since_mono: int = 0

    def current(self, summaries: Sequence[dict], now_mono: int) -> Optional[int]:
        best = min(summaries, key=_rank)["game_id"] if summaries else None
        if self.held_game_id not in {s["game_id"] for s in summaries}:
            return self._hold(best, now_mono)
        if best == self.held_game_id:
            return self.held_game_id
        if is_within(self.held_since_mono, now_mono, self.hold_ns):
            return self.held_game_id
        return self._hold(best, now_mono)

    def clear(self) -> None:
        self.held_game_id = None
        self.held_since_mono = 0

    def _hold(self, game_id: Optional[int], now_mono: int) -> Optional[int]:
        self.held_game_id = game_id
        self.held_since_mono = now_mono
        return game_id
