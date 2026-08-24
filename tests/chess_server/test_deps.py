"""The injected seams. Phase 3c replaces the defaults; 3b runs headless on them."""
import pathlib

from chess_server.engine.deps import EngineDeps

ENGINE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "chess_server" / "engine"


def test_now_mono_reads_the_injected_clock(store, clock):
    deps = EngineDeps(conn=store.writer, executor=store.executor, now_mono=clock)
    clock.set(7_000_000_000)
    assert deps.now_mono() == 7_000_000_000
    clock.advance(3_000_000_000)
    assert deps.now_mono() == 10_000_000_000


def test_only_deps_names_the_real_monotonic_clock():
    offenders = [
        path.name
        for path in sorted(ENGINE_ROOT.rglob("*.py"))
        if path.name != "deps.py" and "monotonic_ns" in path.read_text()
    ]
    assert offenders == []


def test_the_defaults_are_callable_and_inert(store):
    deps = EngineDeps(conn=store.writer, executor=store.executor)
    assert deps.sink(0, "game_created", {}) is None
    assert deps.wake(1) is None
    assert deps.is_paused() is False
    assert isinstance(deps.now_mono(), int)
