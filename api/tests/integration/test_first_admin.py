"""US6: the one-time command that creates the first admin, and nothing else that could."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import app as app_package
from app.cli import EXIT_BAD_INPUT, EXIT_OK, EXIT_REFUSED, create_first_admin
from tests.integration.helpers import add_user, read_code

ADMIN = "first@example.com"


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args], capture_output=True, text=True, env={**os.environ, **(env or {})}
    )


def admins(sql):
    return sql.execute("SELECT email FROM app.users WHERE role = 'admin' AND status = 'active'").fetchall()


async def test_an_empty_system_gets_one_admin_who_can_sign_in(settings, clock, sql, client, mailer):
    assert await create_first_admin(settings, clock, "  First@Example.COM ") == EXIT_OK
    assert [r["email"] for r in admins(sql)] == [ADMIN]
    assert "first_admin_created" in [r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()]

    assert (await client.post("/v1/auth/code/request", json={"email": ADMIN})).status_code == 202
    signed_in = await client.post("/v1/auth/code/verify", json={"email": ADMIN, "code": read_code(mailer, ADMIN)})
    assert signed_in.json() == {"status": "signed_in", "role": "admin"}
    assert (await client.get("/v1/admin/users")).status_code == 200


async def test_a_second_run_creates_nothing_and_is_audited(settings, clock, sql):
    assert await create_first_admin(settings, clock, ADMIN) == EXIT_OK
    assert await create_first_admin(settings, clock, "second@example.com") == EXIT_REFUSED
    assert [r["email"] for r in admins(sql)] == [ADMIN]
    assert sql.execute("SELECT count(*) AS n FROM app.users WHERE email = 'second@example.com'").fetchone()["n"] == 0
    assert "first_admin_refused" in [r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()]


async def test_two_simultaneous_runs_create_exactly_one_admin(settings, clock, sql):
    codes = await asyncio.gather(
        create_first_admin(settings, clock, "one@example.com"), create_first_admin(settings, clock, "two@example.com")
    )
    assert sorted(codes) == [EXIT_OK, EXIT_REFUSED]
    assert len(admins(sql)) == 1


async def test_a_malformed_email_exits_with_code_1_and_creates_nothing(settings, clock, sql):
    assert await create_first_admin(settings, clock, "nope") == EXIT_BAD_INPUT
    assert sql.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"] == 0


async def test_with_only_a_rejected_admin_the_command_works_again(settings, clock, sql):
    add_user(sql, "old@example.com", "rejected", role="admin")
    assert await create_first_admin(settings, clock, ADMIN) == EXIT_OK
    assert [r["email"] for r in admins(sql)] == [ADMIN]


def test_the_command_line_uses_the_documented_exit_codes(sql):
    assert run_cli("create-first-admin", "--email", "bad").returncode == EXIT_BAD_INPUT
    first = run_cli("create-first-admin", "--email", ADMIN)
    assert first.returncode == EXIT_OK and ADMIN in first.stdout
    second = run_cli("create-first-admin", "--email", "other@example.com")
    assert second.returncode == EXIT_REFUSED and "already exists" in second.stderr
    assert [r["email"] for r in admins(sql)] == [ADMIN]


async def test_no_environment_setting_or_file_can_create_an_admin(settings, clock, sql, client, mailer):
    # the code reads only these environment variables; none of them is about admins
    read = set()
    for path in Path(app_package.__file__).parent.rglob("*.py"):
        read |= set(re.findall(r'env(?:ironment)?\.get\(\s*"([A-Z_]+)"', path.read_text(encoding="utf-8")))
        read |= set(re.findall(r'_int\(env, "([A-Z_]+)"', path.read_text(encoding="utf-8")))
    assert read and not [name for name in read if "ADMIN" in name or "BOOTSTRAP" in name or "SEED" in name]

    # odd settings do not create anyone, and a normal person with that address is not an admin
    os.environ.update({"FIRST_ADMIN_EMAIL": ADMIN, "ADMIN_EMAIL": ADMIN, "BOOTSTRAP_ADMIN": "1"})
    try:
        assert sql.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"] == 0
        await client.post("/v1/auth/code/request", json={"email": ADMIN})
        response = await client.post("/v1/auth/code/verify", json={"email": ADMIN, "code": read_code(mailer, ADMIN)})
        assert response.json() == {"status": "pending"}
        assert admins(sql) == []
    finally:
        for name in ("FIRST_ADMIN_EMAIL", "ADMIN_EMAIL", "BOOTSTRAP_ADMIN"):
            os.environ.pop(name, None)
