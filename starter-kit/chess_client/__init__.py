"""Chess Arena SDK types and client.

This module re-exports ClockView from chess_core to provide a stable
import path for bot implementations while maintaining the single source
of truth in chess_core.
"""
from chess_core import ClockView

__all__ = ["ClockView"]
