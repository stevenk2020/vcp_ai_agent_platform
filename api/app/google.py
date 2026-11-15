"""Google sign-in: OpenID Connect authorization-code flow with PKCE.

Only the verified email address is used. Tokens are verified and then discarded: never stored, never
logged.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Protocol
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import Settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class IdentityError(Exception):
    """The identity could not be established (bad code, unverified email, provider error)."""


class IdentityProvider(Protocol):
    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def verify_callback(self, code: str, code_verifier: str, redirect_uri: str) -> str: ...


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class NotConfiguredProvider:
    configured = False

    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        raise IdentityError("google sign-in is not configured")

    async def verify_callback(self, code: str, code_verifier: str, redirect_uri: str) -> str:
        raise IdentityError("google sign-in is not configured")


class GoogleIdentityProvider:
    configured = True

    def __init__(self, client_id: str, client_secret: str, http_client: httpx.AsyncClient | None = None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._http = http_client

    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{AUTH_URL}?{query}"

    async def verify_callback(self, code: str, code_verifier: str, redirect_uri: str) -> str:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        try:
            if self._http is not None:
                response = await self._http.post(TOKEN_URL, data=data)
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(TOKEN_URL, data=data)
        except httpx.HTTPError as exc:
            raise IdentityError("token exchange failed") from exc
        if response.status_code != 200:
            raise IdentityError("token exchange was refused")
        raw_id_token = response.json().get("id_token")
        if not raw_id_token:
            raise IdentityError("no id token in the response")
        try:
            # verifies signature (Google's keys), issuer, audience and expiry; blocking, so in a thread
            claims = await asyncio.to_thread(
                id_token.verify_oauth2_token, raw_id_token, google_requests.Request(), self.client_id
            )
        except ValueError as exc:
            raise IdentityError("id token is not valid") from exc
        email = claims.get("email")
        if not email or claims.get("email_verified") is not True:
            raise IdentityError("email is missing or not verified by Google")
        return str(email).strip().lower()


def build_identity(settings: Settings) -> IdentityProvider:
    if settings.google_configured:
        return GoogleIdentityProvider(settings.google_client_id, settings.google_client_secret)
    return NotConfiguredProvider()
