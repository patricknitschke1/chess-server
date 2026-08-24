"""Configuration, read from the environment once and then frozen (role spec §8.6)."""
import os
from dataclasses import dataclass
from typing import Mapping, Optional

from chess_core import POLL_HOLD_NS

# Written as 1e9 rather than a named constant: the integer form is numerically
# TICK_INTERVAL_NS, and importing that here would assert a dependency that does
# not exist. This is a unit conversion, not a policy.
POLL_HOLD_SECONDS = POLL_HOLD_NS / 1e9


@dataclass(frozen=True)
class Settings:
    db_path: str
    join_code: str
    admin_token: str
    poll_hold_seconds: float = POLL_HOLD_SECONDS

    def __post_init__(self) -> None:
        if not self.join_code:
            raise ValueError(
                "JOIN_CODE is empty. Set it before starting the server, or every"
                " client on the network can register a bot."
            )
        if not self.admin_token:
            # An empty token compares equal to a missing header, so every admin
            # route would be open to anyone who omitted authentication entirely.
            raise ValueError(
                "ADMIN_TOKEN is empty. Set it before starting the server, or the"
                " admin routes are unauthenticated."
            )


def settings_from_env(env: Optional[Mapping[str, str]] = None) -> Settings:
    source = os.environ if env is None else env
    return Settings(
        db_path=source.get("DB_PATH", "arena.db"),
        join_code=source.get("JOIN_CODE", ""),
        admin_token=source.get("ADMIN_TOKEN", ""),
    )
