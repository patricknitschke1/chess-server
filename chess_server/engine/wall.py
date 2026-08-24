"""Wall clock, for display and audit only. Never compared, never subtracted.

Elapsed time is monotonic and injectable (`EngineDeps.now_mono`); these strings
decide nothing, which is why they are read directly rather than injected.
"""
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
