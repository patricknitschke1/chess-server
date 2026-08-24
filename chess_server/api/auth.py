"""Bearer authentication (role spec §8.7, design §16.2).

A fast hash is correct here precisely because the token is high-entropy and
random: there is no username to look up by, so a KDF would force a scan across
every bot on every request.
"""
import hashlib
import secrets
from typing import Optional

from fastapi import Request, status

from chess_server.api.errors import ADMIN_REQUIRED, ApiError, rate_limited, unauthorized
from chess_server.api.state import AppState, get_state
from chess_server.store.repositories import BotRepo
from chess_server.store.rows import BotRow

_BEARER = "bearer"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != _BEARER:
        return None
    return value.strip() or None


async def authenticate(app_state: AppState, authorization: Optional[str]) -> BotRow:
    """Reads on the reader connection: authentication is on every request and must
    never queue behind the writer."""
    token = _bearer_token(authorization)
    if token is None:
        raise unauthorized()
    token_hash = hash_token(token)
    bots = BotRepo(app_state.store.reader, app_state.store.reader_executor)
    bot = await bots.get_by_token_hash(token_hash)
    if bot is None or not secrets.compare_digest(bot.token_hash, token_hash):
        raise unauthorized()
    return bot


def enforce_rate_limit(app_state: AppState, bot: BotRow) -> None:
    if not app_state.limiter.allow(bot.token_hash, app_state.deps.now_mono()):
        raise rate_limited()


def enforce_register_limit(app_state: AppState, client_ip: str) -> None:
    if not app_state.register_limiter.allow(client_ip, app_state.deps.now_mono()):
        raise rate_limited()


async def require_bot(request: Request) -> BotRow:
    app_state = get_state(request)
    bot = await authenticate(app_state, request.headers.get("authorization"))
    enforce_rate_limit(app_state, bot)
    return bot


async def require_admin(request: Request) -> None:
    app_state = get_state(request)
    supplied = _bearer_token(request.headers.get("authorization")) or ""
    if not secrets.compare_digest(supplied, app_state.settings.admin_token):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, ADMIN_REQUIRED)
