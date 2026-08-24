"""Chess rules: move validation, termination detection, notation conversion.

Uses python-chess for move generation. Never hand-rolls validation.
"""
import chess
from typing import List, Optional
from chess_core.types import (
    MoveOutcome, MoveResult, TerminationReason, GameResult
)


STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PLY_CAP = 200  # §22 adjudication cap


def validate_and_apply_move(fen: str, move_uci: str) -> MoveOutcome:
    """Validate and apply a move in UCI notation.
    
    Returns a MoveOutcome that models rejection explicitly. Exceptions
    are reserved for genuinely invalid input (malformed FEN, syntactically
    unparseable UCI).
    
    Args:
        fen: Current position in FEN notation
        move_uci: Move in UCI notation (e.g. "e2e4")
    
    Returns:
        MoveOutcome with accepted=True and move_result on success,
        or accepted=False and rejection_reason on illegal move
    
    Raises:
        ValueError: if fen is malformed or move_uci is syntactically invalid
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Malformed FEN: {e}")
    
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as e:
        raise ValueError(f"Invalid UCI notation: {e}")
    
    if move not in board.legal_moves:
        return MoveOutcome(
            accepted=False,
            move_result=None,
            rejection_reason=f"Illegal move: {move_uci}"
        )
    
    san = board.san(move)
    board.push(move)
    fen_after = board.fen()
    
    # Check termination after the move
    is_terminal = False
    termination = None
    result = None
    
    if board.is_checkmate():
        is_terminal = True
        termination = TerminationReason.CHECKMATE
        result = GameResult.BLACK_WIN if board.turn == chess.WHITE else GameResult.WHITE_WIN
    elif board.is_stalemate():
        is_terminal = True
        termination = TerminationReason.STALEMATE
        result = GameResult.DRAW
    elif board.is_insufficient_material():
        is_terminal = True
        termination = TerminationReason.INSUFFICIENT
        result = GameResult.DRAW
    elif board.can_claim_fifty_moves():
        is_terminal = True
        termination = TerminationReason.FIFTY_MOVE
        result = GameResult.DRAW
    # No threefold arm: this board is built from a FEN and pushed once, so it
    # carries no position history. Threefold belongs to detect_termination.
    
    move_result = MoveResult(
        fen_after=fen_after,
        san=san,
        is_terminal=is_terminal,
        termination=termination,
        result=result
    )
    
    return MoveOutcome(
        accepted=True,
        move_result=move_result,
        rejection_reason=None
    )


def position_key(fen: str) -> str:
    """Extract position key for threefold repetition detection.
    
    Returns only the first four FEN fields (placement, side to move,
    castling rights, en passant target), omitting the halfmove clock
    and fullmove number. Two positions with identical keys are the
    same position for threefold purposes.
    
    Contract: threefold detection compares position_key(fen) strings,
    never full FEN strings.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        Position key string (first four FEN fields joined by space)
    
    Raises:
        ValueError: if fen is invalid
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    # Extract first 4 fields: placement, side, castling, ep
    parts = fen.split()
    if len(parts) < 4:
        raise ValueError(f"FEN missing required fields: {fen}")
    
    return " ".join(parts[:4])


def get_legal_moves(fen: str) -> List[str]:
    """Generate all legal moves from a position in UCI notation.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        List of legal moves in UCI notation, sorted lexicographically
    
    Raises:
        ValueError: if fen is invalid
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    moves = [move.uci() for move in board.legal_moves]
    return sorted(moves)


def detect_termination(
    fen: str,
    history_fens: List[str]
) -> tuple[bool, Optional[TerminationReason], Optional[GameResult]]:
    """Detect if position is terminal and determine result.
    
    Includes server-claimed fifty-move and threefold per §22 (uses
    python-chess can_claim_draw). Threefold detection compares
    position_key(fen) values, not full FEN strings.
    
    Args:
        fen: Current position in FEN notation
        history_fens: All FENs in game history for threefold detection
    
    Returns:
        (is_terminal, termination_reason, result)
        termination_reason and result are None if not terminal
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    # Checkmate
    if board.is_checkmate():
        winner = GameResult.BLACK_WIN if board.turn == chess.WHITE else GameResult.WHITE_WIN
        return (True, TerminationReason.CHECKMATE, winner)
    
    # Stalemate
    if board.is_stalemate():
        return (True, TerminationReason.STALEMATE, GameResult.DRAW)
    
    # Insufficient material is a fact about the position and needs no claim, so it
    # outranks the claimable draws below. K vs K reads better than "fifty_move".
    if board.is_insufficient_material():
        return (True, TerminationReason.INSUFFICIENT, GameResult.DRAW)

    # Fifty-move rule (server-claimed on the bots' behalf, §22)
    if board.can_claim_fifty_moves():
        return (True, TerminationReason.FIFTY_MOVE, GameResult.DRAW)
    
    # Threefold repetition (server-claimed, via position key)
    current_key = position_key(fen)
    key_count = sum(1 for h in history_fens if position_key(h) == current_key)
    if key_count >= 3:
        return (True, TerminationReason.THREEFOLD, GameResult.DRAW)
    
    return (False, None, None)


def fen_to_ascii(fen: str) -> str:
    """Render a position as ASCII art for MCP get_game() per §13.2.
    
    Args:
        fen: Position in FEN notation
    
    Returns:
        ASCII board representation with rank/file labels
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    # Get board string and add rank labels
    board_str = str(board)
    lines = board_str.split('\n')
    
    # Add rank numbers (8 down to 1)
    labeled_lines = []
    for i, line in enumerate(lines):
        rank = 8 - i
        labeled_lines.append(f"{rank} {line}")
    
    # Add file letters
    labeled_lines.append("  a b c d e f g h")
    
    return '\n'.join(labeled_lines)


def uci_to_san(fen: str, move_uci: str) -> str:
    """Convert UCI move to SAN notation in the given position.
    
    Args:
        fen: Position in FEN notation
        move_uci: Move in UCI notation
    
    Returns:
        Move in SAN notation (e.g. "Nf3")
    
    Raises:
        ValueError: if move_uci is illegal in the position
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"Invalid FEN: {e}")
    
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as e:
        raise ValueError(f"Invalid UCI: {e}")
    
    if move not in board.legal_moves:
        raise ValueError(f"Illegal move {move_uci} in position")
    
    return board.san(move)


def san_list_to_pgn(
    san_moves: List[str],
    white_name: str,
    black_name: str,
    result: GameResult,
    white_rating: Optional[int] = None,
    black_rating: Optional[int] = None,
    starting_fen: Optional[str] = None
) -> str:
    """Format a game as PGN for arena.py export.
    
    Args:
        san_moves: Moves in SAN notation
        white_name: White player name
        black_name: Black player name
        result: Game result
        white_rating: Optional ELO rating for White
        black_rating: Optional ELO rating for Black
        starting_fen: Position the game began from. Omit for the standard start.
            A game from an opening position is unreadable without this.
    
    Returns:
        Complete PGN string with headers and movetext
    """
    # Map result to PGN notation
    result_map = {
        GameResult.WHITE_WIN: "1-0",
        GameResult.BLACK_WIN: "0-1",
        GameResult.DRAW: "1/2-1/2"
    }
    result_str = result_map[result]
    
    start = chess.Board(starting_fen) if starting_fen else chess.Board()
    
    # Build headers
    headers = [
        f'[White "{white_name}"]',
        f'[Black "{black_name}"]',
        f'[Result "{result_str}"]'
    ]
    
    if starting_fen is not None and starting_fen != STARTING_FEN:
        headers.append('[SetUp "1"]')
        headers.append(f'[FEN "{starting_fen}"]')
    
    if white_rating is not None:
        headers.append(f'[WhiteElo "{white_rating}"]')
    if black_rating is not None:
        headers.append(f'[BlackElo "{black_rating}"]')
    
    # Build movetext, numbering from wherever the game actually began
    movetext_parts = []
    move_num = start.fullmove_number
    white_to_move = start.turn == chess.WHITE
    for i, move in enumerate(san_moves):
        if white_to_move:
            movetext_parts.append(f"{move_num}. {move}")
        else:
            # A game resuming mid-move needs the ellipsis to place Black's reply.
            movetext_parts.append(f"{move_num}... {move}" if i == 0 else move)
            move_num += 1
        white_to_move = not white_to_move
    
    movetext = " ".join(movetext_parts)
    if movetext:
        movetext += " "
    movetext += result_str
    
    return "\n".join(headers) + "\n\n" + movetext + "\n"
