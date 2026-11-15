"""US2: the permission matrix from contracts/database-access.md.

Every row marked "✘" in the contract must fail with a permission error; every "✔" must succeed.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors

from conftest import connect

DENIED = (errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)


@pytest.fixture
def serving_table(sync_writer):
    sync_writer.execute("DROP TABLE IF EXISTS serving.test_items")
    sync_writer.execute("CREATE TABLE serving.test_items (id int PRIMARY KEY, label text)")
    sync_writer.execute("INSERT INTO serving.test_items VALUES (1, 'one'), (2, 'two')")
    yield "serving.test_items"
    sync_writer.execute("DROP TABLE IF EXISTS serving.test_items")


@pytest.fixture
def app_and_kb_tables(app_owner):
    app_owner.execute("CREATE TABLE IF NOT EXISTS app.perm_probe (id int)")
    app_owner.execute("CREATE TABLE IF NOT EXISTS kb.perm_probe (id int)")
    yield
    app_owner.execute("DROP TABLE IF EXISTS app.perm_probe")
    app_owner.execute("DROP TABLE IF EXISTS kb.perm_probe")


def _denied(conn, sql):
    with pytest.raises(DENIED):
        conn.execute(sql)


# ---- SELECT in serving -------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["agent_ro", "sync_writer", "backup_ro"])
def test_select_in_serving_allowed(role, serving_table):
    with connect(role) as conn:
        assert conn.execute(f"SELECT count(*) FROM {serving_table}").fetchone() == (2,)


def test_app_owner_cannot_select_serving(app_owner, serving_table):
    _denied(app_owner, f"SELECT * FROM {serving_table}")


# ---- writes and DDL in serving ----------------------------------------------------------------

WRITE_STATEMENTS = [
    "INSERT INTO serving.test_items VALUES (3, 'x')",
    "UPDATE serving.test_items SET label = 'x'",
    "DELETE FROM serving.test_items",
    "TRUNCATE serving.test_items",
    "CREATE TABLE serving.should_fail (id int)",
    "ALTER TABLE serving.test_items ADD COLUMN extra int",
    "ALTER TABLE serving.test_items RENAME TO test_items_renamed",
    "DROP TABLE serving.test_items",
]


@pytest.mark.parametrize("role", ["agent_ro", "app_owner", "backup_ro", "api_rw"])
@pytest.mark.parametrize("statement", WRITE_STATEMENTS)
def test_writes_and_ddl_in_serving_denied(role, statement, serving_table):
    with connect(role) as conn:
        _denied(conn, statement)


def test_sync_writer_can_write_and_change_structure(sync_writer, serving_table):
    sync_writer.execute("INSERT INTO serving.test_items VALUES (3, 'three')")
    sync_writer.execute("UPDATE serving.test_items SET label = 'uno' WHERE id = 1")
    sync_writer.execute("DELETE FROM serving.test_items WHERE id = 3")
    sync_writer.execute("ALTER TABLE serving.test_items ADD COLUMN extra int")
    sync_writer.execute("CREATE TABLE serving.test_items_tmp (id int)")
    sync_writer.execute("ALTER TABLE serving.test_items_tmp RENAME TO test_items_tmp2")
    sync_writer.execute("DROP TABLE serving.test_items_tmp2")


# ---- app and kb ---------------------------------------------------------------------------------

@pytest.mark.parametrize("schema", ["app", "kb"])
@pytest.mark.parametrize("role", ["agent_ro", "sync_writer"])
def test_agent_and_sync_cannot_read_or_write_app_and_kb(role, schema, app_and_kb_tables):
    with connect(role) as conn:
        _denied(conn, f"SELECT * FROM {schema}.perm_probe")
        _denied(conn, f"INSERT INTO {schema}.perm_probe VALUES (1)")
        _denied(conn, f"CREATE TABLE {schema}.should_fail (id int)")


@pytest.mark.parametrize("schema", ["app", "kb"])
def test_app_owner_and_backup_read_app_and_kb(schema, app_and_kb_tables):
    with connect("app_owner") as conn:
        conn.execute(f"INSERT INTO {schema}.perm_probe VALUES (1)")
        assert conn.execute(f"SELECT count(*) FROM {schema}.perm_probe").fetchone() == (1,)
    with connect("backup_ro") as conn:
        assert conn.execute(f"SELECT count(*) FROM {schema}.perm_probe").fetchone() == (1,)
        _denied(conn, f"INSERT INTO {schema}.perm_probe VALUES (2)")


# ---- public, temp tables, roles, extensions ------------------------------------------------------

ALL_ROLES = ["agent_ro", "sync_writer", "app_owner", "backup_ro", "api_rw"]


@pytest.mark.parametrize("role", ALL_ROLES)
def test_cannot_create_in_public(role):
    with connect(role) as conn:
        _denied(conn, "CREATE TABLE public.should_fail (id int)")


@pytest.mark.parametrize("role", ALL_ROLES)
def test_cannot_create_temp_tables(role):
    with connect(role) as conn:
        _denied(conn, "CREATE TEMP TABLE should_fail (id int)")


@pytest.mark.parametrize("role", ALL_ROLES)
def test_cannot_create_roles_or_extensions(role):
    with connect(role) as conn:
        _denied(conn, "CREATE ROLE should_not_exist")
        # a role with no schema to create in (api_rw) is stopped earlier with InvalidSchemaName
        with pytest.raises(DENIED + (errors.InvalidSchemaName,)):
            conn.execute("CREATE EXTENSION IF NOT EXISTS hstore")
    with connect("postgres") as admin:
        assert admin.execute("SELECT 1 FROM pg_extension WHERE extname = 'hstore'").fetchone() is None


# ---- agent_ro cannot talk its way out of read-only ----------------------------------------------

def test_agent_cannot_write_even_after_switching_to_read_write(serving_table):
    with connect("agent_ro") as conn:
        conn.execute("SET transaction_read_only = off")
        _denied(conn, "INSERT INTO serving.test_items VALUES (9, 'nine')")


def test_agent_cannot_use_set_role_to_escalate(serving_table):
    with connect("agent_ro") as conn:
        for target in ("sync_writer", "app_owner", "postgres"):
            with pytest.raises((errors.InsufficientPrivilege, psycopg.Error)):
                conn.execute(f"SET ROLE {target}")
