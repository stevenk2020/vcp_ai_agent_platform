import dataclasses

import httpx

from app.main import create_app
from tests.conftest import ORIGIN


async def test_health_reports_ok_and_the_mail_backend(client):
    response = await client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mail_backend": "capture"}


async def test_health_is_503_when_redis_is_unreachable(settings, clock, mailer):
    broken = dataclasses.replace(settings, redis_url="redis://127.0.0.1:1/0")
    application = create_app(settings=broken, clock=clock, mailer=mailer)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as c:
            response = await c.get("/v1/health")
    assert response.status_code == 503 and response.json()["status"] == "unavailable"


async def test_unsafe_requests_need_the_public_origin(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as bare:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = await bare.request(method, "/v1/health")
            assert response.status_code == 403 and response.json()["error"] == "origin_not_allowed"
        wrong = await bare.post("/v1/health", headers={"Origin": "https://evil.example"})
        assert wrong.status_code == 403
        right = await bare.post("/v1/health", headers={"Origin": ORIGIN})
        assert right.status_code == 405 and right.json()["error"] == "method_not_allowed"
        assert (await bare.get("/v1/health")).status_code == 200  # safe methods need no Origin


async def test_errors_share_one_shape(client):
    response = await client.get("/v1/nope")
    assert response.status_code == 404
    assert set(response.json()) == {"error", "message"} and response.json()["error"] == "not_found"
