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
