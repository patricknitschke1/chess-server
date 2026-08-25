"""The Chess Arena SDK.

Attendees see `chess.Board`, `chess.Move` and `ClockView`. FEN strings, UCI
strings, ply numbers and HTTP status codes stay inside this file.
"""
import logging
import time
from typing import Callable, Optional

import chess
import httpx

from chess_core import ClockView

from chess_client.errors import (
    ClientError,
    GameEnded,
    MoveRejected,
    NotYourTurn,
    RateLimited,
    ServerError,
    TokenInvalid,
)

log = logging.getLogger("chess_client")

# The server holds a poll for 20s (design §8.4); 30 leaves room for the reply.
POLL_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 10.0
# Applied when the server answers a poll immediately with "nothing for you yet".
# Without it, `not_your_turn` becomes a hot loop.
IDLE_SECONDS = 0.5
INITIAL_BACKOFF_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 5.0
DEFAULT_RETRY_AFTER_SECONDS = 3

NON_TERMINAL_STATUSES = frozenset({"pending", "active"})


def _error_prose(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or "The server sent an unreadable response."
    if isinstance(body, dict):
        for key in ("error", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return "The server sent an unreadable response."


def _details(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    details = body.get("details") if isinstance(body, dict) else None
    return details if isinstance(details, dict) else {}


def _retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("Retry-After")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


class ChessClient:
    """Registers a bot, polls for turns, and submits moves.

    Args:
        server_url: e.g. "http://localhost:8000"
        token: the token returned by `register`, if you already have one.
    """

    def __init__(
        self,
        server_url: str,
        token: Optional[str] = None,
        *,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.server_url = server_url.rstrip("/")
        self.token = token
        # `transport` is a testing seam; attendees never pass it.
        self._http = httpx.Client(
            base_url=self.server_url,
            transport=transport,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "chess-client/1.0"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ChessClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- HTTP plumbing ----------------------------------------------------

    def _auth_headers(self) -> dict:
        if not self.token:
            raise TokenInvalid(
                "No bot token. Register first with:"
                " python run.py --register --name YourBot --owner you"
            )
        return {"Authorization": f"Bearer {self.token}"}

    def _raise_for_common(self, response: httpx.Response) -> None:
        """The failures that mean the same thing on every endpoint."""
        if response.status_code == 401:
            raise TokenInvalid(
                f"{_error_prose(response)}"
                " Register again and put the new token in your .env."
            )
        if response.status_code == 429:
            seconds = _retry_after(response)
            raise RateLimited(
                f"The server is rate limiting your bot. Waiting {seconds}s"
                " before trying again.",
                seconds,
            )
        if response.status_code >= 500:
            raise ServerError(
                "The arena server had an internal error. Retrying shortly —"
                " tell the workshop host if this keeps happening."
            )

    # -- Registration -----------------------------------------------------

    def register(self, name: str, owner: str, join_code: str) -> str:
        """Register this bot and return its token. Save the token."""
        try:
            response = self._http.post(
                "/bots", json={"name": name, "owner": owner, "join_code": join_code}
            )
        except httpx.RequestError as exc:
            raise ClientError(
                f"Could not reach the arena server at {self.server_url}."
                " Check the address and that the server is running."
            ) from exc
        self._raise_for_common(response)
        if response.status_code != 201:
            raise ClientError(f"Registration failed. {_error_prose(response)}")
        self.token = response.json()["token"]
        return self.token

    def resign(self, game_id: int, ply: int) -> None:
        """Resign a game you are playing."""
        try:
            response = self._http.post(
                f"/games/{game_id}/resign",
                json={"ply": ply},
                headers=self._auth_headers(),
            )
        except httpx.RequestError as exc:
            raise ClientError(
                f"Could not reach the arena server at {self.server_url} to resign."
            ) from exc
        self._raise_for_common(response)
        if response.status_code == 409:
            raise GameEnded(f"That game has already ended. {_error_prose(response)}")
        if response.status_code == 403:
            raise NotYourTurn(_error_prose(response))
        if response.status_code >= 400:
            raise ClientError(f"Could not resign. {_error_prose(response)}")

    # -- The main loop ----------------------------------------------------

    def run(self, choose_move_fn: Callable[[chess.Board, ClockView], chess.Move]) -> None:
        """Poll for turns, call `choose_move_fn`, submit the move. Runs until Ctrl-C."""
        headers = self._auth_headers()
        backoff = INITIAL_BACKOFF_SECONDS
        log.info("Bot running. Waiting to be paired — press Ctrl-C to stop.")
        while True:
            try:
                turn = self._poll(headers)
                backoff = INITIAL_BACKOFF_SECONDS
            except RateLimited as exc:
                time.sleep(exc.retry_after_seconds)
                continue
            except ServerError as exc:
                log.warning("%s", exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            except httpx.RequestError:
                log.warning(
                    "Cannot reach the arena server at %s. Retrying in %.1fs.",
                    self.server_url,
                    backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            if turn is None:
                time.sleep(IDLE_SECONDS)
                continue
            self._play_turn(turn, choose_move_fn, headers)

    def _poll(self, headers: dict) -> Optional[dict]:
        """One long poll. None means "nothing to play yet"."""
        response = self._http.get(
            "/bots/me/turn", headers=headers, timeout=POLL_TIMEOUT_SECONDS
        )
        self._raise_for_common(response)
        if response.status_code != 200:
            raise ClientError(f"Could not poll for your turn. {_error_prose(response)}")
        body = response.json()
        return body if body.get("game_id") is not None else None

    def _play_turn(
        self,
        turn: dict,
        choose_move_fn: Callable[[chess.Board, ClockView], chess.Move],
        headers: dict,
    ) -> None:
        board = chess.Board(turn["fen"])
        log.debug("Turn %s\n%s", turn.get("fen"), board.unicode(borders=True))
        clock = _clock_view(turn)
        started = time.monotonic()
        move = choose_move_fn(board, clock)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            self._submit(turn, move, elapsed_ms, headers)
        except MoveRejected as exc:
            log.warning("%s", exc)
        except NotYourTurn as exc:
            log.info("%s", exc)
        except GameEnded as exc:
            log.info("%s", exc)
        except RateLimited as exc:
            log.warning("%s", exc)
            time.sleep(exc.retry_after_seconds)
        except (ServerError, httpx.RequestError):
            # Discard the move rather than resubmit it: by the time the server is
            # back the position may have moved on. The next poll is authoritative.
            log.warning("Lost contact with the server while sending your move.")

    def _submit(
        self, turn: dict, move: chess.Move, elapsed_ms: int, headers: dict
    ) -> None:
        game_id = turn["game_id"]
        response = self._http.post(
            f"/games/{game_id}/moves",
            json={
                "ply": turn["ply"],
                "move": move.uci(),
                "client_reported_ms": elapsed_ms,
            },
            headers=headers,
        )
        self._raise_for_common(response)

        if response.status_code == 409:
            # Design §8.3: the position changed under us. DISCARD this move and
            # re-poll. Resubmitting it is a hot loop against a position that no
            # longer exists.
            if _details(response).get("status") not in NON_TERMINAL_STATUSES:
                raise GameEnded(
                    f"Game {game_id} ended before your move arrived."
                    f" {_error_prose(response)}"
                )
            raise MoveRejected(
                "The position moved on before your move arrived — it has been"
                " discarded. Polling for the current position."
            )
        if response.status_code == 400:
            details = _details(response)
            raise MoveRejected(
                f"{_error_prose(response)}"
                f" Your bot returned {move.uci()} for position {turn['fen']}."
                f" Pick one of: {', '.join(details.get('legal_moves', turn['legal_moves']))}"
            )
        if response.status_code == 403:
            raise NotYourTurn(f"{_error_prose(response)}")
        if response.status_code != 200:
            raise ClientError(f"Move was not accepted. {_error_prose(response)}")

        body = response.json()
        if body.get("status") not in NON_TERMINAL_STATUSES:
            log.info(
                "Game %s finished: %s (%s).",
                game_id,
                body.get("result"),
                body.get("termination"),
            )
        else:
            log.info(
                "Played %s (%sms). Waiting for your opponent.", move.uci(), elapsed_ms
            )


def _clock_view(turn: dict) -> ClockView:
    """Colour is resolved here so no attendee ever indexes a clock by colour."""
    mine, theirs = (
        (turn["white_ms"], turn["black_ms"])
        if turn["color"] == "white"
        else (turn["black_ms"], turn["white_ms"])
    )
    return ClockView(
        my_ms=mine,
        opponent_ms=theirs,
        increment_ms=turn["increment_ms"],
        ply=turn["ply"],
    )
