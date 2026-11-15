"""Small helpers shared by integration tests."""

from __future__ import annotations


def read_code(mailer, email: str) -> str:
    """The most recent captured code for an address."""
    messages = mailer.messages(email)
    assert messages, f"no mail captured for {email}"
    return messages[-1]["code"]


def add_allowlist(sql, value: str, kind: str = "email", role: str = "user") -> str:
    row = sql.execute(
        "INSERT INTO app.allowlist (kind, value, default_role) VALUES (%s, %s, %s) RETURNING id",
        (kind, value.lower(), role),
    ).fetchone()
    return str(row["id"])
