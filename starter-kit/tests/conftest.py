"""Test configuration for starter-kit tests."""
import sys
from pathlib import Path

# Add starter-kit to path so we can import bot, arena, opening_book and chess_client
starter_kit_path = Path(__file__).parent.parent
sys.path.insert(0, str(starter_kit_path))
