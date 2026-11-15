import dataclasses

import pytest
import redis.asyncio as aioredis

from app.clock import FakeClock
from app.rate_limit import RateLimiter, code_email_key, code_ip_key, verify_ip_key


@pytest.fixture
async def redis(base_settings):
    client = aioredis.from_url(base_settings.redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_limit_is_reached_on_the_next_call(redis):
    limiter = RateLimiter(redis, FakeClock())
    results = [await limiter.hit("rl:test", 3, 3600) for _ in range(5)]
    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert all(1 <= r.retry_after <= 3600 for r in results)


async def test_window_resets_when_the_fake_clock_reaches_the_next_window(redis):
    clock = FakeClock()
    limiter = RateLimiter(redis, clock)
    for _ in range(3):
        await limiter.hit("rl:test", 2, 600)
    assert (await limiter.hit("rl:test", 2, 600)).allowed is False
    clock.advance(601)
    assert (await limiter.hit("rl:test", 2, 600)).allowed is True


async def test_different_keys_do_not_share_counters(redis):
    limiter = RateLimiter(redis, FakeClock())
    await limiter.hit("rl:a", 1, 60)
    assert (await limiter.hit("rl:a", 1, 60)).allowed is False
    assert (await limiter.hit("rl:b", 1, 60)).allowed is True


async def test_keys_never_contain_the_plain_email(redis):
    limiter = RateLimiter(redis, FakeClock())
    await limiter.hit(code_email_key("someone@example.test"), 5, 3600)
    await limiter.hit(code_ip_key("203.0.113.9"), 20, 3600)
    await limiter.hit(verify_ip_key("203.0.113.9"), 30, 600)
    keys = await redis.keys("*")
    assert keys and all("someone" not in k and "@" not in k for k in keys)
