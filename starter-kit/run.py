"""Register your bot, then play. You never edit this file — only `bot.py`.

    ../.venv/bin/python run.py register --name Sirius --owner "ada lovelace"
    ../.venv/bin/python run.py play

Registration saves your token to `.env` next to this file. That token *is* your
bot's identity on the leaderboard, so it is written once and never printed
again.
"""
import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from chess_client import ChessClient, ClientError, TokenInvalid

ENV_PATH = Path(__file__).resolve().parent / ".env"
DEFAULT_SERVER = "http://localhost:8000"
TOKEN_KEY = "ARENA_TOKEN"
SERVER_KEY = "ARENA_SERVER"
JOIN_CODE_KEY = "ARENA_JOIN_CODE"

REGISTER_HINT = (
    'Register first: python run.py register --name YourBot --owner "your name"'
)


@dataclass(frozen=True)
class Config:
    server: str
    join_code: Optional[str]
    token: Optional[str]


def load_env(path: Path) -> dict:
    """Read a `KEY=value` file. A missing file is simply no configuration."""
    if not Path(path).exists():
        return {}
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def save_env(path: Path, updates: dict) -> None:
    """Merge `updates` into the file, leaving anything else in it alone."""
    path = Path(path)
    values = load_env(path)
    values.update(updates)
    body = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
    path.write_text("# Your bot's identity. Do not commit or share this file.\n" + body)
    os.chmod(path, 0o600)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python run.py",
        description="Register your bot with the arena, or play with it.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser("register", help="register a new bot")
    register.add_argument("--name", help="your bot's name, shown on the leaderboard")
    register.add_argument("--owner", help="your own name — must be unique in the room")
    register.add_argument("--join-code", help="ask the workshop host")
    register.add_argument("--server", help=f"arena address (default {DEFAULT_SERVER})")

    play = subparsers.add_parser("play", help="play with your registered bot")
    play.add_argument("--server", help=f"arena address (default {DEFAULT_SERVER})")

    return parser.parse_args(argv)


def resolve(args: argparse.Namespace, env: dict) -> Config:
    """Flags win over the saved `.env`, which wins over the default."""
    return Config(
        server=args.server or env.get(SERVER_KEY) or DEFAULT_SERVER,
        join_code=getattr(args, "join_code", None) or env.get(JOIN_CODE_KEY),
        token=env.get(TOKEN_KEY),
    )


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _check_registration_args(args: argparse.Namespace, config: Config) -> Optional[str]:
    if not args.name:
        return "Your bot needs a name. Add: --name YourBot"
    if not args.owner:
        return (
            "Your bot needs an owner — your own name, not your neighbour's."
            ' Add: --owner "your name"'
        )
    if "@" in args.name or "@" in args.owner:
        return (
            "Names and owners cannot contain '@', so an email address will not"
            " work. Use letters, digits, spaces, '_' or '-' instead."
        )
    if not config.join_code:
        return (
            "No join code. Ask the workshop host for it, then add:"
            " --join-code THECODE"
        )
    return None


def do_register(args: argparse.Namespace, config: Config, client: ChessClient,
                env_path: Path) -> int:
    problem = _check_registration_args(args, config)
    if problem:
        return _fail(problem)
    try:
        client.register(args.name, args.owner, config.join_code)
    except ClientError as exc:
        return _fail(str(exc))
    save_env(
        env_path,
        {
            TOKEN_KEY: client.token,
            SERVER_KEY: config.server,
            JOIN_CODE_KEY: config.join_code,
        },
    )
    print(f"Registered '{args.name}' for owner '{args.owner}' at {config.server}.")
    print(
        f"Your token is your bot's identity and has been saved to {env_path}."
        " It is never printed again — keep the file, and do not commit or share it."
    )
    print("Now play: python run.py play")
    return 0


def do_play(client: ChessClient,
            choose_move: Callable) -> int:
    try:
        client.run(choose_move)
    except TokenInvalid as exc:
        return _fail(f"{exc} {REGISTER_HINT}")
    except ClientError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print("\nStopped. Your rating is kept — run again whenever you like.")
    return 0


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    transport=None,
    choose_move: Optional[Callable] = None,
    env_path: Optional[Path] = None,
) -> int:
    # `transport`, `choose_move` and `env_path` are testing seams; attendees
    # never pass them.
    args = parse_args(argv)
    env_path = Path(env_path) if env_path else ENV_PATH
    config = resolve(args, load_env(env_path))

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs every request with its status code; the SDK exists so that
    # attendees never have to read one.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command != "register" and not config.token:
        return _fail(f"No saved bot token in {env_path}. {REGISTER_HINT}")

    with ChessClient(config.server, config.token, transport=transport) as client:
        if args.command == "register":
            return do_register(args, config, client, env_path)
        if choose_move is None:
            from bot import choose_move as choose_move  # noqa: PLC0415
        print(f"Playing as your registered bot at {config.server}. Ctrl-C to stop.")
        return do_play(client, choose_move)


if __name__ == "__main__":
    raise SystemExit(main())
