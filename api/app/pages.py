"""Static pages: the sign-in/landing page, public assets, the admin area (gated) and the 404 page.

Every file lives in app/web/ and is public except admin.html and admin-assets/, which are only sent to an
admin (see the admin routes below). Pages hold no data and no secret: all data comes from /v1/* calls, which
enforce roles themselves."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

WEB = Path(__file__).resolve().parent / "web"
STATIC = WEB / "static"
# one path segment, no leading dot, only ordinary characters: no traversal, no hidden files
SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

router = APIRouter()


def html(name: str, status: int = 200) -> FileResponse:
    return FileResponse(WEB / name, status_code=status, media_type="text/html; charset=utf-8")


def safe_file(directory: Path, name: str) -> Path | None:
    """The file `name` inside `directory`, or None if the name is not a plain file name or the file is missing."""
    if not SAFE_NAME.match(name) or ".." in name:
        return None
    path = (directory / name).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        return None
    return path


def not_found_page() -> Response:
    return html("404.html", 404)


@router.get("/", include_in_schema=False)
async def index():
    return html("index.html")


@router.get("/static/{name:path}", include_in_schema=False)
async def static_file(name: str):
    path = safe_file(STATIC, name)
    return FileResponse(path) if path else not_found_page()
