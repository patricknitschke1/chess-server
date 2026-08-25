"""`python -m chess_server` — the operator's entrypoint.

Configuration comes from the environment (`settings_from_env`); only the bind
address is a flag, because that is the one thing that changes between a laptop
and the workshop projector.
"""
import argparse
import sys
from typing import Optional, Sequence

import uvicorn
from fastapi import FastAPI

from chess_server.api.app import build_state, create_app
from chess_server.api.settings import settings_from_env


def build_app() -> FastAPI:
    """Zero-argument factory, for `uvicorn --factory chess_server.__main__:build_app`."""
    return create_app(build_state(settings_from_env()))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m chess_server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        app = build_app()
    except ValueError as exc:
        # settings_from_env raises actionable prose; a traceback would bury it.
        print(f"Cannot start the arena server: {exc}", file=sys.stderr)
        return 2

    base = f"http://{args.host}:{args.port}"
    print(f"Chess Arena listening on {base}")
    print(f"Dashboard: {base}/dashboard/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
