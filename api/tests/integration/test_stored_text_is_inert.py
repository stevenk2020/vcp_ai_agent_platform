"""US6: hostile text typed into the access request is stored as typed, returned as JSON data, and the pages can only
render it as text (spec 003 FR-015, SC-005). The browser half is quickstart steps 3 and 4."""

from __future__ import annotations

import re
from pathlib import Path

from app import pages

NAME = "<img src=x onerror=alert(1)>"
REASON = "</td><script>alert(1)</script>&lt;b&gt;"
EVIL_EMAIL = "<svg onload=alert(1)>@example.org"


async def test_hostile_text_round_trips_as_json_data_not_markup(login_pending, login, sql):
    waiting = await login_pending("hostile@example.org")
    assert (await waiting.post("/v1/access/request", json={"name": NAME, "reason": REASON})).status_code == 201
    admin = await login("admin@example.com", role="admin")
    response = await admin.get("/v1/admin/users?status=pending")
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    item = response.json()["items"][0]
    assert (item["name"], item["request_reason"]) == (NAME, REASON)  # stored exactly as typed
    own = (await waiting.get("/v1/access")).json()
    assert (own["name"], own["reason"]) == (NAME, REASON)


async def test_no_page_file_contains_the_hostile_text_or_builds_markup_from_data(login):
    web = Path(pages.WEB)
    for path in web.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "onerror=alert" not in text and NAME not in text and REASON not in text, path.name


def test_the_fields_that_carry_person_text_are_only_written_through_text_nodes():
    """Every script that handles name, reason, email or audit details must create elements with el()/textContent."""
    web = Path(pages.WEB)
    handlers = [web / "static" / "app.js", web / "admin-assets" / "admin.js", web / "static" / "common.js"]
    for path in handlers:
        text = path.read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "createContextualFragment"):
            assert banned not in text, (path.name, banned)
    admin = (web / "admin-assets" / "admin.js").read_text(encoding="utf-8")
    for field in ("person.email", "person.name", "person.request_reason", "entry.value", "event.target", "event.actor_email"):
        assert field in admin, field
        # each use sits inside el(...)/cell(...)/textContent/template text, never concatenated into markup
        for line in [l for l in admin.splitlines() if field in l]:
            assert not re.search(r"['\"]<[a-zA-Z]", line), line
    # el() turns string children into text nodes and refuses unknown attributes
    common = (web / "static" / "common.js").read_text(encoding="utf-8")
    assert "document.createTextNode" in common and "ALLOWED_ATTRIBUTES" in common
    app = (web / "static" / "app.js").read_text(encoding="utf-8")
    assert ".textContent =" in app and "innerHTML" not in app
