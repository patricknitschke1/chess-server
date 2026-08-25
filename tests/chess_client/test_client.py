"""SDK behaviour against the real server, plus the wire faults it cannot stage."""
import httpx
import chess
import pytest

from chess_client import (
    ChessClient,
    ClientError,
    ClockView,
    GameEnded,
    MoveRejected,
    NotYourTurn,
    RateLimited,
    ServerError,
    TokenInvalid,
)
from chess_client.client import IDLE_SECONDS, MAX_BACKOFF_SECONDS
from tests.chess_client.conftest import JOIN_CODE, ScriptedTransport


class Stop(Exception):
    """Raised from a bot to end `run()` in a test."""


def client_for(transport, token=None) -> ChessClient:
    return ChessClient("http://arena.test", token, transport=transport)


def paired(seed_bot, make_game):
    """A game where `alice` is white and on move, holding a known token."""
    white = seed_bot("alice", "tok-alice")
    black = seed_bot("bob", "tok-bob")
    game_id = make_game(white, black)
    return game_id


# -- Registration -----------------------------------------------------------


def test_register_returns_a_token_and_arms_the_client(transport):
    client = client_for(transport)
    token = client.register("alice", "alice", JOIN_CODE)
    assert token
    assert client.token == token


def test_register_with_a_bad_join_code_says_what_to_do(transport):
    with pytest.raises(ClientError) as exc:
        client_for(transport).register("alice", "alice", "nope")
    assert "join code" in str(exc.value).lower()
    assert "host" in str(exc.value).lower()


def test_register_with_a_taken_name_says_what_to_do(transport):
    client_for(transport).register("alice", "alice", JOIN_CODE)
    with pytest.raises(ClientError) as exc:
        client_for(transport).register("alice", "bob", JOIN_CODE)
    assert "already taken" in str(exc.value)


# -- Polling and wire conversion -------------------------------------------


def test_poll_converts_the_wire_into_board_and_clock(transport, seed_bot, make_game):
    paired(seed_bot, make_game)
    client = client_for(transport, "tok-alice")
    seen = {}

    def bot(board: chess.Board, clock: ClockView) -> chess.Move:
        seen["board"] = board
        seen["clock"] = clock
        raise Stop

    with pytest.raises(Stop):
        client.run(bot)

    assert isinstance(seen["board"], chess.Board)
    assert seen["board"].turn is chess.WHITE
    assert isinstance(seen["clock"], ClockView)
    assert seen["clock"].my_ms > 0
    assert seen["clock"].ply == 0


def test_clock_is_never_indexed_by_colour(transport, seed_bot, make_game):
    """Black's `my_ms` is black's clock, without the bot knowing its colour."""
    from chess_client.client import _clock_view

    turn = {
        "color": "black",
        "white_ms": 111,
        "black_ms": 222,
        "increment_ms": 2000,
        "ply": 3,
    }
    view = _clock_view(turn)
    assert (view.my_ms, view.opponent_ms) == (222, 111)


def test_a_legal_move_is_accepted_by_the_real_server(transport, seed_bot, make_game):
    paired(seed_bot, make_game)
    client = client_for(transport, "tok-alice")
    plays = []

    def bot(board: chess.Board, clock: ClockView) -> chess.Move:
        if plays:
            raise Stop
        move = chess.Move.from_uci("e2e4")
        plays.append(move)
        return move

    def one_turn():
        headers = client._auth_headers()
        turn = client._poll(headers)
        client._play_turn(turn, bot, headers)

    one_turn()
    # The move landed: the game is now black to move, so we get no turn back.
    assert client._poll(client._auth_headers()) is None


def test_an_unregistered_token_is_fatal_and_actionable(transport):
    client = client_for(transport, "not-a-real-token")
    with pytest.raises(TokenInvalid) as exc:
        client._poll(client._auth_headers())
    assert "register" in str(exc.value).lower()


def test_running_without_a_token_says_how_to_register(transport):
    with pytest.raises(TokenInvalid) as exc:
        client_for(transport).run(lambda board, clock: None)
    assert "--register" in str(exc.value)


# -- Rejection paths --------------------------------------------------------


def test_an_illegal_move_reports_the_position_and_the_legal_moves(
    transport, seed_bot, make_game
):
    paired(seed_bot, make_game)
    client = client_for(transport, "tok-alice")
    headers = client._auth_headers()
    turn = client._poll(headers)

    with pytest.raises(MoveRejected) as exc:
        client._submit(turn, chess.Move.from_uci("e2e5"), 10, headers)

    message = str(exc.value)
    assert "e2e5" in message
    assert "e2e4" in message  # a legal move it could have played instead
    assert turn["fen"] in message


def test_a_stale_ply_is_a_move_rejected_not_a_retry(transport, seed_bot, make_game):
    paired(seed_bot, make_game)
    client = client_for(transport, "tok-alice")
    headers = client._auth_headers()
    turn = client._poll(headers)
    client._submit(turn, chess.Move.from_uci("e2e4"), 10, headers)

    # Resubmitting against the ply that has already been played.
    with pytest.raises(MoveRejected) as exc:
        client._submit(turn, chess.Move.from_uci("d2d4"), 10, headers)
    assert "discard" in str(exc.value).lower()


def test_moving_in_someone_elses_game_is_not_your_turn(
    transport, seed_bot, make_game
):
    paired(seed_bot, make_game)
    client = client_for(transport, "tok-alice")
    headers = client._auth_headers()
    turn = client._poll(headers)
    client._submit(turn, chess.Move.from_uci("e2e4"), 10, headers)

    black = ChessClient("http://arena.test", "tok-bob", transport=transport)
    black_turn = black._poll(black._auth_headers())
    # Alice tries to move on black's ply.
    with pytest.raises(NotYourTurn):
        client._submit(black_turn, chess.Move.from_uci("d7d5"), 10, headers)


# -- 409: discard, never resubmit ------------------------------------------


def _turn_body(ply=0, game_id=1):
    board = chess.Board()
    return {
        "game_id": game_id,
        "ply": ply,
        "color": "white",
        "fen": board.fen(),
        "legal_moves": [m.uci() for m in board.legal_moves],
        "history_san": [],
        "white_ms": 180000,
        "black_ms": 180000,
        "time_control_ms": 180000,
        "increment_ms": 2000,
    }


def test_409_discards_the_move_and_re_polls_without_resubmitting(monkeypatch):
    """The hot loop this rules out is the worst failure mode in the SDK."""
    conflict = httpx.Response(
        409,
        json={
            "error": "The position has changed since ply 0.",
            "details": {"ply": 1, "fen": chess.Board().fen(), "status": "active"},
        },
    )
    scripted = ScriptedTransport(
        [
            httpx.Response(200, json=_turn_body(ply=0)),
            conflict,
            httpx.Response(200, json=_turn_body(ply=2)),
        ]
    )
    monkeypatch.setattr("chess_client.client.time.sleep", lambda _: None)
    client = client_for(scripted, "tok")

    calls = []

    def bot(board, clock):
        calls.append(clock.ply)
        if len(calls) == 2:
            raise Stop
        return chess.Move.from_uci("e2e4")

    with pytest.raises(Stop):
        client.run(bot)

    posts = [r for r in scripted.requests if r.method == "POST"]
    assert len(posts) == 1, "the rejected move must never be resubmitted"
    assert calls == [0, 2], "the second turn came from a fresh poll, not a retry"


def test_409_on_a_finished_game_is_game_ended(monkeypatch):
    scripted = ScriptedTransport(
        [
            httpx.Response(
                409,
                json={
                    "error": "Your clock ran out before this move arrived.",
                    "details": {"ply": 4, "fen": chess.Board().fen(),
                                "status": "finished"},
                },
            )
        ]
    )
    client = client_for(scripted, "tok")
    with pytest.raises(GameEnded):
        client._submit(_turn_body(), chess.Move.from_uci("e2e4"), 10, {})


# -- Backoff, rate limits, server faults -----------------------------------


def test_a_network_error_backs_off_and_is_capped(monkeypatch):
    scripted = ScriptedTransport(
        [httpx.ConnectError("boom") for _ in range(6)]
        + [httpx.Response(200, json=_turn_body())]
    )
    slept = []
    monkeypatch.setattr("chess_client.client.time.sleep", slept.append)
    client = client_for(scripted, "tok")

    def bot(board, clock):
        raise Stop

    with pytest.raises(Stop):
        client.run(bot)

    assert slept == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]
    assert max(slept) <= MAX_BACKOFF_SECONDS


def test_a_rate_limit_honours_retry_after(monkeypatch):
    scripted = ScriptedTransport(
        [
            httpx.Response(429, json={"error": "Rate limit exceeded."},
                           headers={"Retry-After": "7"}),
            httpx.Response(200, json=_turn_body()),
        ]
    )
    slept = []
    monkeypatch.setattr("chess_client.client.time.sleep", slept.append)
    client = client_for(scripted, "tok")

    def bot(board, clock):
        raise Stop

    with pytest.raises(Stop):
        client.run(bot)
    assert slept == [7]


def test_a_5xx_is_a_server_error_with_prose(monkeypatch):
    scripted = ScriptedTransport([httpx.Response(503, text="nope")])
    client = client_for(scripted, "tok")
    with pytest.raises(ServerError) as exc:
        client._poll({})
    assert "arena server" in str(exc.value)


def test_rate_limited_carries_the_retry_after_seconds():
    scripted = ScriptedTransport(
        [httpx.Response(429, json={"error": "Rate limit exceeded."},
                        headers={"Retry-After": "4"})]
    )
    with pytest.raises(RateLimited) as exc:
        client_for(scripted, "tok")._poll({})
    assert exc.value.retry_after_seconds == 4


def test_no_game_yet_idles_instead_of_hot_looping(monkeypatch):
    scripted = ScriptedTransport(
        [
            httpx.Response(200, json={"game_id": None, "reason": "not_your_turn"}),
            httpx.Response(200, json=_turn_body()),
        ]
    )
    slept = []
    monkeypatch.setattr("chess_client.client.time.sleep", slept.append)
    client = client_for(scripted, "tok")

    def bot(board, clock):
        raise Stop

    with pytest.raises(Stop):
        client.run(bot)
    assert slept == [IDLE_SECONDS]


# -- Timing -----------------------------------------------------------------


def test_client_reported_ms_is_measured_around_choose_move(monkeypatch):
    scripted = ScriptedTransport(
        [
            httpx.Response(200, json=_turn_body()),
            httpx.Response(200, json={"game_id": 1, "ply": 1,
                                      "fen": chess.Board().fen(),
                                      "status": "active"}),
        ]
    )
    ticks = iter([100.0, 100.25])
    monkeypatch.setattr("chess_client.client.time.monotonic", lambda: next(ticks))
    client = client_for(scripted, "tok")
    headers = {}
    turn = client._poll(headers)
    client._play_turn(turn, lambda b, c: chess.Move.from_uci("e2e4"), headers)

    post = [r for r in scripted.requests if r.method == "POST"][0]
    import json

    assert json.loads(post.content)["client_reported_ms"] == 250


# -- Resign -----------------------------------------------------------------


def test_resign_ends_the_game_on_the_real_server(transport, seed_bot, make_game):
    game_id = paired(seed_bot, make_game)
    client = client_for(transport, "tok-alice")
    client._poll(client._auth_headers())
    client.resign(game_id, 0)

    assert client._poll(client._auth_headers()) is None


def test_resigning_a_game_you_are_not_in_is_actionable(transport, seed_bot, make_game):
    game_id = paired(seed_bot, make_game)
    seed_bot("carol", "tok-carol")
    with pytest.raises(ClientError) as exc:
        client_for(transport, "tok-carol").resign(game_id, 0)
    assert f"game {game_id}" in str(exc.value).lower()
