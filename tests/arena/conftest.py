"""Test configuration for arena tests."""
import sys
from pathlib import Path

# Add starter-kit to path so we can import ref_bots and chess_client
starter_kit_path = Path(__file__).parent.parent.parent / "starter-kit"
sys.path.insert(0, str(starter_kit_path))
