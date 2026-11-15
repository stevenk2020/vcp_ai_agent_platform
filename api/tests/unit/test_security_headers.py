import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from app.security_headers import CSP, SecurityHeadersMiddleware


def build(production: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/page", response_class=HTMLResponse)
    def page():
        return "<p>hi</p>"

    @app.get("/v1/thing")
    def thing():
        return {"ok": True}

    @app.get("/v1/boom")
    def boom():
        return JSONResponse({"error": "x"}, status_code=403)

    @app.get("/static/a.js")
    def asset():
        return JSONResponse({})

    app.add_middleware(SecurityHeadersMiddleware, production=production)
    return app


async def get(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.parametrize("path,status", [("/page", 200), ("/v1/thing", 200), ("/v1/boom", 403), ("/missing", 404)])
async def test_every_response_carries_the_full_header_set(path, status):
    response = await get(build(False), path)
    assert response.status_code == status
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in response.headers["permissions-policy"]


def test_the_csp_has_no_unsafe_sources_and_no_remote_origin():
    assert "unsafe-inline" not in CSP and "unsafe-eval" not in CSP
    assert "http:" not in CSP and "https:" not in CSP and "*" not in CSP
    assert "script-src 'self'" in CSP and "frame-ancestors 'none'" in CSP


async def test_hsts_only_in_production():
    assert "strict-transport-security" not in (await get(build(False), "/page")).headers
    assert (await get(build(True), "/page")).headers["strict-transport-security"].startswith("max-age=")


async def test_cache_control_is_no_store_for_html_and_api_and_no_cache_for_assets():
    app = build(False)
    assert (await get(app, "/page")).headers["cache-control"] == "no-store"
    assert (await get(app, "/v1/thing")).headers["cache-control"] == "no-store"
    assert (await get(app, "/static/a.js")).headers["cache-control"] == "no-cache"
