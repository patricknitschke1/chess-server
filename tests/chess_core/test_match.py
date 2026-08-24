"""Tests for match state machine per §7."""
import chess_core.match as match
from chess_core.types import (
    GameStatus, TerminationReason, GameResult, MoveResult
)
from chess_core.rules import PLY_CAP


def test_create_match_is_pending():
    """Initial match state is pending at ply 0."""
    state = match.create_match()
    
    assert state.status == GameStatus.PENDING
    assert state.ply == 0
    assert state.result is None
    assert state.termination is None


def test_transition_pending_to_active():
    """Transition pending → active per §7."""
    state = match.create_match()
    active = match.transition_to_active(state)
    
    assert active.status == GameStatus.ACTIVE
    assert active.ply == 0  # ply unchanged


def test_transition_after_move_increments_ply():
    """Non-terminal move increments ply."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=5,
        result=None,
        termination=None
    )
    
    move_result = MoveResult(
        fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        san="e4",
        is_terminal=False,
        termination=None,
        result=None
    )
    
    new_state = match.transition_after_move(state, move_result)
    
    assert new_state.status == GameStatus.ACTIVE
    assert new_state.ply == 6
    assert new_state.result is None


def test_transition_after_move_adjudicates_at_ply_cap():
    """Reaching PLY_CAP ends the game as a draw, unconditionally, per §22.

    The cap is deliberately not material-based: the position may be completely
    winning for one side and it is still a draw. That is the whole point of
    replacing revision 1's "draw if within a pawn" rule — no judgement call,
    no bespoke evaluation, one line.
    """
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=PLY_CAP - 1,
        result=None,
        termination=None
    )

    # A non-terminal move by a side that is winning on material.
    move_result = MoveResult(
        fen_after="4k3/8/8/8/8/8/8/3QK3 b - - 0 100",
        san="Qd1",
        is_terminal=False,
        termination=None,
        result=None
    )

    new_state = match.transition_after_move(state, move_result)

    assert new_state.status == GameStatus.FINISHED
    assert new_state.ply == PLY_CAP
    assert new_state.termination == TerminationReason.ADJUDICATED
    assert new_state.result == GameResult.DRAW


def test_transition_after_move_does_not_adjudicate_below_cap():
    """One ply short of the cap, play continues."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=PLY_CAP - 2,
        result=None,
        termination=None
    )

    move_result = MoveResult(
        fen_after="4k3/8/8/8/8/8/8/3QK3 b - - 0 100",
        san="Qd1",
        is_terminal=False,
        termination=None,
        result=None
    )

    new_state = match.transition_after_move(state, move_result)

    assert new_state.status == GameStatus.ACTIVE
    assert new_state.ply == PLY_CAP - 1
    assert new_state.termination is None


def test_terminal_move_at_cap_keeps_its_own_termination():
    """A checkmate delivered on the capping ply is checkmate, not adjudication.

    Ordering matters: the terminal check precedes the cap check, so a game that
    ends decisively on ply 200 records the real result rather than a draw.
    """
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=PLY_CAP - 1,
        result=None,
        termination=None
    )

    move_result = MoveResult(
        fen_after="4k3/8/8/8/8/8/5PPP/6K1 b - - 0 100",
        san="Qe8#",
        is_terminal=True,
        termination=TerminationReason.CHECKMATE,
        result=GameResult.WHITE_WIN
    )

    new_state = match.transition_after_move(state, move_result)

    assert new_state.status == GameStatus.FINISHED
    assert new_state.termination == TerminationReason.CHECKMATE
    assert new_state.result == GameResult.WHITE_WIN


def test_transition_after_terminal_move_ends_game():
    """Terminal move transitions to finished."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=10,
        result=None,
        termination=None
    )
    
    move_result = MoveResult(
        fen_after="rnbqkb1r/pppp1ppp/5n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 4 4",
        san="Qh5#",
        is_terminal=True,
        termination=TerminationReason.CHECKMATE,
        result=GameResult.WHITE_WIN
    )
    
    new_state = match.transition_after_move(state, move_result)
    
    assert new_state.status == GameStatus.FINISHED
    assert new_state.ply == 11
    assert new_state.result == GameResult.WHITE_WIN
    assert new_state.termination == TerminationReason.CHECKMATE


def test_transition_to_terminal_finished():
    """Transition to finished for terminal reasons."""
    state = match.MatchState(
        status=GameStatus.ACTIVE,
        ply=20,
        result=None,
        termination=None
    )
    
    terminal = match.transition_to_terminal(
        state,
        TerminationReason.FLAG,
        GameResult.BLACK_WIN
    )
    
    assert terminal.status == GameStatus.FINISHED
    assert terminal.ply == 20  # ply unchanged
    assert terminal.result == GameResult.BLACK_WIN
    assert terminal.termination == TerminationReason.FLAG


def test_transition_to_terminal_aborted():
    """Transition to aborted for abort reasons."""
    state = match.MatchState(
        status=GameStatus.PENDING,
        ply=0,
        result=None,
        termination=None
    )
    
    aborted = match.transition_to_terminal(
        state,
        TerminationReason.NO_SHOW,
        None  # no result for aborted
    )
    
    assert aborted.status == GameStatus.ABORTED
    assert aborted.result is None
    assert aborted.termination == TerminationReason.NO_SHOW


def test_is_terminal():
    """is_terminal detects finished and aborted."""
    finished = match.MatchState(GameStatus.FINISHED, 30, GameResult.DRAW, TerminationReason.STALEMATE)
    aborted = match.MatchState(GameStatus.ABORTED, 0, None, TerminationReason.ADMIN_ABORT)
    active = match.MatchState(GameStatus.ACTIVE, 10, None, None)
    pending = match.MatchState(GameStatus.PENDING, 0, None, None)
    
    assert match.is_terminal(finished) is True
    assert match.is_terminal(aborted) is True
    assert match.is_terminal(active) is False
    assert match.is_terminal(pending) is False


def test_can_transition_valid():
    """can_transition validates legal transitions per §7."""
    pending = match.MatchState(GameStatus.PENDING, 0, None, None)
    active = match.MatchState(GameStatus.ACTIVE, 5, None, None)
    
    # pending → active: valid
    assert match.can_transition(pending, GameStatus.ACTIVE) is True
    
    # pending → aborted: valid
    assert match.can_transition(pending, GameStatus.ABORTED) is True
    
    # active → finished: valid
    assert match.can_transition(active, GameStatus.FINISHED) is True
    
    # active → aborted: valid
    assert match.can_transition(active, GameStatus.ABORTED) is True


def test_can_transition_invalid():
    """can_transition rejects illegal transitions."""
    pending = match.MatchState(GameStatus.PENDING, 0, None, None)
    active = match.MatchState(GameStatus.ACTIVE, 5, None, None)
    finished = match.MatchState(GameStatus.FINISHED, 30, GameResult.DRAW, TerminationReason.STALEMATE)
    
    # pending → finished: invalid (must go through active)
    assert match.can_transition(pending, GameStatus.FINISHED) is False
    
    # active → pending: invalid (cannot go backward)
    assert match.can_transition(active, GameStatus.PENDING) is False
    
    # finished → *: invalid (terminal)
    assert match.can_transition(finished, GameStatus.ACTIVE) is False
    assert match.can_transition(finished, GameStatus.PENDING) is False
