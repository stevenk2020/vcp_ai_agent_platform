"""Pages: delivery, the 404 page, path safety and headers (feature 003)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.security_headers import CSP


def assert_hardened(response):
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_pages_are_in_the_image():
    web = Path(__import__("app.pages", fromlist=["WEB"]).WEB)
    for name in ("404.html", "403.html"):
        assert (web / name).is_file(), name


async def test_an_unknown_path_gets_the_html_404_with_the_full_header_set(client):
    response = await client.get("/nothing-here")
    assert response.status_code == 404 and response.headers["content-type"].startswith("text/html")
    assert "Page not found" in response.text
    assert_hardened(response)
    assert response.headers["cache-control"] == "no-store"


async def test_an_unknown_api_path_keeps_the_json_error_shape(client):
    response = await client.get("/v1/nothing")
    assert response.status_code == 404 and response.json()["error"] == "not_found"
    assert_hardened(response)


@pytest.mark.parametrize("path", [
    "/static/../../etc/passwd", "/static/%2e%2e/app/config.py", "/static/.env", "/static/..%2fconfig.py",
    "/static/a/b.js", "/static//etc/passwd", "/static/%00.js",
])
async def test_static_paths_cannot_escape_or_reach_hidden_files(client, path):
    response = await client.get(path)
    assert response.status_code == 404
    assert "root:" not in response.text and "pepper" not in response.text.lower()


async def test_a_state_changing_request_without_origin_is_still_refused_with_headers(client):
    response = await client.post("/", headers={"Origin": ""})
    assert response.status_code == 403 and response.json()["error"] == "origin_not_allowed"
    assert_hardened(response)


async def test_the_start_page_links_to_google_and_loads_only_site_files(client):
    response = await client.get("/")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert_hardened(response)
    assert 'href="/v1/auth/google/start"' in response.text
    import re

    assert not re.search(r"<script(?![^>]*\bsrc=)", response.text), "inline script"
    for url in re.findall(r'(?:src|href)="([^"]+)"', response.text):
        assert url.startswith("/"), url  # no external origin


@pytest.mark.parametrize("name,kind", [
    ("app.js", "javascript"), ("common.js", "javascript"), ("style.css", "text/css"), ("favicon.svg", "image/svg+xml"),
])
async def test_static_files_are_served_with_the_right_type_and_headers(client, name, kind):
    response = await client.get(f"/static/{name}")
    assert response.status_code == 200 and kind in response.headers["content-type"]
    assert_hardened(response)
    assert response.headers["cache-control"] == "no-cache"


async def test_every_page_and_api_response_has_the_headers_and_only_sign_in_routes_set_cookies(
        app, client, login, login_pending, new_client):
    admin = await login("hdr-admin@example.com", role="admin")
    waiting = await login_pending("hdr-waiting@example.org")
    signed_out = new_client()
    seen = []
    for who, c in (("anonymous", signed_out), ("waiting", waiting), ("admin", admin)):
        for path in ("/", "/static/app.js", "/static/style.css", "/static/common.js", "/static/favicon.svg", "/nope",
                     "/admin", "/admin/assets/admin.js", "/v1/health", "/v1/me", "/v1/access", "/v1/admin/users"):
            response = await c.get(path, follow_redirects=False)
            seen.append((who, path, response.status_code))
            assert_hardened(response)
            assert "unsafe-inline" not in response.headers["content-security-policy"], (who, path)
            if response.headers.get("content-type", "").startswith("text/html"):
                assert response.headers["cache-control"] == "no-store", (who, path)
            assert "set-cookie" not in response.headers, (who, path)  # plain reads never set cookies
    assert any(status == 200 for _, path, status in seen if path == "/admin")


async def test_sign_out_clears_the_cookie_and_keeps_the_headers(login):
    c = await login("hdr@example.com")
    response = await c.post("/v1/auth/logout")
    assert response.status_code == 204 and "__host-session=" in response.headers["set-cookie"].lower()
    assert_hardened(response)
