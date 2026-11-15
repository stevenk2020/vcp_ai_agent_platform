"""Tokens, code hashing and email normalisation. Nothing here talks to the network or database."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid

from email_validator import EmailNotValidError, validate_email


class InvalidEmail(ValueError):
    pass


def normalize_email(raw: str) -> str:
    """Trim, lowercase and syntax-check an address (no DNS lookup); at most 254 characters."""
    candidate = (raw or "").strip()
    if not candidate or len(candidate) > 254:
        raise InvalidEmail("invalid email address")
    try:
        parsed = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvalidEmail("invalid email address") from exc
    return parsed.normalized.lower()


def new_token() -> str:
    """32 random bytes as base64url (the value sent in the cookie)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def hash_token(token: str) -> bytes:
    """SHA-256 of the token; the only form that is stored."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def new_code() -> str:
    """A uniformly random 6-digit code, zero padded."""
    return f"{secrets.randbelow(10**6):06d}"


def code_hmac(pepper: bytes, email: str, code_id: uuid.UUID | str, code: str) -> bytes:
    """Keyed hash of a code. A plain hash of 6 digits would be reversible, so the pepper is required."""
    message = f"{email}:{code_id}:{code}".encode("utf-8")
    return hmac.new(pepper, message, hashlib.sha256).digest()


def constant_time_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)
