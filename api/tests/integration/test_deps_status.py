"""current_user accepts only ACTIVE people; current_person also accepts PENDING ones; nobody else (T007)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from starlette.requests import Request

from app.auth.deps import COOKIE_NAME, current_person, current_user
from app.errors import ApiError
from app.security import hash_token, new_token
from tests.integration.helpers import add_user


def request_with(app, token: str | None) -> Request:
    headers = [(b"cookie", f"{COOKIE_NAME}={token}".encode())] if token else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "app": app,
                    "query_string": b"", "server": ("t", 80), "scheme": "https"})


def open_session(sql, clock, user_id, revoked=False, expired=False) -> str:
    token = new_token()
    now = clock.now()
    expires = now - timedelta(minutes=1) if expired else now + timedelta(days=1)
    sql.execute(
        "INSERT INTO app.sessions (user_id, token_hash, expires_at, revoked_at, revoked_reason) VALUES (%s, %s, %s, %s, %s)",
        (user_id, hash_token(token), expires, now if revoked else None, "signed_out" if revoked else None),
    )
    return token


async def accepted(dependency, app, token) -> bool:
    try:
        await dependency(request_with(app, token))
        return True
    except ApiError as exc:
        assert exc.status == 401
        return False


@pytest.mark.parametrize("status,reason,user_ok,person_ok", [
    ("active", None, True, True),
    ("pending", None, False, True),
    ("suspended", "admin", False, False),
    ("rejected", None, False, False),
])
async def test_which_statuses_each_dependency_accepts(app, sql, clock, status, reason, user_ok, person_ok):
    user_id = add_user(sql, f"{status}@example.com", status, reason=reason)
    token = open_session(sql, clock, user_id)
    assert await accepted(current_user, app, token) is user_ok
    assert await accepted(current_person, app, token) is person_ok


@pytest.mark.parametrize("flags", [{"revoked": True}, {"expired": True}])
async def test_revoked_and_expired_sessions_are_refused_by_both(app, sql, clock, flags):
    for status in ("active", "pending"):
        user_id = add_user(sql, f"{status}-{next(iter(flags))}@example.com", status)
        token = open_session(sql, clock, user_id, **flags)
        assert await accepted(current_user, app, token) is False
        assert await accepted(current_person, app, token) is False


async def test_no_cookie_and_unknown_token_are_refused_by_both(app):
    for token in (None, "not-a-real-token"):
        assert await accepted(current_user, app, token) is False
        assert await accepted(current_person, app, token) is False
