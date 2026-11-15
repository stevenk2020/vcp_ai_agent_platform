"""Application factory. Routers for sign-in and admin are registered as they are added."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.audit import configure_logging
from app.clock import Clock, SystemClock
from app.config import Settings, load_settings
from app.db import Database
from app.errors import ApiError
from app.mailer import Mailer, build_mailer
from app.rate_limit import RateLimiter

log = logging.getLogger("app")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    mailer: Mailer | None = None,
    identity=None,
    database: Database | None = None,
    redis=None,
) -> FastAPI:
    settings = settings or load_settings()
    clock = clock or SystemClock()
    mailer = mailer or build_mailer(settings, clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        db = database or Database(settings)
        await db.open()
        client = redis or aioredis.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )
        app.state.db = db
        app.state.redis = client
        app.state.limiter = RateLimiter(client, clock)
        log.info("api started", extra={"app_env": settings.app_env, "mail_backend": settings.mail_backend})
        try:
            yield
        finally:
            await db.close()
            await client.aclose()

    app = FastAPI(title="Agent Platform API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.mailer = mailer
    app.state.identity = identity

    # ---- errors: one shape, {"error", "message"} --------------------------------------------------

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": exc.error, "message": exc.message}, status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid_request", "message": "The request is not valid."}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        names = {404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(
            {"error": names.get(exc.status_code, "http_error"), "message": str(exc.detail)},
            status_code=exc.status_code,
        )

    # ---- CSRF: every unsafe request must come from the public origin ----------------------------

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if request.method in UNSAFE_METHODS and request.headers.get("origin") != settings.public_origin:
            return JSONResponse(
                {"error": "origin_not_allowed", "message": "This request did not come from an allowed origin."},
                status_code=403,
            )
        return await call_next(request)

    # ---- health -------------------------------------------------------------------------------

    @app.get("/v1/health")
    async def health(request: Request):
        db_ok = redis_ok = False
        try:
            await request.app.state.db.ping()
            db_ok = True
        except Exception:  # noqa: BLE001 - health must report, not raise
            log.warning("health: database check failed")
        try:
            await request.app.state.redis.ping()
            redis_ok = True
        except Exception:  # noqa: BLE001
            log.warning("health: redis check failed")
        body = {"status": "ok" if db_ok and redis_ok else "unavailable", "mail_backend": settings.mail_backend}
        return JSONResponse(body, status_code=200 if db_ok and redis_ok else 503)

    from app.admin.routes import router as admin_router
    from app.auth.routes import router as auth_router

    app.include_router(auth_router)
    app.include_router(admin_router)
    return app
