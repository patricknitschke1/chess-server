"""Match state machine per §7.

Pure state transitions with validation helpers.
"""
from dataclasses import dataclass
from typing import Optional
from chess_core.types import GameStatus, TerminationReason, GameResult, MoveResult
from chess_core.rules import PLY_CAP


@dataclass(frozen=True)
class MatchState:
    """Pure game state machine per §7.
    
    Encodes legal transitions for CAS validation.
    """
    status: GameStatus
    ply: int
    result: Optional[GameResult]
    termination: Optional[TerminationReason]


def create_match() -> MatchState:
    """Create initial match state: pending at ply 0."""
    return MatchState(
        status=GameStatus.PENDING,
        ply=0,
        result=None,
        termination=None
    )


def transition_to_active(state: MatchState) -> MatchState:
    """Transition pending -> active per §7 (first delivery)."""
    if state.status != GameStatus.PENDING:
        raise ValueError(f"Cannot transition to active from {state.status}")
    
    return MatchState(
        status=GameStatus.ACTIVE,
        ply=state.ply,
        result=state.result,
        termination=state.termination
    )


def transition_after_move(
    state: MatchState,
    move_result: MoveResult
) -> MatchState:
    """Transition after applying a move.
    
    If move_result is terminal, transitions to finished.
    Otherwise increments ply, checking for the §22 cap.
    
    Ordering is load-bearing per §22: terminal check PRECEDES cap check,
    so a checkmate delivered on the capping ply is recorded as checkmate,
    not adjudication.
    """
    if state.status != GameStatus.ACTIVE:
        raise ValueError(f"Cannot apply move in status {state.status}")
    
    new_ply = state.ply + 1
    
    # Terminal check FIRST — a game ending decisively on ply 200 keeps its result
    if move_result.is_terminal:
        return MatchState(
            status=GameStatus.FINISHED,
            ply=new_ply,
            result=move_result.result,
            termination=move_result.termination
        )
    # Cap check SECOND
    elif new_ply >= PLY_CAP:
        # §22: flat cap, unconditional draw. Deliberately not material-based —
        # the position may be winning for either side and it is still a draw.
        return MatchState(
            status=GameStatus.FINISHED,
            ply=new_ply,
            result=GameResult.DRAW,
            termination=TerminationReason.ADJUDICATED
        )
    else:
        return MatchState(
            status=GameStatus.ACTIVE,
            ply=new_ply,
            result=None,
            termination=None
        )


def transition_to_terminal(
    state: MatchState,
    termination: TerminationReason,
    result: Optional[GameResult]
) -> MatchState:
    """Transition to a terminal state (finished or aborted).
    
    Covers all terminal transitions: flag, forfeit, resignation,
    abandonment, no-show, adjudication, admin abort, restart abort.
    
    Args:
        state: Current match state
        termination: How the game ended
        result: Game result (None for aborted games)
    
    Returns:
        MatchState with status='finished' or 'aborted' as appropriate
    """
    # Determine if this is an abort or a finish
    abort_reasons = {
        TerminationReason.NO_SHOW,
        TerminationReason.SERVER_RESTART,
        TerminationReason.ADMIN_ABORT
    }
    
    if termination in abort_reasons:
        status = GameStatus.ABORTED
    else:
        status = GameStatus.FINISHED
    
    return MatchState(
        status=status,
        ply=state.ply,
        result=result,
        termination=termination
    )


def is_terminal(state: MatchState) -> bool:
    """Check if match is in a terminal state (finished or aborted)."""
    return state.status in {GameStatus.FINISHED, GameStatus.ABORTED}


def can_transition(state: MatchState, to_status: GameStatus) -> bool:
    """Validate state transition is legal per §7 diagram.
    
    Legal transitions:
    - pending → active
    - pending → aborted
    - active → finished
    - active → aborted
    - (no transitions from terminal states)
    """
    if is_terminal(state):
        return False  # Cannot transition from terminal state
    
    if state.status == GameStatus.PENDING:
        return to_status in {GameStatus.ACTIVE, GameStatus.ABORTED}
    elif state.status == GameStatus.ACTIVE:
        return to_status in {GameStatus.FINISHED, GameStatus.ABORTED}
    else:
        return False
