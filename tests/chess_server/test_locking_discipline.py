"""Design §4.3: write_lock is acquired at exactly one place per call stack.

asyncio.Lock is not re-entrant and has no timeout, so a nested acquire wedges the
coroutine on an await, raises nothing, and looks like an ordinary call in review.
"""
import ast
import asyncio
import pathlib

import pytest

from chess_server.store.txn import critical_section

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[2] / "chess_server"

SYNTHETIC = '''
async def outer_form(conn, executor):
    async with critical_section(conn, executor) as txn:
        await inner_locked(txn)

async def inner_locked(txn):
    await txn.conn.execute("SELECT 1")

async def acquirer_locked(txn):
    async with write_lock:
        pass

async def caller_locked(txn):
    await outer_form(txn.conn, txn.executor)

async def double_outer(conn, executor):
    async with critical_section(conn, executor):
        async with critical_section(conn, executor):
            pass
'''


def _is_lock_acquire(node: ast.AsyncWith) -> bool:
    return any(
        isinstance(item.context_expr, ast.Name) and item.context_expr.id == "write_lock"
        for item in node.items
    )


def _critical_sections(node: ast.AST) -> int:
    return sum(
        1
        for child in ast.walk(node)
        if isinstance(child, ast.AsyncWith)
        for item in child.items
        if isinstance(item.context_expr, ast.Call)
        and getattr(item.context_expr.func, "id", None) == "critical_section"
    )


def _called_names(node: ast.AST) -> set[str]:
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]


def analyse(tree: ast.AST, label: str) -> tuple[list[str], list[str], list[str]]:
    """Returns (inner-form violations, outer-form violations, names of *_locked found)."""
    functions = _functions(tree)
    outer_names = {fn.name for fn in functions if _critical_sections(fn) >= 1}
    locked = [fn for fn in functions if fn.name.endswith("_locked")]

    inner_violations = []
    for fn in locked:
        where = f"{label}:{fn.lineno} {fn.name}"
        if any(isinstance(n, ast.AsyncWith) and _is_lock_acquire(n) for n in ast.walk(fn)):
            inner_violations.append(f"{where} acquires write_lock")
        for called in sorted(_called_names(fn) & outer_names):
            inner_violations.append(f"{where} calls the outer form {called}")

    outer_violations = [
        f"{label}:{fn.lineno} {fn.name} opens {_critical_sections(fn)} critical sections"
        for fn in functions
        if fn.name in outer_names and _critical_sections(fn) != 1
    ] + [
        f"{label}:{fn.lineno} {fn.name} acquires write_lock directly"
        for fn in functions
        if fn.name in outer_names
        and any(isinstance(n, ast.AsyncWith) and _is_lock_acquire(n) for n in ast.walk(fn))
    ]
    return inner_violations, outer_violations, [fn.name for fn in locked]


def _scan_tree():
    inner, outer, locked = [], [], []
    for path in sorted(SERVER_ROOT.rglob("*.py")):
        found = analyse(ast.parse(path.read_text()), path.name)
        inner += found[0]
        outer += found[1]
        locked += found[2]
    return inner, outer, locked


async def _outer_form(store):
    """A two-line outer form, local because none exists in chess_server/ yet."""
    async with critical_section(store.writer, store.executor) as txn:
        await asyncio.get_running_loop().run_in_executor(
            txn.executor, txn.conn.execute, "SELECT 1"
        )


async def test_a_nested_acquire_wedges_rather_than_raising(store):
    async with critical_section(store.writer, store.executor):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_outer_form(store), timeout=1)

    await _outer_form(store)  # the writer survived the wedge


def test_the_analyser_names_both_kinds_of_nested_acquire():
    inner, outer, locked = analyse(ast.parse(SYNTHETIC), "synthetic")

    assert locked == ["inner_locked", "acquirer_locked", "caller_locked"]
    assert inner == [
        "synthetic:9 acquirer_locked acquires write_lock",
        "synthetic:13 caller_locked calls the outer form outer_form",
    ]
    assert outer == ["synthetic:16 double_outer opens 2 critical sections"]


def test_no_locked_helper_in_chess_server_acquires_the_lock():
    inner, _, _ = _scan_tree()
    assert inner == []


def test_every_outer_form_in_chess_server_acquires_the_lock_exactly_once():
    _, outer, _ = _scan_tree()
    assert outer == []
