"""`run.py` — the entrypoint the SDK's own error messages point attendees at."""
import chess
import httpx
import pytest

import run
from tests.chess_client.conftest import JOIN_CODE


class Stop(Exception):
    """Raised from a bot to end `run()` once it has been reached."""


@pytest.fixture
def env_path(tmp_path):
    return tmp_path / ".env"


def register(env_path, transport, *extra):
    argv = [
        "register",
        "--name",
        "Sirius",
        "--owner",
        "ada",
        "--join-code",
        JOIN_CODE,
        *extra,
    ]
    return run.main(argv, transport=transport, env_path=env_path)


# -- The .env file ----------------------------------------------------------


def test_env_file_round_trips_and_keeps_other_keys(env_path):
    env_path.write_text("ARENA_SERVER=http://elsewhere\nOTHER=keep\n")
    run.save_env(env_path, {"ARENA_TOKEN": "tok"})
    values = run.load_env(env_path)
    assert values["OTHER"] == "keep"
    assert values["ARENA_SERVER"] == "http://elsewhere"
    assert values["ARENA_TOKEN"] == "tok"


def test_load_env_of_a_missing_file_is_empty(tmp_path):
    assert run.load_env(tmp_path / "nope.env") == {}


# -- Registering ------------------------------------------------------------


def test_register_saves_the_token_and_never_prints_it(env_path, transport, capsys):
    assert register(env_path, transport) == 0
    token = run.load_env(env_path)["ARENA_TOKEN"]
    assert token
    out = capsys.readouterr().out
    assert token not in out
    assert "identity" in out.lower()


def test_register_without_a_join_code_says_where_to_get_one(env_path, transport, capsys):
    code = run.main(
        ["register", "--name", "Sirius", "--owner", "ada"],
        transport=transport,
        env_path=env_path,
    )
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "join code" in err and "host" in err


def test_register_with_a_wrong_join_code_says_what_to_do(env_path, transport, capsys):
    code = register(env_path, transport)  # correct one first, to prove the seam
    assert code == 0
    code = run.main(
        ["register", "--name", "Vega", "--owner", "grace", "--join-code", "nope"],
        transport=transport,
        env_path=env_path,
    )
    assert code == 2
    assert "join code" in capsys.readouterr().err.lower()


def test_register_with_a_taken_name_says_to_pick_another(env_path, transport, capsys):
    register(env_path, transport)
    code = run.main(
        ["register", "--name", "Sirius", "--owner", "grace", "--join-code", JOIN_CODE],
        transport=transport,
        env_path=env_path,
    )
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "already taken" in err or "another name" in err


def test_register_rejects_an_email_as_owner_before_calling_the_server(env_path, capsys):
    code = run.main(
        ["register", "--name", "Sirius", "--owner", "ada@example.com",
         "--join-code", JOIN_CODE],
        transport=None,
        env_path=env_path,
    )
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "@" in err
    assert not env_path.exists()


def test_register_against_a_dead_server_says_to_check_the_address(env_path, capsys):
    class Dead(httpx.BaseTransport):
        def handle_request(self, request):
            raise httpx.ConnectError("refused", request=request)

    code = register(env_path, Dead())
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "could not reach" in err
    assert "running" in err


# -- Running ----------------------------------------------------------------


def test_run_without_a_saved_token_tells_you_to_register(env_path, transport, capsys):
    code = run.main(["play"], transport=transport, env_path=env_path)
    assert code == 2
    err = capsys.readouterr().err
    assert "register" in err


def test_run_uses_the_saved_token_and_calls_your_bot(
    env_path, transport, seed_bot, make_game, capsys
):
    white = seed_bot("alice", "tok-alice")
    black = seed_bot("bob", "tok-bob")
    make_game(white, black)
    run.save_env(env_path, {"ARENA_TOKEN": "tok-alice"})
    seen = {}

    def bot(board: chess.Board, clock) -> chess.Move:
        seen["called"] = True
        raise Stop

    with pytest.raises(Stop):
        run.main(["play"], transport=transport, env_path=env_path, choose_move=bot)
    assert seen["called"]
    assert "tok-alice" not in capsys.readouterr().out


def test_run_with_an_unknown_token_says_to_register_again(env_path, transport, capsys):
    run.save_env(env_path, {"ARENA_TOKEN": "s3cret-token"})

    def bot(board, clock):
        raise Stop

    code = run.main(["play"], transport=transport, env_path=env_path, choose_move=bot)
    assert code == 2
    captured = capsys.readouterr()
    assert "s3cret-token" not in captured.out + captured.err
    assert "register" in captured.err


def test_bare_invocation_with_no_subcommand_fails_cleanly(env_path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        run.main([], env_path=env_path)
    assert exc_info.value.code != 0
    err = capsys.readouterr().err.lower()
    assert "register" in err and "play" in err


# -- Configuration precedence ----------------------------------------------


def test_server_flag_beats_the_saved_value(env_path):
    run.save_env(env_path, {"ARENA_SERVER": "http://saved"})
    config = run.resolve(
        run.parse_args(["play", "--server", "http://flag"]), run.load_env(env_path)
    )
    assert config.server == "http://flag"


def test_saved_server_is_used_when_no_flag_is_given(env_path):
    run.save_env(env_path, {"ARENA_SERVER": "http://saved"})
    config = run.resolve(run.parse_args(["play"]), run.load_env(env_path))
    assert config.server == "http://saved"
