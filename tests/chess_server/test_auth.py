"""Bearer authentication (role spec §8.7). Failure paths first."""
import dataclasses

import httpx
import pytest
from fastapi import Depends

from chess_server.api.auth import hash_token, require_admin, require_bot
from chess_server.api.errors import ADMIN_REQUIRED, NO_BOT_FOR_TOKEN
from chess_server.store.repositories import BotRepo
from chess_server.store.txn import critical_section

from tests.chess_server.conftest import ADMIN_TOKEN, WALL

TOKEN = "a-real-looking-token-value"


@pytest.fixture
def probe_app(api_app):
    @api_app.get("/probe")
    async def probe(bot=Depends(require_bot)):
        return {"bot_id": bot.id, "name": bot.name, "owner": bot.owner}

    @api_app.get("/probe-admin", dependencies=[Depends(require_admin)])
    async def probe_admin():
        return {"ok": True}

    return api_app


@pytest.fixture
async def probe(probe_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=probe_app), base_url="http://arena.test"
    ) as http:
        yield http


@pytest.fixture
async def registered(store):
    bots = BotRepo(store.writer, store.executor)
    async with critical_section(store.writer, store.executor):
        bot_id = await bots.insert_bot(
            name="ada", owner="ada", token_hash=hash_token(TOKEN),
            role="competitor", rating=1200, is_anchor=0, created_at=WALL,
        )
    return await bots.get_by_id(bot_id)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"Authorization": TOKEN}, id="no-scheme"),
        pytest.param({"Authorization": "Bearer "}, id="empty-bearer"),
        pytest.param({"Authorization": "Bearer nobody-has-this"}, id="unknown"),
    ],
)
async def test_a_request_without_a_usable_token_is_rejected(probe, registered, headers):
    response = await probe.get("/probe", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": NO_BOT_FOR_TOKEN, "details": None}


async def test_a_row_whose_stored_hash_disagrees_is_rejected(
    probe, registered, monkeypatch
):
    """compare_digest is the gate, not the lookup: a repository that returned the
    wrong row for any reason must not authenticate it."""

    async def _mismatched(self, token_hash):
        return dataclasses.replace(registered, token_hash="not-the-same-hash")

    monkeypatch.setattr(BotRepo, "get_by_token_hash", _mismatched)

    response = await probe.get("/probe", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 401
    assert response.json()["error"] == NO_BOT_FOR_TOKEN


async def test_a_valid_token_authenticates_and_never_comes_back(probe, registered):
    response = await probe.get("/probe", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json() == {"bot_id": registered.id, "name": "ada", "owner": "ada"}
    assert TOKEN not in response.text
    assert hash_token(TOKEN) not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"Authorization": "Bearer wrong-admin-token"}, id="wrong"),
    ],
)
async def test_the_admin_routes_refuse_anything_but_the_configured_token(probe, headers):
    response = await probe.get("/probe-admin", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"] == ADMIN_REQUIRED


async def test_the_configured_admin_token_is_accepted(probe):
    response = await probe.get(
        "/probe-admin", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )

    assert response.status_code == 200
