"""The store is async, so the suite must be able to run `async def` tests."""
import asyncio


def test_store_package_importable():
    import chess_server.store

    assert chess_server.store is not None


async def test_async_tests_actually_run():
    marker = []

    async def _set_it():
        await asyncio.sleep(0)
        marker.append("ran")

    await _set_it()
    assert marker == ["ran"]
