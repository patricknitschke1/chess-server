"""Bounded, hash-keyed token buckets (role spec §8.7)."""
import httpx
import pytest
from fastapi import Depends

from chess_server.api.auth import hash_token, require_bot
from chess_server.api.errors import RATE_LIMITED, RETRY_AFTER_SECONDS
from chess_server.api.rate_limit import (
    BURST,
    MAX_KEYS,
    REGISTER_PER_IP_PER_MIN,
    RateLimiter,
    register_limiter,
)
from chess_server.store.repositories import BotRepo
from chess_server.store.txn import critical_section

from tests.chess_server.conftest import WALL

TOKEN = "a-real-looking-token-value"
ONE_SECOND_NS = 1_000_000_000
SUSTAINED_PER_SECOND = 20


@pytest.fixture
async def limited(api_app, store):
    bots = BotRepo(store.writer, store.executor)
    async with critical_section(store.writer, store.executor):
        await bots.insert_bot(
            name="ada", owner="ada", token_hash=hash_token(TOKEN),
            role="competitor", rating=1200, is_anchor=0, created_at=WALL,
        )

    @api_app.get("/probe")
    async def probe(bot=Depends(require_bot)):
        return {"bot_id": bot.id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app), base_url="http://arena.test"
    ) as http:
        yield http


async def _statuses(http, count):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    return [(await http.get("/probe", headers=headers)).status_code for _ in range(count)]


async def test_the_burst_is_allowed_and_the_next_request_is_refused(limited, clock):
    assert await _statuses(limited, BURST) == [200] * BURST

    refused = await limited.get("/probe", headers={"Authorization": f"Bearer {TOKEN}"})

    assert refused.status_code == 429
    assert refused.json()["error"] == RATE_LIMITED
    assert refused.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)


async def test_a_second_of_elapsed_time_buys_exactly_the_sustained_rate(limited, clock):
    await _statuses(limited, BURST + 1)

    clock.advance(ONE_SECOND_NS)

    assert await _statuses(limited, SUSTAINED_PER_SECOND) == [200] * SUSTAINED_PER_SECOND
    assert (await _statuses(limited, 1)) == [429]


def test_garbage_keys_cannot_grow_the_bucket_store(clock):
    limiter = RateLimiter()

    for index in range(MAX_KEYS + 44):
        limiter.allow(f"key-{index}", clock())

    assert len(limiter.keys()) == MAX_KEYS


def test_eviction_is_least_recently_used(clock):
    limiter = RateLimiter(max_keys=2)

    limiter.allow("first", clock())
    limiter.allow("second", clock())
    limiter.allow("first", clock())   # first is now the most recently used
    limiter.allow("third", clock())

    assert limiter.keys() == ["first", "third"]


def test_registration_is_limited_per_ip_and_one_ip_does_not_affect_another(clock):
    limiter = register_limiter()

    assert all(limiter.allow("10.0.0.1", clock()) for _ in range(REGISTER_PER_IP_PER_MIN))
    assert limiter.allow("10.0.0.1", clock()) is False
    assert limiter.allow("10.0.0.2", clock()) is True


async def test_no_bucket_key_is_ever_a_raw_token(limited, api_state):
    """A plaintext token in a long-lived global reaches every traceback frame that
    touches the limiter, which is exactly what "never logged" exists to prevent."""
    await _statuses(limited, 1)

    assert api_state.limiter.keys() == [hash_token(TOKEN)]
    assert TOKEN not in api_state.limiter.keys()
