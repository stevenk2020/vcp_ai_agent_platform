"""Sign-in by emailed code, the allowlist gate, and sessions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import psycopg.errors

from app import audit
from app.errors import ApiError, invalid_code, not_configured, try_again_later
from app.mailer import MailSendError, NotConfigured
from app.security import (
    InvalidEmail, code_hmac, constant_time_equal, hash_token, new_code, new_token, normalize_email,
)

log = logging.getLogger("app.auth")

CODE_MINUTES = 10
MAX_ATTEMPTS = 5


@dataclass
class SignInOutcome:
    status: str  # signed_in | pending | refused
    role: str | None = None
    token: str | None = None


def _normalize(email: str) -> str:
    try:
        return normalize_email(email)
    except InvalidEmail as exc:
        raise ApiError(422, "invalid_request", "The email address is not valid.") from exc


async def request_code(state, raw_email: str, ip: str | None) -> None:
    """Issue and mail a new code. The allowlist is never consulted here (no oracle for who is allowed)."""
    settings, clock, db = state.settings, state.clock, state.db
    if settings.mail_backend == "disabled":
        raise not_configured("Sign-in by email code is not available. Please sign in with Google.")
    email = _normalize(raw_email)
    code, code_id, now = new_code(), uuid.uuid4(), clock.now()

    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE app.email_codes SET cancelled_at = %s, updated_at = %s "
            "WHERE email = %s AND consumed_at IS NULL AND cancelled_at IS NULL",
            (now, now, email),
        )
        await conn.execute(
            "INSERT INTO app.email_codes (id, email, code_hmac, expires_at, requested_ip) "
            "VALUES (%s, %s, %s, %s, %s::inet)",
            (code_id, email, code_hmac(settings.pepper, email, code_id, code),
             now + _minutes(CODE_MINUTES), ip),
        )
        await audit.record(conn, clock, "code_requested", target=email, ip=ip)

    try:
        await state.mailer.send_code(email, code, CODE_MINUTES)
    except NotConfigured as exc:
        raise not_configured("Sign-in by email code is not available.") from exc
    except MailSendError as exc:
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE app.email_codes SET cancelled_at = %s, updated_at = %s WHERE id = %s",
                (clock.now(), clock.now(), code_id),
            )
            await audit.record(conn, clock, "code_send_failed", "failure", target=email, ip=ip)
        log.warning("sending a sign-in code failed")
        raise try_again_later() from exc


def _minutes(n: int):
    from datetime import timedelta

    return timedelta(minutes=n)


async def verify_code(state, raw_email: str, code: str, ip: str | None) -> SignInOutcome:
    """Check a code. Attempts, cancellation and audit are committed BEFORE the error is raised,
    otherwise a rollback would erase the count of failed guesses."""
    settings, clock, db = state.settings, state.clock, state.db
    try:
        email = normalize_email(raw_email)
    except InvalidEmail:
        raise invalid_code() from None
    now = clock.now()
    verified = False

    async with db.transaction() as conn:
        cursor = await conn.execute(
            "SELECT id, code_hmac, attempts FROM app.email_codes "
            "WHERE email = %s AND consumed_at IS NULL AND cancelled_at IS NULL "
            "AND expires_at > %s AND attempts < %s "
            "ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
            (email, now, MAX_ATTEMPTS),
        )
        row = await cursor.fetchone()
        if row is None:
            await audit.record(conn, clock, "code_failed", "failure", target=email, ip=ip,
                               detail={"reason": "no_valid_code"})
        else:
            expected = code_hmac(settings.pepper, email, row["id"], code)
            if constant_time_equal(expected, bytes(row["code_hmac"])):
                await conn.execute(
                    "UPDATE app.email_codes SET consumed_at = %s, updated_at = %s WHERE id = %s",
                    (now, now, row["id"]),
                )
                verified = True
            else:
                attempts = row["attempts"] + 1
                locked = attempts >= MAX_ATTEMPTS
                await conn.execute(
                    "UPDATE app.email_codes SET attempts = %s, updated_at = %s, "
                    "cancelled_at = CASE WHEN %s THEN %s ELSE cancelled_at END WHERE id = %s",
                    (attempts, now, locked, now, row["id"]),
                )
                await audit.record(conn, clock, "code_failed", "failure", target=email, ip=ip,
                                   detail={"attempts": attempts})
                if locked:
                    await audit.record(conn, clock, "code_locked", "failure", target=email, ip=ip)

    if not verified:
        raise invalid_code()
    return await complete_sign_in(state, email, ip, via="code")


async def _find_entry(conn, email: str):
    """The allowlist entry for an address: an exact email entry beats a domain entry."""
    domain = email.rsplit("@", 1)[1]
    cursor = await conn.execute(
        "SELECT id, kind, value, default_role FROM app.allowlist "
        "WHERE (kind = 'email' AND value = %s) OR (kind = 'domain' AND value = %s) "
        "ORDER BY (kind = 'email') DESC LIMIT 1",
        (email, domain),
    )
    return await cursor.fetchone()


async def create_session(conn, state, user_id, ip: str | None) -> str:
    token = new_token()
    now = state.clock.now()
    await conn.execute(
        "INSERT INTO app.sessions (user_id, token_hash, expires_at, ip) VALUES (%s, %s, %s, %s::inet)",
        (user_id, hash_token(token), now + _seconds(state.settings.session_seconds), ip),
    )
    await conn.execute(
        "UPDATE app.users SET last_login_at = %s, updated_at = %s WHERE id = %s", (now, now, user_id)
    )
    return token


def _seconds(n: int):
    from datetime import timedelta

    return timedelta(seconds=n)


async def complete_sign_in(state, email: str, ip: str | None, via: str) -> SignInOutcome:
    """The allowlist gate, shared by every sign-in path once the email has been proven."""
    try:
        return await _gate(state, email, ip, via)
    except psycopg.errors.UniqueViolation:
        # two sign-ins for a brand-new address at the same moment: the second one retries and
        # finds the row the first one created
        return await _gate(state, email, ip, via)


async def _gate(state, email: str, ip: str | None, via: str) -> SignInOutcome:
    clock, db = state.clock, state.db
    async with db.transaction() as conn:
        cursor = await conn.execute(
            "SELECT id, role, status, suspended_reason FROM app.users WHERE email = %s FOR UPDATE", (email,)
        )
        user = await cursor.fetchone()
        entry = await _find_entry(conn, email)
        now = clock.now()

        if user is None and entry is None:
            await conn.execute("INSERT INTO app.users (email, status) VALUES (%s, 'pending')", (email,))
            await audit.record(conn, clock, "code_verified", target=email, ip=ip, detail={"via": via})
            await audit.record(conn, clock, "signin_pending", target=email, ip=ip)
            return SignInOutcome(status="pending")

        if user is None:  # allowlisted newcomer
            created = await conn.execute(
                "INSERT INTO app.users (email, role, status) VALUES (%s, %s, 'active') RETURNING id",
                (email, entry["default_role"]),
            )
            user_id, role = (await created.fetchone())["id"], entry["default_role"]
        elif user["status"] == "active":
            user_id, role = user["id"], user["role"]
        elif entry is not None and (
            user["status"] == "pending"
            or (user["status"] == "suspended" and user["suspended_reason"] == "allowlist_removed")
        ):
            # an entry now matches a waiting person, or one who was suspended only because their
            # entry had been removed: activate them with the entry's role (no separate approval)
            await conn.execute(
                "UPDATE app.users SET status = 'active', role = %s, suspended_reason = NULL, updated_at = %s "
                "WHERE id = %s",
                (entry["default_role"], now, user["id"]),
            )
            user_id, role = user["id"], entry["default_role"]
        elif user["status"] == "pending":
            await audit.record(conn, clock, "code_verified", actor_user_id=user["id"], actor_email=email,
                               target=email, ip=ip, detail={"via": via})
            await audit.record(conn, clock, "signin_pending", actor_user_id=user["id"], actor_email=email,
                               target=email, ip=ip)
            return SignInOutcome(status="pending")
        else:  # suspended by an admin, rejected, or suspended by removal with no entry any more
            await audit.record(conn, clock, "code_verified", actor_user_id=user["id"], actor_email=email,
                               target=email, ip=ip, detail={"via": via})
            await audit.record(conn, clock, "signin_refused", "failure", actor_user_id=user["id"],
                               actor_email=email, target=email, ip=ip, detail={"status": user["status"]})
            return SignInOutcome(status="refused")

        token = await create_session(conn, state, user_id, ip)
        await audit.record(conn, clock, "code_verified", actor_user_id=user_id, actor_email=email,
                           target=email, ip=ip, detail={"via": via})
        await audit.record(conn, clock, "session_created", actor_user_id=user_id, actor_email=email,
                           target=email, ip=ip)
    return SignInOutcome(status="signed_in", role=role, token=token)


async def revoke_session(state, session_id, user, ip: str | None) -> None:
    clock, db = state.clock, state.db
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE app.sessions SET revoked_at = %s, revoked_reason = 'signed_out', updated_at = %s WHERE id = %s",
            (clock.now(), clock.now(), session_id),
        )
        await audit.record(conn, clock, "signed_out", actor_user_id=user.id, actor_email=user.email,
                           target=user.email, ip=ip)
