"""The real GoogleIdentityProvider's verification rules, with the network replaced by fakes."""

import httpx
import pytest

from app import google
from app.google import GoogleIdentityProvider, IdentityError, NotConfiguredProvider, pkce_challenge


def provider(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleIdentityProvider("client-id", "client-secret", http_client=client)


def token_ok(request):
    assert b"code_verifier=verifier123" in request.content and b"client_secret=client-secret" in request.content
    return httpx.Response(200, json={"id_token": "header.payload.signature", "access_token": "ignored"})


async def test_a_verified_email_is_returned_lowercased(monkeypatch):
    seen = {}

    def fake_verify(raw, request, audience):
        seen["audience"] = audience
        return {"email": "Gina@Example.COM", "email_verified": True}

    monkeypatch.setattr(google.id_token, "verify_oauth2_token", fake_verify)
    assert await provider(token_ok).verify_callback("code", "verifier123", "https://x/cb") == "gina@example.com"
    assert seen["audience"] == "client-id"  # our client id is what the token must be issued for


@pytest.mark.parametrize("claims", [
    {"email": "a@b.com", "email_verified": False},
    {"email": "a@b.com"},
    {"email_verified": True},
    {"email": "a@b.com", "email_verified": "true"},
])
async def test_unverified_or_missing_email_is_refused(monkeypatch, claims):
    monkeypatch.setattr(google.id_token, "verify_oauth2_token", lambda *a, **k: claims)
    with pytest.raises(IdentityError):
        await provider(token_ok).verify_callback("code", "verifier123", "https://x/cb")


async def test_an_invalid_token_is_refused(monkeypatch):
    def reject(*args, **kwargs):
        raise ValueError("bad signature")

    monkeypatch.setattr(google.id_token, "verify_oauth2_token", reject)
    with pytest.raises(IdentityError):
        await provider(token_ok).verify_callback("code", "verifier123", "https://x/cb")


async def test_provider_errors_and_missing_id_token_are_refused():
    with pytest.raises(IdentityError):
        await provider(lambda r: httpx.Response(400, json={"error": "invalid_grant"})).verify_callback("c", "v", "u")
    with pytest.raises(IdentityError):
        await provider(lambda r: httpx.Response(200, json={})).verify_callback("c", "v", "u")

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(IdentityError):
        await provider(boom).verify_callback("c", "v", "u")


def test_authorization_url_uses_pkce_and_asks_only_for_openid_email():
    url = GoogleIdentityProvider("client-id", "s").authorization_url("st", pkce_challenge("v"), "https://x/cb")
    assert "code_challenge_method=S256" in url and "scope=openid+email" in url and "client_id=client-id" in url
    assert "client_secret" not in url


async def test_not_configured_provider_refuses_everything():
    with pytest.raises(IdentityError):
        NotConfiguredProvider().authorization_url("s", "c", "u")
    with pytest.raises(IdentityError):
        await NotConfiguredProvider().verify_callback("c", "v", "u")
