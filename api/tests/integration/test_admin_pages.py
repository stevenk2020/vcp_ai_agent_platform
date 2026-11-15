"""US3: the admin page and its script are only ever sent to a signed-in admin (spec 003 FR-014)."""

from __future__ import annotations

import pytest

from app.security_headers import CSP

PEOPLE = ["secret.person@example.org"]


def hardened(response):
    assert response.headers["content-security-policy"] == CSP and response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/admin", "/admin/assets/admin.js"])
async def test_signed_out_visitors_are_sent_to_the_start_page_with_no_data(new_client, path):
    response = await new_client().get(path, follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/"
    assert response.content == b"" or b"admin" not in response.content.lower()
    hardened(response)


@pytest.mark.parametrize("path", ["/admin", "/admin/assets/admin.js"])
async def test_waiting_people_and_non_admins_get_the_refusal_page(login_pending, login, sql, path):
    clients = {
        "waiting": await login_pending(PEOPLE[0]),
        "user": await login("user@example.com", role="user"),
        "builder": await login("builder@example.com", role="builder"),
    }
    for who, client in clients.items():
        response = await client.get(path, follow_redirects=False)
        assert response.status_code == 403, who
        assert response.headers["content-type"].startswith("text/html")
        assert "do not have access" in response.text
        assert "fetch(" not in response.text and "/v1/admin" not in response.text, who  # no admin code or data
        for email in [r["email"] for r in sql.execute("SELECT email FROM app.users").fetchall()]:
            assert email not in response.text, who
        hardened(response)


async def test_an_admin_gets_the_page_and_the_script(login):
    admin = await login("admin@example.com", role="admin")
    page = await admin.get("/admin")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store" and 'src="/admin/assets/admin.js"' in page.text
    hardened(page)
    script = await admin.get("/admin/assets/admin.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert script.headers["cache-control"] == "no-cache"
    hardened(script)


@pytest.mark.parametrize("name", ["other.js", "../app/config.py", "..%2fconfig.py", ".env", "admin.js/../x", "admin.JS"])
async def test_only_the_listed_admin_asset_exists_even_for_admins(login, name):
    admin = await login("admin@example.com", role="admin")
    response = await admin.get(f"/admin/assets/{name}", follow_redirects=False)
    assert response.status_code == 404


async def test_the_page_and_script_contain_no_person_data_or_secret(login, sql, settings):
    admin = await login("admin@example.com", role="admin")
    text = (await admin.get("/admin")).text + (await admin.get("/admin/assets/admin.js")).text
    for email in [r["email"] for r in sql.execute("SELECT email FROM app.users").fetchall()]:
        assert email not in text
    assert settings.google_client_secret not in text or not settings.google_client_secret
    assert "pepper" not in text.lower()


async def test_a_session_revoked_after_the_page_loaded_fails_on_the_next_api_call(login, sql):
    admin = await login("admin@example.com", role="admin")
    assert (await admin.get("/admin")).status_code == 200
    sql.execute("UPDATE app.sessions SET revoked_at = now(), revoked_reason = 'signed_out'")
    assert (await admin.get("/v1/admin/users")).status_code == 401
    assert (await admin.get("/admin", follow_redirects=False)).status_code == 302
