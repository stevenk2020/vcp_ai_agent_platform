"""Operator commands. `create-first-admin` is the only way to create an admin without another admin."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app import audit
from app.clock import Clock, SystemClock
from app.config import Settings, load_settings
from app.db import Database
from app.security import InvalidEmail, normalize_email

EXIT_OK = 0
EXIT_BAD_INPUT = 1
EXIT_REFUSED = 2


async def create_first_admin(settings: Settings, clock: Clock, raw_email: str) -> int:
    """Create the first admin. Refuses if an admin already exists (any status except rejected).

    Takes the same advisory lock as the admin API so two runs at once create exactly one admin."""
    try:
        email = normalize_email(raw_email)
    except InvalidEmail:
        print("The email address is not valid.", file=sys.stderr)
        return EXIT_BAD_INPUT

    db = Database(settings)
    await db.open()
    refused = False
    try:
        async with db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("admins",))
            cursor = await conn.execute("SELECT count(*) AS n FROM app.users WHERE role = 'admin' AND status <> 'rejected'")
            if (await cursor.fetchone())["n"] > 0:
                refused = True
                await audit.record(conn, clock, "first_admin_refused", "failure", actor_email="cli", target=email)
            else:
                await conn.execute(
                    "INSERT INTO app.users (email, role, status) VALUES (%s, 'admin', 'active') "
                    "ON CONFLICT (email) DO UPDATE SET role = 'admin', status = 'active', "
                    "suspended_reason = NULL, updated_at = %s",
                    (email, clock.now()),
                )
                await audit.record(conn, clock, "first_admin_created", actor_email="cli", target=email)
    finally:
        await db.close()

    if refused:
        print("An admin already exists, so nothing was created. Add more admins through the admin API.",
              file=sys.stderr)
        return EXIT_REFUSED
    print(f"First admin created: {email}. Sign in with that address (Google, or an emailed code if enabled).")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    first = sub.add_parser("create-first-admin", help="create the first admin (works once)")
    first.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    settings = load_settings()
    return asyncio.run(create_first_admin(settings, SystemClock(), args.email))


if __name__ == "__main__":
    sys.exit(main())
