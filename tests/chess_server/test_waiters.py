"""One waiter per bot, and supersede is a signal distinct from wake (role spec §5.4).

Supersede cancels a *waiter*. It can never discard a *delivery*, because the
delivery is in the mailbox and the mailbox outlives the request.
"""
from chess_server.engine.mailbox import WaiterRegistry


def test_a_second_registration_supersedes_the_first():
    registry = WaiterRegistry()

    first = registry.register(7)
    second = registry.register(7)

    assert first.superseded is True
    assert first.event.is_set()
    # The flag is what lets the loser answer 'superseded' instead of draining the
    # payload — one position handed to two connections — or reporting
    # 'waiting_for_pairing', which is a different fact.
    assert second.superseded is False
    assert not second.event.is_set()
    assert registry.held_count() == 1


def test_wake_sets_the_event_without_superseding():
    registry = WaiterRegistry()
    waiter = registry.register(7)

    registry.wake(7)

    assert waiter.event.is_set()
    assert waiter.superseded is False


def test_waking_a_bot_that_is_not_polling_is_a_no_op():
    """The ticker wakes both seats on every pairing, and most bots are not held."""
    registry = WaiterRegistry()

    registry.wake(404)

    assert registry.held_count() == 0


def test_discard_removes_only_the_waiter_it_was_handed():
    """A superseded waiter cleaning up in its `finally` must not evict its
    successor, which would leave the live poll registered nowhere and never woken."""
    registry = WaiterRegistry()
    first = registry.register(7)
    second = registry.register(7)

    registry.discard(7, first)

    assert registry.held_count() == 1
    registry.wake(7)
    assert second.event.is_set()


def test_held_count_tracks_register_and_discard():
    registry = WaiterRegistry()

    a = registry.register(1)
    b = registry.register(2)
    assert registry.held_count() == 2

    registry.discard(1, a)
    registry.discard(2, b)
    assert registry.held_count() == 0


def test_on_register_fires_once_the_waiter_is_reachable():
    """It exists so a test can observe registration without sleeping."""
    seen = []
    registry = WaiterRegistry(on_register=lambda bot_id: seen.append(
        (bot_id, registry.held_count())
    ))

    registry.register(7)

    assert seen == [(7, 1)]
