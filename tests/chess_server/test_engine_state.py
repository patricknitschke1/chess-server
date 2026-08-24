"""Process state the engine owns (role spec §5.1, §6.4, §9.3)."""
from chess_server.engine import state


def _populate():
    state.mailbox[1] = "payload"
    state.history[1] = ["fen"]
    state.unpaired_ticks[1] = 3
    state.connected.add(1)


def test_clear_all_empties_every_container():
    _populate()
    state.clear_all()
    assert not state.mailbox
    assert not state.history
    assert not state.unpaired_ticks
    assert not state.connected


def test_clear_all_mutates_in_place_rather_than_rebinding():
    """Recovery captures `clear_all` before it runs; rebinding the names would
    empty objects nobody else is holding."""
    before = [id(c) for c in (state.mailbox, state.history, state.unpaired_ticks,
                              state.connected)]
    _populate()
    state.clear_all()
    after = [id(c) for c in (state.mailbox, state.history, state.unpaired_ticks,
                             state.connected)]
    assert before == after
