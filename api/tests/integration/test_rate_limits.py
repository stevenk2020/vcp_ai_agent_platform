"""US5: caps on codes per email and per IP, and on verify attempts per IP."""

from __future__ import annotations

import dataclasses

import httpx
import pytest
import redis as sync_redis

from app.main import create_app
from tests.conftest import ORIGIN
from tests.integration.helpers import add_allowlist, read_code

ALLOWED = "allowed@example.com"
STRANGER = "stranger@example.org"


async def request_code(client, email, ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return await client.post("/v1/auth/code/request", json={"email": email}, headers=headers)


async def test_the_sixth_code_for_one_email_in_an_hour_is_refused_and_sends_nothing(client, mailer, sql):
    for _ in range(5):
        assert (await request_code(client, ALLOWED)).status_code == 202
    sent = len(mailer.messages(ALLOWED))
    response = await request_code(client, ALLOWED)
    assert response.status_code == 429 and response.json()["error"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1
    assert len(mailer.messages(ALLOWED)) == sent == 5
    assert "rate_limited" in [r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()]


async def test_the_twenty_first_code_from_one_address_is_refused_even_for_other_emails(client, mailer):
    for i in range(20):
        assert (await request_code(client, f"person{i}@example.com", ip="203.0.113.5")).status_code == 202
    response = await request_code(client, "another@example.com", ip="203.0.113.5")
    assert response.status_code == 429 and response.json()["error"] == "rate_limited"
    assert mailer.messages("another@example.com") == []
    # a different address is unaffected
    assert (await request_code(client, "another@example.com", ip="203.0.113.6")).status_code == 202


async def test_the_refusal_is_identical_for_allowed_and_unknown_addresses(client, sql):
    add_allowlist(sql, ALLOWED)
    results = {}
    for email, ip in ((ALLOWED, "198.51.100.1"), (STRANGER, "198.51.100.2")):
        for _ in range(5):
            await request_code(client, email, ip=ip)
        results[email] = await request_code(client, email, ip=ip)
    a, b = results[ALLOWED], results[STRANGER]
    assert (a.status_code, a.json()) == (b.status_code, b.json())
    assert ("retry-after" in a.headers) and ("retry-after" in b.headers)


async def test_requests_work_again_in_the_next_window(client, mailer, clock):
    for _ in range(6):
        await request_code(client, ALLOWED)
    assert (await request_code(client, ALLOWED)).status_code == 429
    clock.advance(hours=1, seconds=1)
    assert (await request_code(client, ALLOWED)).status_code == 202


async def test_the_verify_cap_is_thirty_per_ten_minutes_per_address(client, clock):
    for i in range(30):
        response = await client.post(
            "/v1/auth/code/verify", json={"email": f"x{i}@example.com", "code": "123456"},
            headers={"X-Forwarded-For": "192.0.2.9"},
        )
        assert response.status_code == 401
    blocked = await client.post("/v1/auth/code/verify", json={"email": "x@example.com", "code": "123456"},
                                headers={"X-Forwarded-For": "192.0.2.9"})
    assert blocked.status_code == 429 and blocked.json()["error"] == "rate_limited"
    clock.advance(minutes=10, seconds=1)
    again = await client.post("/v1/auth/code/verify", json={"email": "x@example.com", "code": "123456"},
                              headers={"X-Forwarded-For": "192.0.2.9"})
    assert again.status_code == 401


async def test_limits_come_from_settings(settings, clock, mailer):
    tight = dataclasses.replace(settings, code_rate_per_email=2)
    application = create_app(settings=tight, clock=clock, mailer=mailer)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
            statuses = [(await request_code(c, ALLOWED)).status_code for _ in range(3)]
    assert statuses == [202, 202, 429]


async def test_redis_keys_hold_no_plain_email(client, settings):
    await request_code(client, "someone.private@example.com", ip="203.0.113.77")
    keys = [k.decode() for k in sync_redis.Redis.from_url(settings.redis_url).keys("*")]
    assert keys and all("someone" not in k and "@" not in k for k in keys)


async def test_the_client_address_comes_from_the_proxy_header_and_is_recorded(client, sql):
    await request_code(client, ALLOWED, ip="203.0.113.7")
    assert str(sql.execute("SELECT requested_ip FROM app.email_codes").fetchone()["requested_ip"]) == "203.0.113.7"
    await request_code(client, "second@example.com", ip="not-an-ip")  # garbage header is ignored, not a crash
    rows = sql.execute("SELECT email, requested_ip FROM app.email_codes ORDER BY created_at").fetchall()
    assert rows[1]["requested_ip"] is not None  # falls back to the socket peer
