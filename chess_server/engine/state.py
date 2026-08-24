"""Process state, deliberately not tables (role spec §5.1, §6.4, §9.3).

Nothing here survives a restart, because §7.1 aborts every live game anyway.
"""

mailbox: dict[int, object] = {}          # bot_id -> the turn payload awaiting a poll
history: dict[int, list[str]] = {}       # game_id -> [STARTING_FEN, *fen_after]
history_san: dict[int, list[str]] = {}   # game_id -> the SAN of every ply, in order
unpaired_ticks: dict[int, int] = {}      # bot_id -> consecutive ticks left idle
connected: set[int] = set()              # bot_ids with a live SSE or poll presence


def clear_all() -> None:
    """Empty every container in place: recovery captures this before it runs, and
    rebinding the names would leave every other holder pointing at the old dicts."""
    mailbox.clear()
    history.clear()
    history_san.clear()
    unpaired_ticks.clear()
    connected.clear()
