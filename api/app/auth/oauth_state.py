"""The short-lived signed cookie that carries `state` and the PKCE verifier through the Google redirect."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.clock import Clock

COOKIE_NAME = "__Host-oauth"
MAX_AGE = 600


def _sign(pepper: bytes, state: str, verifier: str, issued: int) -> str:
    return hmac.new(pepper, f"oauth:{state}.{verifier}.{issued}".encode(), hashlib.sha256).hexdigest()


def new_flow(pepper: bytes, clock: Clock) -> tuple[str, str, str]:
    """Returns (state, verifier, cookie value)."""
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    issued = int(clock.now().timestamp())
    return state, verifier, f"{state}.{verifier}.{issued}.{_sign(pepper, state, verifier, issued)}"


def read_flow(pepper: bytes, clock: Clock, cookie: str | None) -> tuple[str, str] | None:
    """(state, verifier) if the cookie is genuine and not older than ten minutes, else None."""
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 4:
        return None
    state, verifier, issued_raw, signature = parts
    try:
        issued = int(issued_raw)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(pepper, state, verifier, issued)):
        return None
    age = clock.now().timestamp() - issued
    if age < 0 or age > MAX_AGE:
        return None
    return state, verifier
