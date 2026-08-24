"""Token buckets, bounded (role spec §8.7).

Keyed on `token_hash`, never on the raw token: a plaintext token living in a
long-lived global appears in every traceback frame that touches the limiter,
which is exactly what "never logged" exists to prevent.
"""
from collections import OrderedDict
from dataclasses import dataclass

from chess_core import elapsed_ms

# 20 requests per second sustained, expressed as the gap between refilled tokens
# so the arithmetic stays in milliseconds and never divides by a nanosecond count.
REFILL_MS_PER_TOKEN = 50
BURST = 40

# One minute per 10 registrations, same shape.
REGISTER_REFILL_MS_PER_TOKEN = 6_000
REGISTER_PER_IP_PER_MIN = 10

# An unauthenticated caller sending garbage tokens must not grow this without
# limit. An evicted bucket restarts full, which at worst grants one extra burst.
MAX_KEYS = 256


@dataclass
class _Bucket:
    tokens: float
    last_mono: int


class RateLimiter:
    def __init__(
        self,
        capacity: int = BURST,
        refill_ms_per_token: int = REFILL_MS_PER_TOKEN,
        max_keys: int = MAX_KEYS,
    ):
        self.capacity = capacity
        self.refill_ms_per_token = refill_ms_per_token
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def keys(self) -> list[str]:
        return list(self._buckets)

    def allow(self, key: str, now_mono: int) -> bool:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.capacity), last_mono=now_mono)
            self._buckets[key] = bucket
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)  # LRU: the oldest key goes
        else:
            self._buckets.move_to_end(key)
            refilled = elapsed_ms(bucket.last_mono, now_mono) / self.refill_ms_per_token
            bucket.tokens = min(self.capacity, bucket.tokens + refilled)
            bucket.last_mono = now_mono
        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True


def register_limiter() -> RateLimiter:
    return RateLimiter(
        capacity=REGISTER_PER_IP_PER_MIN,
        refill_ms_per_token=REGISTER_REFILL_MS_PER_TOKEN,
    )
