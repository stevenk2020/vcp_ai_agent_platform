"""Feature 002: the auth tables, their constraints, and what the API role may do with them.

These tests run against the live stack's database, so they only touch rows they create themselves
(unique `schema-test-*` values) and delete them afterwards; they never truncate anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from psycopg import errors

from conftest import connect

DENIED = (errors.InsufficientPrivilege,)


def mail() -> str:
    return f"schema-test-{uuid.uuid4().hex[:10]}@example.test"


@pytest.fixture(autouse=True)
def cleanup(app_owner):
    yield
    app_owner.execute("DELETE FROM app.users WHERE email LIKE 'schema-test-%@example.test'")
    app_owner.execute("DELETE FROM app.allowlist WHERE value LIKE 'schema-test-%'")
    app_owner.execute("DELETE FROM app.email_codes WHERE email LIKE 'schema-test-%@example.test'")
    app_owner.execute("DELETE FROM app.audit_events WHERE action = 'schema_test'")


def _in_one_hour() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def test_api_rw_can_read_and_write_the_four_working_tables(api_rw):
    email = mail()
    user_id = api_rw.execute("INSERT INTO app.users (email) VALUES (%s) RETURNING id", (email,)).fetchone()[0]
    api_rw.execute("UPDATE app.users SET status = 'active', role = 'builder' WHERE id = %s", (user_id,))
    assert api_rw.execute("SELECT status, role FROM app.users WHERE id = %s", (user_id,)).fetchone() == ("active", "builder")

    api_rw.execute(
        "INSERT INTO app.allowlist (kind, value, default_role) VALUES ('email', %s, 'user')", (email,)
    )
    api_rw.execute(
        "INSERT INTO app.email_codes (email, code_hmac, expires_at) VALUES (%s, %s, %s)",
        (email, b"\x01\x02", _in_one_hour()),
    )
    api_rw.execute(
        "INSERT INTO app.sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user_id, uuid.uuid4().bytes, _in_one_hour()),
    )
    assert api_rw.execute("SELECT count(*) FROM app.sessions WHERE user_id = %s", (user_id,)).fetchone() == (1,)
    api_rw.execute("DELETE FROM app.sessions WHERE user_id = %s", (user_id,))
    api_rw.execute("DELETE FROM app.email_codes WHERE email = %s", (email,))
    api_rw.execute("DELETE FROM app.allowlist WHERE value = %s", (email,))
    api_rw.execute("DELETE FROM app.users WHERE id = %s", (user_id,))


def test_audit_events_are_append_only_for_the_api_role(api_rw):
    api_rw.execute("INSERT INTO app.audit_events (at, action, outcome) VALUES (now(), 'schema_test', 'success')")
    assert api_rw.execute("SELECT count(*) FROM app.audit_events WHERE action = 'schema_test'").fetchone()[0] >= 1
    for statement in (
        "UPDATE app.audit_events SET outcome = 'failure' WHERE action = 'schema_test'",
        "DELETE FROM app.audit_events WHERE action = 'schema_test'",
        "TRUNCATE app.audit_events",
    ):
        with pytest.raises(DENIED):
            api_rw.execute(statement)


@pytest.mark.parametrize("schema", ["serving", "kb"])
def test_api_rw_cannot_reach_serving_or_kb(api_rw, schema):
    with pytest.raises(DENIED):
        api_rw.execute(f"SELECT * FROM {schema}.anything")
    with pytest.raises(DENIED):
        api_rw.execute(f"CREATE TABLE {schema}.should_fail (id int)")


def test_other_roles_cannot_read_the_auth_tables():
    for role in ("agent_ro", "sync_writer"):
        with connect(role) as conn:
            for table in ("users", "allowlist", "email_codes", "sessions", "audit_events"):
                with pytest.raises(DENIED):
                    conn.execute(f"SELECT * FROM app.{table}")


BAD_ROWS = {
    "uppercase email on users": ("INSERT INTO app.users (email) VALUES ('Upper@Example.Test')", ()),
    "unknown role on users": ("INSERT INTO app.users (email, role) VALUES (%s, 'root')", ("mail",)),
    "unknown status on users": ("INSERT INTO app.users (email, status) VALUES (%s, 'banned')", ("mail",)),
    "suspended user without a reason": ("INSERT INTO app.users (email, status) VALUES (%s, 'suspended')", ("mail",)),
    "reason on a non-suspended user": (
        "INSERT INTO app.users (email, status, suspended_reason) VALUES (%s, 'active', 'admin')", ("mail",)),
    "unknown suspended_reason": (
        "INSERT INTO app.users (email, status, suspended_reason) VALUES (%s, 'suspended', 'whim')", ("mail",)),
    "allowlist email without @": (
        "INSERT INTO app.allowlist (kind, value, default_role) VALUES ('email', 'schema-test-nodomain', 'user')", ()),
    "allowlist domain with @": (
        "INSERT INTO app.allowlist (kind, value, default_role) VALUES ('domain', 'schema-test-a@b.test', 'user')", ()),
    "allowlist uppercase value": (
        "INSERT INTO app.allowlist (kind, value, default_role) VALUES ('domain', 'Schema-Test.Example', 'user')", ()),
    "allowlist unknown kind": (
        "INSERT INTO app.allowlist (kind, value, default_role) VALUES ('wildcard', 'schema-test-x', 'user')", ()),
    "allowlist unknown role": (
        "INSERT INTO app.allowlist (kind, value, default_role) VALUES ('domain', 'schema-test-y.example', 'god')", ()),
    "email code attempts above 5": (
        "INSERT INTO app.email_codes (email, code_hmac, expires_at, attempts) "
        "VALUES (%s, '\\x01', now(), 6)", ("mail",)),
    "email code uppercase email": (
        "INSERT INTO app.email_codes (email, code_hmac, expires_at) VALUES ('UP@Example.Test', '\\x01', now())", ()),
    "audit unknown outcome": (
        "INSERT INTO app.audit_events (at, action, outcome) VALUES (now(), 'schema_test', 'maybe')", ()),
}


@pytest.mark.parametrize("name", sorted(BAD_ROWS))
def test_check_constraints_reject_bad_rows(api_rw, name):
    statement, params = BAD_ROWS[name]
    params = tuple(mail() if p == "mail" else p for p in params)
    with pytest.raises(errors.CheckViolation):
        api_rw.execute(statement, params)


def test_session_revoked_reason_is_constrained(api_rw):
    user_id = api_rw.execute("INSERT INTO app.users (email) VALUES (%s) RETURNING id", (mail(),)).fetchone()[0]
    with pytest.raises(errors.CheckViolation):
        api_rw.execute(
            "INSERT INTO app.sessions (user_id, token_hash, expires_at, revoked_reason) VALUES (%s, %s, %s, 'because')",
            (user_id, uuid.uuid4().bytes, _in_one_hour()),
        )
    for reason in ("signed_out", "allowlist_removed", "rejected", "suspended"):
        api_rw.execute(
            "INSERT INTO app.sessions (user_id, token_hash, expires_at, revoked_reason) VALUES (%s, %s, %s, %s)",
            (user_id, uuid.uuid4().bytes, _in_one_hour(), reason),
        )


def test_email_and_domain_entries_are_unique_per_kind(api_rw):
    value = f"schema-test-{uuid.uuid4().hex[:8]}.example"
    api_rw.execute("INSERT INTO app.allowlist (kind, value, default_role) VALUES ('domain', %s, 'user')", (value,))
    with pytest.raises(errors.UniqueViolation):
        api_rw.execute("INSERT INTO app.allowlist (kind, value, default_role) VALUES ('domain', %s, 'admin')", (value,))


def test_deleting_a_user_cascades_to_sessions_and_nulls_added_by(api_rw):
    admin_id = api_rw.execute("INSERT INTO app.users (email) VALUES (%s) RETURNING id", (mail(),)).fetchone()[0]
    entry_value = f"schema-test-{uuid.uuid4().hex[:8]}.example"
    api_rw.execute(
        "INSERT INTO app.allowlist (kind, value, default_role, added_by) VALUES ('domain', %s, 'user', %s)",
        (entry_value, admin_id),
    )
    api_rw.execute(
        "INSERT INTO app.sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (admin_id, uuid.uuid4().bytes, _in_one_hour()),
    )
    api_rw.execute("DELETE FROM app.users WHERE id = %s", (admin_id,))
    assert api_rw.execute("SELECT count(*) FROM app.sessions WHERE user_id = %s", (admin_id,)).fetchone() == (0,)
    assert api_rw.execute(
        "SELECT added_by FROM app.allowlist WHERE value = %s", (entry_value,)
    ).fetchone() == (None,)
