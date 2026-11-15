"""US1: an approved person signs in with an emailed code, uses the session, and signs out."""

from __future__ import annotations

import asyncio
import dataclasses

import httpx
import pytest

from app.mailer import DisabledMailer, MailSendError
from app.main import create_app
from tests.conftest import ORIGIN
from tests.integration.helpers import add_allowlist, read_code

EMAIL = "alice@example.com"


async def request_code(client, email=EMAIL):
    return await client.post("/v1/auth/code/request", json={"email": email})


async def verify(client, code, email=EMAIL):
    return await client.post("/v1/auth/code/verify", json={"email": email, "code": code})


async def sign_in(client, mailer, email=EMAIL):
    assert (await request_code(client, email)).status_code == 202
    response = await verify(client, read_code(mailer, email), email)
    assert response.status_code == 200
    return response


async def test_request_returns_202_and_sends_one_mail(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    response = await request_code(client)
    assert response.status_code == 202
    assert response.json() == {"message": "If this address can receive mail, a code has been sent."}
    assert len(mailer.messages(EMAIL)) == 1


async def test_verify_signs_in_with_the_role_of_the_entry_and_sets_a_hardened_cookie(client, mailer, sql, settings):
    add_allowlist(sql, "example.com", kind="domain", role="builder")
    response = await sign_in(client, mailer)
    assert response.json() == {"status": "signed_in", "role": "builder"}

    cookie = response.headers["set-cookie"]
    lowered = cookie.lower()
    assert cookie.startswith("__Host-session=")
    for flag in ("secure", "httponly", "samesite=lax", "path=/", f"max-age={settings.session_days * 86400}"):
        assert flag in lowered, flag
    assert "domain=" not in lowered
    assert settings.session_days * 86400 == 604800

    token = client.cookies.get("__Host-session")
    stored = sql.execute("SELECT token_hash FROM app.sessions").fetchall()
    assert len(stored) == 1 and bytes(stored[0]["token_hash"]) != token.encode()
    assert token.encode() not in bytes(stored[0]["token_hash"])

    user = sql.execute("SELECT email, role, status, last_login_at FROM app.users").fetchone()
    assert (user["email"], user["role"], user["status"]) == (EMAIL, "builder", "active")
    assert user["last_login_at"] is not None


async def test_me_returns_the_current_user_and_needs_a_session(client, mailer, sql):
    assert (await client.get("/v1/me")).status_code == 401
    add_allowlist(sql, EMAIL, role="user")
    await sign_in(client, mailer)
    me = await client.get("/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert (body["email"], body["role"], body["status"]) == (EMAIL, "user", "active") and body["id"]


async def test_a_code_works_only_once(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    await request_code(client)
    code = read_code(mailer, EMAIL)
    assert (await verify(client, code)).status_code == 200
    again = await verify(client, code)
    assert again.status_code == 401 and again.json()["error"] == "invalid_or_expired_code"


async def test_a_code_expires_after_ten_minutes(client, mailer, clock, sql):
    add_allowlist(sql, EMAIL)
    await request_code(client)
    code = read_code(mailer, EMAIL)
    clock.advance(minutes=10, seconds=1)
    response = await verify(client, code)
    assert response.status_code == 401 and response.json()["error"] == "invalid_or_expired_code"


async def test_a_code_still_works_just_before_ten_minutes(client, mailer, clock, sql):
    add_allowlist(sql, EMAIL)
    await request_code(client)
    code = read_code(mailer, EMAIL)
    clock.advance(minutes=9, seconds=59)
    assert (await verify(client, code)).status_code == 200


async def test_five_wrong_attempts_kill_the_code(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    await request_code(client)
    code = read_code(mailer, EMAIL)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert (await verify(client, wrong)).status_code == 401
    assert (await verify(client, code)).status_code == 401
    row = sql.execute("SELECT attempts, cancelled_at FROM app.email_codes").fetchone()
    assert row["attempts"] == 5 and row["cancelled_at"] is not None


async def test_four_wrong_attempts_do_not_kill_the_code(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    await request_code(client)
    code = read_code(mailer, EMAIL)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert (await verify(client, wrong)).status_code == 401
    assert (await verify(client, code)).status_code == 200


async def test_requesting_a_new_code_cancels_the_earlier_one(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    await request_code(client)
    first = read_code(mailer, EMAIL)
    await request_code(client)
    second = read_code(mailer, EMAIL)
    if first == second:  # one in a million: ask again so the two differ
        await request_code(client)
        second = read_code(mailer, EMAIL)
    assert (await verify(client, first)).status_code == 401
    assert (await verify(client, second)).status_code == 200


async def test_email_is_trimmed_and_case_insensitive(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    assert (await request_code(client, "  Alice@Example.COM ")).status_code == 202
    assert len(mailer.messages(EMAIL)) == 1
    assert (await verify(client, read_code(mailer, EMAIL), "ALICE@example.com")).status_code == 200
    assert sql.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"] == 1


async def test_two_simultaneous_verifications_of_one_code_succeed_once(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    await request_code(client)
    code = read_code(mailer, EMAIL)
    results = await asyncio.gather(verify(client, code), verify(client, code))
    assert sorted(r.status_code for r in results) == [200, 401]
    assert sql.execute("SELECT count(*) AS n FROM app.sessions").fetchone()["n"] == 1


async def test_a_session_ends_after_seven_days(client, mailer, clock, sql):
    add_allowlist(sql, EMAIL)
    await sign_in(client, mailer)
    clock.advance(days=6, hours=23)
    assert (await client.get("/v1/me")).status_code == 200
    clock.advance(hours=1, seconds=1)
    assert (await client.get("/v1/me")).status_code == 401


async def test_logout_ends_the_session_and_clears_the_cookie(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    await sign_in(client, mailer)
    token = client.cookies.get("__Host-session")
    response = await client.post("/v1/auth/logout")
    assert response.status_code == 204
    assert "__Host-session=" in response.headers["set-cookie"] and "max-age=0" in response.headers["set-cookie"].lower()
    # even a client that still holds the old token is refused: the session is revoked server side
    other = httpx.AsyncClient(transport=client._transport, base_url=ORIGIN, headers={"Origin": ORIGIN},
                              cookies={"__Host-session": token})
    async with other:
        assert (await other.get("/v1/me")).status_code == 401
    assert sql.execute("SELECT revoked_reason FROM app.sessions").fetchone()["revoked_reason"] == "signed_out"


async def test_logout_without_a_session_is_harmless(client):
    assert (await client.post("/v1/auth/logout")).status_code == 204


async def test_request_validation(client):
    assert (await client.post("/v1/auth/code/request", json={"email": "not-an-email"})).status_code == 422
    assert (await client.post("/v1/auth/code/request", json={"email": EMAIL, "extra": 1})).status_code == 422
    assert (await client.post("/v1/auth/code/verify", json={"email": EMAIL, "code": "12345"})).status_code == 422
    assert (await client.post("/v1/auth/code/verify", json={"email": EMAIL, "code": "abcdef"})).status_code == 422


async def test_verify_for_an_unknown_email_is_the_generic_error(client):
    response = await verify(client, "123456", "nobody@example.com")
    assert response.status_code == 401 and response.json()["error"] == "invalid_or_expired_code"


async def test_audit_records_the_sign_in_steps(client, mailer, sql):
    add_allowlist(sql, EMAIL)
    await sign_in(client, mailer)
    actions = [r["action"] for r in sql.execute("SELECT action FROM app.audit_events ORDER BY id").fetchall()]
    for expected in ("code_requested", "code_verified", "session_created"):
        assert expected in actions, expected


class FailingMailer:
    async def send_code(self, to, code, minutes):
        raise MailSendError("boom")


async def test_a_failing_mailer_gives_try_again_later_and_leaves_no_usable_code(settings, clock, sql):
    add_allowlist(sql, EMAIL)
    application = create_app(settings=settings, clock=clock, mailer=FailingMailer())
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
            response = await request_code(c)
    assert response.status_code == 503 and response.json()["error"] == "try_again_later"
    row = sql.execute("SELECT cancelled_at FROM app.email_codes").fetchone()
    assert row["cancelled_at"] is not None
    assert "code_send_failed" in [r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()]


async def test_email_codes_can_be_switched_off(settings, clock, capture_dir, sql):
    add_allowlist(sql, EMAIL)
    off = dataclasses.replace(settings, mail_backend="disabled")
    application = create_app(settings=off, clock=clock, mailer=DisabledMailer())
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
            response = await request_code(c)
            health = await c.get("/v1/health")
    assert response.status_code == 503 and response.json()["error"] == "not_configured"
    assert list(capture_dir.iterdir()) == []
    assert sql.execute("SELECT count(*) AS n FROM app.email_codes").fetchone()["n"] == 0
    assert health.json()["mail_backend"] == "disabled"
