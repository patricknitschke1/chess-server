"""Design §10.4: scripted fake bots playing complete games over the real endpoints.

Nothing here reaches into the engine to make progress. The bots register, poll,
and move over HTTP; the only in-process call is `_tick_once`, which stands in for
the supervised ticker's sleep so the suite costs milliseconds rather than seconds.

Events are read off the real `Hub` — the same sink `Txn.flush` publishes to — by
subscribing a client, which is what `GET /events` does with the frames.
"""
import httpx
import pytest

from chess_core import STARTING_RATING
from chess_server.api.app import create_app
from chess_server.api.settings import Settings
from chess_server.api.state import AppState
from chess_server.engine.ticker import TickerMetrics, _tick_once

from tests.chess_server.conftest import ADMIN_TOKEN, JOIN_CODE

# 1. f3 e5 2. g4 Qh4#. Scripted by colour, because the matchmaker picks them.
FOOLS_MATE = {"white": ["f2f3", "g2g4"], "black": ["e7e5", "d8h4"]}

# Enough to refill the per-token bucket between calls; far short of a flag and
# far short of the 500ms move coalescing window.
STEP_NS = 50_000_000

MOVE_LIMIT = 40


@pytest.fixture
def arena_state(store, clock):
    """Like the `api_state` fixture, but with the Hub left as the event sink so
    the harness can assert on what a dashboard would actually receive."""
    return AppState(
        store=store,
        settings=Settings(
            db_path=store.path,
            join_code=JOIN_CODE,
            admin_token=ADMIN_TOKEN,
            # The hold length is the server's business; a test should not sit
            # through it while waiting to be told there is no game yet.
            poll_hold_seconds=0.01,
        ),
        now_mono=clock,
    )


@pytest.fixture
async def http(arena_state):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(arena_state)),
        base_url="http://arena.test",
    ) as client:
        yield client


@pytest.fixture
def spectator(arena_state):
    """A subscribed SSE client, attached before any game exists."""
    return arena_state.hub.subscribe()


def drain(spectator) -> list[dict]:
    envelopes = list(spectator.queue)
    spectator.queue.clear()
    return envelopes


class FakeBot:
    """A scripted competitor speaking only HTTP."""

    def __init__(self, http, clock, name, script=None):
        self.http = http
        self.clock = clock
        self.name = name
        self.script = script
        self.played = 0
        self.bot_id = None
        self.token = None
        self.rejections: list[dict] = []
        self.premoves: dict[int, str] = {}   # ply -> a move to try before the script

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def register(self) -> None:
        response = await self.http.post("/bots", json={
            "name": self.name, "owner": self.name, "join_code": JOIN_CODE,
        })
        assert response.status_code == 201, response.text
        body = response.json()
        self.bot_id, self.token = body["bot_id"], body["token"]

    async def poll(self) -> dict:
        self.clock.advance(STEP_NS)
        response = await self.http.get("/bots/me/turn", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()

    async def me(self) -> dict:
        response = await self.http.get("/bots/me", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()

    def _uci(self, turn: dict) -> str:
        if self.script is None:
            return sorted(turn["legal_moves"])[0]
        return self.script[turn["color"]][self.played]

    async def _submit(self, turn: dict, uci: str) -> httpx.Response:
        self.clock.advance(STEP_NS)
        return await self.http.post(
            f"/games/{turn['game_id']}/moves",
            json={"ply": turn["ply"], "move": uci},
            headers=self.headers,
        )

    async def take_turn(self) -> bool:
        """One poll, and a move if there is one to make. True if it moved."""
        turn = await self.poll()
        if turn.get("game_id") is None:
            return False
        premove = self.premoves.pop(turn["ply"], None)
        if premove is not None:
            rejection = await self._submit(turn, premove)
            assert rejection.status_code == 400, rejection.text
            self.rejections.append(rejection.json())
        response = await self._submit(turn, self._uci(turn))
        assert response.status_code == 200, response.text
        self.played += 1
        return True


async def tick(arena_state) -> None:
    await _tick_once(arena_state.deps, arena_state.metrics or TickerMetrics())


async def play_out(arena_state, http, *bots) -> dict:
    """Poll-and-move round robin until the game is terminal. Returns the detail."""
    game_id = None
    for _ in range(MOVE_LIMIT):
        for bot in bots:
            seat = (await bot.me())["current_game_id"]
            if seat is not None:
                game_id = seat
            await bot.take_turn()
        if game_id is not None:
            detail = (await http.get(f"/games/{game_id}")).json()
            if detail["status"] in ("finished", "aborted"):
                return detail
    raise AssertionError(f"game {game_id} never reached a terminal status")


async def start_game(arena_state, http, *bots) -> None:
    """Register, become pool-eligible, and let the ticker seat the pairing."""
    for bot in bots:
        await bot.register()
    for bot in bots:
        assert (await bot.poll())["reason"] == "waiting_for_pairing"
    await tick(arena_state)
    for bot in bots:
        assert (await bot.me())["current_game_id"] is not None


@pytest.fixture
def scripted(http, clock):
    return (
        FakeBot(http, clock, "mater", FOOLS_MATE),
        FakeBot(http, clock, "matee", FOOLS_MATE),
    )


# -- The failure paths, first ------------------------------------------------


async def test_an_illegal_move_is_refused_and_the_bot_still_finishes_the_game(
    arena_state, http, scripted
):
    """A strike is recorded, the position does not move, and the game completes."""
    one, two = scripted
    await start_game(arena_state, http, one, two)
    one.premoves[0] = "e2e5"
    two.premoves[0] = "e2e5"

    detail = await play_out(arena_state, http, one, two)

    offender = one if one.rejections else two
    assert len(offender.rejections) == 1
    rejection = offender.rejections[0]
    assert "e2e5" in rejection["error"]
    assert rejection["details"]["strikes"] == 1
    assert rejection["details"]["forfeited"] is False
    assert detail["status"] == "finished"
    assert detail["termination"] == "checkmate"
    # The refused move left no trace in the game record.
    assert "e2e5" not in [row["uci"] for row in
                          (await http.get(f"/games/{detail['game_id']}/moves")).json()["moves"]]


async def test_a_bot_that_stops_polling_is_flagged_and_the_game_finalises(
    arena_state, http, clock, scripted
):
    """The opponent's clock never starts, so only the delivered side can flag."""
    one, two = scripted
    await start_game(arena_state, http, one, two)
    game_id = (await one.me())["current_game_id"]
    detail = (await http.get(f"/games/{game_id}")).json()
    white = one if detail["white_bot_id"] == one.bot_id else two

    # The poll delivers, which is what starts white's clock. Then it goes quiet.
    assert (await white.poll())["color"] == "white"
    clock.advance(detail["time_control_ms"] * 1_000_000 * 2)
    await tick(arena_state)

    detail = (await http.get(f"/games/{game_id}")).json()
    assert detail["status"] == "finished"
    assert detail["termination"] == "flag"
    assert detail["result"] == "black_win"
    for bot in (one, two):
        assert (await bot.me())["current_game_id"] is None


# -- A complete game ---------------------------------------------------------


async def test_two_scripted_bots_play_a_complete_game_over_the_real_endpoints(
    arena_state, http, scripted
):
    one, two = scripted
    await start_game(arena_state, http, one, two)

    detail = await play_out(arena_state, http, one, two)

    assert detail["status"] == "finished"
    assert detail["termination"] == "checkmate"
    assert detail["result"] == "black_win"
    assert detail["history_san"] == ["f3", "e5", "g4", "Qh4#"]
    assert detail["rated"] is True
    assert detail["source"] == "matchmaker"
    assert detail["ended_at"] is not None
    for bot in (one, two):
        assert (await bot.me())["current_game_id"] is None


async def test_a_decisive_rated_game_moves_both_ratings_zero_sum(
    arena_state, http, scripted
):
    one, two = scripted
    await start_game(arena_state, http, one, two)
    detail = await play_out(arena_state, http, one, two)

    after = {bot.bot_id: await bot.me() for bot in (one, two)}
    assert detail["result"] in ("white_win", "black_win")
    winner_id = (detail["white_bot_id"] if detail["result"] == "white_win"
                 else detail["black_bot_id"])
    loser_id = next(bot_id for bot_id in after if bot_id != winner_id)

    assert after[winner_id]["rating"] > STARTING_RATING
    assert after[loser_id]["rating"] < STARTING_RATING
    assert (after[winner_id]["rating"] - STARTING_RATING) == -(
        after[loser_id]["rating"] - STARTING_RATING
    )
    assert after[winner_id]["wins"] == 1 and after[loser_id]["losses"] == 1

    for bot_id, me in after.items():
        body = (await http.get(f"/bots/{bot_id}/rating_history")).json()
        assert len(body["points"]) == 1
        point = body["points"][0]
        assert point["game_id"] == detail["game_id"]
        assert point["rating_after"] == me["rating"]
        assert point["delta"] == me["rating"] - STARTING_RATING


# -- The story the dashboard is told -----------------------------------------


async def test_the_event_stream_tells_the_same_story_as_the_database(
    arena_state, http, spectator, scripted
):
    one, two = scripted
    await start_game(arena_state, http, one, two)
    detail = await play_out(arena_state, http, one, two)

    envelopes = drain(spectator)
    seqs = [envelope["seq"] for envelope in envelopes]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    types = [envelope["event_type"] for envelope in envelopes]
    created = types.index("game_created")
    started = types.index("game_started")
    ended = types.index("game_ended")
    moves = [index for index, name in enumerate(types) if name == "move_played"]
    ratings = [index for index, name in enumerate(types) if name == "rating_changed"]

    assert types.index("bot_registered") < created < started
    assert moves and started < min(moves) and max(moves) < ended
    # Non-featured move_played is coalesced at 2Hz, so four plies are not four events.
    assert len(moves) <= 4
    assert len(ratings) == 2 and min(ratings) > ended

    game_ids = {envelope["data"]["game_id"] for envelope in envelopes
                if "game_id" in envelope["data"]}
    assert game_ids == {detail["game_id"]}
    assert envelopes[ended]["data"]["termination"] == "checkmate"
    assert envelopes[ended]["data"]["result"] == detail["result"]

    changes = [envelopes[index]["data"] for index in ratings]
    assert sum(change["delta"] for change in changes) == 0
    for change in changes:
        assert change["rating_before"] + change["delta"] == change["rating_after"]
