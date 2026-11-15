"""US2: required extensions exist and agent_ro is fenced off from app and kb."""

from __future__ import annotations

import pytest

from conftest import connect


@pytest.mark.parametrize("extension", ["vector", "pg_trgm", "citext"])
def test_extension_installed(extension, superuser):
    row = superuser.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (extension,)).fetchone()
    assert row is not None, f"extension {extension} is missing"


@pytest.mark.parametrize("schema", ["app", "kb"])
def test_agent_has_no_usage_on_app_or_kb(schema):
    with connect("agent_ro") as conn:
        usage = conn.execute("SELECT has_schema_privilege('agent_ro', %s, 'USAGE')", (schema,)).fetchone()
        assert usage == (False,)
        create = conn.execute("SELECT has_schema_privilege('agent_ro', %s, 'CREATE')", (schema,)).fetchone()
        assert create == (False,)


def test_agent_has_no_privileges_on_existing_app_or_kb_tables(app_owner, superuser):
    app_owner.execute("CREATE TABLE IF NOT EXISTS app.ext_probe (id int)")
    app_owner.execute("CREATE TABLE IF NOT EXISTS kb.ext_probe (id int)")
    try:
        # asked as superuser: as agent_ro the lookup itself fails (no USAGE on the schema),
        # which is covered by test_agent_has_no_usage_on_app_or_kb
        for table in ("app.ext_probe", "kb.ext_probe"):
            allowed = superuser.execute(
                "SELECT has_table_privilege('agent_ro', %s, 'SELECT')", (table,)
            ).fetchone()
            assert allowed == (False,), table
    finally:
        app_owner.execute("DROP TABLE IF EXISTS app.ext_probe")
        app_owner.execute("DROP TABLE IF EXISTS kb.ext_probe")


def test_roles_are_not_privileged(superuser):
    rows = superuser.execute(
        "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
        "WHERE rolname IN ('app_owner','sync_writer','agent_ro','backup_ro','api_rw')"
    ).fetchall()
    assert len(rows) == 5
    for name, sup, createdb, createrole in rows:
        assert (sup, createdb, createrole) == (False, False, False), name
