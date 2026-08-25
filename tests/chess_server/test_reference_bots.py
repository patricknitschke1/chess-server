"""Test task 10: reference bots and idempotent anchor seeding."""
import chess
import pytest

from chess_core import RATED_TIME_CONTROL_NS, ClockView, ns_to_ms
from chess_server.engine.reference_bots import (
    ANCHORS,
    Refdepth3Bot,
    RefGreedyBot,
    RefRandomBot,
    seed_anchors,
    seed_anchors_locked,
)
from chess_server.store.repositories import BotRepo
from chess_server.store.txn import critical_section

ONE_LEGAL_MOVE = "7k/8/8/8/8/8/6q1/7K w - - 0 1"          # Kh1 must take on g2
ONLY_CAPTURES = "8/8/8/3k4/8/8/4p3/4K3 w - - 0 1"          # Ke1 may only take e2
FREE_QUEEN = "4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1"           # exd5 wins a queen
MATE_IN_ONE = "6k1/5ppp/8/8/8/8/8/R3K2R w KQ - 0 1"        # Ra8#


def clock_view() -> ClockView:
    return ClockView(
        my_ms=ns_to_ms(RATED_TIME_CONTROL_NS),
        opponent_ms=ns_to_ms(RATED_TIME_CONTROL_NS),
        increment_ms=0,
        ply=0,
    )


ALL_BOTS = [RefRandomBot, RefGreedyBot, Refdepth3Bot]


@pytest.mark.parametrize("bot_class", ALL_BOTS)
@pytest.mark.parametrize("fen", [chess.STARTING_FEN, ONE_LEGAL_MOVE, ONLY_CAPTURES])
def test_every_reference_bot_returns_a_legal_move(bot_class, fen):
    board = chess.Board(fen)
    move = bot_class().choose_move(board, clock_view())
    assert move in board.legal_moves


def test_ref_random_with_a_fixed_seed_is_reproducible():
    board = chess.Board()
    first = [RefRandomBot(rng=__import__("random").Random(7)).choose_move(board, clock_view())
             for _ in range(5)]
    second = [RefRandomBot(rng=__import__("random").Random(7)).choose_move(board, clock_view())
              for _ in range(5)]
    assert first == second


def test_ref_greedy_takes_a_free_queen():
    board = chess.Board(FREE_QUEEN)
    assert RefGreedyBot().choose_move(board, clock_view()) == chess.Move.from_uci("e4d5")


def test_ref_depth3_finds_mate_in_one():
    board = chess.Board(MATE_IN_ONE)
    move = Refdepth3Bot().choose_move(board, clock_view())
    board.push(move)
    assert board.is_checkmate()


# White queen on d5 is defended by nothing; Black's c6 pawn recaptures a queen that
# grabs on d5. Greedy sees only the capture; depth 3 sees the reply.
POISONED_PAWN = "4k3/8/2p5/3p4/8/8/8/3QK3 w - - 0 1"


def test_ref_depth3_declines_material_ref_greedy_hangs():
    board = chess.Board(POISONED_PAWN)
    assert RefGreedyBot().choose_move(board, clock_view()) == chess.Move.from_uci("d1d5")
    assert Refdepth3Bot().choose_move(board, clock_view()) != chess.Move.from_uci("d1d5")


async def test_seeding_is_idempotent_and_never_overwrites_a_rating(store):
    bots = BotRepo(store.writer, store.executor)
    await seed_anchors(store.writer, store.executor)

    async with critical_section(store.writer, store.executor):
        seeded = await bots.get_by_name("ref-random")
        store.writer.execute("UPDATE bots SET rating = 1234 WHERE id = ?", (seeded.id,))

    await seed_anchors(store.writer, store.executor)

    assert len(await bots.list_anchors()) == len(ANCHORS) == 3
    assert (await bots.get_by_name("ref-random")).rating == 1234


async def test_seeded_anchors_carry_the_anchor_columns(store):
    bots = BotRepo(store.writer, store.executor)
    await seed_anchors(store.writer, store.executor)

    anchors = await bots.list_anchors()
    assert [a.name for a in anchors] == ["ref-random", "ref-greedy", "ref-depth3"]
    for anchor in anchors:
        assert anchor.role == "anchor"
        assert anchor.is_anchor == 1
        assert anchor.owner == "server"
        assert anchor.last_poll_mono is None


async def test_seeded_anchors_appear_on_the_leaderboard_and_are_marked(store):
    """Plan task 14 and `LeaderboardEntry.is_anchor`: anchors are shown and marked,
    not hidden. Design §10.3's parenthetical still says the leaderboard filters to
    `competitor` — if it did, `is_anchor` on the entry would have no purpose."""
    await seed_anchors(store.writer, store.executor)
    rows = await BotRepo(store.writer, store.executor).list_leaderboard()
    assert rows != []
    assert all(row.role == "anchor" and row.is_anchor == 1 for row in rows)


async def test_seeding_returns_no_token_and_hashes_are_distinct(store):
    assert await seed_anchors(store.writer, store.executor) is None
    hashes = [a.token_hash for a in await BotRepo(store.writer, store.executor).list_anchors()]
    assert all(h for h in hashes)
    assert len(set(hashes)) == 3


async def test_the_locked_form_takes_the_caller_s_transaction(store):
    async with critical_section(store.writer, store.executor) as txn:
        await seed_anchors_locked(txn)
        inside = await BotRepo(txn.conn, txn.executor).list_anchors()
    assert len(inside) == 3
