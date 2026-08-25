"""Featured-game selection and move coalescing (design §11, §14).

Both are presentation choices keyed on process state, so nothing here touches a
transaction and no test sleeps: the hold and the throttle read an injected clock.
"""
import pytest

from chess_server.api.featured import FEATURED_HOLD_NS, FeaturedSelector
from chess_server.api.sse import MOVE_COALESCE_NS, Hub

MS = 1_000_000


def summary(game_id: int, white_rating: int, black_rating: int) -> dict:
    return {
        "game_id": game_id,
        "white_rating": white_rating,
        "black_rating": black_rating,
    }


@pytest.fixture
def selector():
    return FeaturedSelector()


@pytest.fixture
def coalescing_hub(clock):
    hub = Hub()
    hub.now_mono = clock
    return hub


def _moves(hub, game_id, count):
    for ply in range(count):
        hub.publish(ply, "move_played", {"game_id": game_id, "ply": ply})


def _published_for(client, game_id):
    return [
        envelope
        for envelope in client.queue
        if envelope["data"].get("game_id") == game_id
    ]


# --- 1. the hold -------------------------------------------------------------

def test_a_higher_rated_game_does_not_steal_the_board_before_the_hold_expires(
    selector, clock
):
    quiet = [summary(1, 1200, 1200)]
    assert selector.current(quiet, clock()) == 1
    loud = quiet + [summary(2, 1600, 1600)]

    clock.advance(FEATURED_HOLD_NS - 1_000 * MS)
    assert selector.current(loud, clock()) == 1

    clock.advance(2_000 * MS)
    assert selector.current(loud, clock()) == 2


def test_the_held_game_leaving_the_active_set_reselects_immediately(selector, clock):
    both = [summary(1, 1600, 1600), summary(2, 1200, 1200)]
    assert selector.current(both, clock()) == 1

    clock.advance(1_000 * MS)  # far inside the hold
    assert selector.current([summary(2, 1200, 1200)], clock()) == 2


# --- 2. ranking --------------------------------------------------------------

def test_ties_go_to_the_lowest_game_id_and_repeated_calls_are_stable(selector, clock):
    tied = [summary(7, 1300, 1300), summary(3, 1300, 1300), summary(5, 1300, 1300)]
    first = selector.current(tied, clock())
    assert first == 3
    for _ in range(5):
        clock.advance(FEATURED_HOLD_NS * 2)
        assert selector.current(tied, clock()) == 3


def test_an_empty_active_set_is_none_and_does_not_crash(selector, clock):
    assert selector.current([], clock()) is None
    assert selector.current([summary(1, 1200, 1200)], clock()) == 1
    assert selector.current([], clock()) is None


# --- 3. coalescing -----------------------------------------------------------

def test_five_non_featured_moves_inside_the_window_publish_one(coalescing_hub, clock):
    client = coalescing_hub.subscribe()

    for _ in range(5):
        clock.advance(MOVE_COALESCE_NS // 10)
        _moves(coalescing_hub, 42, 1)

    assert len(_published_for(client, 42)) == 1

    clock.advance(MOVE_COALESCE_NS)
    _moves(coalescing_hub, 42, 1)
    assert len(_published_for(client, 42)) == 2


def test_the_featured_game_bypasses_the_throttle(coalescing_hub, clock):
    coalescing_hub.featured_game_id = 42
    client = coalescing_hub.subscribe()

    for _ in range(5):
        clock.advance(MOVE_COALESCE_NS // 10)
        _moves(coalescing_hub, 42, 1)

    assert len(_published_for(client, 42)) == 5


def test_throttling_is_per_game_not_global(coalescing_hub, clock):
    client = coalescing_hub.subscribe()
    _moves(coalescing_hub, 1, 1)
    _moves(coalescing_hub, 2, 1)

    assert len(_published_for(client, 1)) == 1
    assert len(_published_for(client, 2)) == 1


def test_game_ended_forgets_the_throttle_so_a_replay_is_not_swallowed(
    coalescing_hub, clock
):
    client = coalescing_hub.subscribe()
    _moves(coalescing_hub, 42, 1)
    coalescing_hub.publish(1, "game_ended", {"game_id": 42})

    _moves(coalescing_hub, 42, 1)
    assert len(_published_for(client, 42)) == 3  # move, game_ended, move


# --- 4. the stamp ------------------------------------------------------------

def test_is_featured_is_true_on_exactly_the_featured_game(coalescing_hub, clock):
    coalescing_hub.featured_game_id = 7
    client = coalescing_hub.subscribe()

    coalescing_hub.publish(0, "move_played", {"game_id": 7, "ply": 0})
    coalescing_hub.publish(1, "move_played", {"game_id": 8, "ply": 0})

    stamped = {
        envelope["data"]["game_id"]: envelope["data"]["is_featured"]
        for envelope in client.queue
    }
    assert stamped == {7: True, 8: False}


def test_no_other_event_type_is_stamped_or_throttled(coalescing_hub, clock):
    client = coalescing_hub.subscribe()
    for seq in range(5):
        coalescing_hub.publish(seq, "game_created", {"game_id": 42})

    assert len(client.queue) == 5
    assert all("is_featured" not in e["data"] for e in client.queue)
