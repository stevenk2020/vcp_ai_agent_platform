"""Security headers on every response (spec FR-016): strict CSP, no inline script, nothing third-party."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "font-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, production: bool = False) -> None:
        super().__init__(app)
        self.production = production

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers["Content-Security-Policy"] = CSP
        headers["X-Content-Type-Options"] = "nosniff"
        headers["Referrer-Policy"] = "no-referrer"
        headers["Cross-Origin-Opener-Policy"] = "same-origin"
        headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if self.production:
            headers["Strict-Transport-Security"] = "max-age=31536000"
        content_type = headers.get("content-type", "")
        if content_type.startswith("text/html") or request.url.path.startswith("/v1/"):
            headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/") or request.url.path.startswith("/admin/assets/"):
            headers["Cache-Control"] = "no-cache"
        return response
