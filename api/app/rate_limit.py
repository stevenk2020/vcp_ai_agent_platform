"""Fixed-window counters in Redis. The window number comes from the injected clock, so tests can move
to the next window with the fake clock; EXPIRE only cleans old keys up."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.clock import Clock


@dataclass(frozen=True)
class RateResult:
    allowed: bool
    retry_after: int


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def code_email_key(email: str) -> str:
    return f"rl:code:email:{_digest(email)}"


def code_ip_key(ip: str) -> str:
    return f"rl:code:ip:{ip}"


def verify_ip_key(ip: str) -> str:
    return f"rl:verify:ip:{ip}"


class RateLimiter:
    def __init__(self, redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateResult:
        now = self._clock.now().timestamp()
        window = int(now // window_seconds)
        full_key = f"{key}:{window}"
        count = await self._redis.incr(full_key)
        if count == 1:
            await self._redis.expire(full_key, window_seconds * 2)
        retry_after = int((window + 1) * window_seconds - now)
        return RateResult(allowed=count <= limit, retry_after=max(1, retry_after))
