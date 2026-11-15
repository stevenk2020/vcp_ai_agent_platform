"""Test doubles."""

from __future__ import annotations

from urllib.parse import urlencode

from app.google import IdentityError


class FakeIdentityProvider:
    """Stands in for Google: returns the configured verified email, or raises like an unverified one."""

    def __init__(self) -> None:
        self.email: str | None = None
        self.error: str | None = None
        self.seen: list[dict] = []

    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        return "https://accounts.fake.example/auth?" + urlencode(
            {"state": state, "code_challenge": code_challenge, "redirect_uri": redirect_uri}
        )

    async def verify_callback(self, code: str, code_verifier: str, redirect_uri: str) -> str:
        self.seen.append({"verifier_len": len(code_verifier), "redirect_uri": redirect_uri})
        if self.error:
            raise IdentityError(self.error)
        if not self.email:
            raise IdentityError("no identity configured in the fake")
        return self.email
